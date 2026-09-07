"""
News Service - ForexFactory Red News Fetcher, Clusterer, and AI Macro Engine
=============================================================================
Provides:
- Resilient fetching of ForexFactory economic calendar (JSON + XML fallback)
- In-memory caching with 15-minute TTL to prevent WAF / rate-limit blocks
- High-Impact / Red News filtering and simultaneous timestamp clustering
- AI prompt formulation for Macroeconomic & SMC quantitative assessment
- Structured JSON and bilingual Markdown response parsing
- SQLite persistence in portfolio.db for assessments history
- Active news blackout shield checking for cBots and dashboard
"""

from __future__ import annotations

import json
import re
import time
import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import httpx
import logging
from app.llm_client import create_llm_client

logger = logging.getLogger("AgentFxTrading.News")
_NEWS_CACHE: Dict[str, Dict[str, Any]] = {}
_CLUSTERS_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes

FF_JSON_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_JSON_NEXTWEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
FF_XML_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/xml, */*"
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "portfolio.db"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = 900  # 15 minutes

FF_JSON_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_JSON_NEXTWEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
FF_XML_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/xml, */*"
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "portfolio.db"

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def init_news_db(conn: Optional[sqlite3.Connection] = None) -> None:
    """Initialize news_assessments table in SQLite."""
    close_after = False
    if conn is None:
        conn = get_db()
        close_after = True
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                volatility_level TEXT,
                expected_pips_range TEXT,
                trend_type TEXT,
                prob_buy REAL,
                prob_sell REAL,
                scenario_better_vi TEXT,
                scenario_better_en TEXT,
                scenario_worse_vi TEXT,
                scenario_worse_en TEXT,
                bot_guidance_vi TEXT,
                bot_guidance_en TEXT,
                analysis_markdown_vi TEXT,
                analysis_markdown_en TEXT,
                events_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_cluster ON news_assessments (cluster_id, symbol)")
        conn.commit()
    finally:
        if close_after:
            conn.close()

def generate_cluster_hash(timestamp_utc: str, event_titles: List[str], symbol: str = "") -> str:
    """Generates a stable MD5 signature for an event cluster and symbol."""
    sorted_titles = sorted([t.strip().lower() for t in event_titles])
    raw_str = f"{timestamp_utc.strip()}_{'|'.join(sorted_titles)}_{symbol.strip().upper()}"
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()

