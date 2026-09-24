"""Commands the dashboard sends to a cBot: close one position, or close all of the bot's positions.

The server cannot reach a cBot, so each bot polls GET /api/cbot/commands every 2 s (its
CommandPollMs) and reports back on POST /api/cbot/commands/{id}/result. The queue lives in this
process's memory because the poll route runs ~22 times a second with 45 containers and must never
touch the DB. That relies on the single-worker service (`uvicorn app.server:app`, no --workers).

    pending --(bot polls)--> delivered --(bot reports)--> done | failed
       |                         |
       +- not polled in 15 s --> expired      +- no report in 30 s --> unconfirmed

An expired command is never handed out: a bot that restarts hours later must not act on a stale
close. The bot also skips command ids it has already run.
"""

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional

PENDING_TTL_S = 15.0          # not polled by then -> expired, never delivered
RESULT_TIMEOUT_S = 30.0       # delivered but no report by then -> unconfirmed
RETAIN_S = 600.0              # finished commands stay readable for the dashboard this long
WAIT_POLL_S = 0.5             # wait_for_final's polling interval
CLOSE_AND_STOP_WAIT_S = 20.0  # how long Close & Stop waits for the bot's close_all result

ACTIONS = ("close_position", "close_all")
FINAL_STATUSES = ("done", "failed", "expired", "unconfirmed")


class CommandNotFound(KeyError):
    pass


class CommandNotOwned(PermissionError):
    pass


@dataclass
class Command:
    id: str
    bot_id: str
    action: str
    position_id: Optional[int]
    created_at: float
    status: str = "pending"
    message: str = ""
    closed: int = 0
    failed: int = 0
    delivered_at: Optional[float] = None
    finished_at: Optional[float] = None

    def for_bot(self) -> Dict:
        return {"id": self.id, "action": self.action, "position_id": self.position_id}

    def for_dashboard(self) -> Dict:
        return {"id": self.id, "bot_id": self.bot_id, "action": self.action,
                "position_id": self.position_id, "status": self.status, "message": self.message,
                "closed": self.closed, "failed": self.failed}


class CommandQueue:
    """Thread-safe: the poll route runs on the event loop, dashboard routes in the threadpool."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._commands: Dict[str, Command] = {}

    def enqueue(self, bot_id: str, action: str, position_id: Optional[int] = None) -> Command:
        """Queue a command; the same one still in flight (a double click) is returned instead."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        if action == "close_position" and position_id is None:
            raise ValueError("close_position needs a position_id")
        with self._lock:
            now = self._clock()
            self._age_all(now)
            for cmd in self._commands.values():
                if (cmd.bot_id == bot_id and cmd.action == action and cmd.position_id == position_id
                        and cmd.status in ("pending", "delivered")):
                    return replace(cmd)
            cmd = Command(id=uuid.uuid4().hex, bot_id=bot_id, action=action,
                          position_id=position_id, created_at=now)
            self._commands[cmd.id] = cmd
            return replace(cmd)

    def take_pending(self, bot_id: str) -> List[Command]:
        """The bot's pending commands, marked delivered: each is handed out once."""
        with self._lock:
            now = self._clock()
            taken = []
            for cmd in self._commands.values():
                if cmd.bot_id != bot_id:
                    continue
                self._age(cmd, now)
                if cmd.status == "pending":
                    cmd.status = "delivered"
                    cmd.delivered_at = now
                    taken.append(replace(cmd))
            return taken

    def record_result(self, command_id: str, bot_id: str, status: str, message: str = "",
                      closed: int = 0, failed: int = 0) -> Command:
        if status not in ("done", "failed"):
            raise ValueError(f"status must be 'done' or 'failed', not {status!r}")
        with self._lock:
            cmd = self._commands.get(command_id)
            if cmd is None:
                raise CommandNotFound(command_id)
            if cmd.bot_id != bot_id:
                raise CommandNotOwned(command_id)
            cmd.status = status
            cmd.message = message or ""
            cmd.closed = int(closed)
            cmd.failed = int(failed)
            cmd.finished_at = self._clock()
            return replace(cmd)

    def get(self, command_id: str) -> Optional[Command]:
        with self._lock:
            cmd = self._commands.get(command_id)
            if cmd is None:
                return None
            self._age(cmd, self._clock())
            return replace(cmd)

    # Module constants are read at call time so tests can shorten them.
    def _age(self, cmd: Command, now: float) -> None:
        if cmd.status == "pending" and now - cmd.created_at > PENDING_TTL_S:
            cmd.status = "expired"
            cmd.finished_at = now
            cmd.message = f"the bot did not pick up the command within {PENDING_TTL_S:.0f} s"
        elif cmd.status == "delivered" and now - (cmd.delivered_at or now) > RESULT_TIMEOUT_S:
            cmd.status = "unconfirmed"
            cmd.finished_at = now
            cmd.message = "the bot received the command but did not report a result"

    def _age_all(self, now: float) -> None:
        for command_id in list(self._commands):
            cmd = self._commands[command_id]
            self._age(cmd, now)
            if cmd.finished_at is not None and now - cmd.finished_at > RETAIN_S:
                del self._commands[command_id]


async def wait_for_final(queue: CommandQueue, command_id: str, timeout_s: float) -> Optional[Command]:
    """Poll `queue` until the command is final or `timeout_s` has passed; the latest copy either way."""
    deadline = time.monotonic() + timeout_s
    while True:
        cmd = queue.get(command_id)
        if cmd is None or cmd.status in FINAL_STATUSES or time.monotonic() >= deadline:
            return cmd
        await asyncio.sleep(WAIT_POLL_S)


command_queue = CommandQueue()
