"""Cleaning the forward-test journal: backtest lines move out, evidence stays, nothing is deleted."""

import json
from pathlib import Path

from app.mtf_cleanup import classify, main, quarantine, report, split


def _rec(event="decision", **extra):
    return {"event": event, "ts_utc": "2026-09-29T10:00:00Z", "client_order_id": "cid", **extra}


def _journal(tmp_path):
    path = tmp_path / "mtf_journal.jsonl"
    lines = [
        _rec(running_mode="RealTime"),
        _rec(running_mode="Backtesting", approved=True),
        _rec(running_mode="Optimization", approved=True),
        _rec(),                                   # written before the cBot sent running_mode
        _rec(event="fill", running_mode="Backtesting"),
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def test_a_line_is_classified_by_the_mode_the_bot_reported():
    assert classify(_rec(running_mode="RealTime")) == "realtime"
    assert classify(_rec(running_mode="Backtesting")) == "backtest"
    assert classify(_rec(running_mode="Optimization")) == "backtest"
    assert classify(_rec()) == "unknown"


def test_the_default_policy_only_moves_explicit_backtest_lines():
    records = [_rec(running_mode="RealTime"), _rec(running_mode="Backtesting"), _rec()]
    keep, removed = split(records)
    assert len(keep) == 2 and len(removed) == 1
    assert removed[0]["running_mode"] == "Backtesting"


def test_unknown_lines_move_only_when_asked_for():
    records = [_rec(running_mode="RealTime"), _rec()]
    keep, removed = split(records, include_unknown=True)
    assert len(keep) == 1 and len(removed) == 1


def test_the_report_counts_what_polluted_the_ledger():
    out = report([_rec(running_mode="RealTime", approved=True), _rec(running_mode="Backtesting", approved=True), _rec()])
    assert out["lines"] == 3
    assert out["by_class"] == {"realtime": 1, "backtest": 1, "unknown": 1}
    assert out["approved_decisions"] == {"realtime": 1, "backtest": 1}
    assert out["by_event"]["backtest"] == {"decision": 1}


def test_a_dry_run_writes_nothing(tmp_path):
    path = _journal(tmp_path)
    before = path.read_text(encoding="utf-8")
    out = quarantine(path, apply=False)
    assert out["removed"] == 3 and out["applied"] is False
    assert path.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*quarantine*")) == []


def test_apply_rewrites_the_journal_and_keeps_the_removed_lines_beside_it(tmp_path):
    path = _journal(tmp_path)
    out = quarantine(path, apply=True)
    assert out["applied"] is True
    kept = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [rec.get("running_mode") for rec in kept] == ["RealTime", None]
    quarantined = [json.loads(line) for line in Path(out["quarantine_path"]).read_text(encoding="utf-8").splitlines()]
    assert len(quarantined) == 3
    assert all(r["running_mode"] in ("Backtesting", "Optimization") for r in quarantined)
    assert out["after"]["by_class"] == {"realtime": 1, "unknown": 1}


def test_include_unknown_empties_a_pre_fix_journal_completely(tmp_path):
    path = _journal(tmp_path)
    quarantine(path, include_unknown=True, apply=True)
    assert [json.loads(line)["running_mode"] for line in path.read_text(encoding="utf-8").splitlines()] == ["RealTime"]


def test_a_clean_journal_is_left_untouched(tmp_path):
    path = tmp_path / "mtf_journal.jsonl"
    path.write_text(json.dumps(_rec(running_mode="RealTime")) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    out = quarantine(path, apply=True)
    assert out["removed"] == 0 and path.read_text(encoding="utf-8") == before


def test_the_cli_reports_and_exits_zero_on_a_dry_run(tmp_path, capsys):
    path = _journal(tmp_path)
    assert main([str(path)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["removed"] == 3 and body["applied"] is False
    assert main([str(tmp_path / "missing.jsonl")]) == 1
