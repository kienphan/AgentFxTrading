"""cTrader backtest report -> Backtest page summary and chart data.

The report's per-hour/weekday sections only count winning and losing trades, so the P&L buckets are
computed from history.items by UTC entry time; avg win/loss come from the same items."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backtest_report import MAX_EQUITY_POINTS, downsample, summarize, ui_payload  # noqa: E402

REPORT = json.loads((ROOT / "tests" / "fixtures" / "backtest" / "report_small.json").read_text())


def test_summary_reads_statistics_equity_and_history():
    assert summarize(REPORT) == {
        "net_profit": -0.98, "roi_pct": -0.01, "starting_capital": 10000.0, "ending_equity": 9999.02,
        "profit_factor": 0.98, "total_trades": 4, "winning_trades": 2, "losing_trades": 2, "win_rate": 50.0,
        "avg_trade": -0.25, "avg_win": 27.51, "avg_loss": -28.0,
        "max_equity_dd_pct": 0.65, "max_equity_dd": 65.7, "max_balance_dd_pct": 0.44,
        "commissions": -4.2, "swaps": -0.5, "largest_win": 30.0, "largest_loss": -44.0,
        "max_consecutive_losses": 2, "long_net": -14.0, "short_net": 13.02, "long_trades": 2, "short_trades": 2,
        "positions": 4, "winning_positions": 2, "losing_positions": 2, "position_win_rate": 50.0,
        "avg_position_win": 27.51, "avg_position_loss": -28.0, "full_loss_pct": 50.0,
    }


def _deal(deal_id, direction, entry_time, entry_price, net):
    return {"id": deal_id, "symbol": "EURUSD", "direction": direction, "net": net, "gross": net,
            "entryTime": entry_time, "closeTime": entry_time + 3_600_000, "entryPrice": entry_price,
            "closePrice": entry_price, "swaps": 0, "commissions": 0, "volume": 10000, "label": "bt-7",
            "comment": "bt-7", "quantity": 0.1, "pips": 0}


def _with_deals(*deals):
    report = copy.deepcopy(REPORT)
    report["history"]["items"] += list(deals)
    return report


def test_a_partial_close_and_its_runner_count_as_one_position():
    # FlowRSI closes half at 1R (deal 6) and the runner later at break-even (deal 7): two history
    # items, one position. cTrader's per-deal statistics stay as they are.
    s = summarize(_with_deals(_deal(6, "buy", 1785600000000, 1.142, 20.0),
                              _deal(7, "buy", 1785600000000, 1.142, 0.5)))
    assert (s["positions"], s["winning_positions"], s["losing_positions"]) == (5, 3, 2)
    assert s["position_win_rate"] == 60.0
    assert (s["avg_position_win"], s["avg_position_loss"]) == (25.17, -28.0)
    assert s["full_loss_pct"] == 40.0
    assert (s["total_trades"], s["win_rate"]) == (4, 50.0)


def test_a_position_that_loses_after_a_partial_close_is_not_a_full_size_loss():
    s = summarize(_with_deals(_deal(8, "sell", 1785700000000, 1.15, 10.0),
                              _deal(9, "sell", 1785700000000, 1.15, -30.0)))
    assert (s["positions"], s["losing_positions"], s["avg_position_loss"]) == (5, 3, -25.33)
    assert s["full_loss_pct"] == 40.0                  # only the two single-deal losses


def test_positions_opened_at_the_same_time_on_both_sides_stay_apart():
    s = summarize(_with_deals(_deal(10, "buy", 1785800000000, 1.16, 5.0),
                              _deal(11, "sell", 1785800000000, 1.16, -5.0)))
    assert (s["positions"], s["winning_positions"], s["losing_positions"]) == (6, 3, 3)


def test_summary_of_a_run_without_trades():
    empty = copy.deepcopy(REPORT)
    empty["history"]["items"] = []
    empty["tradeStatistics"] = {k: {"all": None, "long": None, "short": None} for k in REPORT["tradeStatistics"]}
    s = summarize(empty)
    assert (s["total_trades"], s["win_rate"], s["avg_win"], s["avg_loss"], s["profit_factor"]) == (0, 0.0, 0.0, 0.0, 0.0)
    assert (s["positions"], s["position_win_rate"], s["avg_position_win"], s["avg_position_loss"],
            s["full_loss_pct"]) == (0, 0.0, 0.0, 0.0, 0.0)


def test_pnl_by_hour_and_weekday_use_utc_entry_time():
    payload = ui_payload(REPORT)
    hours = payload["pnl_by_hour"]
    assert len(hours) == 24
    assert hours[8] == {"net": 25.02, "count": 1}
    assert hours[13] == {"net": -14.0, "count": 2}
    assert hours[21] == {"net": -12.0, "count": 1}
    assert sum(b["count"] for b in hours) == 4
    days = payload["pnl_by_weekday"]
    assert len(days) == 7
    assert days[0] == {"net": 25.02, "count": 1}      # Monday
    assert days[1] == {"net": -14.0, "count": 2}      # Tuesday
    assert days[4] == {"net": -12.0, "count": 1}      # Friday
    assert days[5] == {"net": 0.0, "count": 0}


def test_trades_are_sorted_by_entry_and_formatted():
    trades = ui_payload(REPORT)["trades"]
    assert [t["id"] for t in trades] == [2, 3, 4, 5]
    assert trades[0] == {"id": 2, "direction": "sell", "entry_time": "2026-07-27 08:00",
                         "close_time": "2026-07-27 10:30", "entry_price": 1.13901, "close_price": 1.13747,
                         "lots": 0.17, "pips": 15.4, "net": 25.02, "commissions": -1.16, "swaps": 0.0}


def test_equity_points_and_parameters():
    payload = ui_payload(REPORT)
    assert payload["equity"][0] == {"t": 1784851200000, "balance": 10000.0, "equity": 10000.0}
    assert payload["equity"][3] == {"t": 1785290400000, "balance": 10011.02, "equity": 9990.4}
    assert payload["parameters"] == {"BotId": "bt-7", "TargetRiskReward": "1.5", "SlMode": "Technical_Swing"}


def test_downsample_keeps_first_and_last_and_the_limit():
    points = list(range(5000))
    sampled = downsample(points)
    assert len(sampled) <= MAX_EQUITY_POINTS
    assert sampled[0] == 0 and sampled[-1] == 4999
    assert sampled == sorted(sampled)
    assert downsample([1, 2, 3]) == [1, 2, 3]
