"""
Unit and integration tests for News Service & Macro Assessment engine.
"""

import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app import news_service
from app.server import app

client = TestClient(app)

def test_parse_iso_or_ff_date():
    # ISO 8601
    dt = news_service.parse_iso_or_ff_date("2026-09-08T08:30:00-04:00")
    assert dt is not None
    assert dt.tzinfo is not None

    # Custom formats
    dt2 = news_service.parse_iso_or_ff_date("09-08-2026 08:30PM")
    assert dt2 is not None

    # Empty / Invalid
    assert news_service.parse_iso_or_ff_date("") is None
    assert news_service.parse_iso_or_ff_date("invalid-date-string") is None

def test_cluster_red_news():
    raw_events = [
        {
            "title": "CPI m/m",
            "country": "USD",
            "date": "2026-09-08T12:30:00Z",
            "impact": "High",
            "forecast": "0.2%",
            "previous": "0.1%"
        },
        {
            "title": "Core CPI m/m",
            "country": "USD",
            "date": "2026-09-08T12:30:00Z",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%"
        },
        {
            "title": "Minor Housing Starts",
            "country": "USD",
            "date": "2026-09-08T12:30:00Z",
            "impact": "Low",
            "forecast": "1.2M",
            "previous": "1.1M"
        },
        {
            "title": "ECB Rate Decision",
            "country": "EUR",
            "date": "2026-09-09T11:45:00Z",
            "impact": "High",
            "forecast": "3.50%",
            "previous": "3.75%"
        }
    ]

    clusters = news_service.cluster_red_news(raw_events)
    # Low impact is discarded -> exactly 2 clusters
    assert len(clusters) == 2

    usd_cluster = next(c for c in clusters if "USD" in c["currencies"])
    assert usd_cluster["events_count"] == 2
    assert len(usd_cluster["events"]) == 2
    assert "CPI m/m" in [e["title"] for e in usd_cluster["events"]]
    assert "Core CPI m/m" in [e["title"] for e in usd_cluster["events"]]
    assert "19:30 (GMT+7)" in usd_cluster["time_formatted_vn"]

def test_symbol_currency_correlation():
    assert news_service.is_symbol_related_to_currencies("XAUUSD", ["USD"]) is True
    assert news_service.is_symbol_related_to_currencies("XAUUSD", ["EUR"]) is False
    assert news_service.is_symbol_related_to_currencies("EURUSD", ["EUR"]) is True
    assert news_service.is_symbol_related_to_currencies("EURUSD", ["USD"]) is True
    assert news_service.is_symbol_related_to_currencies("EURUSD", ["JPY"]) is False
    assert news_service.is_symbol_related_to_currencies("BTCUSD", ["USD"]) is True
    assert news_service.is_symbol_related_to_currencies("GER40", ["EUR"]) is True

def test_prompt_generation_and_bilingual_parser():
    cluster = {
        "id": "mock_hash",
        "timestamp_utc": "2026-09-10T12:30:00Z",
        "date_formatted_vn": "Thursday, 10/09/2026",
        "time_formatted_vn": "19:30 (GMT+7)",
        "time_formatted_utc": "12:30 UTC",
        "currencies": ["USD"],
        "events": [
            {"title": "Core CPI m/m", "country": "USD", "impact": "High", "forecast": "0.3%", "previous": "0.2%"}
        ]
    }
    sys_p, usr_p = news_service.generate_news_cluster_prompt(cluster, "XAUUSD", user_notes="Watch out for FOMC next week")
    assert "XAUUSD" in usr_p
    assert "Core CPI m/m" in usr_p
    assert "Watch out for FOMC" in usr_p
    assert "Macroeconomic Strategist" in sys_p

    sample_response = """```json
{
  "volatility_level": "EXTREME",
  "expected_pips_range": "180 - 320 pips",
  "trend_type": "SWEEP_THEN_TREND",
  "prob_buy": 70.0,
  "prob_sell": 30.0,
  "scenario_better_vi": "Kịch bản tốt (VN)",
  "scenario_better_en": "Scenario better (EN)",
  "scenario_worse_vi": "Kịch bản xấu (VN)",
  "scenario_worse_en": "Scenario worse (EN)",
  "bot_guidance_vi": "Chỉ dẫn bot (VN)",
  "bot_guidance_en": "Bot guidance (EN)"
}
```
<!-- SECTION_VI -->
### 🇻🇳 PHÂN TÍCH TIẾNG VIỆT
Nội dung phân tích SMC và vĩ mô tiếng Việt.
<!-- SECTION_EN -->
### 🇬🇧 ENGLISH ANALYSIS
Institutional SMC and macro analysis in English.
"""
    parsed = news_service.parse_news_ai_response(sample_response)
    assert parsed["volatility_level"] == "EXTREME"
    assert parsed["expected_pips_range"] == "180 - 320 pips"
    assert parsed["trend_type"] == "SWEEP_THEN_TREND"
    assert parsed["prob_buy"] == 70.0
    assert parsed["prob_sell"] == 30.0
    assert "Kịch bản tốt (VN)" in parsed["scenario_better_vi"]
    assert "Scenario better (EN)" in parsed["scenario_better_en"]
    assert "PHÂN TÍCH TIẾNG VIỆT" in parsed["analysis_markdown_vi"]
    assert "ENGLISH ANALYSIS" in parsed["analysis_markdown_en"]

def test_database_persistence(tmp_path, monkeypatch):
    test_db = tmp_path / "test_portfolio.db"
    monkeypatch.setattr(news_service, "DB_PATH", test_db)

    news_service.init_news_db()

    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO news_assessments (
            cluster_id, timestamp_utc, symbol, volatility_level, expected_pips_range,
            trend_type, prob_buy, prob_sell, scenario_better_vi, scenario_better_en,
            scenario_worse_vi, scenario_worse_en, bot_guidance_vi, bot_guidance_en,
            analysis_markdown_vi, analysis_markdown_en, events_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "cluster_123", "2026-09-08T12:30:00Z", "XAUUSD", "HIGH", "150-250 pips",
        "2_WAY_WHIPSAW", 55.0, 45.0, "Better VI", "Better EN",
        "Worse VI", "Worse EN", "Guidance VI", "Guidance EN",
        "Report VI", "Report EN", "[]", "2026-09-08T10:00:00Z"
    ))
    conn.commit()
    inserted_id = cur.lastrowid
    conn.close()

    items = news_service.get_recent_news_assessments(limit=10)
    assert len(items) == 1
    assert items[0]["cluster_id"] == "cluster_123"

    item = news_service.get_news_assessment_by_id(inserted_id)
    assert item is not None
    assert item["symbol"] == "XAUUSD"

    latest = news_service.get_latest_assessment_for_cluster("cluster_123", "XAUUSD")
    assert latest is not None
    assert latest["volatility_level"] == "HIGH"

def test_api_news_endpoints():
    # Test Shield Status API
    resp = client.get("/api/news/shield-status?symbol=XAUUSD")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "is_blackout" in data

    # Test Calendar API (thisweek, today, tomorrow)
    resp = client.get("/api/news/calendar?range=thisweek")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "clusters" in data

    resp_today = client.get("/api/news/calendar?range=today")
    assert resp_today.status_code == 200
    assert resp_today.json()["success"] is True

    resp_tomorrow = client.get("/api/news/calendar?range=tomorrow")
    assert resp_tomorrow.status_code == 200
    assert resp_tomorrow.json()["success"] is True
    # Test Assessments API
    resp = client.get("/api/news/assessments")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "assessments" in data
