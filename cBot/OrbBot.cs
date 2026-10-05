// ORB (Opening Range Breakout) as a single-instrument cBot — no AI, no news, pure rules.
//
// Ported from the dnse-kash reference implementation (src/dnse_kash/bots/orb_bot.py), keeping its
// rule semantics rather than a lookalike:
//
//   OR window   A TIME window starting at the session open: [SessionOpen, SessionOpen +
//               OpeningRangeMinutes), built from CLOSED bars only. It is not a candle body.
//   Breakout    The first CLOSED bar after the window with Close > OR_high + buffer (long) or
//               Close < OR_low - buffer (short). One signal per session (MaxTradesPerSession).
//   OR width    A window narrower than MinOrWidthPips says nothing about the day; the session is
//               SKIPPED rather than traded on noise.
//
// The "previous session's OR" regime (the bias this bot exists to test):
//
//   PrevOrFilterEnabled  The previous session's OR sits ENTIRELY on the wrong side of today's:
//                          LONG : prevOR_low  > todayOR_high  (today opened below yesterday) -> skip
//                          SHORT: prevOR_high < todayOR_low   (today opened above yesterday) -> skip
//                        Only the SIGNAL is rejected, not the session: the opposite direction can
//                        still trade, because both ORs are already fixed for the day.
//   PrevOrSlEnabled      Anchor the stop at the opposite boundary of the previous session's OR
//                        (LONG -> prevOR_low, SHORT -> prevOR_high). Falls back to StopMode when
//                        that level is missing or on the wrong side of the entry.
//   Freshness            Only the IMMEDIATELY preceding session counts. If it had an OR window but
//                        the range was too narrow, the levels are dropped rather than reused — a
//                        stale level once produced a stop hundreds of pips from the entry. A
//                        session with no OR window at all (weekend, holiday) leaves the previous
//                        reference in place, because the market was simply shut.
//
// What the rules cost was measured on XAUUSD M15 (2023-01 -> 2026-08, 927 sessions, spread $0.20,
// NY-open OR): the previous-OR FILTER has no edge — breakout direction does not persist from one
// session to the next (50.5% agreement against a 50.0% coin flip, z = +0.33), and the filter alone
// left total R unchanged. The previous-OR STOP does cut drawdown roughly in half (-46R -> -17R) but
// also cuts return, and the comparison flips sign with the stop floor. Both switches therefore
// default to OFF, exactly as in the Python reference; see research/orb_prevday/SUMMARY.md.
//
// Two guardrails that analysis showed are not optional:
//
//   MinSlPips   The previous-OR stop can land a few cents from the entry when the ranges barely
//               overlap. One such trade (stop $0.34 away on gold) contributed +69R of a headline
//               +71.5R result and made an edgeless variant look profitable. The stop is therefore
//               clamped to MinSlPips, the same idea as AiAgentBot's MinSlAtr guardrail.
//   TakeProfitPips = 0  ORB lives on its right tail. Capping at 1R turned +45R into -82R over the
//               same trades. A take profit is available but defaults to none.
//
// Run one instance per symbol. The bot is timeframe-agnostic but the OR window is defined in wall
// clock minutes, so it needs bars fine enough to resolve it (m5 for a 15-minute OR).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    /// <summary>Which daylight-saving calendar shifts the session clock. Named apart from
    /// AiAgentBot's DstRule so the two sources can be read side by side without ambiguity.</summary>
    public enum OrbDstRule
    {
        None = 0,
        US = 1,
        Europe = 2
    }

    public enum OrbStopMode
    {
        OppositeOrBoundary = 0,
        FixedPips = 1,
        DailyAtr = 2
    }

    public enum OrbDirection
    {
        Both = 0,
        LongOnly = 1,
        ShortOnly = 2
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class OrbBot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "ORB")]
        public string BotId { get; set; }

        [Parameter("Account Identifier", Group = "General", DefaultValue = "demo")]
        public string AccountLabel { get; set; }

        [Parameter("Show Detailed Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        [Parameter("Enable Trading", Group = "General", DefaultValue = true)]
        public bool EnableTrading { get; set; }

        // ---- Session -------------------------------------------------------------------------
        // "Winter UTC" means the UTC hour that matches the local open while DST is off; the DstRule
        // pulls it back an hour while DST is on, so the local open stays put.
        [Parameter("Session Open Hour (Winter UTC)", Group = "Session", DefaultValue = 13, MinValue = 0, MaxValue = 23)]
        public int SessionOpenHourWinterUtc { get; set; }

        [Parameter("Session Open Minute", Group = "Session", DefaultValue = 0, MinValue = 0, MaxValue = 59)]
        public int SessionOpenMinute { get; set; }

        [Parameter("DST Rule", Group = "Session", DefaultValue = OrbDstRule.US)]
        public OrbDstRule SessionDstRule { get; set; }

        [Parameter("Opening Range (minutes)", Group = "Session", DefaultValue = 15, MinValue = 1, MaxValue = 240)]
        public int OpeningRangeMinutes { get; set; }

        [Parameter("EOD Flatten Hour (Winter UTC)", Group = "Session", DefaultValue = 21, MinValue = 0, MaxValue = 23)]
        public int EodFlattenHourWinterUtc { get; set; }

        [Parameter("EOD Flatten Minute", Group = "Session", DefaultValue = 0, MinValue = 0, MaxValue = 59)]
        public int EodFlattenMinute { get; set; }

        [Parameter("Trade Direction", Group = "Session", DefaultValue = OrbDirection.Both)]
        public OrbDirection Direction { get; set; }

        [Parameter("Max Trades per Session", Group = "Session", DefaultValue = 1, MinValue = 1, MaxValue = 5)]
        public int MaxTradesPerSession { get; set; }

        [Parameter("Allow Re-entry After Stop", Group = "Session", DefaultValue = false)]
        public bool AllowReentryAfterSl { get; set; }

        // ---- Opening range -------------------------------------------------------------------
        [Parameter("Min OR Width (pips)", Group = "Opening Range", DefaultValue = 20.0, MinValue = 0.0)]
        public double MinOrWidthPips { get; set; }

        [Parameter("Breakout Buffer (pips)", Group = "Opening Range", DefaultValue = 3.0, MinValue = 0.0)]
        public double BufferPips { get; set; }

        // ---- Previous session's opening range (regime / bias) ---------------------------------
        [Parameter("Prev-Day OR Gap Filter", Group = "Prev-Day OR", DefaultValue = false)]
        public bool PrevDayOrFilterEnabled { get; set; }

        [Parameter("Prev-Day OR Stop", Group = "Prev-Day OR", DefaultValue = false)]
        public bool PrevDayOrSlEnabled { get; set; }

        // Bias from previous day's close vs previous day's OR:
        // Long only if prev close > prev OR high, Short only if prev close < prev OR low.
        // Otherwise neutral -> no trade today.
        [Parameter("Prev-Day Close vs OR Bias", Group = "Prev-Day OR", DefaultValue = false)]
        public bool PrevDayCloseBiasEnabled { get; set; }

        // Daily ATR multiplier for DailyAtr stop mode (e.g. 1.0 = 1x yesterday's Daily ATR)
        [Parameter("Daily ATR Multiplier", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10.0, Step = 0.05)]
        public double DailyAtrMultiplier { get; set; }

        [Parameter("Daily ATR Period", Group = "Risk", DefaultValue = 14, MinValue = 1, MaxValue = 100)]
        public int DailyAtrPeriod { get; set; }

        // Take Profit in R multiple (0 = off / use TakeProfitPips)
        [Parameter("Take Profit (R multiple, 0=off)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 20.0, Step = 0.1)]
        public double TakeProfitRMultiple { get; set; }

        // ---- Breakeven ----
        // Breakeven trigger in R multiple (e.g. 1.5 = move SL to entry when profit >= 1.5R; 0 = off)
        [Parameter("Breakeven Trigger (R multiple, 0=off)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
        public double BreakevenTriggerR { get; set; }

        // Breakeven buffer/offset in pips past entry to lock in spread/commission
        [Parameter("Breakeven Offset (pips)", Group = "Risk", DefaultValue = 5.0, MinValue = 0.0)]
        public double BreakevenOffsetPips { get; set; }

        // Partial close ratio at Breakeven trigger (e.g. 0.5 = close 50% volume; 0 = off)
        [Parameter("Partial Close at BE (0-1)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 0.9, Step = 0.1)]
        public double PartialCloseRatio { get; set; }

        // ---- Trailing Stop ----
        // Trailing Stop trigger in R multiple (e.g. 2.0 = trail SL once profit >= 2.0R; 0 = off)
        [Parameter("Trail Trigger (R multiple, 0=off)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
        public double TrailTriggerR { get; set; }

        // Trailing distance behind market price in R multiple (e.g. 1.0 = trail 1.0R behind current price)
        [Parameter("Trail Distance (R multiple)", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
        public double TrailDistanceR { get; set; }

        // ---- Breakout quality ----------------------------------------------------------------
        // Refuse to chase a bar that closed far past the boundary. 0 disables the check.
        [Parameter("Max Breakout Distance (pips, 0=off)", Group = "Breakout Quality", DefaultValue = 0.0, MinValue = 0.0)]
        public double MaxBreakoutDistancePips { get; set; }

        // True: one too-far bar kills the whole session (the move already happened).
        // False: skip that bar only and stay armed for a later, in-range breakout.
        [Parameter("Skip Session When Too Far", Group = "Breakout Quality", DefaultValue = false)]
        public bool InvalidateWhenTooFar { get; set; }

        [Parameter("ATR Breakout Filter", Group = "Breakout Quality", DefaultValue = false)]
        public bool AtrBreakoutFilterEnabled { get; set; }

        [Parameter("ATR Period", Group = "Breakout Quality", DefaultValue = 14, MinValue = 1, MaxValue = 100)]
        public int AtrPeriod { get; set; }

        [Parameter("ATR Min Multiple", Group = "Breakout Quality", DefaultValue = 0.35, MinValue = 0.0, MaxValue = 5.0, Step = 0.05)]
        public double AtrMinMultiple { get; set; }

        [Parameter("ATR Min Pips Floor", Group = "Breakout Quality", DefaultValue = 0.0, MinValue = 0.0)]
        public double AtrMinPipsFloor { get; set; }

        // When there are too few bars to compute the ATR: false lets the signal through, true blocks it.
        [Parameter("ATR Require Full Bars", Group = "Breakout Quality", DefaultValue = false)]
        public bool AtrRequireFullBars { get; set; }

        [Parameter("ATR Exclude OR Window Bars", Group = "Breakout Quality", DefaultValue = true)]
        public bool AtrExcludeOrWindowBars { get; set; }

        // ---- Risk ----------------------------------------------------------------------------
        [Parameter("Stop Mode", Group = "Risk", DefaultValue = OrbStopMode.DailyAtr)]
        public OrbStopMode StopMode { get; set; }

        [Parameter("Fixed Stop (pips, FixedPips mode)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0)]
        public double FixedSlPips { get; set; }

        // Floor for every stop. A structural level can land almost on the entry; without this the
        // trade is a coin flip decided by the spread. The default is tuned for XAUUSD, where 300
        // pips = $3.00 ~ one M15 ATR — the floor the study in research/orb_prevday/ used. Re-tune it
        // per instrument: on a 5-digit FX pair 300 pips is an order of magnitude too wide.
        [Parameter("Min Stop (pips)", Group = "Risk", DefaultValue = 300.0, MinValue = 0.0)]
        public double MinSlPips { get; set; }

        [Parameter("Take Profit (pips, 0=none)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0)]
        public double TakeProfitPips { get; set; }

        [Parameter("Default Stop (pips, FixedPips fallback)", Group = "Risk", DefaultValue = 50.0, MinValue = 1.0)]
        public double DefaultSlPips { get; set; }

        [Parameter("Risk per Trade (% Equity)", Group = "Risk", DefaultValue = 0.2, MinValue = 0.01, MaxValue = 10.0, Step = 0.01)]
        public double RiskPerTradePercent { get; set; }

        [Parameter("Max Lots per Trade", Group = "Risk", DefaultValue = 5.0, MinValue = 0.01)]
        public double MaxAllowedLots { get; set; }

        // The broker's minimum lot may not be able to respect this at the chosen stop; the entry is
        // then refused rather than sized up (same guardrail as FlowRsiBot / TurtleBot / AiAgentBot).
        [Parameter("Max Dollar Risk per Trade ($)", Group = "Risk", DefaultValue = 100.0, MinValue = 1.0)]
        public double MaxDollarRiskPerTrade { get; set; }

        [Parameter("Max Spread (pips)", Group = "Risk", DefaultValue = 50.0, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        // ---- Reporting -----------------------------------------------------------------------
        [Parameter("Agent API URL", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/trade")]
        public string ApiUrl { get; set; }

        [Parameter("Portfolio Report Endpoint", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/portfolio/report")]
        public string AiReportUrl { get; set; }

        #endregion

        #region State

        private const string LongTag = "ORB-L";
        private const string ShortTag = "ORB-S";
        private const double MaxQuoteAgeSeconds = 60;

        // ---- Session -------------------------------------------------------------------------
        private DateTime _sessionDate = DateTime.MinValue;
        private bool _eodFlattened;
        private int _tradesFired;
        private bool _blockedAfterStop;

        // ---- Today's opening range -----------------------------------------------------------
        private double _orHigh = double.MinValue;
        private double _orLow = double.MaxValue;
        private bool _orHadWindow;      // at least one closed bar fell inside the window
        private bool _orFinalized;      // the window has closed and the width was judged
        private bool _orValid;          // ...and the width cleared MinOrWidthPips
        private int _orBarCount;
        private bool _orTooFarLogged;
        private bool _sessionInvalidated;   // a too-far breakout killed the rest of the session

        // ---- Previous session's opening range (regime reference) ------------------------------
        private double _prevOrHigh = double.MinValue;
        private double _prevOrLow = double.MaxValue;
        private bool _prevOrFresh;
        private DateTime _prevOrDate = DateTime.MinValue;
        private readonly HashSet<string> _prevOrBlockLogged = new HashSet<string>();
        private double _prevDayClose = double.NaN;
        private Bar _lastClosedBar;
        private Bars _dailyBars;

        // ---- Execution -----------------------------------------------------------------------
        private readonly Dictionary<int, double> _initialSlPips = new Dictionary<int, double>();
        private readonly Dictionary<int, string> _closeReasons = new Dictionary<int, string>();
        private readonly HashSet<int> _breakevenApplied = new HashSet<int>();

        private DateTime _lastTickAt = DateTime.MinValue;
        private volatile bool _isStopped;

        // uvicorn closes an idle keep-alive connection after 5 s; retire pooled ones before it does
        // (see tests/test_cbot_report_retry.py).
        private readonly HttpClient _httpClient = new HttpClient(new SocketsHttpHandler { PooledConnectionIdleTimeout = TimeSpan.FromSeconds(4) })
        {
            Timeout = TimeSpan.FromSeconds(60)
        };

        #endregion

        #region Lifecycle

        protected override void OnStart()
        {
            Print($"[ORB] Starting '{BotId}' on {SymbolName} ({TimeFrame}) | OR {OpeningRangeMinutes}m from " +
                  $"{SessionOpenHourWinterUtc:00}:{SessionOpenMinute:00} UTC (winter), EOD flatten " +
                  $"{EodFlattenHourWinterUtc:00}:{EodFlattenMinute:00} UTC | dir {Direction} | " +
                  $"stop {StopMode} (min {MinSlPips:F0} pips) | PrevOR[filter=" +
                  $"{(PrevDayOrFilterEnabled ? "on" : "off")} sl={(PrevDayOrSlEnabled ? "on" : "off")}] | " +
                  $"risk {RiskPerTradePercent:F2}%/trade");

            Positions.Closed += OnPositionClosed;
            _dailyBars = MarketData.GetBars(TimeFrame.Daily);
            if (_dailyBars != null && _dailyBars.Count >= 2)
                _prevDayClose = _dailyBars.ClosePrices.Last(1);
            SendAccountSync();
            Evaluate();
        }
        protected override void OnTick()
        {
            _lastTickAt = Server.Time;
            Evaluate();
        }

        protected override void OnBarClosed()
        {
            // The OR window and the breakout are both defined on CLOSED bars, so all of the signal
            // logic runs here. OnTick only carries the end-of-day flatten, which must not wait for
            // another bar to close.
            var closed = Bars.Last(1);
            _lastTickAt = Server.Time;      // a bar closed, so a quote did arrive
            RollOverSessionIfNeeded(closed.OpenTime);
            _lastClosedBar = closed;
            CollectOrBar(closed);
            EvaluateBreakout(closed);
        }
        protected override void OnStop()
        {
            _isStopped = true;
            Positions.Closed -= OnPositionClosed;
            Print($"[ORB] Stopped on {SymbolName}.");
            _httpClient?.Dispose();
        }

        #endregion

        #region Session clock

        /// <summary>True while the US DST calendar is in force (2nd Sunday of March 07:00 UTC to
        /// 1st Sunday of November 06:00 UTC) — the same boundaries AiAgentBot uses.</summary>
        private bool IsUsDst(DateTime utc)
        {
            DateTime start = NthSunday(utc.Year, 3, 2).AddHours(7);
            DateTime end = NthSunday(utc.Year, 11, 1).AddHours(6);
            return utc >= start && utc < end;
        }

        private bool IsEuropeDst(DateTime utc)
        {
            DateTime start = LastSunday(utc.Year, 3).AddHours(1);
            DateTime end = LastSunday(utc.Year, 10).AddHours(1);
            return utc >= start && utc < end;
        }

        /// <summary>The UTC hour a local open corresponds to: one hour earlier while DST is on, so
        /// the local clock time of the session never moves.</summary>
        private int AdjustedHour(DateTime utc, int winterHour)
        {
            if (SessionDstRule == OrbDstRule.None || winterHour == 0) return winterHour;
            bool isDst = SessionDstRule == OrbDstRule.US ? IsUsDst(utc) : IsEuropeDst(utc);
            int hour = isDst ? winterHour - 1 : winterHour;
            return hour < 0 ? hour + 24 : hour;
        }

        private static DateTime NthSunday(int year, int month, int n)
        {
            var first = new DateTime(year, month, 1);
            int offset = (7 - (int)first.DayOfWeek) % 7;
            return first.AddDays(offset + (n - 1) * 7);
        }

        private static DateTime LastSunday(int year, int month)
        {
            var last = new DateTime(year, month, DateTime.DaysInMonth(year, month));
            return last.AddDays(-(int)last.DayOfWeek);
        }

        private DateTime SessionOpenUtc(DateTime anyTimeInSession)
        {
            int hour = AdjustedHour(anyTimeInSession, SessionOpenHourWinterUtc);
            return anyTimeInSession.Date.AddHours(hour).AddMinutes(SessionOpenMinute);
        }

        private DateTime OrWindowEndUtc(DateTime sessionOpen) => sessionOpen.AddMinutes(OpeningRangeMinutes);

        private DateTime EodFlattenUtc(DateTime anyTimeInSession)
        {
            int hour = AdjustedHour(anyTimeInSession, EodFlattenHourWinterUtc);
            return anyTimeInSession.Date.AddHours(hour).AddMinutes(EodFlattenMinute);
        }

        /// <summary>A tick at most MaxQuoteAgeSeconds old, so Symbol.Bid/Ask are the market's
        /// current prices and not the last quote before a weekend or a holiday.</summary>
        private bool HasFreshQuote(DateTime now) => (now - _lastTickAt).TotalSeconds <= MaxQuoteAgeSeconds;

        #endregion

        #region Opening range

        private void RollOverSessionIfNeeded(DateTime barOpenUtc)
        {
            var date = barOpenUtc.Date;
            if (_sessionDate == date) return;

            if (_sessionDate != DateTime.MinValue) CapturePrevDayOr();

            _sessionDate = date;
            _orHigh = double.MinValue;
            _orLow = double.MaxValue;
            _orHadWindow = false;
            _orFinalized = false;
            _orValid = false;
            _orBarCount = 0;
            _orTooFarLogged = false;
            _sessionInvalidated = false;
            _tradesFired = 0;
            _blockedAfterStop = false;
            _eodFlattened = false;
            _prevOrBlockLogged.Clear();

            if (ShowLogs) Print($"[ORB] New session {date:yyyy-MM-dd} | " + PrevOrDescription());
        }

        /// <summary>
        /// Hands the session that just ended to the D-1 reference. A session that had an OR window
        /// but failed MinOrWidthPips deliberately drops the reference (the levels would be stale);
        /// a session with no window at all — weekend, holiday — keeps the one before it, because
        /// the market never opened and "yesterday" is still the last session that traded.
        /// </summary>
        private void CapturePrevDayOr()
        {
            if (!_orHadWindow)
            {
                if (ShowLogs && _prevOrFresh)
                    Print($"[ORB] Prev-OR kept | session {_sessionDate:yyyy-MM-dd} had no opening range (market shut)");
                return;
            }

            if (_orHigh == double.MinValue || _orLow == double.MaxValue) return;

            if (!_orValid)
            {
                if (_prevOrFresh)
                    Print($"[ORB] Prev-OR dropped | session {_sessionDate:yyyy-MM-dd} opening range under " +
                          $"{MinOrWidthPips:F0} pips -> the D-1 gate and stop are off for the new session");
                _prevOrFresh = false;
                return;
            }

            _prevOrHigh = _orHigh;
            _prevOrLow = _orLow;
            _prevOrDate = _sessionDate;
            _prevOrFresh = true;
            var dBars = _dailyBars ?? MarketData.GetBars(TimeFrame.Daily);
            if (dBars != null && dBars.Count >= 2)
                _prevDayClose = dBars.ClosePrices.Last(1);
            else if (_lastClosedBar != null)
                _prevDayClose = _lastClosedBar.Close;
            else if (Bars.Count >= 2)
                _prevDayClose = Bars.ClosePrices.Last(1);
            if (ShowLogs)
                Print($"[ORB] Prev-OR captured | session {_prevOrDate:yyyy-MM-dd} " +
                      $"[{_prevOrLow:F5} - {_prevOrHigh:F5}], prevClose={_prevDayClose:F5}");
        }

        private string PrevOrDescription() => _prevOrFresh
            ? $"prevOR {_prevOrDate:yyyy-MM-dd} [{_prevOrLow:F5} - {_prevOrHigh:F5}] (prevClose {(!double.IsNaN(_prevDayClose) ? _prevDayClose.ToString("F5") : "none")})"
            : "prevOR none";

        /// <summary>Extends today's range with a closed bar inside the window, then judges the
        /// width once the window has passed.</summary>
        private void CollectOrBar(Bar bar)
        {
            if (_orFinalized) return;

            DateTime open = SessionOpenUtc(bar.OpenTime);
            DateTime end = OrWindowEndUtc(open);

            if (bar.OpenTime < open) return;          // the tail of the previous session, not this one
            if (bar.OpenTime >= end) { FinalizeOr(); return; }

            _orHadWindow = true;
            _orBarCount++;
            if (bar.High > _orHigh) _orHigh = bar.High;
            if (bar.Low < _orLow) _orLow = bar.Low;
        }

        private void FinalizeOr()
        {
            if (_orFinalized) return;
            _orFinalized = true;

            // A cold start or a restart after the window closed would otherwise lose the session:
            // the incremental collection only sees bars that close while the bot is running.
            if (!_orHadWindow) BackfillOrFromHistory();

            if (!_orHadWindow || _orHigh == double.MinValue || _orLow == double.MaxValue)
            {
                _orValid = false;
                return;
            }

            double widthPips = (_orHigh - _orLow) / Symbol.PipSize;
            _orValid = widthPips >= MinOrWidthPips;

            if (_orValid)
                Print($"[ORB] Opening range {_sessionDate:yyyy-MM-dd} [{_orLow:F5} - {_orHigh:F5}] " +
                      $"= {widthPips:F1} pips over {_orBarCount} bars");
            else
                Print($"[ORB] Opening range {_sessionDate:yyyy-MM-dd} only {widthPips:F1} pips " +
                      $"(< {MinOrWidthPips:F1}); session skipped");
        }

        /// <summary>
        /// Rebuilds the window from Bars history. Only reached when the incremental collection saw
        /// no bar inside it — the bot started after the window had already closed.
        /// </summary>
        private void BackfillOrFromHistory()
        {
            DateTime open = SessionOpenUtc(_sessionDate);
            DateTime end = OrWindowEndUtc(open);

            for (int i = Bars.Count - 2; i >= 0; i--)
            {
                DateTime t = Bars.OpenTimes[i];
                if (t >= end) continue;
                if (t < open) break;                 // ordered series: everything older than this
                _orHadWindow = true;
                _orBarCount++;
                if (Bars.HighPrices[i] > _orHigh) _orHigh = Bars.HighPrices[i];
                if (Bars.LowPrices[i] < _orLow) _orLow = Bars.LowPrices[i];
            }
        }

        #endregion

        #region Previous session's opening range

        /// <summary>Both D-1 levels exist and belong to the session immediately before this one.</summary>
        private bool PrevOrReady => _prevOrFresh && _prevOrHigh > double.MinValue && _prevOrLow < double.MaxValue;

        /// <summary>
        /// True when the previous session's range sits entirely on the wrong side of today's in the
        /// direction of the breakout — the market opened away from the trade:
        ///   LONG : prevOR_low  > todayOR_high  (today is below yesterday)
        ///   SHORT: prevOR_high &lt; todayOR_low   (today is above yesterday)
        /// Missing D-1 data never blocks; that keeps the first session of a window and a cold start
        /// behaving like the plain ORB instead of silently refusing to trade.
        /// </summary>
        private bool PrevDayOrBlocks(TradeType side)
        {
            if (!PrevDayOrFilterEnabled) return false;
            if (!PrevOrReady || !_orValid) return false;
            return side == TradeType.Buy ? _prevOrLow > _orHigh : _prevOrHigh < _orLow;
        }

        /// <summary>
        /// The stop anchored at the opposite boundary of the previous session's range, or null when
        /// the switch is off, the levels are missing, or the level is on the wrong side of the
        /// entry — in which case the caller falls back to StopMode.
        /// </summary>
        private double? PrevDayOrStopPrice(TradeType side, double entry)
        {
            if (!PrevDayOrSlEnabled || !PrevOrReady) return null;
            double reference = side == TradeType.Buy ? _prevOrLow : _prevOrHigh;
            if (side == TradeType.Buy && reference >= entry) return null;
            if (side == TradeType.Sell && reference <= entry) return null;
            return reference;
        }

        /// <summary>
        /// Checks whether the trade direction is allowed under PrevDayCloseBias:
        /// If prevDayClose > prevOrHigh -> LongOnly
        /// If prevDayClose < prevOrLow -> ShortOnly
        /// Otherwise (closed inside prevOR) -> Neutral (neither side allowed).
        /// </summary>
        private bool PassesPrevDayCloseBias(TradeType side)
        {
            if (!PrevDayCloseBiasEnabled) return true;
            if (!PrevOrReady || double.IsNaN(_prevDayClose)) return true; // cold start / no data -> pass through

            bool isLong = side == TradeType.Buy;
            if (isLong && _prevDayClose <= _prevOrHigh)
            {
                string key = $"CloseBias-{side}-{_prevOrDate:yyyy-MM-dd}";
                if (ShowLogs && _prevOrBlockLogged.Add(key))
                    Print($"[ORB] Prev-Day Close Bias blocked Buy: prevClose {_prevDayClose:F5} <= prevOR high {_prevOrHigh:F5}");
                return false;
            }
            if (!isLong && _prevDayClose >= _prevOrLow)
            {
                string key = $"CloseBias-{side}-{_prevOrDate:yyyy-MM-dd}";
                if (ShowLogs && _prevOrBlockLogged.Add(key))
                    Print($"[ORB] Prev-Day Close Bias blocked Sell: prevClose {_prevDayClose:F5} >= prevOR low {_prevOrLow:F5}");
                return false;
            }
            return true;
        }

        private double GetYesterdayDailyAtrPips()
        {
            try
            {
                var dBars = _dailyBars ?? MarketData.GetBars(TimeFrame.Daily);
                if (dBars == null || dBars.Count < 2) return 0.0;

                int count = Math.Min(dBars.Count - 1, DailyAtrPeriod);
                if (count <= 0) return 0.0;

                double trSum = 0.0;
                int actualCount = 0;
                // Calculate over closed daily bars (1 to count)
                for (int i = 1; i <= count; i++)
                {
                    int idx = dBars.Count - 1 - i;
                    if (idx < 0) break;
                    double h = dBars.HighPrices[idx];
                    double l = dBars.LowPrices[idx];
                    double pc = idx > 0 ? dBars.ClosePrices[idx - 1] : l;
                    double tr = Math.Max(h - l, Math.Max(Math.Abs(h - pc), Math.Abs(l - pc)));
                    trSum += tr;
                    actualCount++;
                }
                if (actualCount <= 0) return 0.0;
                double avgTr = trSum / actualCount;
                return avgTr / Symbol.PipSize;
            }
            catch
            {
                return 0.0;
            }
        }

        #endregion

        #region Breakout

        private void EvaluateBreakout(Bar closed)
        {
            if (!EnableTrading || _isStopped) return;
            if (!_orFinalized || !_orValid) return;
            if (_sessionInvalidated || _eodFlattened) return;
            // The session ends at the flatten: a breakout after it has nothing to hold it.
            if (closed.OpenTime >= EodFlattenUtc(closed.OpenTime)) return;
            if (_tradesFired >= MaxTradesPerSession) return;
            if (_blockedAfterStop && !AllowReentryAfterSl) return;
            if (Positions.FindAll(BotId, SymbolName).Length > 0) return;

            double buffer = BufferPips * Symbol.PipSize;
            bool longConfirm = closed.Close > _orHigh + buffer;
            bool shortConfirm = closed.Close < _orLow - buffer;
            bool allowLong = Direction != OrbDirection.ShortOnly;
            bool allowShort = Direction != OrbDirection.LongOnly;

            if (!((longConfirm && allowLong) || (shortConfirm && allowShort))) return;
            if (longConfirm && allowLong && !PassesPrevDayCloseBias(TradeType.Buy)) return;
            if (shortConfirm && allowShort && !PassesPrevDayCloseBias(TradeType.Sell)) return;

            // Structural gate first: it depends only on levels already fixed for the day, so a
            // rejected direction costs nothing and the other direction stays available.
            if (longConfirm && allowLong && PrevDayOrBlocks(TradeType.Buy))
            {
                RejectPrevDayOrGap(TradeType.Buy, closed.Close);
                return;
            }
            if (shortConfirm && allowShort && PrevDayOrBlocks(TradeType.Sell))
            {
                RejectPrevDayOrGap(TradeType.Sell, closed.Close);
                return;
            }

            if (!PassesAtrFilter(closed, longConfirm && allowLong, shortConfirm && allowShort)) return;
            if (!PassesBreakoutDistance(closed, longConfirm && allowLong, shortConfirm && allowShort)) return;

            double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                if (ShowLogs) Print($"[ORB] Spread {spreadPips:F1} pips > {MaxSpreadPips:F1}; signal skipped");
                return;
            }

            // One direction only: a bar cannot close beyond both boundaries, but a narrow OR with a
            // wide buffer could qualify on both sides in a single print.
            if (longConfirm && allowLong) TryOpen(TradeType.Buy, closed.Close);
            else if (shortConfirm && allowShort) TryOpen(TradeType.Sell, closed.Close);
        }

        private void RejectPrevDayOrGap(TradeType side, double close)
        {
            bool isLong = side == TradeType.Buy;
            double reference = isLong ? _prevOrLow : _prevOrHigh;
            double todayBound = isLong ? _orHigh : _orLow;
            string key = $"{side}-{_prevOrDate:yyyy-MM-dd}";
            if (ShowLogs && _prevOrBlockLogged.Add(key))
                Print($"[ORB] Prev-OR gap filter rejected {side} | prevOR {(isLong ? "low" : "high")} " +
                      $"{reference:F5} {(isLong ? ">" : "<")} today OR {(isLong ? "high" : "low")} " +
                      $"{todayBound:F5} | close {close:F5} — still armed");
        }

        #endregion

        #region Breakout quality filters

        private double TrueRangeAt(int i)
        {
            double prevClose = Bars.ClosePrices[i - 1];
            double high = Bars.HighPrices[i];
            double low = Bars.LowPrices[i];
            return Math.Max(high - low, Math.Max(Math.Abs(high - prevClose), Math.Abs(low - prevClose)));
        }

        /// <summary>
        /// Wilder's ATR over the closed bars that precede the breakout bar — never including the
        /// forming one, so the threshold cannot move with the bar it is judging. Bars inside the OR
        /// window are optionally excluded: on a quiet open they drag the ATR down and would let
        /// through exactly the breakouts the filter exists to reject.
        /// </summary>
        private double WilderAtrBefore(DateTime confirmOpenUtc, out int sampleCount)
        {
            var indices = new List<int>();
            DateTime orStart = SessionOpenUtc(confirmOpenUtc);
            DateTime orEnd = OrWindowEndUtc(orStart);
            int want = Math.Max(AtrPeriod * 3, 60);

            for (int i = Bars.Count - 2; i >= 1 && indices.Count < want; i--)
            {
                DateTime t = Bars.OpenTimes[i];
                if (t >= confirmOpenUtc) continue;
                if (AtrExcludeOrWindowBars && t >= orStart && t < orEnd) continue;
                indices.Add(i);
            }
            indices.Reverse();
            sampleCount = indices.Count;

            if (indices.Count < AtrPeriod + 1) return 0.0;

            double sum = 0.0;
            for (int k = 1; k <= AtrPeriod; k++) sum += TrueRangeAt(indices[k]);
            double atr = sum / AtrPeriod;
            for (int k = AtrPeriod + 1; k < indices.Count; k++)
                atr = ((AtrPeriod - 1) * atr + TrueRangeAt(indices[k])) / AtrPeriod;
            return atr;
        }

        /// <summary>The breakout must clear the boundary by a meaningful fraction of recent
        /// volatility, not by the buffer alone.</summary>
        private bool PassesAtrFilter(Bar closed, bool longConfirm, bool shortConfirm)
        {
            if (!AtrBreakoutFilterEnabled) return true;

            double atr = WilderAtrBefore(closed.OpenTime, out int samples);
            if (atr <= 0.0)
            {
                if (AtrRequireFullBars)
                {
                    if (ShowLogs)
                        Print($"[ORB] ATR filter blocked: only {samples} prior bars " +
                              $"(needs {AtrPeriod + 1}); ATR Require Full Bars is on");
                    return false;
                }
                if (ShowLogs)
                    Print($"[ORB] ATR filter passed through: only {samples} prior bars (needs {AtrPeriod + 1})");
                return true;
            }

            double threshold = Math.Max(AtrMinMultiple * atr, AtrMinPipsFloor * Symbol.PipSize);
            if (longConfirm)
            {
                double distance = closed.Close - (_orHigh + BufferPips * Symbol.PipSize);
                if (distance < threshold)
                {
                    if (ShowLogs)
                        Print($"[ORB] ATR filter rejected Buy | cleared the boundary by {distance / Symbol.PipSize:F1} pips " +
                              $"(needs {threshold / Symbol.PipSize:F1}; ATR {atr / Symbol.PipSize:F1})");
                    return false;
                }
            }
            else if (shortConfirm)
            {
                double distance = (_orLow - BufferPips * Symbol.PipSize) - closed.Close;
                if (distance < threshold)
                {
                    if (ShowLogs)
                        Print($"[ORB] ATR filter rejected Sell | cleared the boundary by {distance / Symbol.PipSize:F1} pips " +
                              $"(needs {threshold / Symbol.PipSize:F1}; ATR {atr / Symbol.PipSize:F1})");
                    return false;
                }
            }
            return true;
        }

        /// <summary>Refuses to chase a bar that closed far past the boundary.</summary>
        private bool PassesBreakoutDistance(Bar closed, bool longConfirm, bool shortConfirm)
        {
            if (MaxBreakoutDistancePips <= 0) return true;

            double distance = longConfirm
                ? (closed.Close - _orHigh) / Symbol.PipSize
                : (shortConfirm ? (_orLow - closed.Close) / Symbol.PipSize : 0.0);
            if (distance <= MaxBreakoutDistancePips) return true;

            string side = longConfirm ? "Buy" : "Sell";
            string action = InvalidateWhenTooFar ? "session skipped" : "bar skipped, still armed";
            if (!_orTooFarLogged || InvalidateWhenTooFar)
            {
                _orTooFarLogged = true;
                Print($"[ORB] Breakout too far ({side}) | closed {distance:F1} pips past the boundary " +
                      $"(> {MaxBreakoutDistancePips:F1}) — {action}");
            }
            if (InvalidateWhenTooFar) _sessionInvalidated = true;   // nothing else may fire today
            return false;
        }

        #endregion

        #region Entries

        private void TryOpen(TradeType side, double confirmClose)
        {
            double entry = side == TradeType.Buy ? Symbol.Ask : Symbol.Bid;

            double? prevOrStop = PrevDayOrStopPrice(side, entry);
            double slPips;
            if (prevOrStop.HasValue)
                slPips = side == TradeType.Buy
                    ? (entry - prevOrStop.Value) / Symbol.PipSize
                    : (prevOrStop.Value - entry) / Symbol.PipSize;
            else if (StopMode == OrbStopMode.OppositeOrBoundary)
                slPips = side == TradeType.Buy
                    ? (entry - _orLow) / Symbol.PipSize
                    : (_orHigh - entry) / Symbol.PipSize;
            else if (StopMode == OrbStopMode.DailyAtr)
            {
                double dailyAtr = GetYesterdayDailyAtrPips();
                slPips = dailyAtr > 0 ? dailyAtr * DailyAtrMultiplier : DefaultSlPips;
            }
            else
                slPips = FixedSlPips > 0 ? FixedSlPips : DefaultSlPips;

            // The structural stop can land almost on the entry when the two ranges barely overlap.
            // Clamping here is what keeps a single degenerate trade from dominating the record.
            double rawSlPips = slPips;
            slPips = Math.Max(slPips, MinSlPips);
            if (slPips <= 0)
            {
                Print($"[ORB] {side} refused: stop distance is not positive ({slPips:F1} pips)");
                return;
            }

            double volume = VolumeForRisk(slPips, out double dollarRisk, out string refusal);
            if (volume <= 0)
            {
                Print($"[ORB] {side} refused: {refusal}");
                return;
            }

            double? tpPips = TakeProfitPips > 0 ? TakeProfitPips : (double?)null;
            if (TakeProfitRMultiple > 0) tpPips = slPips * TakeProfitRMultiple;
            string comment = side == TradeType.Buy
                ? $"{LongTag} {_sessionDate:yyyy-MM-dd}"
                : $"{ShortTag} {_sessionDate:yyyy-MM-dd}";

            var result = ExecuteMarketOrder(side, SymbolName, volume, BotId, slPips, tpPips, comment);
            if (result != null && result.IsSuccessful && result.Position != null)
            {
                _tradesFired++;
                _initialSlPips[result.Position.Id] = slPips;
                Print($"[ORB] Opened #{result.Position.Id} {side} {volume / Symbol.LotSize:F2} lots " +
                      $"{SymbolName} @ {result.Position.EntryPrice} | stop {slPips:F1} pips " +
                      (rawSlPips < slPips ? $"(clamped up from {rawSlPips:F1}) " : "") +
                      $"| risk ${dollarRisk:F2} | OR [{_orLow:F5} - {_orHigh:F5}]" +
                      (prevOrStop.HasValue ? $" | stop at prevOR {prevOrStop.Value:F5}" : ""));

                string reason = prevOrStop.HasValue
                    ? "ORB breakout, stop on the previous session's opening range"
                    : "ORB breakout of the opening range";
                ReportPositionOpen(result.Position, slPips, tpPips, reason);
            }
            else
            {
                Print($"[ORB] {side} order failed: {(result != null ? result.Error.ToString() : "no result")}");
            }
        }

        /// <summary>
        /// Volume risking RiskPerTradePercent of equity at `slPips`, floored at the broker's minimum
        /// and capped at MaxAllowedLots. Returns 0 (with a reason) when even the minimum lot would
        /// risk more than MaxDollarRiskPerTrade — refused rather than sized up.
        /// </summary>
        private double VolumeForRisk(double slPips, out double dollarRisk, out string refusal)
        {
            refusal = null;
            dollarRisk = 0.0;

            double riskAmount = Account.Balance * (RiskPerTradePercent / 100.0);
            double perUnitRisk = slPips * Symbol.PipValue;      // PipValue is per 1 unit of volume
            if (perUnitRisk <= 0)
            {
                refusal = "pip value is not positive";
                return 0.0;
            }

            double units = Symbol.NormalizeVolumeInUnits(riskAmount / perUnitRisk, RoundingMode.Down);
            units = Math.Min(units, Symbol.NormalizeVolumeInUnits(MaxAllowedLots * Symbol.LotSize, RoundingMode.Down));

            if (units < Symbol.VolumeInUnitsMin)
            {
                double minLotRisk = Symbol.VolumeInUnitsMin * perUnitRisk;
                if (minLotRisk > MaxDollarRiskPerTrade)
                {
                    refusal = $"minimum lot ({Symbol.VolumeInUnitsMin / Symbol.LotSize:F2}) at a {slPips:F1}-pip " +
                              $"stop risks ${minLotRisk:F2} > Max Dollar Risk ${MaxDollarRiskPerTrade:F2}";
                    return 0.0;
                }
                units = Symbol.VolumeInUnitsMin;
            }

            dollarRisk = units * perUnitRisk;
            return units;
        }

        #endregion

        #region Exits

        private void Evaluate()
        {
            if (_isStopped || !EnableTrading) return;
            try
            {
                var now = Server.Time;
                if (!HasFreshQuote(now)) return;

                if (_sessionDate != DateTime.MinValue && !_eodFlattened && now >= EodFlattenUtc(now))
                {
                    _eodFlattened = true;
                    FlattenAll("End of session");
                }

                ManageBreakeven();
                ManageTrailingStop();
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORB] Evaluate error: {ex.Message}");
            }
        }

        private void FlattenAll(string reason)
        {
            foreach (var position in Positions.FindAll(BotId, SymbolName))
            {
                var result = CloseWithReason(position, reason);
                if (result == null || !result.IsSuccessful)
                    Print($"[ORB] Could not flatten #{position.Id}: " +
                          $"{(result != null ? result.Error.ToString() : "no result")}");
            }
        }

        private TradeResult CloseWithReason(Position position, string reason)
        {
            _closeReasons[position.Id] = reason;
            return ClosePosition(position);
        }

        private void ManageBreakeven()
        {
            if (BreakevenTriggerR <= 0) return;

            foreach (var pos in Positions.FindAll(BotId, SymbolName))
            {
                if (_breakevenApplied.Contains(pos.Id)) continue;
                if (!_initialSlPips.TryGetValue(pos.Id, out double initialSlPips) || initialSlPips <= 0) continue;

                double profitPips = pos.Pips;
                double triggerPips = initialSlPips * BreakevenTriggerR;
                if (profitPips < triggerPips) continue;

                double beSlPrice = pos.TradeType == TradeType.Buy
                    ? pos.EntryPrice + BreakevenOffsetPips * Symbol.PipSize
                    : pos.EntryPrice - BreakevenOffsetPips * Symbol.PipSize;

                bool shouldMove = pos.TradeType == TradeType.Buy
                    ? (pos.StopLoss == null || beSlPrice > pos.StopLoss.Value)
                    : (pos.StopLoss == null || beSlPrice < pos.StopLoss.Value);

                if (!shouldMove)
                {
                    _breakevenApplied.Add(pos.Id);
                    continue;
                }

                var res = pos.ModifyStopLossPrice(beSlPrice);
                if (res != null && res.IsSuccessful)
                {
                    _breakevenApplied.Add(pos.Id);
                    Print($"[ORB] Breakeven applied on #{pos.Id} {pos.TradeType} | SL moved to {beSlPrice:F5} (+{BreakevenOffsetPips:F1} pips past entry)");

                    // Partial Close at Breakeven
                    if (PartialCloseRatio > 0 && PartialCloseRatio < 1.0)
                    {
                        double volumeToClose = Symbol.NormalizeVolumeInUnits(pos.VolumeInUnits * PartialCloseRatio);
                        double remainingVolume = pos.VolumeInUnits - volumeToClose;
                        if (volumeToClose >= Symbol.VolumeInUnitsMin && remainingVolume >= Symbol.VolumeInUnitsMin)
                        {
                            double pnlBeforePartial = pos.NetProfit;
                            var partialRes = pos.ModifyVolume(remainingVolume);
                            if (partialRes != null && partialRes.IsSuccessful)
                            {
                                double realizedPnl = pnlBeforePartial - pos.NetProfit;
                                try
                                {
                                    var partialHist = History.LastOrDefault(h => h.PositionId == pos.Id);
                                    if (partialHist != null) realizedPnl = partialHist.NetProfit;
                                }
                                catch { }

                                Print($"[ORB] Partial Close on #{pos.Id}: closed {volumeToClose / Symbol.LotSize:F2} lots ({(PartialCloseRatio * 100):F0}%), remaining {remainingVolume / Symbol.LotSize:F2} lots");
                                ReportPartialClose(pos, volumeToClose / Symbol.LotSize, remainingVolume / Symbol.LotSize, realizedPnl, "Partial close at Breakeven");
                            }
                            else
                            {
                                Print($"[ORB] Partial Close failed on #{pos.Id}: {partialRes?.Error}");
                            }
                        }
                    }
                }
            }
        }

        private void ManageTrailingStop()
        {
            if (TrailTriggerR <= 0 || TrailDistanceR <= 0) return;

            foreach (var pos in Positions.FindAll(BotId, SymbolName))
            {
                if (!_initialSlPips.TryGetValue(pos.Id, out double initialSlPips) || initialSlPips <= 0) continue;

                double profitPips = pos.Pips;
                double triggerPips = initialSlPips * TrailTriggerR;
                if (profitPips < triggerPips) continue;

                double trailDistancePips = initialSlPips * TrailDistanceR;
                double currentPrice = pos.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                double newSlPrice = pos.TradeType == TradeType.Buy
                    ? currentPrice - trailDistancePips * Symbol.PipSize
                    : currentPrice + trailDistancePips * Symbol.PipSize;

                bool shouldMove = pos.TradeType == TradeType.Buy
                    ? (pos.StopLoss == null || newSlPrice > pos.StopLoss.Value + Symbol.PipSize)
                    : (pos.StopLoss == null || newSlPrice < pos.StopLoss.Value - Symbol.PipSize);

                if (shouldMove)
                {
                    var res = pos.ModifyStopLossPrice(newSlPrice);
                    if (res != null && res.IsSuccessful)
                    {
                        Print($"[ORB] Trailing Stop updated on #{pos.Id} {pos.TradeType} | SL moved to {newSlPrice:F5} (current price {currentPrice:F5}, trail {trailDistancePips:F1} pips)");
                    }
                }
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            BeginInvokeOnMainThread(() =>
            {
                try
                {
                    var position = args.Position;
                    if (position == null || position.Label != BotId || position.SymbolName != SymbolName) return;

                    double exitPrice = position.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                    if (args.Reason == PositionCloseReason.TakeProfit && position.TakeProfit.HasValue)
                        exitPrice = position.TakeProfit.Value;
                    else if (args.Reason == PositionCloseReason.StopLoss && position.StopLoss.HasValue)
                        exitPrice = position.StopLoss.Value;
                    double? bookedExit = FinalClosingPrice(position);
                    if (bookedExit.HasValue) exitPrice = bookedExit.Value;

                    bool hasBotReason = _closeReasons.TryGetValue(position.Id, out string botReason);
                    _closeReasons.Remove(position.Id);
                    double initialSl = _initialSlPips.TryGetValue(position.Id, out double sl) ? sl : 0.0;
                    _initialSlPips.Remove(position.Id);
                    _breakevenApplied.Remove(position.Id);

                    string reason;
                    if (args.Reason == PositionCloseReason.StopLoss)
                        reason = $"Stop loss ({initialSl:F1} pips)";
                    else if (args.Reason == PositionCloseReason.TakeProfit)
                    {
                        double tpDisplay = (position.TakeProfit.HasValue && position.EntryPrice > 0)
                            ? Math.Abs(position.TakeProfit.Value - position.EntryPrice) / Symbol.PipSize
                            : (TakeProfitPips > 0 ? TakeProfitPips : 0.0);
                        reason = $"Take profit ({tpDisplay:F1} pips)";
                    }
                    else if (args.Reason == PositionCloseReason.Closed)
                        reason = hasBotReason ? botReason : "Closed outside the bot";
                    else
                        reason = args.Reason.ToString();

                    if (args.Reason == PositionCloseReason.StopLoss) _blockedAfterStop = true;

                    Print($"[ORB] Closed #{position.Id} {position.TradeType} {position.Comment}: {reason}, " +
                          $"net {position.NetProfit:F2}");
                    ReportPositionClosed(position, position.NetProfit, reason, exitPrice, position.Pips, initialSl);
                }
                catch (Exception ex)
                {
                    if (ShowLogs) Print($"[ORB] OnPositionClosed error: {ex.Message}");
                }
            });
        }

        /// <summary>The price the position's close actually traded at, or null when History has not
        /// booked the deal yet.</summary>
        private double? FinalClosingPrice(Position position)
        {
            try
            {
                var cutoff = Server.Time.AddSeconds(-60);
                double tolerance = Symbol.VolumeInUnitsMin / 2.0;
                var deal = History
                    .Where(h => h.PositionId == position.Id
                                && h.ClosingTime >= cutoff
                                && Math.Abs(h.VolumeInUnits - position.VolumeInUnits) <= tolerance)
                    .OrderByDescending(h => h.ClosingTime)
                    .FirstOrDefault();
                if (deal != null && deal.ClosingPrice > 0) return deal.ClosingPrice;
            }
            catch { }
            return null;
        }

        #endregion

        #region Portfolio reporting

        private string ReportUrl() => !string.IsNullOrWhiteSpace(AiReportUrl)
            ? AiReportUrl.Trim()
            : ApiUrl.Replace("/trade", "/portfolio/report");

        private void SendAccountSync()
        {
            if (RunningMode != RunningMode.RealTime) return;
            try
            {
                var report = new
                {
                    bot_id = BotId,
                    action = "sync",
                    symbol = SymbolName,
                    account_number = Account.Number.ToString(CultureInfo.InvariantCulture),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = AccountLabel,
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };
                string json = JsonSerializer.Serialize(report);
                string url = ReportUrl();
                Task.Run(async () => await PostReportAsync(url, json));
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORB] Account sync failed: {ex.Message}");
            }
        }

        private void ReportPositionOpen(Position position, double slPips, double? tpPips, string reason)
        {
            if (RunningMode != RunningMode.RealTime) return;
            try
            {
                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = BotId,
                    action = "open",
                    symbol = position.SymbolName,
                    side = position.TradeType.ToString(),
                    volume = position.VolumeInUnits / Symbol.LotSize,
                    entry_price = position.EntryPrice,
                    sl_price = position.StopLoss,
                    tp_price = position.TakeProfit,
                    sl_pips = Math.Round(slPips, 1),
                    tp_pips = tpPips.HasValue ? Math.Round(tpPips.Value, 1) : (TakeProfitPips > 0 ? TakeProfitPips : 0.0),
                    reason = reason,
                    entry_indicators = JsonSerializer.Serialize(new
                    {
                        or_high = _orHigh > double.MinValue ? Math.Round(_orHigh, Symbol.Digits) : 0.0,
                        or_low = _orLow < double.MaxValue ? Math.Round(_orLow, Symbol.Digits) : 0.0,
                        or_width_pips = Math.Round((_orHigh - _orLow) / Symbol.PipSize, 1),
                        prev_or_high = PrevOrReady ? Math.Round(_prevOrHigh, Symbol.Digits) : (double?)null,
                        prev_or_low = PrevOrReady ? Math.Round(_prevOrLow, Symbol.Digits) : (double?)null,
                        prev_or_date = PrevOrReady ? _prevOrDate.ToString("yyyy-MM-dd") : null,
                        prev_or_filter = PrevDayOrFilterEnabled,
                        prev_or_stop = PrevDayOrSlEnabled,
                        spread = Math.Round((Symbol.Ask - Symbol.Bid) / Symbol.PipSize, 1)
                    }),
                    entry_time = position.EntryTime.ToUniversalTime().ToString("o"),
                    account_number = Account.Number.ToString(CultureInfo.InvariantCulture),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = AccountLabel,
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                string json = JsonSerializer.Serialize(report);
                string url = ReportUrl();
                Task.Run(async () =>
                {
                    string error = await PostReportAsync(url, json);
                    if (error != null) Print($"[ORB] Failed to report open #{position.Id}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORB] ReportPositionOpen error: {ex.Message}");
            }
        }

        private void ReportPositionClosed(Position position, double pnl, string reason, double exitPrice,
                                          double pips, double initialSlPips)
        {
            if (RunningMode != RunningMode.RealTime) return;
            try
            {
                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = BotId,
                    action = "close",
                    symbol = position.SymbolName,
                    side = position.TradeType.ToString(),
                    volume = position.VolumeInUnits / Symbol.LotSize,
                    entry_price = position.EntryPrice,
                    exit_price = exitPrice,
                    sl_price = position.StopLoss,
                    tp_price = position.TakeProfit,
                    sl_pips = Math.Round(initialSlPips, 1),
                    pnl = pnl,
                    pips = Math.Round(pips, 1),
                    reason = string.IsNullOrWhiteSpace(reason) ? "Closed" : reason,
                    entry_time = position.EntryTime.ToUniversalTime().ToString("o"),
                    exit_time = DateTime.UtcNow.ToString("o"),
                    account_number = Account.Number.ToString(CultureInfo.InvariantCulture),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = AccountLabel,
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                string json = JsonSerializer.Serialize(report);
                string url = ReportUrl();
                Task.Run(async () =>
                {
                    string error = await PostReportAsync(url, json);
                    if (error != null)
                        Print($"[ORB] Failed to report close #{position.Id} PnL {pnl:F2}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORB] ReportPositionClosed error: {ex.Message}");
            }
        }

        private void ReportPartialClose(Position position, double closedLots, double remainingLots, double realizedPnl, string reason)
        {
            if (RunningMode != RunningMode.RealTime || position == null) return;
            try
            {
                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = BotId,
                    action = "partial_close",
                    symbol = position.SymbolName,
                    side = position.TradeType.ToString(),
                    closed_volume = Math.Round(closedLots, 2),
                    remaining_volume = Math.Round(remainingLots, 2),
                    realized_pnl = Math.Round(realizedPnl, 2),
                    reason = string.IsNullOrWhiteSpace(reason) ? "Partial close at Breakeven" : reason,
                    account_number = Account.Number.ToString(CultureInfo.InvariantCulture),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = AccountLabel,
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                string json = JsonSerializer.Serialize(report);
                string url = ReportUrl();
                Task.Run(async () =>
                {
                    string error = await PostReportAsync(url, json);
                    if (error != null)
                        Print($"[ORB] Failed to report partial close #{position.Id}: {error}");
                    else if (ShowLogs)
                        Print($"[ORB] Reported partial close #{position.Id} {closedLots:F2} lots, pnl {realizedPnl:F2}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORB] ReportPartialClose error: {ex.Message}");
            }
        }

        // The server ignores a replayed open/close (matched on ctrader_id), so retrying cannot book
        // a trade twice.
        private static readonly int[] ReportRetryDelaysSeconds = { 2, 4, 8, 16 };

        /// <summary>Posts a report, retrying on failure. Returns null once delivered, else the last error.</summary>
        private async Task<string> PostReportAsync(string url, string json)
        {
            string lastError = null;
            for (int attempt = 0; attempt <= ReportRetryDelaysSeconds.Length; attempt++)
            {
                if (attempt > 0)
                    await Task.Delay(TimeSpan.FromSeconds(ReportRetryDelaysSeconds[attempt - 1]));
                try
                {
                    using (var content = new StringContent(json, Encoding.UTF8, "application/json"))
                    using (var response = await _httpClient.PostAsync(url, content))
                    {
                        if (response.IsSuccessStatusCode) return null;
                        lastError = $"HTTP {(int)response.StatusCode}";
                    }
                }
                catch (Exception ex)
                {
                    lastError = ex.InnerException != null ? $"{ex.Message} ({ex.InnerException.Message})" : ex.Message;
                }
            }
            return $"{lastError} (after {ReportRetryDelaysSeconds.Length + 1} attempts)";
        }

        #endregion
    }
}
