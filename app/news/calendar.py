"""
Economic Calendar Service.

Fetches economic calendar from free APIs and checks for high-impact news events.
Blocks trading around major news (NFP, FOMC, CPI, etc.).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class NewsEvent:
    """Represents an economic news event."""

    def __init__(
        self,
        timestamp: datetime,
        currency: str,
        event: str,
        impact: str,  # "high", "medium", "low"
        forecast: Optional[str] = None,
        previous: Optional[str] = None,
    ) -> None:
        self.timestamp = timestamp
        self.currency = currency
        self.event = event
        self.impact = impact
        self.forecast = forecast
        self.previous = previous

    def __repr__(self) -> str:
        return f"NewsEvent({self.timestamp}, {self.currency}, {self.event}, {self.impact})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "currency": self.currency,
            "event": self.event,
            "impact": self.impact,
            "forecast": self.forecast,
            "previous": self.previous,
        }


class EconomicCalendar:
    """
    Economic calendar service.

    Fetches news events from free APIs and checks for upcoming high-impact events.
    """

    # High-impact events to watch
    HIGH_IMPACT_KEYWORDS = [
        "NFP", "Non-Farm", "Employment", "FOMC", "Fed", "Interest Rate",
        "CPI", "Inflation", "GDP", "Retail Sales", "PMI", "PPI",
        "ECB", "BOE", "BOJ", "Central Bank", "Monetary Policy",
    ]

    # Currencies that affect major pairs
    MAJOR_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF"]

    def __init__(self, cache_minutes: int = 240) -> None:
        """
        Initialize economic calendar.

        Parameters
        ----------
        cache_minutes : int
            How long to cache calendar data (default: 4 hours)
            Economic calendar doesn't change frequently, so 4h cache is safe.
        """
        self.cache_minutes = cache_minutes
        self._events: List[NewsEvent] = []
        self._last_fetch: Optional[datetime] = None
        self._fetch_retries = 0
        self._max_retries = 3

    async def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        currencies: Optional[List[str]] = None,
        min_impact: str = "high",
    ) -> List[NewsEvent]:
        """
        Get upcoming economic events.

        Parameters
        ----------
        hours_ahead : int
            How many hours ahead to look (default: 24)
        currencies : list, optional
            Filter by currencies (default: all major)
        min_impact : str
            Minimum impact level: "high", "medium", "low"

        Returns
        -------
        list of NewsEvent
        """
        # Check cache
        now = datetime.now(timezone.utc)
        if self._last_fetch and (now - self._last_fetch).total_seconds() < self.cache_minutes * 60:
            logger.debug("Using cached calendar data")
        else:
            await self._fetch_calendar()
            self._last_fetch = now

        # Filter events
        cutoff = now + timedelta(hours=hours_ahead)
        currencies = currencies or self.MAJOR_CURRENCIES

        filtered = []
        for event in self._events:
            if event.timestamp > cutoff:
                continue
            if event.currency not in currencies:
                continue
            if min_impact == "high" and event.impact != "high":
                continue
            filtered.append(event)

        return sorted(filtered, key=lambda e: e.timestamp)

    async def is_near_news(
        self,
        buffer_minutes: int = 30,
        currencies: Optional[List[str]] = None,
    ) -> Optional[NewsEvent]:
        """
        Check if current time is near a high-impact news event.

        Parameters
        ----------
        buffer_minutes : int
            Minutes before/after news to consider "near"
        currencies : list, optional
            Filter by currencies

        Returns
        -------
        NewsEvent or None
            The upcoming/recent news event, or None if clear
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=buffer_minutes)
        window_end = now + timedelta(minutes=buffer_minutes)

        events = await self.get_upcoming_events(
            hours_ahead=1,
            currencies=currencies,
            min_impact="high",
        )

        for event in events:
            if window_start <= event.timestamp <= window_end:
                logger.warning(
                    "Near news event: %s %s at %s (impact: %s)",
                    event.currency, event.event, event.timestamp, event.impact
                )
                return event

        return None

    async def _fetch_calendar(self) -> None:
        """Fetch economic calendar from Faireconomy API (ForexFactory data)."""
        import asyncio

        for attempt in range(self._max_retries):
            try:
                logger.info("Fetching economic calendar from Faireconomy API (attempt %d)...", attempt + 1)

                # Faireconomy API - free, no API key needed
                # Provides ForexFactory calendar data in JSON format
                url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url)

                    # Handle rate limiting
                    if response.status_code == 429:
                        wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        logger.warning("Rate limited (429). Waiting %ds before retry...", wait_time)
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    data = response.json()

                self._events = self._parse_faireconomy_data(data)
                self._fetch_retries = 0
                logger.info("Loaded %d calendar events from Faireconomy", len(self._events))
                return

            except httpx.HTTPStatusError as e:
                logger.error("HTTP error fetching calendar: %s", e)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                continue
            except Exception as e:
                logger.error("Failed to fetch economic calendar: %s", e)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                continue

        # All retries failed, use mock data as fallback
        logger.warning("All API attempts failed. Using mock calendar data as fallback.")
        self._events = self._get_mock_events()

    def _parse_faireconomy_data(self, data: List[Dict[str, Any]]) -> List[NewsEvent]:
        """
        Parse Faireconomy API response into NewsEvent objects.

        Faireconomy format:
        {
            "date": "2024-01-05T13:30:00-05:00",
            "country": "USD",
            "event": "Non-Farm Payrolls",
            "impact": "High",
            "forecast": "180K",
            "previous": "175K"
        }
        """
        events = []

        for item in data:
            try:
                # Parse timestamp
                date_str = item.get("date", "")
                if not date_str:
                    continue

                # Convert to UTC datetime
                timestamp = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                timestamp = timestamp.astimezone(timezone.utc)

                # Get impact level
                impact_str = item.get("impact", "").lower()
                if impact_str in ["high", "holiday", "non-economic"]:
                    impact = "high"
                elif impact_str == "medium":
                    impact = "medium"
                else:
                    impact = "low"

                # Only keep high-impact events
                if impact != "high":
                    continue

                event = NewsEvent(
                    timestamp=timestamp,
                    currency=item.get("country", "USD"),
                    event=item.get("event", "Unknown"),
                    impact=impact,
                    forecast=item.get("forecast"),
                    previous=item.get("previous"),
                )
                events.append(event)

            except Exception as e:
                logger.warning("Failed to parse calendar event: %s", e)
                continue

        return events

    def _get_mock_events(self) -> List[NewsEvent]:
        """
        Generate mock news events for testing.

        In production, replace with actual API call.
        """
        now = datetime.now(timezone.utc)
        events = []

        # Example: Add some mock high-impact events
        # NFP on first Friday of month
        if now.weekday() == 4 and now.day <= 7:  # Friday, first week
            nfp_time = now.replace(hour=13, minute=30, second=0, microsecond=0)
            events.append(NewsEvent(
                timestamp=nfp_time,
                currency="USD",
                event="Non-Farm Payrolls",
                impact="high",
                forecast="180K",
                previous="175K",
            ))

        # FOMC meeting (example)
        fomc_time = now + timedelta(days=2, hours=1)
        fomc_time = fomc_time.replace(hour=19, minute=0, second=0, microsecond=0)
        events.append(NewsEvent(
            timestamp=fomc_time,
            currency="USD",
            event="FOMC Rate Decision",
            impact="high",
            forecast="5.25%",
            previous="5.25%",
        ))

        # CPI release
        cpi_time = now + timedelta(days=5)
        cpi_time = cpi_time.replace(hour=13, minute=30, second=0, microsecond=0)
        events.append(NewsEvent(
            timestamp=cpi_time,
            currency="USD",
            event="CPI m/m",
            impact="high",
            forecast="0.3%",
            previous="0.2%",
        ))

        return events

    def get_status(self) -> Dict[str, Any]:
        """Get calendar service status."""
        return {
            "events_loaded": len(self._events),
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "cache_minutes": self.cache_minutes,
            "high_impact_count": len([e for e in self._events if e.impact == "high"]),
        }
