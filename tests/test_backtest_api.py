"""/api/backtests*: sources, the parameter form, job creation with validation, list/detail/report,
cancel and delete. Docker and the metadata container are faked; the DB is the pytest SQLite file."""
import inspect
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.backtest_api as api  # noqa: E402
import app.server as server  # noqa: E402
from app import backtest_store as store  # noqa: E402
from app.backtest_params import MetadataError  # noqa: E402
from app.cbot_presets import build_run_command, container_name  # noqa: E402
from app.portfolio import get_portfolio_manager  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "backtest"
META = json.loads((FIXTURES / "flowrsi_metadata.json").read_text())
REPORT = (FIXTURES / "report_small.json").read_bytes()
client = TestClient(server.app)
ACCOUNT = {"slug": "bttest", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_bttest_pwd"}
FLOW = container_name("bttest", "flowrsi", "EURUSD")
JUDAS = container_name("bttest", "judas", "EURUSD")


class FakeCache:
    def __init__(self):
        self.error = None

    def get(self, algo_path):
        if self.error:
            raise self.error
        return {**META, "sha256": "f" * 64}


@pytest.fixture
def env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "cBot").mkdir(parents=True)
    (project / "cBot" / "FlowRsiBot.algo").write_bytes(b"algo")
    ctrader = tmp_path / "ctrader"
    (ctrader / "ctrader_data").mkdir(parents=True)
    (ctrader / "ctrader_data" / "ctid_bttest_pwd").write_text("pw")
    cache = FakeCache()
    monkeypatch.setenv("CTRADER_HOME", str(ctrader))
    monkeypatch.setattr(api, "PROJECT_ROOT", project)
    monkeypatch.setattr(api, "metadata_cache", cache)
    monkeypatch.setattr(api, "docker_available", lambda: True)
    monkeypatch.setattr(api, "today_utc", lambda: date(2026, 9, 25))
    monkeypatch.setattr(store, "REPORT_DIR", tmp_path / "reports")
    pm = get_portfolio_manager()
    for strategy, name in (("flowrsi", FLOW), ("judas", JUDAS)):
        pm.delete_cbot_config(name)
        assert pm.add_cbot_config(name, "", build_run_command(ACCOUNT, strategy, "EURUSD", "/w", "/r"))
    with store.connect() as conn:
        conn.execute("DELETE FROM backtest_jobs")
    yield cache
    for name in (FLOW, JUDAS):
        pm.delete_cbot_config(name)
    with store.connect() as conn:
        conn.execute("DELETE FROM backtest_jobs")


def body(**over):
    data = {"bot_name": FLOW, "start": "2026-07-24", "end": "2026-09-23", "data_mode": "ticks",
            "balance": 10000, "note": "rr 2", "overrides": {"TargetRiskReward": 2.0}}
    data.update(over)
    return data


