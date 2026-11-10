// ORB-RE2 — opening range breakout with a TICK break and a daily trend filter.
//
// Ported from the Pine variant "ORB VN30F v2 - tick break + lọc trend" (shorttitle ORB-RE2) and its
// Python reference (dnse-kash: src/dnse_kash/bots/orb_re2_bot.py), keeping its rule semantics rather
// than a lookalike. cBot/OrbBot.cs is the older, closed-bar sibling; docs/orb-re2.md is the write-up.
//
// What RE2 changes against OrbBot:
//
//   OR window     5 minutes from the session open by default (09:00-09:05 local), not 15.
//   Entry         A TICK break, not a closed bar: the first tick at or beyond OR_high + buffer
//                 (LONG) / OR_low - buffer (SHORT) is filled at market. This is what the study in
//                 docs/orb-re2.md calls the alpha: entering on the closed M5 bar instead kept only
//                 ~56% of the net and dropped PF from 1.32 to 1.15.
//   Trend filter  A DAILY moving average decides which side may trade at all (price vs MA, or fast
//                 MA vs slow MA), with a neutral buffer. The MA is read off the last CLOSED daily
//                 bar and held for the whole session — the analogue of Pine's
//                 request.security(..., f_ma(len)[1], lookahead_on), so there is no look-ahead.
//   Stop          sl_dist = SlAtrDailyMultiplier x ATR14(D-1), default 0.965 — Pine's number, which
//                 on the reference instrument is a wide ~30-point stop. Take profit is TpRMultiple
//                 x sl_dist (default 2R). Both are broker-side brackets, so they fill on the tick.
//   Session       09:05-14:15 entry window, 14:25 flatten (all local; 02:00-07:25 UTC with the
//   (Vietnam)     defaults below, because Vietnam trades UTC+7 and has no DST).
//   Dropped       The ATR-breakout / anti-chase filters, StopMode/FixedSlPips, MinSlPips,
//                 TakeProfitPips, breakeven, trailing and partial closes. RE2 uses the daily ATR
//                 for the STOP, never for entry, and its exit is the bracket or the session end.
//
// Prerequisites and failure modes that matter:
//
//   ATR14(D-1)    Needs at least AtrPeriod + 1 closed daily bars. Without it sl_dist is undefined,
//                 so the entry is REFUSED (logged once per session), exactly as the Pine script
//                 leaves qty/entry undefined when slDist is na.
//   Chart         The OR window is wall clock, not a candle body. Run this bot on an M1 chart: on a
//                 timeframe wider than OpeningRangeMinutes one bar covers the whole window and the
//                 range swallows post-window movement.
//   cancel_all    RE2 places market orders only, so there is nothing to cancel at the flatten; the
//                 session end just closes what is open.
//
// One instance per symbol. The bot is pure rules: no AI gate, no news filter, no Telegram.

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
    /// <summary>Daylight-saving calendar for the session clock. Vietnam has none, so the default is
    /// None; the enum exists so the same bot can be pointed at a London or New York open.</summary>
    public enum OrbRe2DstRule
    {
        None = 0,
        US = 1,
        Europe = 2
    }

    public enum OrbRe2Direction
    {
        Both = 0,
        LongOnly = 1,
        ShortOnly = 2
    }

    /// <summary>How the daily trend filter decides which side may trade.</summary>
    public enum OrbRe2TrendFilterMode
    {
        Off = 0,
        PriceVsMa = 1,
        MaCross = 2
    }

    public enum OrbRe2MaType
    {
        Ema = 0,
        Sma = 1,
        Wma = 2
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class OrbRe2Bot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "ORBRE2")]
        public string BotId { get; set; }

        [Parameter("Account Identifier", Group = "General", DefaultValue = "demo")]
        public string AccountLabel { get; set; }

        [Parameter("Show Detailed Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        [Parameter("Enable Trading", Group = "General", DefaultValue = true)]
        public bool EnableTrading { get; set; }

        // ---- Session -------------------------------------------------------------------------
        // "Winter UTC" is the UTC hour that matches the local open while DST is off, exactly as in
        // OrbBot. The default 02:00 UTC is 09:00 in Vietnam, where the reference session opens.
        [Parameter("Session Open Hour (Winter UTC)", Group = "Session", DefaultValue = 2, MinValue = 0, MaxValue = 23)]
        public int SessionOpenHourWinterUtc { get; set; }

        [Parameter("Session Open Minute", Group = "Session", DefaultValue = 0, MinValue = 0, MaxValue = 59)]
        public int SessionOpenMinute { get; set; }

        [Parameter("DST Rule", Group = "Session", DefaultValue = OrbRe2DstRule.None)]
        public OrbRe2DstRule SessionDstRule { get; set; }

        [Parameter("Opening Range (minutes)", Group = "Session", DefaultValue = 5, MinValue = 1, MaxValue = 240)]
        public int OpeningRangeMinutes { get; set; }

        // 14:15 local: after this the break is no longer an opening range break, it is a chase.
        [Parameter("Entry Cutoff Hour (Winter UTC)", Group = "Session", DefaultValue = 7, MinValue = 0, MaxValue = 23)]
        public int EntryCutoffHourWinterUtc { get; set; }

        [Parameter("Entry Cutoff Minute", Group = "Session", DefaultValue = 15, MinValue = 0, MaxValue = 59)]
        public int EntryCutoffMinute { get; set; }

        // 14:25 local: closing before the 14:30-14:45 ATC auction. Anything held into the auction has
        // a -6 point 5th percentile and a -72 point worst case (docs/orb-re2.md section 8.3).
        [Parameter("EOD Flatten Hour (Winter UTC)", Group = "Session", DefaultValue = 7, MinValue = 0, MaxValue = 23)]
        public int EodFlattenHourWinterUtc { get; set; }

        [Parameter("EOD Flatten Minute", Group = "Session", DefaultValue = 25, MinValue = 0, MaxValue = 59)]
        public int EodFlattenMinute { get; set; }

        [Parameter("Trade Direction", Group = "Session", DefaultValue = OrbRe2Direction.Both)]
        public OrbRe2Direction Direction { get; set; }

        [Parameter("Max Trades per Session", Group = "Session", DefaultValue = 1, MinValue = 1, MaxValue = 10)]
        public int MaxTradesPerSession { get; set; }

        [Parameter("Allow Re-entry After Stop", Group = "Session", DefaultValue = false)]
        public bool AllowReentryAfterSl { get; set; }

        // ---- Opening range -------------------------------------------------------------------
        // RE2 has no minimum width in the reference; the switch is kept so a session whose range says
        // nothing about the day can be skipped instead of traded on noise. 0 = off.
        [Parameter("Min OR Width (pips, 0=off)", Group = "Opening Range", DefaultValue = 0.0, MinValue = 0.0)]
        public double MinOrWidthPips { get; set; }

        // The tick buffer: the break must clear the boundary by this much, so a quote that merely
        // touches it does not fill. The reference's tick_buffer; 0 fills on the touch itself.
        [Parameter("Tick Break Buffer (pips)", Group = "Opening Range", DefaultValue = 0.0, MinValue = 0.0)]
        public double TickBufferPips { get; set; }

        // ---- Daily trend filter --------------------------------------------------------------
        [Parameter("Trend Filter Mode", Group = "Trend Filter", DefaultValue = OrbRe2TrendFilterMode.PriceVsMa)]
        public OrbRe2TrendFilterMode TrendFilterMode { get; set; }

        [Parameter("MA Type", Group = "Trend Filter", DefaultValue = OrbRe2MaType.Ema)]
        public OrbRe2MaType MaType { get; set; }

        // The reference's measured configuration A: EMA10 of daily closes, price vs MA.
        [Parameter("MA Length (fast)", Group = "Trend Filter", DefaultValue = 10, MinValue = 1, MaxValue = 400)]
        public int MaLengthFast { get; set; }

        // Only read in MaCross mode.
        [Parameter("MA Length (slow)", Group = "Trend Filter", DefaultValue = 30, MinValue = 1, MaxValue = 400)]
        public int MaLengthSlow { get; set; }

        // Neutral zone: price/MA closer than this to the average allows neither side.
        [Parameter("Trend Buffer (pips)", Group = "Trend Filter", DefaultValue = 0.0, MinValue = 0.0)]
        public double TrendBufferPips { get; set; }

        // Closed daily bars used to seed and converge the MA. The seed is the oldest bar of this
        // window, the way ta.ema seeds on the first length values of whatever history it has.
        [Parameter("MA Lookback Bars", Group = "Trend Filter", DefaultValue = 750, MinValue = 20, MaxValue = 5000)]
        public int MaLookbackBars { get; set; }

        // ---- Risk ----------------------------------------------------------------------------
        // Pine: slDist = 0.965 * ta.atr(14) on the DAILY timeframe, read from the previous session.
        [Parameter("SL = x * ATR14(D-1)", Group = "Risk", DefaultValue = 0.965, MinValue = 0.05, MaxValue = 10.0, Step = 0.005)]
        public double SlAtrDailyMultiplier { get; set; }

        [Parameter("Daily ATR Period", Group = "Risk", DefaultValue = 14, MinValue = 1, MaxValue = 100)]
        public int DailyAtrPeriod { get; set; }

        [Parameter("Take Profit (R multiple)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 20.0, Step = 0.1)]
        public double TpRMultiple { get; set; }

        // False = the Pine default: Fixed Lots (or the broker minimum when that is 0).
        [Parameter("Risk Sizing Enabled", Group = "Risk", DefaultValue = false)]
        public bool RiskSizingEnabled { get; set; }

        // 0 = one broker minimum lot, the closest cTrader has to Pine's single contract.
        [Parameter("Fixed Lots (0=broker min)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0)]
        public double FixedLots { get; set; }

        [Parameter("Risk per Trade (% Equity)", Group = "Risk", DefaultValue = 0.2, MinValue = 0.01, MaxValue = 10.0, Step = 0.01)]
        public double RiskPerTradePercent { get; set; }

        [Parameter("Max Lots per Trade", Group = "Risk", DefaultValue = 5.0, MinValue = 0.01)]
        public double MaxAllowedLots { get; set; }

        // A minimum lot that would breach this at the chosen stop refuses the entry instead of
        // sizing up (same guardrail as OrbBot / FlowRsiBot / TurtleBot).
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

        private const string LongTag = "ORBRE2-L";
        private const string ShortTag = "ORBRE2-S";
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
        private bool _orCoarseChartWarned;

        // ---- Daily references (D-1) ----------------------------------------------------------
        private Bars _dailyBars;
        private double _atrPips = double.NaN;          // ATR14 of closed daily bars, in pips
        private double _maFast = double.NaN;
        private double _maSlow = double.NaN;
        private DateTime _dailyRefDate = DateTime.MinValue;   // the session these were read for
        private bool _atrShortLogged;                  // "not enough ATR" logged once per session
        private readonly HashSet<string> _trendBlockLogged = new HashSet<string>();

        // ---- Execution -----------------------------------------------------------------------
        private readonly Dictionary<int, double> _initialSlPips = new Dictionary<int, double>();
        private readonly Dictionary<int, string> _closeReasons = new Dictionary<int, string>();

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
            Print($"[ORBRE2] Starting '{BotId}' on {SymbolName} ({TimeFrame}) | OR {OpeningRangeMinutes}m from " +
                  $"{SessionOpenHourWinterUtc:00}:{SessionOpenMinute:00} UTC (winter) | entry until " +
                  $"{EntryCutoffHourWinterUtc:00}:{EntryCutoffMinute:00} | flatten " +
                  $"{EodFlattenHourWinterUtc:00}:{EodFlattenMinute:00} | dir {Direction} | " +
                  $"trend {TrendFilterMode} {MaType}{MaLengthFast}" +
                  (TrendFilterMode == OrbRe2TrendFilterMode.MaCross ? $"/{MaLengthSlow}" : "") +
                  $" | SL {SlAtrDailyMultiplier:F3}xATR{DailyAtrPeriod}(D-1) | TP {TpRMultiple:F1}R | " +
                  (RiskSizingEnabled ? $"risk {RiskPerTradePercent:F2}%/trade" : $"fixed {FixedLots:F2} lots (0=min)"));

            Positions.Closed += OnPositionClosed;
            _dailyBars = MarketData.GetBars(TimeFrame.Daily);
            SendAccountSync();
            RollOverSessionIfNeeded(Server.Time);

            // A restart after the flatten hour must not leave yesterday's position open until the
            // next bar. Nothing else runs on start: the tick break needs a live quote, not a restart.
            FlattenIfPastEod();
        }

        /// <summary>Closes anything still open when the bot comes up past the session's flatten
        /// hour.</summary>
        private void FlattenIfPastEod()
        {
            if (_sessionDate == DateTime.MinValue || _eodFlattened) return;
            if (Server.Time < EodFlattenUtc(Server.Time)) return;
            _eodFlattened = true;
            FlattenAll("End of session");
        }

        protected override void OnTick()
        {
            // RE2 enters on the tick, so this is where the signal lives. The end-of-day flatten rides
            // along, because it must not wait for another bar to close.
            _lastTickAt = Server.Time;
            Evaluate();
        }

        protected override void OnBarClosed()
        {
            var closed = Bars.Last(1);
            _lastTickAt = Server.Time;
            RollOverSessionIfNeeded(closed.OpenTime);
            CollectOrBar(closed);

            // A bar that opens at or after the window end also closes the window, which is what arms
            // the session when no tick happened to arrive exactly on the boundary.
            if (!_orFinalized && closed.OpenTime >= OrWindowEndUtc(SessionOpenUtc(closed.OpenTime)))
                FinalizeOr();
        }

        protected override void OnStop()
        {
            _isStopped = true;
            Positions.Closed -= OnPositionClosed;
            Print($"[ORBRE2] Stopped on {SymbolName}.");
            _httpClient?.Dispose();
        }

        #endregion

        #region Session clock

        /// <summary>True while the US DST calendar is in force (2nd Sunday of March 07:00 UTC to
        /// 1st Sunday of November 06:00 UTC) — the same boundaries the other session bots use.</summary>
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
            if (SessionDstRule == OrbRe2DstRule.None || winterHour == 0) return winterHour;
            bool isDst = SessionDstRule == OrbRe2DstRule.US ? IsUsDst(utc) : IsEuropeDst(utc);
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

        private DateTime EntryCutoffUtc(DateTime anyTimeInSession)
        {
            int hour = AdjustedHour(anyTimeInSession, EntryCutoffHourWinterUtc);
            return anyTimeInSession.Date.AddHours(hour).AddMinutes(EntryCutoffMinute);
        }

        private DateTime EodFlattenUtc(DateTime anyTimeInSession)
        {
            int hour = AdjustedHour(anyTimeInSession, EodFlattenHourWinterUtc);
            return anyTimeInSession.Date.AddHours(hour).AddMinutes(EodFlattenMinute);
        }

        /// <summary>A tick at most MaxQuoteAgeSeconds old, so Symbol.Bid/Ask are the market's
        /// current prices and not the last quote before a weekend or a holiday.</summary>
        private bool HasFreshQuote(DateTime now) => (now - _lastTickAt).TotalSeconds <= MaxQuoteAgeSeconds;

        /// <summary>Resets the session state. The daily references (ATR, MAs) are recomputed lazily
        /// on first use, because they only depend on CLOSED daily bars.</summary>
        private void RollOverSessionIfNeeded(DateTime barOpenUtc)
        {
            var date = barOpenUtc.Date;
            if (_sessionDate == date) return;

            _sessionDate = date;
            _orHigh = double.MinValue;
            _orLow = double.MaxValue;
            _orHadWindow = false;
            _orFinalized = false;
            _orValid = false;
            _orBarCount = 0;
            _orCoarseChartWarned = false;
            _tradesFired = 0;
            _blockedAfterStop = false;
            _eodFlattened = false;
            _dailyRefDate = DateTime.MinValue;
            _atrShortLogged = false;
            _trendBlockLogged.Clear();

            if (ShowLogs) Print($"[ORBRE2] New session {date:yyyy-MM-dd} | " + DailyRefDescription());
        }

        private string DailyRefDescription()
        {
            var dBars = _dailyBars ?? MarketData.GetBars(TimeFrame.Daily);
            int closed = dBars != null ? dBars.Count - 1 : 0;
            return $"D-1 refs from {closed} closed daily bars (need {DailyAtrPeriod + 1} for ATR14)";
        }

        #endregion

        #region Opening range

        /// <summary>Extends today's range with a closed bar inside the window.</summary>
        private void CollectOrBar(Bar bar)
        {
            if (_orFinalized) return;

            DateTime open = SessionOpenUtc(bar.OpenTime);
            DateTime end = OrWindowEndUtc(open);

            if (bar.OpenTime < open) return;          // the tail of the previous session, not this one
            if (bar.OpenTime >= end) return;          // the window already closed; the tick path finalizes

            _orHadWindow = true;
            _orBarCount++;
            if (bar.High > _orHigh) _orHigh = bar.High;
            if (bar.Low < _orLow) _orLow = bar.Low;
        }

        /// <summary>Judges the window. Called from the tick path as soon as the clock passes the end
        /// of the window — every bar the window owns has closed by then, so a tick that arrives on the
        /// 09:05:00 boundary already sees the finished range instead of waiting for the next bar.</summary>
        private void FinalizeOr()
        {
            if (_orFinalized) return;
            _orFinalized = true;

            // A cold start or a watchdog restart after the window closed would otherwise lose the
            // session: the incremental collection only sees bars that close while the bot runs.
            if (!_orHadWindow) BackfillOrFromHistory();

            if (!_orHadWindow || _orHigh == double.MinValue || _orLow == double.MaxValue)
            {
                _orValid = false;
                Print($"[ORBRE2] Opening range {_sessionDate:yyyy-MM-dd} missing; session skipped");
                return;
            }

            double widthPips = (_orHigh - _orLow) / Symbol.PipSize;
            _orValid = widthPips >= MinOrWidthPips;

            if (_orValid)
            {
                Print($"[ORBRE2] Opening range {_sessionDate:yyyy-MM-dd} [{_orLow:F5} - {_orHigh:F5}] " +
                      $"= {widthPips:F1} pips over {_orBarCount} bars | " + TrendDescription());
                if (_orBarCount == 1 && OpeningRangeMinutes > 1 && !_orCoarseChartWarned)
                {
                    _orCoarseChartWarned = true;
                    Print($"[ORBRE2] One bar covered the whole {OpeningRangeMinutes}m window — the chart " +
                          $"timeframe is wider than the opening range. Run M1 for a faithful range.");
                }
            }
            else
            {
                Print($"[ORBRE2] Opening range {_sessionDate:yyyy-MM-dd} only {widthPips:F1} pips " +
                      $"(< {MinOrWidthPips:F1}); session skipped");
            }
        }

        /// <summary>Rebuilds the window from Bars history, for a bot that started after it closed.</summary>
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

        #region Daily references: ATR14(D-1) and the trend MA

        /// <summary>The closes of CLOSED daily bars, oldest first, capped at MaLookbackBars. The last
        /// element is D-1: index Count-1 is the bar the current session is still building.</summary>
        private List<double> ClosedDailyCloses(int maxBars)
        {
            var values = new List<double>();
            var dBars = _dailyBars ?? MarketData.GetBars(TimeFrame.Daily);
            if (dBars == null) return values;
            for (int i = dBars.Count - 2; i >= 0 && values.Count < maxBars; i--)
                values.Add(dBars.ClosePrices[i]);
            values.Reverse();                            // oldest -> newest, newest = D-1
            return values;
        }

        /// <summary>ATR14 of the closed daily bars ending at D-1, in pips. Wilder's RMA seeded with
        /// the simple average of the first period true ranges — Pine's ta.atr. 0 when there is not
        /// enough history, which is the reference's "not enough ATR14" refusal.</summary>
        private double ComputeDailyAtrPips()
        {
            var dBars = _dailyBars ?? MarketData.GetBars(TimeFrame.Daily);
            if (dBars == null || dBars.Count < DailyAtrPeriod + 2) return 0.0;

            var trs = new List<double>();
            int want = Math.Min(dBars.Count - 1, DailyAtrPeriod + 200);
            for (int i = dBars.Count - 2; i >= 1 && trs.Count < want; i--)
            {
                double h = dBars.HighPrices[i];
                double l = dBars.LowPrices[i];
                double pc = dBars.ClosePrices[i - 1];
                trs.Add(Math.Max(h - l, Math.Max(Math.Abs(h - pc), Math.Abs(l - pc))));
            }
            trs.Reverse();                               // oldest -> newest, newest = D-1
            if (trs.Count < DailyAtrPeriod) return 0.0;

            double atr = 0.0;
            for (int i = 0; i < DailyAtrPeriod; i++) atr += trs[i];
            atr /= DailyAtrPeriod;
            for (int i = DailyAtrPeriod; i < trs.Count; i++)
                atr = ((DailyAtrPeriod - 1) * atr + trs[i]) / DailyAtrPeriod;
            return atr / Symbol.PipSize;
        }

        /// <summary>EMA seeded with SMA(length) then alpha = 2/(length+1) — Pine's ta.ema, not a
        /// recursive EMA from the first bar of history.</summary>
        private static double EmaOf(List<double> values, int length)
        {
            if (length <= 0 || values.Count < length) return double.NaN;
            double seed = 0.0;
            for (int i = 0; i < length; i++) seed += values[i];
            double ema = seed / length;
            double alpha = 2.0 / (length + 1.0);
            for (int i = length; i < values.Count; i++) ema = alpha * values[i] + (1.0 - alpha) * ema;
            return ema;
        }

        private static double SmaOf(List<double> values, int length)
        {
            if (length <= 0 || values.Count < length) return double.NaN;
            double sum = 0.0;
            for (int i = values.Count - length; i < values.Count; i++) sum += values[i];
            return sum / length;
        }

        private static double WmaOf(List<double> values, int length)
        {
            if (length <= 0 || values.Count < length) return double.NaN;
            double weighted = 0.0;
            double weightSum = 0.0;
            int start = values.Count - length;
            for (int i = 0; i < length; i++)
            {
                double w = i + 1.0;                      // the newest close carries the heaviest weight
                weighted += values[start + i] * w;
                weightSum += w;
            }
            return weighted / weightSum;
        }

        private double MovingAverage(List<double> closes, int length)
        {
            switch (MaType)
            {
                case OrbRe2MaType.Sma: return SmaOf(closes, length);
                case OrbRe2MaType.Wma: return WmaOf(closes, length);
                default: return EmaOf(closes, length);
            }
        }

        /// <summary>Reads the D-1 references once per session. The daily bars that are still forming
        /// are never used, so nothing here can look ahead.</summary>
        private void EnsureDailyReferences()
        {
            if (_dailyRefDate == _sessionDate) return;
            _dailyRefDate = _sessionDate;

            var closes = ClosedDailyCloses(MaLookbackBars);
            _atrPips = ComputeDailyAtrPips();
            _maFast = MovingAverage(closes, MaLengthFast);
            _maSlow = MovingAverage(closes, MaLengthSlow);

            if (ShowLogs)
                Print($"[ORBRE2] D-1 refs | ATR{DailyAtrPeriod}=" +
                      $"{(_atrPips > 0 ? (_atrPips * Symbol.PipSize).ToString("F5") : "n/a")} " +
                      $"({(_atrPips > 0 ? _atrPips.ToString("F1") + " pips" : "not enough daily bars")}) | " +
                      $"MA fast {_maFast:F5} slow {_maSlow:F5} from {closes.Count} closed daily bars");
        }

        private string TrendDescription()
        {
            if (TrendFilterMode == OrbRe2TrendFilterMode.Off) return "trend filter off";
            EnsureDailyReferences();
            string fast = double.IsNaN(_maFast) ? "n/a" : _maFast.ToString("F5");
            string slow = double.IsNaN(_maSlow) ? "n/a" : _maSlow.ToString("F5");
            string mode = TrendFilterMode == OrbRe2TrendFilterMode.MaCross
                ? $"{MaType}{MaLengthFast}/{MaLengthSlow} [{fast}/{slow}]"
                : $"price vs {MaType}{MaLengthFast} [{fast}]";
            return $"trend {mode} buf {TrendBufferPips:F1} pips";
        }

        /// <summary>
        /// The daily gate. LONG needs price above (MA + buffer) or the fast MA above the slow one;
        /// SHORT the mirror image; a missing MA allows the side (the reference only refuses an
        /// entry when the ATR is missing, not when the MA is).
        /// </summary>
        private bool TrendAllows(TradeType side, double price)
        {
            if (TrendFilterMode == OrbRe2TrendFilterMode.Off) return true;
            EnsureDailyReferences();

            double buffer = TrendBufferPips * Symbol.PipSize;
            bool isLong = side == TradeType.Buy;

            if (TrendFilterMode == OrbRe2TrendFilterMode.PriceVsMa)
            {
                if (double.IsNaN(_maFast)) return true;
                return isLong ? price > _maFast + buffer : price < _maFast - buffer;
            }

            if (double.IsNaN(_maFast) || double.IsNaN(_maSlow)) return true;
            return isLong ? _maFast > _maSlow + buffer : _maFast < _maSlow - buffer;
        }

        /// <summary>Logs the refusal once per session per side — the reference's "chặn L" / "chặn S"
        /// labels, which are worth seeing exactly once and not on every tick.</summary>
        private void LogTrendBlock(TradeType side, double price)
        {
            if (!ShowLogs) return;
            string key = side == TradeType.Buy ? "L" : "S";
            if (!_trendBlockLogged.Add(key)) return;
            Print($"[ORBRE2] Trend filter blocked {(side == TradeType.Buy ? "LONG" : "SHORT")} | price {price:F5} " +
                  $"| {TrendDescription()}");
        }

        #endregion

        #region Entries

        private void Evaluate()
        {
            if (_isStopped) return;
            try
            {
                var now = Server.Time;
                if (!HasFreshQuote(now)) return;

                if (_sessionDate != DateTime.MinValue && !_eodFlattened && now >= EodFlattenUtc(now))
                {
                    _eodFlattened = true;
                    FlattenAll("End of session");
                    return;
                }

                EvaluateEntry(now);
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORBRE2] Evaluate error: {ex.Message}");
            }
        }

        /// <summary>The tick break. Everything here is decided on live quotes: the range is fixed by
        /// the closed window bars, the trigger is the current bid/ask.</summary>
        private void EvaluateEntry(DateTime now)
        {
            if (!EnableTrading) return;
            if (_sessionDate == DateTime.MinValue) return;

            DateTime sessionOpen = SessionOpenUtc(now);
            if (now < OrWindowEndUtc(sessionOpen)) return;      // the window is still forming
            if (!_orFinalized) FinalizeOr();
            if (!_orValid || _eodFlattened) return;
            if (now >= EntryCutoffUtc(now)) return;             // 14:15: no new chases
            if (_tradesFired >= MaxTradesPerSession) return;
            if (_blockedAfterStop && !AllowReentryAfterSl) return;
            if (Positions.FindAll(BotId, SymbolName).Length > 0) return;

            EnsureDailyReferences();
            if (!(_atrPips > 0))
            {
                if (!_atrShortLogged)
                {
                    _atrShortLogged = true;
                    Print($"[ORBRE2] BREAK blocked — not enough daily bars for ATR{DailyAtrPeriod}(D-1) " +
                          $"(need {DailyAtrPeriod + 1} closed days); no entry, as the reference refuses when slDist is na");
                }
                return;
            }

            double slPips = SlAtrDailyMultiplier * _atrPips;
            if (!(slPips > 0)) return;

            double buffer = TickBufferPips * Symbol.PipSize;
            double ask = Symbol.Ask;
            double bid = Symbol.Bid;
            bool longTrigger = ask >= _orHigh + buffer;
            bool shortTrigger = bid <= _orLow - buffer;
            bool allowLong = Direction != OrbRe2Direction.ShortOnly;
            bool allowShort = Direction != OrbRe2Direction.LongOnly;

            if (!((longTrigger && allowLong) || (shortTrigger && allowShort))) return;

            // A narrow range with a wide buffer could satisfy both sides on one quote; the long side
            // wins, exactly as OrbBot resolves it.
            TradeType side;
            double price;
            if (longTrigger && allowLong) { side = TradeType.Buy; price = ask; }
            else { side = TradeType.Sell; price = bid; }

            if (!TrendAllows(side, price))
            {
                LogTrendBlock(side, price);
                return;
            }

            double spreadPips = (ask - bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                if (ShowLogs) Print($"[ORBRE2] Spread {spreadPips:F1} pips > {MaxSpreadPips:F1}; signal skipped");
                return;
            }

            TryOpen(side, price, slPips);
        }

        private void TryOpen(TradeType side, double price, double slPips)
        {
            double volume = VolumeForRisk(slPips, out double dollarRisk, out string refusal);
            if (volume <= 0)
            {
                Print($"[ORBRE2] {side} refused: {refusal}");
                return;
            }

            double tpPips = slPips * TpRMultiple;
            string comment = side == TradeType.Buy
                ? $"{LongTag} {_sessionDate:yyyy-MM-dd}"
                : $"{ShortTag} {_sessionDate:yyyy-MM-dd}";

            var result = ExecuteMarketOrder(side, SymbolName, volume, BotId, slPips, tpPips, comment);
            if (result != null && result.IsSuccessful && result.Position != null)
            {
                _tradesFired++;
                _initialSlPips[result.Position.Id] = slPips;
                Print($"[ORBRE2] Opened #{result.Position.Id} {side} {volume / Symbol.LotSize:F2} lots " +
                      $"{SymbolName} @ {result.Position.EntryPrice} | OR [{_orLow:F5} - {_orHigh:F5}] " +
                      $"| ATR{DailyAtrPeriod}(D-1) {_atrPips:F1} pips | tick {bufferDescription()} " +
                      $"| SL {slPips:F1} pips ({SlAtrDailyMultiplier:F3}xATR) | TP {tpPips:F1} pips ({TpRMultiple:F1}R) " +
                      $"| risk ${dollarRisk:F2} | {TrendDescription()}");

                ReportPositionOpen(result.Position, slPips, tpPips,
                    $"ORB-RE2 tick break {SlAtrDailyMultiplier:F3}xATR{DailyAtrPeriod}(D-1), TP {TpRMultiple:F1}R" +
                    (TrendFilterMode == OrbRe2TrendFilterMode.Off ? "" : $", trend {TrendFilterMode}"));
            }
            else
            {
                Print($"[ORBRE2] {side} order failed: {(result != null ? result.Error.ToString() : "no result")}");
            }
        }

        private string bufferDescription() => $"{TickBufferPips:F1} pips";

        /// <summary>
        /// The reference sizes by a fixed contract count and only optionally by a risk budget
        /// (qty = risk / (sl_dist * point_value)). In cTrader the budget is a share of equity and the
        /// point value is Symbol.PipValue, so RiskSizingEnabled reproduces that branch; otherwise the
        /// size is FixedLots, or the broker minimum when that is 0 (Pine's single contract).
        /// </summary>
        private double VolumeForRisk(double slPips, out double dollarRisk, out string refusal)
        {
            refusal = null;
            dollarRisk = 0.0;

            double perUnitRisk = slPips * Symbol.PipValue;      // PipValue is per 1 unit of volume
            if (perUnitRisk <= 0)
            {
                refusal = "pip value is not positive";
                return 0.0;
            }

            double minVolume = Symbol.VolumeInUnitsMin;
            double maxVolume = Symbol.NormalizeVolumeInUnits(MaxAllowedLots * Symbol.LotSize, RoundingMode.Down);
            double units;

            if (!RiskSizingEnabled)
            {
                units = FixedLots > 0
                    ? Symbol.NormalizeVolumeInUnits(FixedLots * Symbol.LotSize, RoundingMode.Down)
                    : minVolume;
                if (units < minVolume) units = minVolume;
                if (units > maxVolume) units = maxVolume;
            }
            else
            {
                double riskAmount = Account.Balance * (RiskPerTradePercent / 100.0);
                units = Symbol.NormalizeVolumeInUnits(riskAmount / perUnitRisk, RoundingMode.Down);
                units = Math.Min(units, maxVolume);
                if (units < minVolume)
                {
                    double minLotRisk = minVolume * perUnitRisk;
                    if (minLotRisk > MaxDollarRiskPerTrade)
                    {
                        refusal = $"minimum lot ({minVolume / Symbol.LotSize:F2}) at a {slPips:F1}-pip " +
                                  $"stop risks ${minLotRisk:F2} > Max Dollar Risk ${MaxDollarRiskPerTrade:F2}";
                        return 0.0;
                    }
                    units = minVolume;
                }
            }

            dollarRisk = units * perUnitRisk;
            return units;
        }

        #endregion

        #region Exits

        private void FlattenAll(string reason)
        {
            foreach (var position in Positions.FindAll(BotId, SymbolName))
            {
                var result = CloseWithReason(position, reason);
                if (result == null || !result.IsSuccessful)
                    Print($"[ORBRE2] Could not flatten #{position.Id}: " +
                          $"{(result != null ? result.Error.ToString() : "no result")}");
            }
        }

        private TradeResult CloseWithReason(Position position, string reason)
        {
            _closeReasons[position.Id] = reason;
            return ClosePosition(position);
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

                    string reason;
                    if (args.Reason == PositionCloseReason.StopLoss)
                        reason = $"Stop loss ({initialSl:F1} pips, {SlAtrDailyMultiplier:F3}xATR)";
                    else if (args.Reason == PositionCloseReason.TakeProfit)
                        reason = $"Take profit ({TpRMultiple:F1}R)";
                    else if (args.Reason == PositionCloseReason.Closed)
                        reason = hasBotReason ? botReason : "Closed outside the bot";
                    else
                        reason = args.Reason.ToString();

                    if (args.Reason == PositionCloseReason.StopLoss) _blockedAfterStop = true;

                    Print($"[ORBRE2] Closed #{position.Id} {position.TradeType} {position.Comment}: {reason}, " +
                          $"net {position.NetProfit:F2}");
                    ReportPositionClosed(position, position.NetProfit, reason, exitPrice, position.Pips, initialSl);
                }
                catch (Exception ex)
                {
                    if (ShowLogs) Print($"[ORBRE2] OnPositionClosed error: {ex.Message}");
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
                if (ShowLogs) Print($"[ORBRE2] Account sync failed: {ex.Message}");
            }
        }

        private void ReportPositionOpen(Position position, double slPips, double tpPips, string reason)
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
                    tp_pips = Math.Round(tpPips, 1),
                    reason = reason,
                    entry_indicators = JsonSerializer.Serialize(new
                    {
                        or_high = _orHigh > double.MinValue ? Math.Round(_orHigh, Symbol.Digits) : 0.0,
                        or_low = _orLow < double.MaxValue ? Math.Round(_orLow, Symbol.Digits) : 0.0,
                        or_width_pips = Math.Round((_orHigh - _orLow) / Symbol.PipSize, 1),
                        atr_daily_pips = _atrPips > 0 ? Math.Round(_atrPips, 1) : 0.0,
                        sl_atr_multiple = SlAtrDailyMultiplier,
                        tp_r_multiple = TpRMultiple,
                        tick_buffer_pips = TickBufferPips,
                        trend_filter = TrendFilterMode.ToString(),
                        ma_type = MaType.ToString(),
                        ma_fast = double.IsNaN(_maFast) ? (double?)null : Math.Round(_maFast, Symbol.Digits),
                        ma_slow = double.IsNaN(_maSlow) ? (double?)null : Math.Round(_maSlow, Symbol.Digits),
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
                    if (error != null) Print($"[ORBRE2] Failed to report open #{position.Id}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORBRE2] ReportPositionOpen error: {ex.Message}");
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
                        Print($"[ORBRE2] Failed to report close #{position.Id} PnL {pnl:F2}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ORBRE2] ReportPositionClosed error: {ex.Message}");
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
