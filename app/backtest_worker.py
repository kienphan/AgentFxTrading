"""
Runs the Backtest page's jobs, one at a time, in `bt-<id>` containers
(docs/superpowers/specs/2026-09-24-backtest-page-design.md).

The asyncio loop only schedules. tick() does the work in a thread, so Docker and DB calls never block
the event loop that serves /trade and /ws/cbot. Containers outlive the app process, so every tick
starts from the containers labelled agentfx.backtest: after a restart it picks a running job back up,
collects one that finished meanwhile, and marks one whose container vanished as lost.
"""
import asyncio
import io
import json
import logging
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from app import backtest_store as store
from app.backtest_command import LABEL, REPORT_PATH, build_container_spec
from app.backtest_report import summarize
from app.ctrader_accounts import ctrader_home

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLL_S = 3.0
TIMEOUT_S = 45 * 60
LOG_TAIL = 20
ERROR_TAIL = 50
PROGRESS_RE = re.compile(r"^Progress \| (.+?) \| ([\d.]+) %", re.MULTILINE)


def parse_progress(logs: str) -> Optional[Tuple[str, float]]:
    """The CLI prints `Progress | Loading EURUSD, m15 | 33.87 % |`, then `Progress | Backtesting | 20.50 % |`."""
    found = PROGRESS_RE.findall(logs or "")
    if not found:
        return None
    text, pct = found[-1]
    return ("backtesting" if text.startswith("Backtesting") else "loading"), float(pct)


def _docker_client():
    from app.docker_manager import docker_manager
    return docker_manager.client if docker_manager.is_available else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BacktestWorker:
    def __init__(self, client_provider: Callable = _docker_client, db_target=None,
                 report_dir: Optional[Path] = None, project_root: Path = PROJECT_ROOT,
                 ctrader_home_dir: Optional[Path] = None, clock: Callable[[], datetime] = _utcnow):
        self.client_provider = client_provider
        self.db_target = db_target
        self.report_dir = report_dir
        self.project_root = Path(project_root)
        self.ctrader_home_dir = ctrader_home_dir
        self.clock = clock
        self._running = False

    async def run_loop(self):
        self._running = True
        logger.info("Backtest worker started")
        while self._running:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:
                logger.error(f"Backtest worker tick failed: {e}", exc_info=True)
            await asyncio.sleep(POLL_S)

    def stop(self):
        self._running = False

    def tick(self) -> None:
        client = self.client_provider()
        if client is None:
            return
        containers = {}
        for container in client.containers.list(all=True, filters={"label": LABEL}):
            try:
                containers[int(container.labels.get(LABEL, ""))] = container
            except ValueError:
                continue
        with store.connect(self.db_target) as conn:
            # Containers whose job was cancelled, deleted or already finished.
            for job_id, container in list(containers.items()):
                job = store.get_job(conn, job_id)
                if job is None or job["status"] != "running":
                    self._remove(container)
                    containers.pop(job_id)
            for job in store.jobs_with_status(conn, "running"):
                container = containers.get(job["id"])
                try:
                    if container is None:
                        self._fail(conn, job, "container lost")
                    elif container.status in ("exited", "dead"):
                        self._collect(conn, job, container)
                    elif self._elapsed(job) > TIMEOUT_S:
                        self._remove(container)
                        self._fail(conn, job, f"timeout ({TIMEOUT_S // 60} min)")
                    else:
                        progress = parse_progress(self._logs(container, LOG_TAIL))
                        if progress:
                            store.update_job(conn, job["id"], expect_status="running",
                                             phase=progress[0], progress=progress[1])
                except Exception as e:
                    # One job's unexpected failure (a Docker SDK call, a write to disk) must never
                    # block the queue or leave the other jobs stuck.
                    logger.error(f"Backtest #{job['id']} worker error: {e}", exc_info=True)
                    self._fail(conn, job, f"worker error: {e}")
                    if container is not None:
                        self._remove(container)
            if not store.jobs_with_status(conn, "running"):
                job = store.next_queued(conn)
                if job:
                    self._start(conn, client, job)

    def _start(self, conn, client, job: Dict) -> None:
        try:
            home = self.ctrader_home_dir or Path(ctrader_home())
            container = client.containers.run(**build_container_spec(job, self.project_root, home))
        except Exception as e:
            self._fail(conn, job, f"start failed: {e}", expect="queued")
            return
        started = store.update_job(conn, job["id"], expect_status="queued", status="running", phase="starting",
                                   progress=0.0, started_at=store.now_text())
        if not started:
            # A cancel won the race between next_queued() and this update: the row is no longer
            # "queued", so leave it alone and stop the container that just started for it.
            self._remove(container)
            return
        logger.info(f"Backtest #{job['id']} started ({job['symbol']} {job['start_date']}..{job['end_date']})")

    def _collect(self, conn, job: Dict, container) -> None:
        exit_code = (container.attrs.get("State") or {}).get("ExitCode")
        raw = self._read_report(container) if exit_code == 0 else None
        if raw is None:
            reason = "no report written" if exit_code == 0 else f"exit code {exit_code}"
            self._fail(conn, job, f"{reason}\n{self._logs(container, ERROR_TAIL).strip()}".strip())
        else:
            try:
                store.write_report(job["id"], raw, self.report_dir)
            except OSError as e:
                self._fail(conn, job, f"report not saved: {e}")
            else:
                try:
                    summary = summarize(json.loads(raw))
                except Exception as e:
                    self._fail(conn, job, f"report unparsable: {e}")
                else:
                    store.update_job(conn, job["id"], expect_status="running", status="done", phase="done",
                                     progress=100.0, summary=summary, finished_at=store.now_text())
                    logger.info(f"Backtest #{job['id']} done: net {summary['net_profit']}, {summary['total_trades']} trades")
        self._remove(container)

    def _fail(self, conn, job: Dict, error: str, expect: str = "running") -> None:
        store.update_job(conn, job["id"], expect_status=expect, status="failed", error=error,
                         finished_at=store.now_text())
        logger.warning(f"Backtest #{job['id']} failed: {error.splitlines()[0]}")

    def _elapsed(self, job: Dict) -> float:
        started = job.get("started_at")
        if not started:
            return 0.0
        at = datetime.strptime(started[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (self.clock() - at).total_seconds()

    @staticmethod
    def _read_report(container) -> Optional[bytes]:
        try:
            stream, _ = container.get_archive(REPORT_PATH)
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
                member = next((m for m in tar.getmembers() if m.isfile()), None)
                handle = tar.extractfile(member) if member else None
                return handle.read() if handle else None
        except Exception:
            return None

    @staticmethod
    def _logs(container, tail: int) -> str:
        try:
            return container.logs(tail=tail).decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _remove(container) -> None:
        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning(f"Could not remove backtest container {getattr(container, 'name', '?')}: {e}")


backtest_worker = BacktestWorker()