def create(**over):
    resp = client.post("/api/backtests", json=body(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_sources_list_flowrsi_as_supported_and_judas_as_not(env):
    resp = client.get("/api/backtests/sources")
    assert resp.status_code == 200
    rows = {r["name"]: r for r in resp.json()["sources"]}
    assert rows[FLOW]["supported"] is True and rows[FLOW]["symbol"] == "EURUSD"
    assert rows[JUDAS]["supported"] is False and "AI" in rows[JUDAS]["reason"]


def test_params_view(env):
    resp = client.get(f"/api/backtests/sources/{FLOW}/params")
    assert resp.status_code == 200
    view = resp.json()
    assert view["locked"]["UseAiGateMode"] == "false"
    params = {p["key"]: p for g in view["groups"] for p in g["params"]}
    assert params["TargetRiskReward"]["bot_value"] == "1.5" and "UseAiGateMode" not in params


def test_params_error_codes(env, monkeypatch):
    assert client.get("/api/backtests/sources/cbot-nope/params").status_code == 404
    assert client.get(f"/api/backtests/sources/{JUDAS}/params").status_code == 422
    env.error = MetadataError("metadata printed no JSON")
    assert client.get(f"/api/backtests/sources/{FLOW}/params").status_code == 502
    env.error = None
    monkeypatch.setattr(api, "docker_available", lambda: False)
    assert client.get(f"/api/backtests/sources/{FLOW}/params").status_code == 503


def test_create_stores_a_queued_job(env):
    res = create()
    assert (res["status"], res["queue_position"]) == ("queued", 1)
    with store.connect() as conn:
        job = store.get_job(conn, res["id"])
    assert job["overrides"] == {"TargetRiskReward": {"from": "1.5", "to": "2.0"}}
    assert job["params"]["TargetRiskReward"] == "2.0" and "UseAiGateMode" not in job["params"]
    assert (job["algo"], job["algo_sha"], job["algo_build_time"]) == (
        "FlowRsiBot.algo", "f" * 64, "2026-09-24T16:34:43.3439105Z")
    assert (job["start_date"], job["end_date"], job["note"]) == ("2026-07-24", "2026-09-23", "rr 2")
    assert (job["ctid_email"], job["pwd_file"]) == ("trader@example.com", "/root/ctrader_data/ctid_bttest_pwd")
    listed = client.get("/api/backtests").json()["jobs"]
    assert listed[0]["id"] == res["id"]
    assert "ctid_email" not in listed[0] and "pwd_file" not in listed[0] and "params" not in listed[0]
    detail = client.get(f"/api/backtests/{res['id']}").json()
    assert detail["params"]["TargetRiskReward"] == "2.0"


def test_queue_position_counts_jobs_ahead(env):
    assert [create()["queue_position"] for _ in range(2)] == [1, 2]


@pytest.mark.parametrize("over", [
    {"start": "2026-09-24", "end": "2026-09-23"},
    {"end": "2026-09-26"},
    {"start": "2025-09-25", "end": "2026-09-25"},            # 366 days
    {"data_mode": "m1"},                                      # no spread
    {"data_mode": "m1", "spread_pips": 0},
    {"spread_pips": 1.0},                                     # spread with ticks
    {"data_mode": "csv"},
    {"balance": 50},
    {"note": "two\nlines"},
    {"note": "x" * 201},
    {"overrides": {"UseAiGateMode": True}},
    {"overrides": {"NoSuchParam": 1}},
    {"overrides": {"FastRsiPeriod": 1}},
])
def test_create_rejects_bad_input(env, over):
    assert client.post("/api/backtests", json=body(**over)).status_code == 422


@pytest.mark.parametrize("field,extra", [("spread_pips", {"data_mode": "m1"}), ("balance", {})])
def test_create_rejects_non_finite_spread_and_balance(env, field, extra):
    # httpx's TestClient encodes json= with allow_nan=False, so float('inf') never reaches the
    # server that way: send the raw body so the handler actually sees an Infinity value.
    data = body(**extra)
    data[field] = None                                     # placeholder, turned into a bare Infinity below
    raw = json.dumps(data).replace("null", "Infinity", 1)
    resp = client.post("/api/backtests", content=raw.encode(), headers={"content-type": "application/json"})
    assert resp.status_code == 422, resp.text


def test_create_accepts_m1_with_spread_and_a_full_year(env):
    create(data_mode="m1", spread_pips=1.2)
    create(start="2025-09-26", end="2026-09-25")              # 365 days


def test_create_refuses_unknown_and_unsupported_bots(env):
    assert client.post("/api/backtests", json=body(bot_name="cbot-nope")).status_code == 404
    assert client.post("/api/backtests", json=body(bot_name=JUDAS, overrides={})).status_code == 422


def test_detail_of_a_done_job_includes_the_report(env):
    job_id = create()["id"]
    with store.connect() as conn:
        store.update_job(conn, job_id, status="done", summary={"net_profit": -0.98})
    store.write_report(job_id, REPORT)
    detail = client.get(f"/api/backtests/{job_id}").json()
    assert detail["summary"] == {"net_profit": -0.98}
    assert len(detail["report"]["trades"]) == 4 and len(detail["report"]["pnl_by_hour"]) == 24
    assert detail["report"]["parameters"]["TargetRiskReward"] == "1.5"
    raw = client.get(f"/api/backtests/{job_id}/report.json")
    assert raw.status_code == 200 and raw.json()["main"]["symbol"] == "EURUSD"


def test_detail_of_a_queued_job_has_no_report(env):
    job_id = create()["id"]
    assert client.get(f"/api/backtests/{job_id}").json()["report"] is None
    assert client.get(f"/api/backtests/{job_id}/report.json").status_code == 404
    assert client.get("/api/backtests/999999").status_code == 404


def test_cancel_and_delete(env):
    job_id = create()["id"]
    assert client.delete(f"/api/backtests/{job_id}").status_code == 409        # still queued
    assert client.post(f"/api/backtests/{job_id}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/backtests/{job_id}/cancel").status_code == 409
    store.write_report(job_id, REPORT)
    assert client.delete(f"/api/backtests/{job_id}").status_code == 200
    assert client.get(f"/api/backtests/{job_id}").status_code == 404
    assert not store.report_path(job_id).exists()


def test_worker_is_started_by_the_lifespan_but_not_under_pytest():
    src = inspect.getsource(server.lifespan.__wrapped__)
    assert "backtest_worker.run_loop()" in src
    assert src.index("is_running_under_test()") < src.index("backtest_worker.run_loop()")
