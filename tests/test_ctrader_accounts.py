import sqlite3
import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.accounts import AccountRegistry

CTRADER_COLUMNS = {"id", "slug", "ctid_email", "account_number", "account_type", "label", "pwd_file", "created_at"}


@pytest.fixture
def registry(tmp_path):
    return AccountRegistry(str(tmp_path / "accounts_test.db"))


def _insert(reg, slug="demo-main", account_type="demo", label="Demo Main", number="10101649"):
    return reg.insert_ctrader_account(slug, "me@example.com", number, account_type, label,
                                      f"/root/ctrader_data/ctid_{slug}_pwd")


def test_ctrader_accounts_crud(registry):
    assert registry.list_ctrader_accounts() == []
    row = _insert(registry)
    assert set(row) == CTRADER_COLUMNS
    assert row["id"] >= 1
    assert row["slug"] == "demo-main"
    assert row["pwd_file"] == "/root/ctrader_data/ctid_demo-main_pwd"
    assert row["created_at"]

    assert registry.get_ctrader_account(row["id"])["label"] == "Demo Main"
    assert registry.get_ctrader_account(row["id"] + 1000) is None
    assert registry.get_ctrader_account_by_slug("demo-main")["id"] == row["id"]
    assert registry.get_ctrader_account_by_slug("nope") is None
    assert [a["slug"] for a in registry.list_ctrader_accounts()] == ["demo-main"]

    assert registry.delete_ctrader_account(row["id"]) is True
    assert registry.delete_ctrader_account(row["id"]) is False
    assert registry.get_ctrader_account(row["id"]) is None
    assert registry.list_ctrader_accounts() == []


def test_slug_is_unique(registry):
    _insert(registry)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(registry, number="999")


def test_account_type_is_checked(registry):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(registry, slug="x-main", account_type="paper")


def test_list_orders_live_first_then_label(registry):
    _insert(registry, slug="demo-zulu", account_type="demo", label="Zulu")
    _insert(registry, slug="demo-alpha", account_type="demo", label="Alpha")
    _insert(registry, slug="live-ic", account_type="live", label="IC", number="6094347")
    assert [a["slug"] for a in registry.list_ctrader_accounts()] == ["live-ic", "demo-alpha", "demo-zulu"]


def test_upsert_configured_account_marks_dashboard_account(registry):
    registry.upsert_configured_account("live-6094347", "6094347", "live", "IC")
    accounts = registry.list_accounts()          # configured only
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == "live-6094347"
    assert accounts[0]["label"] == "IC"
    assert accounts[0]["is_configured"] == 1
    # Re-upsert relabels instead of duplicating (unique on number+type)
    registry.upsert_configured_account("live-6094347", "6094347", "live", "IC Main")
    accounts = registry.list_accounts()
    assert len(accounts) == 1 and accounts[0]["label"] == "IC Main"


