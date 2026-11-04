// ORB RE2 (tick break + daily trend filter) as a single-instrument cBot.
//
// Ported from the Python reference src/dnse_kash/bots/orb_re2_bot.py (Pine strategy
// "ORB VN30F v2 - tick break + lọc trend"), keeping its rule semantics:
//
//   OR window      A TIME window [SessionOpen, SessionOpen + OpeningRangeMinutes) built from
//                  CLOSED bars only.
//   Entry          A TICK stop: the moment price reaches OR_high + TickBuffer (long) or
//                  OR_low - TickBuffer (short) the order fires. There is deliberately NO
//                  closed-bar entry path in this bot (the Python RE2 disables it too).
//   Trend filter   Daily MA computed on CLOSED daily bars only (no look-ahead):
//                    PriceVsMa  price must be above/below MA_fast ± TrendBufferPips
//                    MaCross    MA_fast must be above/below MA_slow ± TrendBufferPips
//   Whipsaw guard  If the OPPOSITE boundary was already broken within WhipsawWindowMinutes
//                  (0 = whole session), the breakout in this direction is refused. This is
//                  what stops the bot buying the high and selling the low of a chop day.
//   ATR band       Daily ATR outside [MinAtrPips, MaxAtrPips] -> no entry that day.
//   Anti-ext       |price - MA_fast| > MaxMaDistanceAtrMult × DailyATR -> no entry (the move
//                  is already stretched and mean-reversion risk dominates).
//   Risk           SL = DailyAtrMultiplier × DailyATR (floored by MinSlPips),
//                  TP = TakeProfitRMultiple × SL, one signal per session (MaxTradesPerSession),
//                  flatten at EOD.
//
// Differences from the Python RE2, driven by the target market (FX / index CFDs instead of
// VN30F futures): pip-based levels, FixedLots or %-risk position sizing with a hard
// MaxDollarRiskPerTrade guardrail, and the optional breakeven / trailing / partial-close
// layers inherited from OrbBot.cs (all OFF by default so the default behaviour is RE2).
//
// Differences from OrbBot.cs: the entry is a tick stop instead of a closed-bar confirm, and
// the three gates above (daily trend, whipsaw, ATR band / anti-extension) do not exist there.
//
// Run one instance per symbol on an M15 (or finer) chart: the OR window is wall-clock minutes.

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
    /// <summary>Daylight-saving calendar that shifts the session clock.</summary>
    public enum Re2DstRule
    {
        None = 0,
        US = 1,
        Europe = 2
    }

    public enum Re2Direction
    {
        Both = 0,
        LongOnly = 1,
        ShortOnly = 2
    }

    /// <summary>Trend filter modes, named as in the Python reference (TREND_*).</summary>
    public enum Re2TrendMode
    {
        Off = 0,
        PriceVsMa = 1,
        MaCross = 2
    }

    public enum Re2MaType
    {
        Ema = 0,
        Sma = 1,
        Wma = 2
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class OrbRE2Bot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "ORB-RE2")]
        public string BotId { get; set; }

        [Parameter("Account Identifier", Group = "General", DefaultValue = "demo")]
        public string AccountLabel { get; set; }

        [Parameter("Show Detailed Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        [Parameter("Enable Trading", Group = "General", DefaultValue = true)]
        public bool EnableTrading { get; set; }

        // Positions opened by another bot id (for example a predecessor that was renamed) are
        // managed here as well: entries, breakeven, trailing, the EOD flatten and the close
        // report all treat them as ours. Comma separated, exact labels.
        [Parameter("Adopted Bot IDs (comma separated)", Group = "General", DefaultValue = "")]
        public string AdoptedBotIds { get; set; }

        // ---- Session -------------------------------------------------------------------------
        // "Winter UTC" means the UTC hour matching the local open while DST is off; the DstRule
        // pulls it back an hour while DST is on, so the local open stays put.
        [Parameter("Session Open Hour (Winter UTC)", Group = "Session", DefaultValue = 13, MinValue = 0, MaxValue = 23)]
        public int SessionOpenHourWinterUtc { get; set; }

        [Parameter("Session Open Minute", Group = "Session", DefaultValue = 0, MinValue = 0, MaxValue = 59)]
        public int SessionOpenMinute { get; set; }

        [Parameter("DST Rule", Group = "Session", DefaultValue = Re2DstRule.US)]
        public Re2DstRule SessionDstRule { get; set; }

        [Parameter("Opening Range (minutes)", Group = "Session", DefaultValue = 15, MinValue = 1, MaxValue = 240)]
        public int OpeningRangeMinutes { get; set; }

        [Parameter("EOD Flatten Hour (Winter UTC)", Group = "Session", DefaultValue = 21, MinValue = 0, MaxValue = 23)]
        public int EodFlattenHourWinterUtc { get; set; }

        [Parameter("EOD Flatten Minute", Group = "Session", DefaultValue = 0, MinValue = 0, MaxValue = 59)]
        public int EodFlattenMinute { get; set; }

        [Parameter("Trade Direction", Group = "Session", DefaultValue = Re2Direction.Both)]
        public Re2Direction Direction { get; set; }

        [Parameter("Max Trades per Session", Group = "Session", DefaultValue = 1, MinValue = 1, MaxValue = 5)]
        public int MaxTradesPerSession { get; set; }

        [Parameter("Allow Re-entry After Stop", Group = "Session", DefaultValue = false)]
        public bool AllowReentryAfterSl { get; set; }

        // Maximum minutes after the session open to accept a breakout (0 = any time before flatten).
        [Parameter("Max Entry Window (minutes, 0=off)", Group = "Session", DefaultValue = 180, MinValue = 0, MaxValue = 1440)]
        public int MaxEntryWindowMinutes { get; set; } = 180;

        // ---- Opening range -------------------------------------------------------------------
        [Parameter("Min OR Width (pips)", Group = "Opening Range", DefaultValue = 20.0, MinValue = 0.0)]
        public double MinOrWidthPips { get; set; }

        // The tick stop sits this far outside the boundary so a single stray print on the line
        // does not fire the order.
        [Parameter("Tick Break Buffer (pips)", Group = "Opening Range", DefaultValue = 0.5, MinValue = 0.0)]
        public double TickBufferPips { get; set; }

        // ---- Daily trend filter (RE2) --------------------------------------------------------
        [Parameter("Trend Filter", Group = "Trend Filter", DefaultValue = Re2TrendMode.PriceVsMa)]
        public Re2TrendMode TrendFilterMode { get; set; }

        [Parameter("MA Type", Group = "Trend Filter", DefaultValue = Re2MaType.Ema)]
        public Re2MaType MaType { get; set; }

        [Parameter("MA Fast Length (daily)", Group = "Trend Filter", DefaultValue = 10, MinValue = 1, MaxValue = 400)]
        public int MaFastLength { get; set; }

        [Parameter("MA Slow Length (daily)", Group = "Trend Filter", DefaultValue = 20, MinValue = 1, MaxValue = 400)]
        public int MaSlowLength { get; set; }

        // Neutral zone around the MA: price has to clear it to count as a direction.
        [Parameter("Trend Buffer (pips)", Group = "Trend Filter", DefaultValue = 0.0, MinValue = 0.0)]
        public double TrendBufferPips { get; set; }

        // 0 = off. When on, an entry is refused once price is already this many daily ATRs
        // away from the fast MA (the move is stretched; pullback risk dominates).
        [Parameter("Max Price-MA Distance (x ATR, 0=off)", Group = "Trend Filter", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.05)]
        public double MaxMaDistanceAtrMult { get; set; }

        // ---- Daily ATR band (RE2) ------------------------------------------------------------
        [Parameter("Daily ATR Period", Group = "ATR Band", DefaultValue = 14, MinValue = 2, MaxValue = 100)]
        public int AtrPeriod { get; set; }

        [Parameter("Min Daily ATR (pips, 0=off)", Group = "ATR Band", DefaultValue = 0.0, MinValue = 0.0)]
        public double MinAtrPips { get; set; }

        [Parameter("Max Daily ATR (pips, 0=off)", Group = "ATR Band", DefaultValue = 0.0, MinValue = 0.0)]
        public double MaxAtrPips { get; set; }

        // ---- Whipsaw guard (RE2) -------------------------------------------------------------
        [Parameter("Whipsaw Guard", Group = "Whipsaw", DefaultValue = true)]
        public bool WhipsawGuardEnabled { get; set; }

        // 0 = remember the whole session. Otherwise only a reverse break inside the last N
        // minutes blocks the entry (a chop burst, not an early-session fade).
        [Parameter("Whipsaw Window (minutes, 0=session)", Group = "Whipsaw", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 1440.0)]
        public double WhipsawWindowMinutes { get; set; }

        // ---- Risk ----------------------------------------------------------------------------
        // SL = DailyAtrMultiplier × DailyATR(D-1). Re-tune per instrument: 0.5 is a sensible
        // FX start, the Python bot's 0.965 was tuned for VN30F index points.
        [Parameter("SL Daily ATR Multiplier", Group = "Risk", DefaultValue = 0.5, MinValue = 0.05, MaxValue = 5.0, Step = 0.05)]
        public double DailyAtrMultiplier { get; set; }

        // Floor for every stop, so a quiet day cannot produce a stop a few ticks wide.
        [Parameter("Min Stop (pips)", Group = "Risk", DefaultValue = 10.0, MinValue = 0.0)]
        public double MinSlPips { get; set; }

        [Parameter("Take Profit (R multiple, 0=off)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.0, MaxValue = 20.0, Step = 0.1)]
        public double TakeProfitRMultiple { get; set; }

        [Parameter("Take Profit (pips, 0=use R)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0)]
        public double TakeProfitPips { get; set; }

        [Parameter("Default Stop (pips, ATR fallback)", Group = "Risk", DefaultValue = 50.0, MinValue = 1.0)]
        public double DefaultSlPips { get; set; }

        [Parameter("Breakeven Trigger (R multiple, 0=off)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
        public double BreakevenTriggerR { get; set; }

        [Parameter("Breakeven Offset (pips)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.0)]
        public double BreakevenOffsetPips { get; set; }

        [Parameter("Partial Close at BE (0-1)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 0.9, Step = 0.1)]
        public double PartialCloseRatio { get; set; }

        [Parameter("Trail Trigger (R multiple, 0=off)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
        public double TrailTriggerR { get; set; }

        [Parameter("Trail Distance (R multiple)", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
        public double TrailDistanceR { get; set; }

        [Parameter("Risk per Trade (% Equity)", Group = "Risk", DefaultValue = 0.2, MinValue = 0.01, MaxValue = 10.0, Step = 0.01)]
        public double RiskPerTradePercent { get; set; }

        [Parameter("Fixed Lots (0=dynamic)", Group = "Risk", DefaultValue = 0.1, MinValue = 0.0, Step = 0.01)]
        public double FixedLots { get; set; }

        [Parameter("Max Lots per Trade", Group = "Risk", DefaultValue = 5.0, MinValue = 0.01)]
        public double MaxAllowedLots { get; set; }

        [Parameter("Max Dollar Risk per Trade ($)", Group = "Risk", DefaultValue = 100.0, MinValue = 1.0)]
        public double MaxDollarRiskPerTrade { get; set; }

        [Parameter("Max Spread (pips)", Group = "Risk", DefaultValue = 50.0, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        // ---- Reporting -----------------------------------------------------------------------
        [Parameter("Agent API URL", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/trade")]
        public string ApiUrl { get; set; }

        [Parameter("Portfolio Report Endpoint", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/portfolio/report")]
        public string AiReportUrl { get; set; }

        // A position that closes while the process is down (restart, watchdog heal, crash loop)
        // raises no PositionClosed event, so the server never learns about it and the dashboard
        // keeps showing the trade as open. On start we re-send a close report for every deal the
        // broker booked inside the window; the server dedupes on ctrader_id, so an already
        // reported deal is ignored rather than booked twice. 0 disables the sweep.
        [Parameter("Reconcile Closed Deals (hours, 0=off)", Group = "Reporting", DefaultValue = 24, MinValue = 0, MaxValue = 168)]
        public int ReconcileClosedHours { get; set; }

        #endregion

        #region State

        private const string LongTag = "RE2-L";
        private const string ShortTag = "RE2-S";
        private const double MaxQuoteAgeSeconds = 60;

        // ---- Session -------------------------------------------------------------------------
        private DateTime _sessionDate = DateTime.MinValue;
        private bool _eodFlattened;
        private int _tradesFired;
        private bool _blockedAfterStop;

        // ---- Today's opening range -----------------------------------------------------------
        private double _orHigh = double.MinValue;
        private double _orLow = double.MaxValue;
        private bool _orHadWindow;
        private bool _orFinalized;
        private bool _orValid;
        private int _orBarCount;

        // ---- Whipsaw guard -------------------------------------------------------------------
        private DateTime? _brokenHighAt;
        private DateTime? _brokenLowAt;
        private bool _whipsawLongLogged;
        private bool _whipsawShortLogged;
        private bool _trendBlockLongLogged;
        private bool _trendBlockShortLogged;
        private bool _farMaLogged;
        private bool _atrBandLogged;

        // ---- Daily context (closed daily bars only) ------------------------------------------
        private Bars _dailyBars;
        private readonly List<double> _dailyCloses = new List<double>();
        private double _dailyAtrPips;
        private double? _maFast;
        private double? _maSlow;
        private bool _dailyContextLoggedMissing;

        // ---- Trend state for the current tick ------------------------------------------------
        private bool _trendUp;
        private bool _trendDown;
        private string _trendNote = "off";

        // ---- Execution -----------------------------------------------------------------------
        private readonly HashSet<string> _adoptedLabels = new HashSet<string>();
        private readonly Dictionary<int, double> _initialSlPips = new Dictionary<int, double>();
        private readonly Dictionary<int, string> _closeReasons = new Dictionary<int, string>();
        private readonly HashSet<int> _breakevenApplied = new HashSet<int>();

        private DateTime _lastTickAt = DateTime.MinValue;
        private DateTime _lastTickTelemetryTime = DateTime.MinValue;
        private DateTime _lastBarHandled = DateTime.MinValue;
        private volatile bool _isStopped;

        // uvicorn closes an idle keep-alive connection after 5 s; retire pooled ones before it does.
        private readonly HttpClient _httpClient = new HttpClient(new SocketsHttpHandler { PooledConnectionIdleTimeout = TimeSpan.FromSeconds(4) })
        {
            Timeout = TimeSpan.FromSeconds(60)
        };

        #endregion

        #region Lifecycle

        protected override void OnStart()
        {
            Print($"[RE2] Starting '{BotId}' on {SymbolName} ({TimeFrame}) | OR {OpeningRangeMinutes}m from " +
                  $"{SessionOpenHourWinterUtc:00}:{SessionOpenMinute:00} UTC (winter), EOD flatten " +
                  $"{EodFlattenHourWinterUtc:00}:{EodFlattenMinute:00} UTC | dir {Direction} | " +
                  $"tick break {TickBufferPips:F1}p | trend {TrendFilterMode} " +
                  $"({MaType}{MaFastLength}/{MaFastLength}:{MaSlowLength}, buffer {TrendBufferPips:F1}p) | " +
                  $"whipsaw {(WhipsawGuardEnabled ? (WhipsawWindowMinutes > 0 ? $"{WhipsawWindowMinutes:F0}m" : "session") : "off")} | " +
                  $"ATR band [{MinAtrPips:F0}-{MaxAtrPips:F0}] | " +
                  $"SL {DailyAtrMultiplier:F2}xATR (min {MinSlPips:F0}p) | TP " +
                  $"{(TakeProfitRMultiple > 0 ? $"{TakeProfitRMultiple:F1}R" : (TakeProfitPips > 0 ? $"{TakeProfitPips:F0}p" : "none"))} | " +
                  (FixedLots > 0 ? $"lots fixed {FixedLots:F2}" : $"risk {RiskPerTradePercent:F2}%/trade"));

            foreach (var part in (AdoptedBotIds ?? string.Empty).Split(','))
            {
                string label = part.Trim();
                if (label.Length > 0 && label != BotId) _adoptedLabels.Add(label);
            }
            if (_adoptedLabels.Count > 0)
                Print($"[RE2] Adopting positions labelled {string.Join(", ", _adoptedLabels)} as '{BotId}'");

            Positions.Closed += OnPositionClosed;
            _dailyBars = MarketData.GetBars(TimeFrame.Daily);
            SendAccountSync();
            ReconcileClosedDealsFromHistory();
            Evaluate();
            SendLiveTickTelemetry(force: true);
        }

        protected override void OnTick()
        {
            _lastTickAt = Server.Time;
            // Roll the session on ticks as well: the tick entry needs today's clock even when no
            // bar has closed yet (bot started right at the open).
            RollOverSessionIfNeeded(Server.Time);
            TryEnterOnTick();
            Evaluate();
            SendLiveTickTelemetry();
        }

        protected override void OnBarClosed()
        {
            // Only the OR window is bar-driven; every entry decision is made on ticks.
            var closed = Bars.Last(1);
            _lastBarHandled = closed.OpenTime;
            _lastTickAt = Server.Time;
            RollOverSessionIfNeeded(closed.OpenTime);
            CollectOrBar(closed);
            SendLiveTickTelemetry(force: true);
        }

        protected override void OnStop()
        {
            _isStopped = true;
            Positions.Closed -= OnPositionClosed;
            Print($"[RE2] Stopped on {SymbolName}.");
            _httpClient?.Dispose();
        }

        #endregion

        #region Session clock

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

        private int AdjustedHour(DateTime utc, int winterHour)
        {
            if (SessionDstRule == Re2DstRule.None || winterHour == 0) return winterHour;
            bool isDst = SessionDstRule == Re2DstRule.US ? IsUsDst(utc) : IsEuropeDst(utc);
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

        private bool HasFreshQuote(DateTime now) => (now - _lastTickAt).TotalSeconds <= MaxQuoteAgeSeconds;

        #endregion

        #region Opening range

        private void RollOverSessionIfNeeded(DateTime utc)
        {
            var date = utc.Date;
            // The last bar of a day opens on the previous date (23:45 -> 00:00): a bar-driven
            // call must never roll the session backwards and wipe today's OR/whipsaw memory.
            if (_sessionDate != DateTime.MinValue && date < _sessionDate) return;
            if (_sessionDate == date) return;

            _sessionDate = date;
            _orHigh = double.MinValue;
            _orLow = double.MaxValue;
            _orHadWindow = false;
            _orFinalized = false;
            _orValid = false;
            _orBarCount = 0;
            _tradesFired = 0;
            _blockedAfterStop = false;
            _eodFlattened = false;
            _brokenHighAt = null;
            _brokenLowAt = null;
            _whipsawLongLogged = false;
            _whipsawShortLogged = false;
            _trendBlockLongLogged = false;
            _trendBlockShortLogged = false;
            _farMaLogged = false;
            _atrBandLogged = false;
            _dailyContextLoggedMissing = false;

            RefreshDailyContext();

            if (ShowLogs)
                Print($"[RE2] New session {date:yyyy-MM-dd} | {DailyContextDescription()}");
        }

        private string DailyContextDescription()
        {
            string trend = TrendFilterMode == Re2TrendMode.Off
                ? "trend off"
                : (_maFast.HasValue
                    ? $"MA{MaFastLength}={_maFast.Value:F5}" +
                      (MaSlowLength > 0 && _maSlow.HasValue ? $" MA{MaSlowLength}={_maSlow.Value:F5}" : "")
                    : "MA not ready");
            return $"dailyATR={( _dailyAtrPips > 0 ? _dailyAtrPips.ToString("F1") + "p" : "n/a")} | {trend}";
        }

        /// <summary>Extends today's range with a closed bar inside the window, then judges the
        /// width once the window has passed.</summary>
        private void CollectOrBar(Bar bar)
        {
            if (_orFinalized) return;
            // The final bar of the previous session closes at 00:00 and carries yesterday's date.
            // Letting it through would finalize TODAY's window before a single bar arrived.
            if (_sessionDate != DateTime.MinValue && bar.OpenTime.Date != _sessionDate) return;

            DateTime open = SessionOpenUtc(bar.OpenTime);
            DateTime end = OrWindowEndUtc(open);

            if (bar.OpenTime < open) return;
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

            // A restart after the window closed would otherwise lose the session.
            if (!_orHadWindow) BackfillOrFromHistory();

            if (!_orHadWindow || _orHigh == double.MinValue || _orLow == double.MaxValue)
            {
                _orValid = false;
                return;
            }

            double widthPips = (_orHigh - _orLow) / Symbol.PipSize;
            _orValid = widthPips >= MinOrWidthPips;

            if (_orValid)
                Print($"[RE2] Opening range {_sessionDate:yyyy-MM-dd} [{_orLow:F5} - {_orHigh:F5}] " +
                      $"= {widthPips:F1} pips over {_orBarCount} bars");
            else
                Print($"[RE2] Opening range {_sessionDate:yyyy-MM-dd} only {widthPips:F1} pips " +
                      $"(< {MinOrWidthPips:F1}); session skipped");
        }

        private void BackfillOrFromHistory()
        {
            DateTime open = SessionOpenUtc(_sessionDate);
            DateTime end = OrWindowEndUtc(open);

            for (int i = Bars.Count - 2; i >= 0; i--)
            {
                DateTime t = Bars.OpenTimes[i];
                if (t >= end) continue;
                if (t < open) break;
                _orHadWindow = true;
                _orBarCount++;
                if (Bars.HighPrices[i] > _orHigh) _orHigh = Bars.HighPrices[i];
                if (Bars.LowPrices[i] < _orLow) _orLow = Bars.LowPrices[i];
            }
        }

        #endregion

        #region Daily context (ATR + trend MA)

        /// <summary>
        /// Rebuilds the daily ATR and the trend MA from CLOSED daily bars only. Today's forming
        /// daily bar is never used, so the filter cannot look ahead (same contract as the Python
        /// bot's ``request.security(...)[1]``).
        /// </summary>
        private void RefreshDailyContext()
        {
            _dailyCloses.Clear();
            _maFast = null;
            _maSlow = null;
            _dailyAtrPips = 0.0;

            var daily = _dailyBars ?? MarketData.GetBars(TimeFrame.Daily);
            _dailyBars = daily;
            if (daily == null || daily.Count < 2) return;

            var indices = new List<int>();
            for (int i = daily.Count - 2; i >= 0; i--)      // skip the forming bar
            {
                DateTime t = daily.OpenTimes[i];
                if (_sessionDate != DateTime.MinValue && t.Date >= _sessionDate) continue;
                indices.Add(i);
            }
            indices.Reverse();
            if (indices.Count == 0) return;

            foreach (int i in indices) _dailyCloses.Add(daily.ClosePrices[i]);

            // Wilder-style simple average of the last `AtrPeriod` daily true ranges.
            int period = Math.Max(2, AtrPeriod);
            if (indices.Count >= period)
            {
                double sum = 0.0;
                int n = 0;
                for (int k = indices.Count - period; k < indices.Count; k++)
                {
                    int i = indices[k];
                    if (i <= 0) continue;
                    double h = daily.HighPrices[i];
                    double l = daily.LowPrices[i];
                    double pc = daily.ClosePrices[i - 1];
                    double tr = Math.Max(h - l, Math.Max(Math.Abs(h - pc), Math.Abs(l - pc)));
                    sum += tr;
                    n++;
                }
                if (n > 0 && Symbol.PipSize > 0) _dailyAtrPips = (sum / n) / Symbol.PipSize;
            }

            _maFast = MovingAverage(_dailyCloses, MaFastLength);
            _maSlow = MovingAverage(_dailyCloses, MaSlowLength);

            if (ShowLogs && !_dailyContextLoggedMissing && _dailyAtrPips <= 0)
            {
                _dailyContextLoggedMissing = true;
                Print($"[RE2] Daily context incomplete: {indices.Count} closed daily bars, " +
                      $"{period} needed for ATR{AtrPeriod}; entries will be refused until it fills");
            }
        }

        private double? MovingAverage(List<double> values, int length)
        {
            if (values == null || length < 1 || values.Count < length) return null;
            switch (MaType)
            {
                case Re2MaType.Sma:
                {
                    double sum = 0.0;
                    for (int i = values.Count - length; i < values.Count; i++) sum += values[i];
                    return sum / length;
                }
                case Re2MaType.Wma:
                {
                    double denom = length * (length + 1) / 2.0;
                    double total = 0.0;
                    int start = values.Count - length;
                    for (int i = 0; i < length; i++) total += (i + 1) * values[start + i];
                    return total / denom;
                }
                default:
                {
                    double alpha = 2.0 / (length + 1.0);
                    double ema = 0.0;
                    for (int i = 0; i < length; i++) ema += values[i];
                    ema /= length;                                   // Pine seeds the EMA with the SMA
                    for (int i = length; i < values.Count; i++)
                        ema = (values[i] - ema) * alpha + ema;
                    return ema;
                }
            }
        }

        /// <summary>Recomputes the trend gates for the current price, exactly like
        /// ``_re2_trend_state`` in the Python bot.</summary>
        private void RefreshTrend(double price)
        {
            if (TrendFilterMode == Re2TrendMode.Off)
            {
                _trendUp = true;
                _trendDown = true;
                _trendNote = "off";
                return;
            }

            double buffer = TrendBufferPips * Symbol.PipSize;

            if (TrendFilterMode == Re2TrendMode.PriceVsMa)
            {
                if (!_maFast.HasValue)
                {
                    _trendUp = false;
                    _trendDown = false;
                    _trendNote = $"MA{MaFastLength} not ready";
                    return;
                }
                _trendUp = price > _maFast.Value + buffer;
                _trendDown = price < _maFast.Value - buffer;
                _trendNote = $"price={price:F5} ma{MaFastLength}={_maFast.Value:F5}";
                return;
            }

            if (!_maFast.HasValue || !_maSlow.HasValue)
            {
                _trendUp = false;
                _trendDown = false;
                _trendNote = $"MA{MaFastLength}/{MaSlowLength} not ready";
                return;
            }
            _trendUp = _maFast.Value > _maSlow.Value + buffer;
            _trendDown = _maFast.Value < _maSlow.Value - buffer;
            _trendNote = $"fast={_maFast.Value:F5} slow={_maSlow.Value:F5}";
        }

        /// <summary>Refuses entries once price is stretched too far from the fast MA.</summary>
        private bool PriceTooFarFromMa(double price)
        {
            if (MaxMaDistanceAtrMult <= 0) return false;
            if (!_maFast.HasValue || _dailyAtrPips <= 0) return false;

            double atrPrice = _dailyAtrPips * Symbol.PipSize;
            if (atrPrice <= 0) return false;

            double distance = Math.Abs(price - _maFast.Value);
            double limit = MaxMaDistanceAtrMult * atrPrice;
            if (distance <= limit) return false;

            if (ShowLogs && !_farMaLogged)
            {
                _farMaLogged = true;
                Print($"[RE2] Entry blocked — price {price:F5} is {distance / Symbol.PipSize:F1} pips " +
                      $"from MA{MaFastLength} (> {MaxMaDistanceAtrMult:F2}xATR = {limit / Symbol.PipSize:F1} pips); pullback risk");
            }
            return true;
        }

        /// <summary>ATR band gate: a dead market or a blow-off day both get skipped.</summary>
        private bool DailyAtrOutOfBand()
        {
            if (MinAtrPips <= 0 && MaxAtrPips <= 0) return false;
            if (_dailyAtrPips <= 0) return false;

            if (MinAtrPips > 0 && _dailyAtrPips < MinAtrPips)
            {
                if (ShowLogs && !_atrBandLogged)
                {
                    _atrBandLogged = true;
                    Print($"[RE2] Entry blocked — daily ATR {_dailyAtrPips:F1} pips < min {MinAtrPips:F1}");
                }
                return true;
            }
            if (MaxAtrPips > 0 && _dailyAtrPips > MaxAtrPips)
            {
                if (ShowLogs && !_atrBandLogged)
                {
                    _atrBandLogged = true;
                    Print($"[RE2] Entry blocked — daily ATR {_dailyAtrPips:F1} pips > max {MaxAtrPips:F1}");
                }
                return true;
            }
            return false;
        }

        /// <summary>A reverse break ``at`` is still inside the whipsaw window.</summary>
        private bool RecentBreak(DateTime? at, DateTime now)
        {
            if (!at.HasValue) return false;
            if (WhipsawWindowMinutes <= 0) return true;
            return (now - at.Value).TotalSeconds <= WhipsawWindowMinutes * 60.0;
        }

        #endregion

        #region Tick entry (RE2 core)

        /// <summary>
        /// Stop-order semantics on ticks: the first print reaching OR ± TickBuffer fires the
        /// order, provided the daily trend, whipsaw and ATR gates agree.
        /// </summary>
        private void TryEnterOnTick()
        {
            if (!EnableTrading || _isStopped) return;
            if (!_orFinalized || !_orValid) return;
            if (_eodFlattened) return;
            if (_sessionDate == DateTime.MinValue) return;
            if (!HasFreshQuote(Server.Time)) return;

            var now = Server.Time;
            if (now >= EodFlattenUtc(now)) return;

            DateTime sessionOpen = SessionOpenUtc(now);
            if (now < OrWindowEndUtc(sessionOpen)) return;       // OR still forming
            if (MaxEntryWindowMinutes > 0 && (now - sessionOpen).TotalMinutes > MaxEntryWindowMinutes) return;
            if (_tradesFired >= MaxTradesPerSession) return;
            if (_blockedAfterStop && !AllowReentryAfterSl) return;
            if (MyPositions().Length > 0) return;

            double price = Symbol.Bid;                            // chart/bid basis, as the OR is built
            double longTrig = _orHigh + TickBufferPips * Symbol.PipSize;
            double shortTrig = _orLow - TickBufferPips * Symbol.PipSize;

            bool brokeLong = price >= longTrig;
            bool brokeShort = price <= shortTrig;
            if (!brokeLong && !brokeShort) return;

            // Record the break BEFORE any gate: the whipsaw guard has to know that the opposite
            // side was pierced even when that attempt was refused by the trend filter.
            bool whipsawLong = RecentBreak(_brokenLowAt, now);
            bool whipsawShort = RecentBreak(_brokenHighAt, now);
            if (brokeLong) _brokenHighAt = now;
            if (brokeShort) _brokenLowAt = now;

            RefreshTrend(price);

            if (_dailyAtrPips <= 0)
            {
                if (ShowLogs && !_dailyContextLoggedMissing)
                {
                    _dailyContextLoggedMissing = true;
                    Print($"[RE2] Entry refused — daily ATR{AtrPeriod} not available yet");
                }
                return;
            }
            if (PriceTooFarFromMa(price)) return;
            if (DailyAtrOutOfBand()) return;

            bool allowLong = Direction != Re2Direction.ShortOnly;
            bool allowShort = Direction != Re2Direction.LongOnly;
            bool blockLong = WhipsawGuardEnabled && whipsawLong;
            bool blockShort = WhipsawGuardEnabled && whipsawShort;

            if (brokeLong && allowLong && !blockLong)
            {
                if (_trendUp) { TryOpen(TradeType.Buy, longTrig); return; }
                if (!_trendBlockLongLogged)
                {
                    _trendBlockLongLogged = true;
                    Print($"[RE2] LONG blocked — price {price:F5} broke OR_high {_orHigh:F5} " +
                          $"but the trend filter refuses ({_trendNote})");
                }
            }

            if (brokeShort && allowShort && !blockShort)
            {
                if (_trendDown) { TryOpen(TradeType.Sell, shortTrig); return; }
                if (!_trendBlockShortLogged)
                {
                    _trendBlockShortLogged = true;
                    Print($"[RE2] SHORT blocked — price {price:F5} broke OR_low {_orLow:F5} " +
                          $"but the trend filter refuses ({_trendNote})");
                }
            }

            if (brokeLong && blockLong && !_whipsawLongLogged)
            {
                _whipsawLongLogged = true;
                Print($"[RE2] LONG blocked — whipsaw guard: OR_low was pierced at " +
                      $"{_brokenLowAt:HH:mm:ss} (price {price:F5} broke OR_high {_orHigh:F5})");
            }
            if (brokeShort && blockShort && !_whipsawShortLogged)
            {
                _whipsawShortLogged = true;
                Print($"[RE2] SHORT blocked — whipsaw guard: OR_high was pierced at " +
                      $"{_brokenHighAt:HH:mm:ss} (price {price:F5} broke OR_low {_orLow:F5})");
            }
        }

        #endregion

        #region Entries

        private void TryOpen(TradeType side, double triggerLevel)
        {
            double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                if (ShowLogs) Print($"[RE2] Spread {spreadPips:F1} pips > {MaxSpreadPips:F1}; signal skipped");
                return;
            }

            double entry = side == TradeType.Buy ? Symbol.Ask : Symbol.Bid;

            double rawSlPips = _dailyAtrPips > 0
                ? _dailyAtrPips * DailyAtrMultiplier
                : DefaultSlPips;
            double slPips = Math.Max(rawSlPips, MinSlPips);
            if (slPips <= 0)
            {
                Print($"[RE2] {side} refused: stop distance is not positive ({slPips:F1} pips)");
                return;
            }

            double volume = VolumeForRisk(slPips, out double dollarRisk, out string refusal);
            if (volume <= 0)
            {
                Print($"[RE2] {side} refused: {refusal}");
                return;
            }

            double? tpPips = TakeProfitRMultiple > 0
                ? slPips * TakeProfitRMultiple
                : (TakeProfitPips > 0 ? TakeProfitPips : (double?)null);
            string comment = side == TradeType.Buy
                ? $"{LongTag} {_sessionDate:yyyy-MM-dd}"
                : $"{ShortTag} {_sessionDate:yyyy-MM-dd}";

            var result = ExecuteMarketOrder(side, SymbolName, volume, BotId, slPips, tpPips, comment);
            if (result != null && result.IsSuccessful && result.Position != null)
            {
                _tradesFired++;
                _initialSlPips[result.Position.Id] = slPips;
                Print($"[RE2] Opened #{result.Position.Id} {side} {volume / Symbol.LotSize:F2} lots " +
                      $"{SymbolName} @ {result.Position.EntryPrice} | trigger {triggerLevel:F5} | " +
                      $"stop {slPips:F1} pips ({(rawSlPips < slPips ? $"clamped from {rawSlPips:F1}" : $"{DailyAtrMultiplier:F2}xATR")}) " +
                      (tpPips.HasValue ? $"| tp {tpPips.Value:F1} pips " : "") +
                      $"| risk ${dollarRisk:F2} | spread {spreadPips:F1}p | OR [{_orLow:F5} - {_orHigh:F5}] | trend {_trendNote}");

                ReportPositionOpen(result.Position, slPips, tpPips,
                    $"RE2 tick break of the opening range ({TrendFilterMode})");
            }
            else
            {
                Print($"[RE2] {side} order failed: {(result != null ? result.Error.ToString() : "no result")}");
            }
        }

        /// <summary>
        /// Volume risking RiskPerTradePercent of equity at `slPips`, floored at the broker's
        /// minimum and capped at MaxAllowedLots. Returns 0 (with a reason) when even the minimum
        /// lot would risk more than MaxDollarRiskPerTrade — refused rather than sized up.
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

            if (FixedLots > 0)
            {
                double fixedUnits = Symbol.NormalizeVolumeInUnits(FixedLots * Symbol.LotSize, RoundingMode.Down);
                if (fixedUnits < Symbol.VolumeInUnitsMin)
                {
                    refusal = $"fixed lots ({FixedLots:F2}) is below broker minimum ({Symbol.VolumeInUnitsMin / Symbol.LotSize:F2})";
                    return 0.0;
                }
                if (fixedUnits > Symbol.VolumeInUnitsMax)
                    fixedUnits = Symbol.NormalizeVolumeInUnits(Symbol.VolumeInUnitsMax, RoundingMode.Down);

                dollarRisk = fixedUnits * perUnitRisk;
                return fixedUnits;
            }

            double units = Symbol.NormalizeVolumeInUnits(riskAmount / perUnitRisk, RoundingMode.Down);
            units = Math.Min(units, Symbol.NormalizeVolumeInUnits(MaxAllowedLots * Symbol.LotSize, RoundingMode.Down));

            if (units < Symbol.VolumeInUnitsMin)
            {
                double minLotRisk = Symbol.VolumeInUnitsMin * perUnitRisk;
                if (MaxDollarRiskPerTrade > 0 && minLotRisk > MaxDollarRiskPerTrade)
                {
                    refusal = $"minimum lot ({Symbol.VolumeInUnitsMin / Symbol.LotSize:F2}) at a {slPips:F1}-pip " +
                              $"stop risks ${minLotRisk:F2} > Max Dollar Risk ${MaxDollarRiskPerTrade:F2}";
                    return 0.0;
                }
                units = Symbol.VolumeInUnitsMin;
            }

            dollarRisk = units * perUnitRisk;
            if (MaxDollarRiskPerTrade > 0 && dollarRisk > MaxDollarRiskPerTrade * 1.05)
            {
                refusal = $"order dollar risk ${dollarRisk:F2} exceeds MaxDollarRisk ${MaxDollarRiskPerTrade:F2}";
                return 0.0;
            }
            return units;
        }

        #endregion

        #region Exits

        /// <summary>Posisitions this instance owns: its own label plus every adopted label.</summary>
        private Position[] MyPositions()
        {
            var mine = Positions.FindAll(BotId, SymbolName).ToList();
            foreach (var label in _adoptedLabels)
            {
                foreach (var p in Positions.FindAll(label, SymbolName))
                    if (!mine.Any(x => x.Id == p.Id)) mine.Add(p);
            }
            return mine.ToArray();
        }

        /// <summary>True when a position label belongs to this instance (own or adopted).</summary>
        private bool ManagesLabel(string label) =>
            label == BotId || (!string.IsNullOrEmpty(label) && _adoptedLabels.Contains(label));

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
                if (ShowLogs) Print($"[RE2] Evaluate error: {ex.Message}");
            }
        }

        private void FlattenAll(string reason)
        {
            foreach (var position in MyPositions())
            {
                var result = CloseWithReason(position, reason);
                if (result == null || !result.IsSuccessful)
                    Print($"[RE2] Could not flatten #{position.Id}: " +
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

            foreach (var pos in MyPositions())
            {
                if (_breakevenApplied.Contains(pos.Id)) continue;
                if (!_initialSlPips.TryGetValue(pos.Id, out double initialSlPips) || initialSlPips <= 0)
                {
                    if (pos.StopLoss.HasValue)
                    {
                        initialSlPips = Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / Symbol.PipSize;
                        _initialSlPips[pos.Id] = initialSlPips;
                    }
                    else continue;
                }

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
                    Print($"[RE2] Breakeven applied on #{pos.Id} {pos.TradeType} | SL moved to {beSlPrice:F5} " +
                          $"(+{BreakevenOffsetPips:F1} pips past entry)");
                    SendLiveTickTelemetry(force: true);

                    if (PartialCloseRatio > 0 && PartialCloseRatio < 1.0)
                    {
                        if (pos.VolumeInUnits <= Symbol.VolumeInUnitsMin)
                        {
                            if (ShowLogs)
                                Print($"[RE2] Partial close skipped on #{pos.Id}: volume is at the broker minimum");
                        }
                        else
                        {
                            double volumeToClose = Symbol.NormalizeVolumeInUnits(pos.VolumeInUnits * PartialCloseRatio, RoundingMode.Down);
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

                                    Print($"[RE2] Partial Close on #{pos.Id}: closed {volumeToClose / Symbol.LotSize:F2} lots " +
                                          $"({(PartialCloseRatio * 100):F0}%), remaining {remainingVolume / Symbol.LotSize:F2} lots");
                                    ReportPartialClose(pos, volumeToClose / Symbol.LotSize, remainingVolume / Symbol.LotSize,
                                        realizedPnl, "Partial close at Breakeven");
                                }
                                else
                                {
                                    Print($"[RE2] Partial Close failed on #{pos.Id}: {partialRes?.Error}");
                                }
                            }
                        }
                    }
                }
            }
        }

        private void ManageTrailingStop()
        {
            if (TrailTriggerR <= 0 || TrailDistanceR <= 0) return;

            foreach (var pos in MyPositions())
            {
                if (!_initialSlPips.TryGetValue(pos.Id, out double initialSlPips) || initialSlPips <= 0)
                {
                    if (pos.StopLoss.HasValue)
                    {
                        initialSlPips = Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / Symbol.PipSize;
                        _initialSlPips[pos.Id] = initialSlPips;
                    }
                    else continue;
                }

                double profitPips = pos.Pips;
                double triggerPips = initialSlPips * TrailTriggerR;
                if (profitPips < triggerPips) continue;

                double trailDistancePips = initialSlPips * TrailDistanceR;
                double currentPrice = pos.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                double minStopDistance = MinStopDistancePrice(currentPrice);
                double newSlPrice = pos.TradeType == TradeType.Buy
                    ? currentPrice - Math.Max(trailDistancePips * Symbol.PipSize, minStopDistance)
                    : currentPrice + Math.Max(trailDistancePips * Symbol.PipSize, minStopDistance);
                newSlPrice = Math.Round(newSlPrice, Symbol.Digits);

                bool shouldMove = pos.TradeType == TradeType.Buy
                    ? (pos.StopLoss == null || newSlPrice > pos.StopLoss.Value + Symbol.PipSize)
                    : (pos.StopLoss == null || newSlPrice < pos.StopLoss.Value - Symbol.PipSize);

                if (!shouldMove) continue;

                var res = pos.ModifyStopLossPrice(newSlPrice);
                if (res == null || !res.IsSuccessful)
                {
                    double retryPrice = Math.Round(
                        pos.TradeType == TradeType.Buy
                            ? newSlPrice - Symbol.TickSize
                            : newSlPrice + Symbol.TickSize,
                        Symbol.Digits);
                    res = pos.ModifyStopLossPrice(retryPrice);
                    if (res != null && res.IsSuccessful) newSlPrice = retryPrice;
                }

                if (res != null && res.IsSuccessful)
                {
                    Print($"[RE2] Trailing Stop updated on #{pos.Id} {pos.TradeType} | SL moved to {newSlPrice:F5} " +
                          $"(price {currentPrice:F5}, trail {trailDistancePips:F1} pips)");
                    SendLiveTickTelemetry(force: true);
                }
                else
                {
                    Print($"[RE2] Trailing Stop rejected on #{pos.Id}: {res?.Error}");
                }
            }
        }

        /// <summary>Broker's minimum stop distance as a price, in the symbol's own units.</summary>
        private double MinStopDistancePrice(double referencePrice)
        {
            double raw = Symbol.MinStopLossDistance;
            if (raw <= 0) return Symbol.TickSize;
            double price = Symbol.MinDistanceType == SymbolMinDistanceType.Percentage
                ? referencePrice * raw / 100.0
                : raw * Symbol.PipSize;
            return Math.Max(Symbol.TickSize, price);
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            BeginInvokeOnMainThread(() =>
            {
                try
                {
                    var position = args.Position;
                    if (position == null || position.SymbolName != SymbolName) return;
                    if (!ManagesLabel(position.Label)) return;

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
                            : 0.0;
                        reason = $"Take profit ({tpDisplay:F1} pips)";
                    }
                    else if (args.Reason == PositionCloseReason.Closed)
                        reason = hasBotReason ? botReason : "Closed outside the bot";
                    else
                        reason = args.Reason.ToString();

                    if (args.Reason == PositionCloseReason.StopLoss) _blockedAfterStop = true;

                    Print($"[RE2] Closed #{position.Id} {position.TradeType} {position.Comment}: {reason}, " +
                          $"net {position.NetProfit:F2}");
                    ReportPositionClosed(position, position.NetProfit, reason, exitPrice, position.Pips, initialSl);
                    SendLiveTickTelemetry(force: true);
                }
                catch (Exception ex)
                {
                    if (ShowLogs) Print($"[RE2] OnPositionClosed error: {ex.Message}");
                }
            });
        }

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

        /// <summary>
        /// Re-sends a close report for deals the broker booked while this cBot was not running.
        /// The server matches on `ctrader_id` and only touches rows still `open`, so replaying a
        /// deal it already knows is a no-op — the sweep is safe to run on every start.
        /// </summary>
        private void ReconcileClosedDealsFromHistory()
        {
            if (ReconcileClosedHours <= 0) return;
            if (RunningMode != RunningMode.RealTime) return;

            try
            {
                var openIds = new HashSet<int>();
                foreach (var p in (Positions.FindAll(BotId, SymbolName))) openIds.Add(p.Id);

                var since = Server.Time.ToUniversalTime().AddHours(-ReconcileClosedHours);
                var seen = new HashSet<int>();
                int sent = 0;

                foreach (var deal in History)
                {
                    if (!string.Equals(deal.SymbolName, SymbolName, StringComparison.OrdinalIgnoreCase)) continue;
                    if (deal.ClosingTime.ToUniversalTime() < since) continue;
                    if (!ManagesLabel(deal.Label)) continue;
                    if (openIds.Contains(deal.PositionId)) continue;   // partial close: position still alive
                    if (!seen.Add(deal.PositionId)) continue;          // one report per position
                    SendCloseReportForDeal(deal);
                    sent++;
                }

                if (sent > 0)
                    Print("[RE2] Reconcile|sent={0}|window_h={1}", sent, ReconcileClosedHours);
            }
            catch (Exception ex)
            {
                Print("[RE2] Reconcile|error|{0}", OneLine(ex.Message));
            }
        }

        private void SendCloseReportForDeal(HistoricalTrade deal)
        {
            try
            {
                string owner = string.IsNullOrEmpty(deal.Label) ? BotId : deal.Label;
                double pips = (deal.ClosingPrice - deal.EntryPrice) / Symbol.PipSize;
                if (deal.TradeType == TradeType.Sell) pips = -pips;

                var report = new
                {
                    ctrader_id = deal.PositionId,
                    bot_id = owner,
                    action = "close",
                    symbol = deal.SymbolName,
                    side = deal.TradeType.ToString(),
                    volume = deal.VolumeInUnits / Symbol.LotSize,
                    entry_price = deal.EntryPrice,
                    exit_price = deal.ClosingPrice,
                    sl_price = (double?)null,
                    tp_price = (double?)null,
                    sl_pips = 0.0,
                    pnl = Math.Round(deal.NetProfit, 2),
                    pips = Math.Round(pips, 1),
                    reason = "Reconciled from broker history (bot was down at close)",
                    entry_time = deal.EntryTime.ToUniversalTime().ToString("o"),
                    exit_time = deal.ClosingTime.ToUniversalTime().ToString("o"),
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
                        Print("[RE2] Reconcile|failed|pos={0}|{1}", deal.PositionId, error);
                });
            }
            catch (Exception ex)
            {
                Print("[RE2] Reconcile|payload_error|pos={0}|{1}", deal.PositionId, OneLine(ex.Message));
            }
        }

        /// <summary>Collapses a multi-line exception message so one log line stays parseable.</summary>
        private static string OneLine(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            return text.Replace("\r", " ").Replace("\n", " ");
        }

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
                if (ShowLogs) Print($"[RE2] Account sync failed: {ex.Message}");
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
                    tp_pips = tpPips.HasValue ? Math.Round(tpPips.Value, 1) : 0.0,
                    reason = reason,
                    entry_indicators = JsonSerializer.Serialize(new
                    {
                        or_high = _orHigh > double.MinValue ? Math.Round(_orHigh, Symbol.Digits) : 0.0,
                        or_low = _orLow < double.MaxValue ? Math.Round(_orLow, Symbol.Digits) : 0.0,
                        or_width_pips = Math.Round((_orHigh - _orLow) / Symbol.PipSize, 1),
                        tick_buffer_pips = TickBufferPips,
                        trend_mode = TrendFilterMode.ToString(),
                        trend_up = _trendUp,
                        trend_down = _trendDown,
                        trend_note = _trendNote,
                        ma_fast = _maFast.HasValue ? Math.Round(_maFast.Value, Symbol.Digits) : (double?)null,
                        ma_slow = _maSlow.HasValue ? Math.Round(_maSlow.Value, Symbol.Digits) : (double?)null,
                        daily_atr_pips = Math.Round(_dailyAtrPips, 1),
                        whipsaw_guard = WhipsawGuardEnabled,
                        whipsaw_window_minutes = WhipsawWindowMinutes,
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
                    if (error != null) Print($"[RE2] Failed to report open #{position.Id}: {error}");
                });
                SendLiveTickTelemetry(force: true);
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[RE2] ReportPositionOpen error: {ex.Message}");
            }
        }

        private void ReportPositionClosed(Position position, double pnl, string reason, double exitPrice,
                                          double pips, double initialSlPips)
        {
            if (RunningMode != RunningMode.RealTime) return;
            try
            {
                string owner = string.IsNullOrEmpty(position.Label) ? BotId : position.Label;
                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = owner,
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
                        Print($"[RE2] Failed to report close #{position.Id} PnL {pnl:F2}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[RE2] ReportPositionClosed error: {ex.Message}");
            }
        }

        private void ReportPartialClose(Position position, double closedLots, double remainingLots,
                                        double realizedPnl, string reason)
        {
            if (RunningMode != RunningMode.RealTime || position == null) return;
            try
            {
                string owner = string.IsNullOrEmpty(position.Label) ? BotId : position.Label;
                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = owner,
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
                        Print($"[RE2] Failed to report partial close #{position.Id}: {error}");
                });
                SendLiveTickTelemetry(force: true);
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[RE2] ReportPartialClose error: {ex.Message}");
            }
        }

        // The server ignores a replayed open/close (matched on ctrader_id), so retrying cannot
        // book a trade twice.
        private static readonly int[] ReportRetryDelaysSeconds = { 2, 4, 8, 16 };

        private async Task<string> PostReportAsync(string url, string json, bool retry = true)
        {
            string lastError = null;
            int maxAttempts = retry ? ReportRetryDelaysSeconds.Length : 0;
            for (int attempt = 0; attempt <= maxAttempts; attempt++)
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
                        if (!retry || ((int)response.StatusCode < 500 && (int)response.StatusCode != 408))
                            return lastError;
                    }
                }
                catch (Exception ex)
                {
                    lastError = ex.InnerException != null ? $"{ex.Message} ({ex.InnerException.Message})" : ex.Message;
                }
            }
            return $"{lastError} (after {maxAttempts + 1} attempts)";
        }

        private double? PnlAtLevel(Position pos, double? level)
        {
            if (pos == null || !level.HasValue || level.Value <= 0 || Symbol.PipSize <= 0) return null;
            double pips = (pos.TradeType == TradeType.Buy
                ? level.Value - pos.EntryPrice
                : pos.EntryPrice - level.Value) / Symbol.PipSize;
            return Math.Round(pips * Symbol.PipValue * pos.VolumeInUnits - Math.Abs(pos.Commissions) * 2.0 + pos.Swap, 2);
        }

        private string TickUrl()
        {
            if (!string.IsNullOrWhiteSpace(ApiUrl))
            {
                var baseUri = ApiUrl.Trim().TrimEnd('/');
                if (baseUri.EndsWith("/trade"))
                    baseUri = baseUri.Substring(0, baseUri.Length - "/trade".Length);
                return $"{baseUri}/api/tick";
            }
            return "http://127.0.0.1:8000/api/tick";
        }

        private void SendLiveTickTelemetry(bool force = false)
        {
            if (RunningMode != RunningMode.RealTime) return;

            var positions = MyPositions();
            double interval = positions.Length > 0 ? 5.0 : 15.0;
            if (!force && (DateTime.UtcNow - _lastTickTelemetryTime).TotalSeconds < interval) return;
            _lastTickTelemetryTime = DateTime.UtcNow;

            // The dashboard keeps one live snapshot per reported bot id, so an adopted position has
            // to be published under its own label as well or its price/P&L freezes on screen.
            PostTelemetry(BotId, positions.Where(x => string.IsNullOrEmpty(x.Label) || x.Label == BotId).ToArray());
            foreach (var label in _adoptedLabels)
            {
                var adopted = positions.Where(x => x.Label == label).ToArray();
                if (adopted.Length > 0) PostTelemetry(label, adopted);
            }
        }

        private void PostTelemetry(string label, Position[] positions)
        {
            try
            {
                var posList = new List<object>();
                Position firstPos = positions.Length > 0 ? positions[0] : null;
                foreach (var p in positions)
                {
                    posList.Add(new
                    {
                        id = p.Id,
                        symbol = p.SymbolName,
                        side = p.TradeType.ToString(),
                        volume = p.VolumeInUnits / Symbol.LotSize,
                        entry_price = p.EntryPrice,
                        current_price = p.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask,
                        net_profit = Math.Round(p.NetProfit, 2),
                        pips = Math.Round(p.Pips, 1),
                        sl_price = p.StopLoss,
                        tp_price = p.TakeProfit,
                        sl_pnl = PnlAtLevel(p, p.StopLoss),
                        tp_pnl = PnlAtLevel(p, p.TakeProfit),
                        label = p.Label
                    });
                }

                var telemetry = new
                {
                    bot_id = label,
                    account_number = Account.Number.ToString(CultureInfo.InvariantCulture),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = AccountLabel,
                    symbol = SymbolName,
                    bid = Symbol.Bid,
                    ask = Symbol.Ask,
                    equity = Account.Equity,
                    balance = Account.Balance,
                    pnl = firstPos != null ? (double?)Math.Round(firstPos.NetProfit, 2) : null,
                    pips = firstPos != null ? (double?)Math.Round(firstPos.Pips, 1) : null,
                    sl_price = firstPos?.StopLoss,
                    tp_price = firstPos?.TakeProfit,
                    sl_pnl = firstPos != null ? PnlAtLevel(firstPos, firstPos.StopLoss) : null,
                    tp_pnl = firstPos != null ? PnlAtLevel(firstPos, firstPos.TakeProfit) : null,
                    positions = posList,
                    last_bar = _lastBarHandled == DateTime.MinValue ? null : _lastBarHandled.ToString("yyyy-MM-ddTHH:mm:ss")
                };

                string json = JsonSerializer.Serialize(telemetry);
                string url = TickUrl();
                Task.Run(async () => await PostReportAsync(url, json, retry: false));
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[RE2] SendLiveTickTelemetry error: {ex.Message}");
            }
        }

        #endregion
    }
}
