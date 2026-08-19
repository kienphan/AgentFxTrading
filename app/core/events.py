"""
Async event bus for the FX Trading Agent.
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional
from app.core.logger import get_logger

log = get_logger(__name__)


class EventType(Enum):
    TICK_RECEIVED = auto()
    CANDLE_CLOSED = auto()
    SIGNAL_GENERATED = auto()
    RISK_APPROVED = auto()
    RISK_REJECTED = auto()
    ORDER_PLACED = auto()
    ORDER_FILLED = auto()
    ORDER_CANCELLED = auto()
    POSITION_OPENED = auto()
    POSITION_CLOSED = auto()
    CONNECTION_LOST = auto()
    CONNECTION_RESTORED = auto()


AsyncHandler = Callable[["TradingEvent"], Coroutine[Any, Any, None]]


@dataclass
class TradingEvent:
    type: EventType
    symbol: Optional[str] = None
    data: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


class EventBus:
    """Async publish/subscribe event bus."""

    def __init__(self) -> None:
        self._handlers: Dict[EventType, List[AsyncHandler]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, event_type: EventType, handler: AsyncHandler) -> None:
        async with self._lock:
            self._handlers.setdefault(event_type, [])
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)
                log.debug(f"Subscribed handler '{handler.__name__}' to {event_type.name}")

    async def unsubscribe(self, event_type: EventType, handler: AsyncHandler) -> None:
        async with self._lock:
            handlers = self._handlers.get(event_type, [])
            try:
                handlers.remove(handler)
                log.debug(f"Unsubscribed handler '{handler.__name__}' from {event_type.name}")
            except ValueError:
                pass

    async def publish(self, event: TradingEvent) -> None:
        async with self._lock:
            handlers = list(self._handlers.get(event.type, []))
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                log.error(f"Handler '{handler.__name__}' raised {type(result).__name__}: {result}")

    def subscribe_sync(self, event_type: EventType, handler: AsyncHandler) -> None:
        self._handlers.setdefault(event_type, [])
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def clear(self, event_type: Optional[EventType] = None) -> None:
        if event_type is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event_type, None)

    def handler_count(self, event_type: EventType) -> int:
        return len(self._handlers.get(event_type, []))