def test_seed_from_env_still_uses_the_upsert(registry, monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACCOUNTS", "demo-1|7654321|demo|Demo Test;live-main|1234567|live|Live Main")
    registry.seed_from_env()
    ids = sorted(a["account_id"] for a in registry.list_accounts())
    assert ids == ["demo-1", "live-main"]


# ---------------------------------------------------------------------------
# Service + API
# ---------------------------------------------------------------------------
import stat

from fastapi.testclient import TestClient

from app.server import app
from app.accounts import get_account_registry
from app.ctrader_accounts import slugify

client = TestClient(app)


@pytest.fixture
def ctrader_home(tmp_path, monkeypatch):
    """Point CTRADER_HOME at a temp dir and start each test with an empty ctrader_accounts table."""
    monkeypatch.setenv("CTRADER_HOME", str(tmp_path))
    reg = get_account_registry()
    with reg._get_connection() as conn:
        conn.execute("DELETE FROM ctrader_accounts")
        conn.commit()
    return tmp_path


def _payload(**overrides):
    base = {"label": "Main", "ctid_email": "me@example.com", "password": "s3cret!",
            "account_number": "10101649", "account_type": "demo"}
    base.update(overrides)
    return base


def test_slugify():
    assert slugify("demo", "Main") == "demo-main"
    assert slugify("live", "IC Markets #2") == "live-ic-markets-2"
    assert slugify("live", "  Spaces  ") == "live-spaces"
    long = slugify("demo", "a very long account label indeed")
    assert len(long) <= 24 and not long.endswith("-") and long.startswith("demo-")


def test_create_account_writes_row_file_and_dashboard_tab(ctrader_home):
    res = client.post("/api/ctrader-accounts", json=_payload())
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["success"] is True
    acc = body["account"]
    assert acc["slug"] == "demo-main"
    assert acc["label"] == "Main" and acc["account_type"] == "demo" and acc["account_number"] == "10101649"
    assert "password" not in acc and "pwd_file" not in acc
    assert "s3cret!" not in res.text

    pwd = ctrader_home / "ctrader_data" / "ctid_demo-main_pwd"
    assert pwd.read_text() == "s3cret!"
    assert stat.S_IMODE(pwd.stat().st_mode) == 0o600
    assert stat.S_IMODE(pwd.parent.stat().st_mode) == 0o700

    row = get_account_registry().get_ctrader_account(acc["id"])
    assert row["pwd_file"] == "/root/ctrader_data/ctid_demo-main_pwd"     # in-container path

    tabs = get_account_registry().list_accounts()
    assert any(t["account_id"] == "demo-10101649" and t["label"] == "Main" and t["is_configured"] == 1 for t in tabs)


def test_list_omits_secrets(ctrader_home):
    client.post("/api/ctrader-accounts", json=_payload())
    client.post("/api/ctrader-accounts", json=_payload(label="IC", account_type="live", account_number="6094347"))
    res = client.get("/api/ctrader-accounts")
    assert res.status_code == 200
    accounts = res.json()["accounts"]
    assert [a["slug"] for a in accounts] == ["live-ic", "demo-main"]
    for a in accounts:
        assert set(a) == {"id", "slug", "ctid_email", "account_number", "account_type", "label", "created_at"}
    assert "s3cret" not in res.text


def test_duplicate_slug_is_409_and_writes_no_second_file(ctrader_home):
    assert client.post("/api/ctrader-accounts", json=_payload()).status_code == 201
    res = client.post("/api/ctrader-accounts", json=_payload(account_number="222", password="other"))
    assert res.status_code == 409
    assert res.json() == {"success": False, "message": res.json()["message"]}
    assert "demo-main" in res.json()["message"]
    assert (ctrader_home / "ctrader_data" / "ctid_demo-main_pwd").read_text() == "s3cret!"   # untouched
    assert len(client.get("/api/ctrader-accounts").json()["accounts"]) == 1


def test_delete_removes_row_and_file(ctrader_home):
    acc = client.post("/api/ctrader-accounts", json=_payload()).json()["account"]
    pwd = ctrader_home / "ctrader_data" / "ctid_demo-main_pwd"
    assert pwd.exists()
    res = client.delete(f"/api/ctrader-accounts/{acc['id']}")
    assert res.status_code == 200 and res.json() == {"success": True}
    assert not pwd.exists()
    assert client.get("/api/ctrader-accounts").json()["accounts"] == []
    res = client.delete(f"/api/ctrader-accounts/{acc['id']}")
    assert res.status_code == 404 and res.json()["success"] is False


@pytest.mark.parametrize("bad, fragment", [
    ({"ctid_email": "not-an-email"}, "email"),
    ({"ctid_email": "a b@example.com"}, "email"),
    ({"account_number": "12ab"}, "digits"),
    ({"account_number": ""}, "digits"),
    ({"label": ""}, "label"),
    ({"label": "x" * 41}, "label"),
    ({"label": 'Has "quote"'}, "quotes"),
    ({"label": "line\nbreak"}, "quotes"),
    ({"label": "Back\\slash"}, "backslashes"),
    ({"label": "<b>bold</b>"}, "angle brackets"),
    ({"ctid_email": "o'brien@example.com"}, "email"),
    ({"ctid_email": "back\\slash@example.com"}, "email"),
    ({"label": "!!!"}, "ASCII letter or digit"),
    ({"label": "Not Live", "account_type": "demo"}, "live"),
    ({"password": ""}, "password"),
    ({"account_type": "paper"}, "account_type"),
])
def test_validation_returns_422_and_writes_nothing(ctrader_home, bad, fragment):
    res = client.post("/api/ctrader-accounts", json=_payload(**bad))
    assert res.status_code == 422, res.text
    assert res.json()["success"] is False
    assert fragment in res.json()["message"]
    assert not (ctrader_home / "ctrader_data").exists()
    assert client.get("/api/ctrader-accounts").json()["accounts"] == []


def test_live_label_may_contain_live(ctrader_home):
    res = client.post("/api/ctrader-accounts", json=_payload(label="Live IC", account_type="live", account_number="6094347"))
    assert res.status_code == 201 and res.json()["account"]["slug"] == "live-live-ic"


def test_unwritable_ctrader_home_is_500_and_no_row(ctrader_home, monkeypatch):
    blocker = ctrader_home / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("CTRADER_HOME", str(blocker))
    res = client.post("/api/ctrader-accounts", json=_payload())
    assert res.status_code == 500
    assert res.json()["success"] is False and "password file" in res.json()["message"]
    assert client.get("/api/ctrader-accounts").json()["accounts"] == []


def test_create_keeps_a_preconfigured_dashboard_account_id(ctrader_home):
    """A .env-style id like 'demo-1' must survive registering the same number in Setup Instances."""
    reg = get_account_registry()
    reg.upsert_configured_account("demo-1", "7654321", "demo", "Demo Test")
    res = client.post("/api/ctrader-accounts", json=_payload(label="Main", account_number="7654321"))
    assert res.status_code == 201, res.text
    assert reg.resolve_account_id("7654321", "demo") == "demo-1"
    row = next(t for t in reg.list_accounts() if t["account_number"] == "7654321" and t["account_type"] == "demo")
    assert row["account_id"] == "demo-1" and row["label"] == "Main" and row["is_configured"] == 1


@pytest.mark.parametrize("label, ok", [("Oliver", True), ("Delivery", True), ("Olive", False), ("Liverpool", False), ("Live", False)])
def test_demo_live_rule_matches_the_bots_table_markers(ctrader_home, label, ok):
    res = client.post("/api/ctrader-accounts", json=_payload(label=label))
    assert (res.status_code == 201) == ok, res.text


def test_ctrader_home_defaults_to_root(monkeypatch):
    from app.ctrader_accounts import ctrader_home, password_file_paths
    monkeypatch.delenv("CTRADER_HOME", raising=False)
    assert ctrader_home() == "/root"
    host, container = password_file_paths("demo-main")
    assert str(host) == "/root/ctrader_data/ctid_demo-main_pwd" and container == "/root/ctrader_data/ctid_demo-main_pwd"
    monkeypatch.setenv("CTRADER_HOME", "  ")
    assert ctrader_home() == "/root"


def test_upsert_from_bot_preserves_balance_on_zero_or_negative(registry):
    """Disconnected or unauthenticated cBots send 0.0 balance, which must not zero out an existing balance."""
    acc_id = registry.upsert_from_bot(
        account_number="123456", account_type="live", label="Live IC",
        balance=1500.0, equity=1520.0
    )
    row = next(a for a in registry.list_accounts(include_unconfigured=True) if a["account_id"] == acc_id)
    assert row["last_balance"] == 1500.0
    assert row["last_equity"] == 1520.0

    # A zero balance from a disconnected bot must NOT overwrite last_balance/last_equity
    registry.upsert_from_bot(
        account_number="123456", account_type="live", label="Live IC",
        balance=0.0, equity=0.0
    )
    row = next(a for a in registry.list_accounts(include_unconfigured=True) if a["account_id"] == acc_id)
    assert row["last_balance"] == 1500.0
    assert row["last_equity"] == 1520.0

    # A negative balance must also not overwrite
    registry.upsert_from_bot(
        account_number="123456", account_type="live", label="Live IC",
        balance=-50.0, equity=-50.0
    )
    row = next(a for a in registry.list_accounts(include_unconfigured=True) if a["account_id"] == acc_id)
    assert row["last_balance"] == 1500.0
    assert row["last_equity"] == 1520.0

    # A subsequent positive balance updates properly
    registry.upsert_from_bot(
        account_number="123456", account_type="live", label="Live IC",
        balance=1600.0, equity=1610.0
    )
    row = next(a for a in registry.list_accounts(include_unconfigured=True) if a["account_id"] == acc_id)
    assert row["last_balance"] == 1600.0
    assert row["last_equity"] == 1610.0
