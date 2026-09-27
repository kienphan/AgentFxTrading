"""The forward-test verdict is computed from the journal, never from memory."""

import json

from app.mtf_review import load, review


def _decision(cid, action="SELL_LIMIT", approved=True, entry=4284.0, stop=4296.0, tp=4260.0, gate="Order authorised"):
    return {"event": "decision", "client_order_id": cid if approved else "", "approved": approved,
            "action": action if approved else "WAIT", "gate_reason": gate,
            "intended": {"entry": entry, "stop": stop, "take_profit": tp}}


def _fill(cid, fill=4284.0, stop=4296.0, side="SELL", balance=10000.0, risk_pct=0.5):
    return {"event": "fill", "client_order_id": cid, "side": side, "fill_entry": fill, "stop": stop,
            "balance": balance, "risk_pct": risk_pct}


def _close(cid, net, reason, exit_=None):
    return {"event": "close", "client_order_id": cid, "net": net, "exit_reason": reason, "fill_exit": exit_}


def _trade(cid, net, reason):
    return [_decision(cid), _fill(cid), _close(cid, net, reason)]


def test_expectancy_is_net_money_over_the_money_risked():
    # 0.5% of 10 000 = 50 risked per trade: +100 is +2R, -50 is -1R
    recs = _trade("a", 100.0, "take_profit") + _trade("b", -50.0, "stop_loss")
    out = review(recs)
    assert out["trades"] == 2
    assert out["win_rate"] == 50.0
    assert out["expectancy_r"] == 0.5
    assert out["total_r"] == 1.0


def test_max_drawdown_is_measured_in_r_from_the_equity_peak():
    recs = []
    for cid, net in [("a", 100.0), ("b", -50.0), ("c", -50.0), ("d", -50.0), ("e", 100.0)]:
        recs += _trade(cid, net, "take_profit" if net > 0 else "stop_loss")
    out = review(recs)
    assert out["max_drawdown_r"] == 3.0
    assert out["max_loss_streak"] == 3


def test_small_samples_are_flagged_as_noise():
    out = review(_trade("a", 100.0, "take_profit"))
    assert out["trades"] == 1
    assert "noise" in out["verdict"]


def test_manual_exits_and_waits_are_counted():
    recs = _trade("a", 20.0, "manual") + _trade("b", -50.0, "stop_loss")
    recs += [_decision("", approved=False, gate="timeframes conflict")] * 3
    recs += [_decision("", approved=False, gate="Confidence 50 is below 65")]
    out = review(recs)
    assert out["exit_reasons"] == {"manual": 1, "stop_loss": 1}
    assert out["manual_exit_rate"] == 50.0
    assert out["decisions"] == {"total": 6, "approved": 2, "wait": 4}
    assert out["top_wait_reasons"][0] == ["timeframes conflict", 3]


def test_entry_slippage_and_intended_risk_drift_are_reported():
    # sold 0.5 lower than planned: adverse slippage for a SELL
    recs = [_decision("a", entry=4284.0, stop=4296.0), _fill("a", fill=4283.5, stop=4296.0), _close("a", -52.0, "stop_loss")]
    out = review(recs)
    assert out["avg_entry_slippage"] == 0.5
    assert out["avg_actual_vs_intended_risk"] == 1.04


def test_open_trades_and_orphan_closes_are_not_counted_as_results():
    recs = [_decision("a"), _fill("a")] + [_close("zzz", 100.0, "take_profit")]
    out = review(recs)
    assert out["trades"] == 0
    assert out["open_trades"] == 1
    assert out["orphan_closes"] == 1


def test_load_reads_jsonl_and_skips_broken_lines(tmp_path):
    path = tmp_path / "j.jsonl"
    path.write_text(json.dumps({"event": "decision"}) + "\nnot json\n\n" + json.dumps({"event": "close"}) + "\n", encoding="utf-8")
    assert [r["event"] for r in load(path)] == ["decision", "close"]
