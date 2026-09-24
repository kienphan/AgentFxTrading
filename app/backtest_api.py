"""
HTTP API of the dashboard's Backtest page (docs/superpowers/specs/2026-09-24-backtest-page-design.md).

Handlers are plain `def`: FastAPI runs them in its threadpool, so the Docker and DB calls never block
the event loop that serves the cBots (see the note above /api/bots in app/dashboard.py).
"""
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Literal, Optional, Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import backtest_store as store
from app.backtest_command import BotSource, algo_host_path, describe_source, parse_run_command
from app.backtest_params import (DockerUnavailable, MetadataCache, MetadataError, OverrideError, job_params,
                                 param_view, validate_overrides)
from app.backtest_report import ui_payload
from app.ctrader_accounts import ctrader_home
from app.portfolio import get_portfolio_manager

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_SPAN_DAYS = 365
PRIVATE_FIELDS = ("ctid", "pwd_file")
# The list view never reads `params` (only `overrides`, for the chips); dropping it keeps the
# 3 s poll of up to 200 rows light.
LIST_OMITTED_FIELDS = PRIVATE_FIELDS + ("params",)
metadata_cache = MetadataCache(store.REPORT_DIR / "metadata")


def docker_available() -> bool:
    from app.docker_manager import docker_manager
    return bool(docker_manager.is_available)


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


class BacktestCreate(BaseModel):
    bot_name: str
    start: date
    end: date
    data_mode: Literal["ticks", "m1"] = "ticks"
    spread_pips: Optional[float] = None
    balance: float = 10000.0
    note: Optional[str] = None
    overrides: Dict[str, Union[bool, int, float, str]] = Field(default_factory=dict)


def validate_run_config(req: BacktestCreate, today: date) -> None:
    if req.start > req.end:
        raise HTTPException(422, "start must be on or before end")
    if req.end > today:
        raise HTTPException(422, "end must not be after today (UTC)")
    if (req.end - req.start).days + 1 > MAX_SPAN_DAYS:
        raise HTTPException(422, f"the range must be at most {MAX_SPAN_DAYS} days")
    if req.data_mode == "m1":
        if req.spread_pips is None or not math.isfinite(req.spread_pips) or req.spread_pips <= 0:
            raise HTTPException(422, "m1 data needs a finite spread in pips (> 0): m1 bars carry no spread")
    elif req.spread_pips is not None:
        raise HTTPException(422, "spread_pips is only used with m1 data")
    if not math.isfinite(req.balance) or not 100 <= req.balance <= 10_000_000:
        raise HTTPException(422, "balance must be a finite number between 100 and 10,000,000")
    if req.note is not None and (len(req.note) > 200 or re.search(r"[\x00-\x1f]", req.note)):
        raise HTTPException(422, "note must be at most 200 characters on one line")


def _load_source(bot_name: str) -> BotSource:
    cfg = get_portfolio_manager().get_cbot_config(bot_name)
    if cfg is None:
        raise HTTPException(404, f"unknown bot {bot_name}")
    row = describe_source(cfg["name"], cfg["run_command"], PROJECT_ROOT, Path(ctrader_home()))
    if not row["supported"]:
        raise HTTPException(422, row["reason"])
    return parse_run_command(cfg["name"], cfg["run_command"])


def _metadata(source: BotSource) -> Dict:
    if not docker_available():
        raise HTTPException(503, "Docker is not available on this server")
    try:
        return metadata_cache.get(algo_host_path(source.algo, PROJECT_ROOT))
    except DockerUnavailable as e:
        raise HTTPException(503, str(e))
    except MetadataError as e:
        raise HTTPException(502, str(e))


def _public(job: Dict, omit: tuple = PRIVATE_FIELDS) -> Dict:
    return {k: v for k, v in job.items() if k not in omit}


def _job_or_404(conn, job_id: int) -> Dict:
    job = store.get_job(conn, job_id)
    if job is None:
        raise HTTPException(404, f"backtest {job_id} not found")
    return job


@router.get("/api/backtests/sources")
def api_backtest_sources():
    home = Path(ctrader_home())
    rows = [describe_source(c["name"], c["run_command"], PROJECT_ROOT, home)
            for c in get_portfolio_manager().get_cbot_configs()]
    rows.sort(key=lambda r: (not r["supported"], r["name"]))
    return {"sources": rows, "docker_available": docker_available()}


@router.get("/api/backtests/sources/{bot_name}/params")
def api_backtest_source_params(bot_name: str):
    source = _load_source(bot_name)
    return param_view(_metadata(source), source)


@router.post("/api/backtests", status_code=201)
def api_create_backtest(req: BacktestCreate):
    validate_run_config(req, today_utc())
    source = _load_source(req.bot_name)
    meta = _metadata(source)
    try:
        overrides = validate_overrides(meta, source, req.overrides)
    except OverrideError as e:
        raise HTTPException(422, str(e))
    fields = {
        "bot_name": source.name, "strategy": source.strategy, "symbol": source.symbol, "period": source.period,
        "algo": source.algo, "algo_sha": meta.get("sha256"), "algo_build_time": meta.get("BuildTime"),
        "ctid": source.ctid, "account": source.account, "pwd_file": source.pwd_file,
        "start_date": req.start.isoformat(), "end_date": req.end.isoformat(), "data_mode": req.data_mode,
        "spread_pips": req.spread_pips, "balance": req.balance, "overrides": overrides,
        "params": job_params(source, overrides), "note": (req.note or "").strip() or None,
    }
    with store.connect() as conn:
        job_id = store.create_job(conn, fields)
        position = store.queue_position(conn, job_id)
    return {"id": job_id, "status": "queued", "queue_position": position}


@router.get("/api/backtests")
def api_list_backtests():
    with store.connect() as conn:
        jobs = store.list_jobs(conn)
    return {"jobs": [_public(j, LIST_OMITTED_FIELDS) for j in jobs], "docker_available": docker_available()}


@router.get("/api/backtests/{job_id}")
def api_get_backtest(job_id: int):
    with store.connect() as conn:
        job = _job_or_404(conn, job_id)
    result = _public(job)
    report = store.read_report(job_id) if job["status"] == "done" else None
    result["report"] = ui_payload(report) if report else None
    return result


@router.get("/api/backtests/{job_id}/report.json")
def api_backtest_report_file(job_id: int):
    with store.connect() as conn:
        _job_or_404(conn, job_id)
    path = store.report_path(job_id)
    if not path.is_file():
        raise HTTPException(404, "no report for this backtest")
    return FileResponse(path, media_type="application/json", filename=f"backtest-{job_id}.json")


@router.post("/api/backtests/{job_id}/cancel")
def api_cancel_backtest(job_id: int):
    with store.connect() as conn:
        job = _job_or_404(conn, job_id)
        if job["status"] not in store.ACTIVE:
            raise HTTPException(409, f"backtest {job_id} is already {job['status']}")
        # A running job's container is stopped by the worker on its next tick.
        if not store.update_job(conn, job_id, expect_status=job["status"], status="cancelled",
                                finished_at=store.now_text()):
            raise HTTPException(409, f"backtest {job_id} changed state; try again")
    return {"id": job_id, "status": "cancelled"}


@router.delete("/api/backtests/{job_id}")
def api_delete_backtest(job_id: int):
    with store.connect() as conn:
        job = _job_or_404(conn, job_id)
        if job["status"] in store.ACTIVE:
            raise HTTPException(409, "cancel the backtest before deleting it")
        store.delete_job(conn, job_id)
    store.delete_report(job_id)
    return {"id": job_id, "deleted": True}
