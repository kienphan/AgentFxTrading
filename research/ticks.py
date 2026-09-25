"""Decode the cTrader CLI backtest tick cache (.zticks) into bid/ask arrays."""
import gzip
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

PRICE_SCALE = 1e5
DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class Ticks:
    ts: np.ndarray   # int64, ms UTC
    bid: np.ndarray  # float64
    ask: np.ndarray  # float64

    def __len__(self) -> int:
        return len(self.ts)


def day_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def decode_day(path: Path) -> np.ndarray:
    """(n, 3) int64 rows of one .zticks file: ts ms UTC, bid x1e5, ask x1e5; 0 = side unchanged."""
    raw = gzip.decompress(Path(path).read_bytes())
    return np.frombuffer(raw, dtype="<i8").reshape(-1, 3)


def _ffill(col: np.ndarray) -> np.ndarray:
    """Each 0 becomes the last non-zero value before it; leading zeros stay 0."""
    idx = np.where(col != 0, np.arange(col.size), 0)
    np.maximum.accumulate(idx, out=idx)
    return col[idx]


def load_ticks(symbol: str, start: date, end: date, data_dir: Path = DATA_DIR) -> Ticks:
    """Ticks of the UTC days start..end (both included). Ticks before both sides are known are dropped."""
    days = []
    d = start
    while d <= end:
        path = data_dir / symbol / "t1" / f"{d:%Y%m%d}.zticks"
        if path.exists():
            days.append(decode_day(path))
        d += timedelta(days=1)
    if not days:
        return Ticks(np.empty(0, np.int64), np.empty(0), np.empty(0))
    rows = np.concatenate(days)
    bid, ask = _ffill(rows[:, 1]), _ffill(rows[:, 2])
    keep = (bid != 0) & (ask != 0)
    return Ticks(rows[keep, 0].copy(), bid[keep] / PRICE_SCALE, ask[keep] / PRICE_SCALE)