async def fetch_forexfactory_raw_events(week_range: str = "thisweek", force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches raw economic calendar events with in-memory caching, disk persistence, and XML fallback.
    """
    cache_key = f"ff_raw_{week_range.lower()}"
    now_ts = time.time()
    disk_cache_file = DATA_DIR / f"ff_calendar_{week_range.lower()}.json"
    
    if not force_refresh and cache_key in _NEWS_CACHE:
        cached_entry = _NEWS_CACHE[cache_key]
        if now_ts - cached_entry["timestamp"] < CACHE_TTL_SECONDS:
            return cached_entry["data"]

    target_url = FF_JSON_NEXTWEEK if week_range.lower() == "nextweek" else FF_JSON_THISWEEK
    events: List[Dict[str, Any]] = []
    fetch_error = None

    try:
        async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=12.0) as client:
            resp = await client.get(target_url)
            if resp.status_code == 200:
                events = resp.json()
            else:
                raise ValueError(f"HTTP status {resp.status_code}")
    except Exception as json_err:
        fetch_error = json_err
        # Fallback to XML if thisweek fails
        if week_range.lower() == "thisweek":
            try:
                async with httpx.AsyncClient(headers=HTTP_HEADERS, timeout=12.0) as client:
                    xml_resp = await client.get(FF_XML_THISWEEK)
                    if xml_resp.status_code == 200:
                        root = ET.fromstring(xml_resp.content)
                        for item in root.findall(".//event"):
                            d_val = (item.findtext("date", "") or "").strip()
                            t_val = (item.findtext("time", "") or "").strip()
                            full_dt = f"{d_val} {t_val}".strip() if t_val else d_val
                            events.append({
                                "title": (item.findtext("title", "") or "").strip(),
                                "country": (item.findtext("country", "") or "").strip(),
                                "date": full_dt,
                                "time": t_val,
                                "impact": (item.findtext("impact", "") or "").strip(),
                                "forecast": (item.findtext("forecast", "") or "").strip(),
                                "previous": (item.findtext("previous", "") or "").strip()
                            })
            except Exception as xml_err:
                logger.error(f"[NewsService] ForexFactory XML fallback failed: {xml_err}")

    if events:
        _NEWS_CACHE[cache_key] = {
            "timestamp": now_ts,
            "data": events
        }
        try:
            with open(disk_cache_file, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[NewsService] Could not save disk cache: {e}")
        return events

    # ── Fallback to existing memory or disk cache on 429/network failure ────
    logger.warning(f"[NewsService] ForexFactory fetch failed ({fetch_error}), attempting stale cache recovery...")
    if cache_key in _NEWS_CACHE and _NEWS_CACHE[cache_key].get("data"):
        _NEWS_CACHE[cache_key]["timestamp"] = now_ts  # extend expiry so we don't spam 429
        return _NEWS_CACHE[cache_key]["data"]

    if disk_cache_file.exists():
        try:
            with open(disk_cache_file, "r", encoding="utf-8") as f:
                disk_events = json.load(f)
                if disk_events:
                    _NEWS_CACHE[cache_key] = {
                        "timestamp": now_ts,
                        "data": disk_events
                    }
                    logger.info(f"[NewsService] Successfully recovered {len(disk_events)} events from disk cache {disk_cache_file.name}")
                    return disk_events
        except Exception as disk_err:
            logger.error(f"[NewsService] Error reading disk cache: {disk_err}")

    return []

def parse_iso_or_ff_date(date_str: str) -> Optional[datetime]:
    """Robust parser for ForexFactory ISO 8601 or custom date strings."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.strip())
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in [
        "%m-%d-%Y %I:%M%p",
        "%m-%d-%Y %I:%M %p",
        "%m-%d-%Y %H:%M",
        "%m-%d-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %I:%M%p"
    ]:
        try:
            parsed = datetime.strptime(date_str.strip(), fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
            
    return None
def cluster_red_news(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filters for Red News (impact == 'High') and groups events that occur
    at the exact same UTC release timestamp into clusters.
    """
    red_events = []
    for ev in events:
        impact = str(ev.get("impact", "")).strip().lower()
        if impact == "high":
            red_events.append(ev)

    clusters_map: Dict[str, List[Dict[str, Any]]] = {}
    
    for ev in red_events:
        raw_date = ev.get("date", "")
        dt_utc = parse_iso_or_ff_date(raw_date)
        if not dt_utc:
            continue
            
        time_key = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        if time_key not in clusters_map:
            clusters_map[time_key] = []
            
        clusters_map[time_key].append({
            "title": str(ev.get("title", "")).strip(),
            "country": str(ev.get("country", "")).strip().upper(),
            "impact": "High",
            "forecast": str(ev.get("forecast", "") or "").strip(),
            "previous": str(ev.get("previous", "") or "").strip(),
            "date_utc": time_key
        })

    now_utc = datetime.now(timezone.utc)
    cluster_list = []

    for time_key in sorted(clusters_map.keys()):
        items = clusters_map[time_key]
        if not items:
            continue
            
        dt_utc = datetime.fromisoformat(time_key.replace("Z", "+00:00"))
        # Local Vietnam / ICT Time (UTC+7)
        dt_vn = dt_utc.astimezone(timezone(timedelta(hours=7)))
        
        diff_seconds = (dt_utc - now_utc).total_seconds()
        diff_minutes = int(diff_seconds // 60)
        
        currencies = sorted(list(set(it["country"] for it in items if it["country"])))
        titles = [it["title"] for it in items]
        
        is_past = diff_seconds < -300  # More than 5 mins ago
        is_upcoming_soon = 0 <= diff_minutes <= 120  # Within 2 hours
        
        cluster_id = generate_cluster_hash(time_key, titles)

        cluster_list.append({
            "id": cluster_id,
            "timestamp_utc": time_key,
            "date_formatted_vn": dt_vn.strftime("%A, %d/%m/%Y"),
            "time_formatted_vn": dt_vn.strftime("%H:%M (GMT+7)"),
            "time_formatted_utc": dt_utc.strftime("%H:%M UTC"),
            "day_key": dt_vn.strftime("%Y-%m-%d"),
            "currencies": currencies,
            "events_count": len(items),
            "events": items,
            "diff_minutes": diff_minutes,
            "is_past": is_past,
            "is_upcoming_soon": is_upcoming_soon,
            "is_assessed": False,
            "latest_assessment": None
        })

        # Register in global cluster cache for instant lookup
        _CLUSTERS_CACHE[cluster_id] = cluster_list[-1]
    return cluster_list

def is_symbol_related_to_currencies(symbol: str, currencies: List[str]) -> bool:
    """
    Determines if a target trading instrument is fundamentally impacted by the given currencies.
    """
    sym = str(symbol or "").strip().upper()
    currs = [str(c).strip().upper() for c in currencies if str(c).strip()]
    if not sym or not currs:
        return False

    known_symbol_currencies = {
        "XAUUSD": {"USD"},
        "GOLD": {"USD"},
        "XAGUSD": {"USD"},
        "BTCUSD": {"USD"},
        "ETHUSD": {"USD"},
        "US30": {"USD"},
        "NAS100": {"USD"},
        "SPX500": {"USD"},
        "US500": {"USD"},
        "GER40": {"EUR"},
        "GER30": {"EUR"},
        "UK100": {"GBP"},
        "JP225": {"JPY"},
        "AUS200": {"AUD"}
    }

    sym_currencies = set()
    if sym in known_symbol_currencies:
        sym_currencies.update(known_symbol_currencies[sym])
    elif len(sym) == 6:
        sym_currencies.add(sym[:3])
        sym_currencies.add(sym[3:])
    else:
        for c_code in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "CNY"]:
            if c_code in sym:
                sym_currencies.add(c_code)

    for c in currs:
        if c in sym_currencies or c in sym:
            return True

    return False

async def is_news_blackout_active(symbol: str, pause_before_mins: int = 30, pause_after_mins: int = 30) -> Tuple[bool, str, int]:
    """
    Returns (is_active, active_event_title, remaining_blackout_minutes)
    for a specified symbol based on current cached/fetched red news events.
    """
    events = await fetch_forexfactory_raw_events("thisweek")
    clusters = cluster_red_news(events)
    now_utc = datetime.now(timezone.utc)

    for cluster in clusters:
        if not is_symbol_related_to_currencies(symbol, cluster.get("currencies", [])):
            continue

        dt_utc = datetime.fromisoformat(cluster["timestamp_utc"].replace("Z", "+00:00"))
        start_blackout = dt_utc - timedelta(minutes=pause_before_mins)
        end_blackout = dt_utc + timedelta(minutes=pause_after_mins)

        if start_blackout <= now_utc <= end_blackout:
            remaining_mins = max(1, int((end_blackout - now_utc).total_seconds() // 60))
            titles = ", ".join(e["title"] for e in cluster.get("events", [])[:2])
            return True, titles, remaining_mins

    return False, "", 0

def generate_news_cluster_prompt(
    cluster: Dict[str, Any],
    symbol: str,
    user_notes: str = ""
) -> Tuple[str, str]:
    """
    Builds the System and User Prompt for AI Engine.
    Instructs the LLM to analyze simultaneous economic indicators and their
    impact on the target symbol (especially Gold / XAUUSD, Forex, or Crypto).
    """
    system_prompt = """You are an elite Macroeconomic Strategist, Quantitative Market Analyst, and Smart Money Concepts (SMC) Expert for algorithmic trading hubs.
Your mission is to perform a comprehensive, institutional-grade assessment of simultaneous High-Impact (Red) economic news events and forecast their precise impact on a specified trading instrument (e.g. XAUUSD, EURUSD, US30).

You MUST evaluate:
1. Inter-indicator Correlation & Conflict:
   - If multiple indicators release at once (e.g. US NFP strong + Unemployment Rate rising, or CPI Headline up + Core down), examine whether they reinforce each other or generate conflicting signals leading to high volatility whipsaws.
2. Target Symbol Reaction & Sensitivity:
   - Detail how the specific symbol is fundamentally and quantitatively linked to the released currency (e.g. XAUUSD vs USD yield curve, EURUSD vs ECB/Fed rate differential).
3. Volatility Magnitude & Expected Pips:
   - Classify volatility into LOW, MEDIUM, HIGH, or EXTREME.
   - Provide a realistic expected price movement range in pips (e.g. "120 - 250 pips").
4. Trend Directionality vs 2-Way Liquidity Sweep:
   - Classify into:
     * "1_WAY_TREND": Clear, one-directional aggressive trend extension.
     * "2_WAY_WHIPSAW": Violent two-way volatility, hunting liquidity on both sides (BSL & SSL) before picking a direction.
     * "SWEEP_THEN_TREND": Initial liquidity sweep / Judas Swing against the true fundamental direction, followed by strong reversal into the real trend.
5. Probabilities:
   - Estimate the probability of an overall BUY bias (%) vs SELL bias (%), ensuring they sum to 100%.
6. Bilingual Bifurcated Scenario Analysis:
   - Provide both Vietnamese and English explanations for Scenario A (Actual > Forecast) and Scenario B (Actual < Forecast).
7. Bilingual cBot & Trader Guidance:
   - Provide clear actionable instructions in both Vietnamese and English regarding blackout windows, scalping hazards, and SMC entry blueprints.

IMPORTANT FORMAT REQUIREMENT:
You MUST start your response with a valid JSON block enclosed in ```json ... ``` with the exact schema below, followed by an in-depth bilingual Markdown report:

```json
{
  "volatility_level": "EXTREME",
  "expected_pips_range": "150 - 300 pips",
  "trend_type": "2_WAY_WHIPSAW",
  "prob_buy": 65.0,
  "prob_sell": 35.0,
  "scenario_better_vi": "Nếu Thực tế > Dự báo: USD bật tăng mạnh, gây áp lực khiến giá vàng giảm về kiểm tra vùng hỗ trợ.",
  "scenario_better_en": "If Actual > Forecast: USD surges, Treasury yields spike, forcing target symbol to drop sharply to test lower key support.",
  "scenario_worse_vi": "Nếu Thực tế < Dự báo: USD lao dốc trước kỳ vọng nới lỏng chính sách, kích hoạt đà bứt phá tăng giá mạnh cho vàng/cặp tiền.",
  "scenario_worse_en": "If Actual < Forecast: USD plummets on rate-cut expectations, propelling target symbol into aggressive bullish breakout.",
  "bot_guidance_vi": "Tạm dừng các bot Scalping trước giờ tin 30 phút. Đối với bot săn thanh khoản Judas Sweep, chỉ canh vào lệnh sau khi có râu quét rõ ràng.",
  "bot_guidance_en": "Suspend scalping bots 30 minutes before release. For Judas Sweep bots, only enter after confirmed liquidity sweep wicks."
}
```

After the JSON block, provide a comprehensive bilingual Markdown analysis structured with two distinct section comments:

<!-- SECTION_VI -->
### 🇻🇳 PHÂN TÍCH VĨ MÔ & KỊCH BẢN SMC (TIẾNG VIỆT)
- 📊 **Tóm Tắt Vĩ Mô & Tác Động Chung**: ...
- ⚡ **Tương Quan Giữa Các Chỉ Số & Xung Đột Dữ Liệu**: ...
- 🎯 **Kịch Bản Thanh Khoản SMC & Điểm Phản Ứng Giá**: ...
- 🛡️ **Chiến Lược Quản Trị Rủi Ro & Lệnh Giao Dịch cBot**: ...

<!-- SECTION_EN -->
### 🇬🇧 INSTITUTIONAL MACRO & SMC ORDER FLOW ANALYSIS (ENGLISH)
- 📊 **Executive Macro Summary**: ...
- ⚡ **Inter-Indicator Dynamics & Potential Conflicts**: ...
- 🎯 **SMC Liquidity & Price Reaction Blueprint for Target Symbol**: ...
- 🛡️ **Tactical Risk & cBot Execution Recommendations**: ...
"""

    events_summary_lines = []
    for idx, ev in enumerate(cluster.get("events", []), 1):
        fc = ev.get("forecast") or "N/A"
        pr = ev.get("previous") or "N/A"
        events_summary_lines.append(f"  {idx}. [{ev.get('country')}] {ev.get('title')} | Forecast: {fc} | Previous: {pr}")

    events_text = "\n".join(events_summary_lines)
    user_notes_section = f"\nUser Scenario / Context Notes: {user_notes.strip()}" if user_notes.strip() else ""

    user_prompt = f"""Assess the following High-Impact Economic News Cluster:

Release Time: {cluster.get('date_formatted_vn')} at {cluster.get('time_formatted_vn')} ({cluster.get('time_formatted_utc')})
Target Symbol to Analyze: {symbol.strip().upper()}
Simultaneous High-Impact Events ({len(cluster.get('events', []))} events):
{events_text}{user_notes_section}

Please provide your rigorous institutional assessment following the required bilingual JSON schema and Markdown report structure.
"""
    return system_prompt, user_prompt

def parse_news_ai_response(raw_text: str) -> Dict[str, Any]:
    """
    Extracts structured JSON metrics and separate bilingual Markdown report from LLM response.
    """
    clean_text = raw_text.strip()
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', clean_text, re.DOTALL)
    json_data = {}
    markdown_report = clean_text

    if json_match:
        json_str = json_match.group(1)
        markdown_report = clean_text[json_match.end():].strip()
        try:
            json_data = json.loads(json_str)
        except Exception:
            json_data = {}
    else:
        brace_match = re.search(r'(\{[\s\S]*?\})', clean_text)
        if brace_match:
            try:
                json_data = json.loads(brace_match.group(1))
                markdown_report = clean_text[brace_match.end():].strip()
            except Exception:
                pass

    volatility = str(json_data.get("volatility_level", "HIGH")).upper().strip()
    if volatility not in ["LOW", "MEDIUM", "HIGH", "EXTREME"]:
        volatility = "HIGH"

    trend_type = str(json_data.get("trend_type", "2_WAY_WHIPSAW")).upper().strip()
    if trend_type not in ["1_WAY_TREND", "2_WAY_WHIPSAW", "SWEEP_THEN_TREND"]:
        trend_type = "2_WAY_WHIPSAW"

    try:
        prob_buy = float(json_data.get("prob_buy", 50.0))
        prob_sell = float(json_data.get("prob_sell", 50.0))
    except (ValueError, TypeError):
        prob_buy, prob_sell = 50.0, 50.0

    total_prob = prob_buy + prob_sell
    if total_prob > 0 and abs(total_prob - 100.0) > 0.5:
        prob_buy = round((prob_buy / total_prob) * 100.0, 1)
        prob_sell = round(100.0 - prob_buy, 1)

    expected_pips = str(json_data.get("expected_pips_range") or "100 - 250 pips").strip()
    scenario_better_vi = str(json_data.get("scenario_better_vi") or json_data.get("scenario_better") or "").strip()
    scenario_better_en = str(json_data.get("scenario_better_en") or scenario_better_vi or "If actual data surpasses expectations, expect bullish continuation for base currency.").strip()
    scenario_worse_vi = str(json_data.get("scenario_worse_vi") or json_data.get("scenario_worse") or "").strip()
    scenario_worse_en = str(json_data.get("scenario_worse_en") or scenario_worse_vi or "If actual data falls short, expect rapid re-pricing and reversal against forecast.").strip()
    bot_guidance_vi = str(json_data.get("bot_guidance_vi") or json_data.get("bot_guidance") or "").strip()
    bot_guidance_en = str(json_data.get("bot_guidance_en") or bot_guidance_vi or "Maintain minimum 30-minute pre-news blackout and avoid tight stop loss placement.").strip()

    analysis_markdown_vi = ""
    analysis_markdown_en = ""

    if "<!-- SECTION_VI -->" in markdown_report and "<!-- SECTION_EN -->" in markdown_report:
        parts = markdown_report.split("<!-- SECTION_EN -->")
        analysis_markdown_vi = parts[0].replace("<!-- SECTION_VI -->", "").strip()
        analysis_markdown_en = parts[1].strip() if len(parts) > 1 else ""
    elif "<!-- SECTION_VI -->" in markdown_report:
        analysis_markdown_vi = markdown_report.replace("<!-- SECTION_VI -->", "").strip()
        analysis_markdown_en = analysis_markdown_vi
    elif "<!-- SECTION_EN -->" in markdown_report:
        analysis_markdown_en = markdown_report.replace("<!-- SECTION_EN -->", "").strip()
        analysis_markdown_vi = analysis_markdown_en
    else:
        match_split = re.split(r'(?=###\s*(?:🇬🇧|ENGLISH|Institutional Macro|INSTITUTIONAL MACRO))', markdown_report, flags=re.IGNORECASE)
        if len(match_split) >= 2:
            analysis_markdown_vi = match_split[0].strip()
            analysis_markdown_en = match_split[1].strip()
        else:
            analysis_markdown_vi = markdown_report
            analysis_markdown_en = markdown_report

    return {
        "volatility_level": volatility,
        "expected_pips_range": expected_pips,
        "trend_type": trend_type,
        "prob_buy": prob_buy,
        "prob_sell": prob_sell,
        "scenario_better_vi": scenario_better_vi,
        "scenario_better_en": scenario_better_en,
        "scenario_worse_vi": scenario_worse_vi,
        "scenario_worse_en": scenario_worse_en,
        "bot_guidance_vi": bot_guidance_vi,
        "bot_guidance_en": bot_guidance_en,
        "analysis_markdown_vi": analysis_markdown_vi,
        "analysis_markdown_en": analysis_markdown_en
    }

async def assess_news_cluster(
    cluster_id: str,
    symbol: str = "XAUUSD",
    week_range: str = "thisweek",
    user_notes: str = "",
    cluster_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Executes AI Assessment on a specific Red News Cluster.
    Uses the project's configured LLM Provider.
    Employs robust multi-tier cluster resolution (direct, memory cache, calendar search).
    """
    init_news_db()

    # 1. Direct cluster payload if provided by frontend
    target_cluster = cluster_data

    # 2. Check in-memory cluster registry
    if not target_cluster and cluster_id in _CLUSTERS_CACHE:
        target_cluster = _CLUSTERS_CACHE[cluster_id]

    # 3. Search target week_range
    if not target_cluster:
        events = await fetch_forexfactory_raw_events(week_range)
        clusters = cluster_red_news(events)
        target_cluster = next((c for c in clusters if c["id"] == cluster_id), None)

    # 4. Search alternate week_range fallback
    if not target_cluster:
        alt_range = "nextweek" if week_range == "thisweek" else "thisweek"
        alt_events = await fetch_forexfactory_raw_events(alt_range)
        alt_clusters = cluster_red_news(alt_events)
        target_cluster = next((c for c in alt_clusters if c["id"] == cluster_id), None)

    if not target_cluster:
        raise ValueError(f"Cluster '{cluster_id}' not found in calendar.")
    system_prompt, user_prompt = generate_news_cluster_prompt(target_cluster, symbol, user_notes)
    
    # Call LLM client
    llm = create_llm_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    raw_response = await llm.chat(messages)
    parsed = parse_news_ai_response(raw_response)

    # Save to SQLite
    now_str = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO news_assessments (
                cluster_id, timestamp_utc, symbol, volatility_level, expected_pips_range,
                trend_type, prob_buy, prob_sell, scenario_better_vi, scenario_better_en,
                scenario_worse_vi, scenario_worse_en, bot_guidance_vi, bot_guidance_en,
                analysis_markdown_vi, analysis_markdown_en, events_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cluster_id,
            target_cluster["timestamp_utc"],
            symbol.upper(),
            parsed["volatility_level"],
            parsed["expected_pips_range"],
            parsed["trend_type"],
            parsed["prob_buy"],
            parsed["prob_sell"],
            parsed["scenario_better_vi"],
            parsed["scenario_better_en"],
            parsed["scenario_worse_vi"],
            parsed["scenario_worse_en"],
            parsed["bot_guidance_vi"],
            parsed["bot_guidance_en"],
            parsed["analysis_markdown_vi"],
            parsed["analysis_markdown_en"],
            json.dumps(target_cluster["events"], ensure_ascii=False),
            now_str
        ))
        conn.commit()
        assessment_id = cur.lastrowid
    finally:
        conn.close()

    result = {
        "id": assessment_id,
        "cluster_id": cluster_id,
        "symbol": symbol.upper(),
        "created_at": now_str,
        **parsed
    }
    return result

def get_recent_news_assessments(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieves recent assessments from SQLite."""
    init_news_db()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM news_assessments 
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_news_assessment_by_id(assessment_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves a single assessment by primary key."""
    init_news_db()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM news_assessments WHERE id = ?", (assessment_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_latest_assessment_for_cluster(cluster_id: str, symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves latest assessment for a cluster (optionally matching symbol)."""
    init_news_db()
    conn = get_db()
    try:
        cur = conn.cursor()
        if symbol:
            cur.execute("""
                SELECT * FROM news_assessments 
                WHERE cluster_id = ? AND symbol = ? 
                ORDER BY id DESC LIMIT 1
            """, (cluster_id, symbol.upper()))
        else:
            cur.execute("""
                SELECT * FROM news_assessments 
                WHERE cluster_id = ? 
                ORDER BY id DESC LIMIT 1
            """, (cluster_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
