"""Quarantine records a cTrader backtest leaked into the forward-test journal.

    python -m app.mtf_cleanup                  # report only, writes nothing
    python -m app.mtf_cleanup --apply          # move backtest lines into a quarantine file
    python -m app.mtf_cleanup --apply --include-unknown
    python -m app.mtf_cleanup --db             # also report the accounts/positions the DB holds

`trades/mtf_journal.jsonl` is forward-test evidence and append-only, so this tool never deletes a
line: it rewrites the journal with the RealTime lines and writes everything it removed to a
timestamped file beside it. Both stay readable and the split can be undone by hand.

A line is classified from its `running_mode` field, which the cBot started sending with the fix that
separates a backtest from a live run:

* ``realtime``  — the bot was running live; this is evidence.
* ``backtest``  — the bot was backtesting or optimising; simulated fills, not evidence.
* ``unknown``   — written before the cBot sent the field. It may be either, so it is only moved when
  ``--include-unknown`` says so: the journal of a bot that has never been backtested would otherwise
  be emptied on a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.mtf_agent import _journal_path, is_backtest
from app.mtf_review import load


def classify(record: Dict[str, Any]) -> str:
    """`realtime`, `backtest` or `unknown` for one journal line."""
    mode = record.get("running_mode")
    if mode is None or str(mode).strip() == "":
        return "unknown"
    return "backtest" if is_backtest(str(mode)) else "realtime"


def split(records: Iterable[Dict[str, Any]], include_unknown: bool = False) -> Tuple[List[Dict], List[Dict]]:
    """(kept, quarantined) journal lines for the requested policy."""
    keep: List[Dict] = []
    quarantined: List[Dict] = []
    for record in records:
        kind = classify(record)
        if kind == "realtime" or (kind == "unknown" and not include_unknown):
            keep.append(record)
        else:
            quarantined.append(record)
    return keep, quarantined


def report(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts per class and event, plus the approved decisions that polluted the ledger."""
    per_class: Counter = Counter()
    per_event: Dict[str, Counter] = {}
    approved: Counter = Counter()
    first, last = {}, {}
    for record in records:
        kind = classify(record)
        per_class[kind] += 1
        event = str(record.get("event") or "?")
        per_event.setdefault(kind, Counter())[event] += 1
        if record.get("approved"):
            approved[kind] += 1
        ts = str(record.get("ts_utc") or "")
        if ts:
            first[kind] = min(first.get(kind, ts), ts)
            last[kind] = max(last.get(kind, ts), ts)
    return {
        "lines": sum(per_class.values()),
        "by_class": dict(per_class),
        "by_event": {kind: dict(counts) for kind, counts in per_event.items()},
        "approved_decisions": dict(approved),
        "span": {kind: [first.get(kind), last.get(kind)] for kind in per_class},
    }


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    """Replace `path` atomically: a half-written evidence file is worse than none."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def quarantine(journal: Path, include_unknown: bool = False, apply: bool = False) -> Dict[str, Any]:
    """Report the split, and (with apply) rewrite the journal leaving the removed lines beside it."""
    records = load(journal)
    keep, removed = split(records, include_unknown=include_unknown)
    out = {
        "journal": str(journal),
        "before": report(records),
        "kept": len(keep),
        "removed": len(removed),
        "include_unknown": include_unknown,
        "applied": False,
    }
    if not removed:
        return out
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = journal.with_name(f"{journal.stem}.quarantine-{stamp}.jsonl")
    out["quarantine_path"] = str(quarantine_path)
    if apply:
        _write_jsonl(quarantine_path, removed)
        _write_jsonl(journal, keep)
        out["applied"] = True
        out["after"] = report(keep)
    return out


def db_report() -> Dict[str, Any]:
    """Read-only look at what a backtest may have written into the portfolio database."""
    out: Dict[str, Any] = {}
    # Outside the server nothing has wired the globals yet; init only opens a DB, and this stays read-only.
    try:
        from app.accounts import get_account_registry, init_account_registry

        try:
            registry = get_account_registry()
        except RuntimeError:
            registry = init_account_registry()
        out["accounts"] = [
            {key: row.get(key) for key in
             ("account_id", "account_number", "account_type", "label", "last_balance", "last_equity", "last_seen")}
            for row in registry.list_accounts(include_unconfigured=True)
        ]
    except Exception as exc:  # a cleanup report must still print when the DB is unreachable
        out["accounts_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from app.portfolio import get_portfolio_manager, init_portfolio

        try:
            pm = get_portfolio_manager()
        except RuntimeError:
            pm = init_portfolio()
        out["open_positions_per_bot"] = pm.open_position_counts()
    except Exception as exc:
        out["positions_error"] = f"{type(exc).__name__}: {exc}"
    out["note"] = (
        "A backtest's /portfolio/report calls overwrote last_balance/last_equity with simulated "
        "values: restore them from the broker (cTrader) rather than editing them by hand."
    )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Quarantine backtest lines from the MTF forward-test journal.")
    parser.add_argument("journal", nargs="?", default=None, help="journal path (default: MTF_JOURNAL_PATH)")
    parser.add_argument("--apply", action="store_true", help="rewrite the journal; without it nothing is written")
    parser.add_argument("--include-unknown", action="store_true",
                        help="also move lines written before the cBot sent running_mode")
    parser.add_argument("--db", action="store_true", help="also report the accounts and open positions in the DB")
    args = parser.parse_args(argv)

    journal = Path(args.journal) if args.journal else _journal_path()
    if not journal.exists():
        print(f"No journal at {journal}", file=sys.stderr)
        return 1
    out = quarantine(journal, include_unknown=args.include_unknown, apply=args.apply)
    if args.db:
        out["db"] = db_report()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if not args.apply and out["removed"]:
        print(f"\nDry run: {out['removed']} line(s) would move. Re-run with --apply.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
