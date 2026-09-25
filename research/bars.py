"""Bid OHLC bars built from ticks the way cTrader builds them, plus the tick that closes each bar."""
import gzip
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from research.ticks import Ticks

MINUTE_MS = 60_000


@dataclass
class Bars:
    open_time: np.ndarray   # int64 ms, bucket start (UTC-aligned)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    first_tick: np.ndarray  # index of the bar's first tick
    event_tick: np.ndarray  # first tick after the bar: OnBarClosed runs and orders fill there; len(ticks) if none
    minutes: int

    def __len__(self) -> int:
        return len(self.open_time)

    @property
    def close_time(self) -> np.ndarray:
        return self.open_time + self.minutes * MINUTE_MS


def build_bars(t: Ticks, minutes: int) -> Bars:
    span = minutes * MINUTE_MS
    bucket = t.ts // span
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(t)]
    return Bars(
        open_time=bucket[starts] * span,
        o=t.bid[starts],
        h=np.maximum.reduceat(t.bid, starts),
        l=np.minimum.reduceat(t.bid, starts),
        c=t.bid[ends - 1],
        first_tick=starts,
        event_tick=ends,
        minutes=minutes,
    )


def decode_zbars(path: Path) -> np.ndarray:
    """(n, 6) int64 rows of a cTrader .zbars file: ts, open, high, low, close (x1e5), tick count."""
    raw = gzip.decompress(Path(path).read_bytes())
    return np.frombuffer(raw, dtype="<i8").reshape(-1, 6)
