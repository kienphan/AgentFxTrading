import sys
from pathlib import Path
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.server import app

client = TestClient(app)

def test_ui_endpoints():
    # Test Sessions API
    resp_sess = client.get("/api/dashboard/sessions")
    assert resp_sess.status_code == 200
    data_sess = resp_sess.json()
    assert "sessions" in data_sess
    assert "sydney" in data_sess["sessions"]
    assert "killzone" in data_sess
    assert "forex_status" in data_sess

    # Test Exposure API
    resp_exp = client.get("/api/dashboard/exposure?account_id=all")
    assert resp_exp.status_code == 200
    data_exp = resp_exp.json()
    assert "by_asset_class" in data_exp
    assert "total_volume" in data_exp

    # Test Cumulative PnL API
    resp_cum = client.get("/api/dashboard/cumulative-pnl?days=30&account_id=all")
    assert resp_cum.status_code == 200
    data_cum = resp_cum.json()
    assert isinstance(data_cum, list)

    # Test Latest Decisions API
    resp_dec = client.get("/api/dashboard/latest-decisions?limit=3")
    assert resp_dec.status_code == 200
    data_dec = resp_dec.json()
    assert isinstance(data_dec, list)

    # Test Demo and Real Dashboard HTML renders cleanly with new widgets
    resp_demo = client.get("/demo/dashboard")
    assert resp_demo.status_code == 200
    html_demo = resp_demo.text
    assert "market-session-bar" in html_demo
    assert "ai-decision-card" in html_demo
    assert "exposure-chart" in html_demo
    assert "btn-chart-equity" in html_demo

    resp_real = client.get("/real/dashboard")
    assert resp_real.status_code == 200
    html_real = resp_real.text
    assert "market-session-bar" in html_real
    assert "ai-decision-card" in html_real
