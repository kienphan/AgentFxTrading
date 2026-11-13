// Matrix Trend (Matrix Pro replica) as a single-instrument cBot — no AI, pure rules.
//
// Ported from the dnse-kash reference implementation
// (src/dnse_kash/bots/matrix_bot.py, docs/Matrix.md), keeping its rule semantics:
//
//   Matrix     Three EMAs (fast/mid/slow) on the chart timeframe. Each line votes on the
//              CLOSED bar: close above the EMA = up. upCount 0..3.
//   Entry      cross    : close crosses the fast EMA.
//              matrix3  : upCount flips to 3/3 (long) or 0/3 (short).
//              pullback : trend >= 2/3, price touches the fast EMA and closes back above it.
//   Filter     none / mid (price on the mid's side) / matrix2 (>= 2/3 long, <= 1/3 short).
//   Risk       R = clamp(sl_atr_mult * ATR(atr_period), min_sl, max_sl), all in price units.
//              SL = 1R; TP1/TP2/TP3 = 0.4R / 0.8R / 1.2R. The position is split into three
//              broker positions (one per TP); BE moves the rest to entry after TP1.
//   Session    Entries only inside [start, end); everything is flattened at EOD. One group
//              at a time, max trades/session, consecutive-SL stop, optional cooldown.
//
// Deliberate deltas from the Python bot:
//   * Entries fill at the first tick after the bar closes instead of at bar.close, and the
//     bracket is anchored to the actual fill (cTrader semantics). This is more realistic.
//   * The Python add-on levels [2]..[9] are not reproduced: they only multiply risk and do
//     not create an edge (docs/Matrix.md § "Vì sao không có [2]…[9]").
//   * The D1-ATR source is not carried over. The Python study concluded it does not fit a
//     0.4R bracket (test PF 1.08–1.09, win rate ~49%); M15 ATR is the only risk source here.
//
// XAUUSD M15 defaults: EMA 20/50/200, ATR14, 1R stop with a $3 floor / $30 cap, NY session
// 13:00–20:30 UTC (winter clock, US DST aware), flatten 20:45 UTC, 0.03 lots or 0.5% risk.
// Pip = $0.01 on gold, so 300 pips = $3.00 and 120 pips = the 0.4R TP1.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;

namespace cAlgo.Robots
{
    public enum MatrixEntryMode
    {
        Cross,
        Matrix3,
        Pullback
    }

    public enum MatrixEntryFilter
    {
        None,
        Mid,
        Matrix2
    }

    public enum MatrixDirection
    {
        Both,
        LongOnly,
        ShortOnly
    }

    public enum MatrixDstRule
    {
        None,
        US
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class MatrixBot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "Matrix")]
        public string BotId { get; set; }

        [Parameter("Show Bar Journal", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        [Parameter("Enable Trading", Group = "General", DefaultValue = true)]
        public bool EnableTrading { get; set; }

        // ---- Matrix --------------------------------------------------------------------------
        [Parameter("EMA Fast", Group = "Matrix", DefaultValue = 20, MinValue = 2, MaxValue = 500)]
        public int EmaFast { get; set; }

        [Parameter("EMA Mid", Group = "Matrix", DefaultValue = 50, MinValue = 3, MaxValue = 1000)]
        public int EmaMid { get; set; }

        [Parameter("EMA Slow", Group = "Matrix", DefaultValue = 200, MinValue = 4, MaxValue = 2000)]
        public int EmaSlow { get; set; }

        [Parameter("Entry Mode", Group = "Matrix", DefaultValue = MatrixEntryMode.Cross)]
        public MatrixEntryMode EntryMode { get; set; }

        [Parameter("Entry Filter", Group = "Matrix", DefaultValue = MatrixEntryFilter.None)]
        public MatrixEntryFilter EntryFilter { get; set; }

        [Parameter("Direction", Group = "Matrix", DefaultValue = MatrixDirection.Both)]
        public MatrixDirection Direction { get; set; }

        // ---- Risk / bracket ------------------------------------------------------------------
        [Parameter("ATR Period (Wilder)", Group = "Risk", DefaultValue = 14, MinValue = 2, MaxValue = 200)]
        public int AtrPeriod { get; set; }

        [Parameter("SL = ATR Multiple", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10.0, Step = 0.05)]
        public double SlAtrMultiple { get; set; }

        // Gold is quoted in cents: 300 pips = $3.00. The Python bot's 12-point floor existed
        // to keep a 0.4R bracket above noise and costs; scale it by instrument, not by lot.
        [Parameter("Min SL (pips, 0=off)", Group = "Risk", DefaultValue = 300.0, MinValue = 0.0)]
        public double MinSlPips { get; set; }

        [Parameter("Max SL (pips, 0=off)", Group = "Risk", DefaultValue = 3000.0, MinValue = 0.0)]
        public double MaxSlPips { get; set; }

        [Parameter("TP1 (R multiple)", Group = "Risk", DefaultValue = 0.4, MinValue = 0.05, MaxValue = 10.0, Step = 0.05)]
        public double Tp1R { get; set; }

        [Parameter("TP2 (R multiple)", Group = "Risk", DefaultValue = 0.8, MinValue = 0.05, MaxValue = 10.0, Step = 0.05)]
        public double Tp2R { get; set; }

        [Parameter("TP3 (R multiple)", Group = "Risk", DefaultValue = 1.2, MinValue = 0.05, MaxValue = 10.0, Step = 0.05)]
        public double Tp3R { get; set; }

        [Parameter("BE After TP1", Group = "Risk", DefaultValue = true)]
        public bool BeAfterTp1 { get; set; }

        [Parameter("BE Offset (pips past entry)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0)]
        public double BeOffsetPips { get; set; }

        [Parameter("Exit On Matrix Flip", Group = "Risk", DefaultValue = false)]
        public bool ExitOnFlip { get; set; }

        // ---- Position sizing -----------------------------------------------------------------
        [Parameter("Risk per Trade (% Equity)", Group = "Sizing", DefaultValue = 0.5, MinValue = 0.01, MaxValue = 10.0, Step = 0.01)]
        public double RiskPercent { get; set; }

        [Parameter("Fixed Lots (0 = risk %)", Group = "Sizing", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 100.0, Step = 0.01)]
        public double FixedLots { get; set; }

        [Parameter("Max Lots per Trade", Group = "Sizing", DefaultValue = 1.0, MinValue = 0.01, MaxValue = 100.0, Step = 0.01)]
        public double MaxLots { get; set; }

        [Parameter("Max Spread (pips)", Group = "Sizing", DefaultValue = 50.0, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        // ---- Session -------------------------------------------------------------------------
        [Parameter("Session Start Hour (winter UTC)", Group = "Session", DefaultValue = 13, MinValue = 0, MaxValue = 23)]
        public int StartHourUtc { get; set; }

        [Parameter("Session Start Minute", Group = "Session", DefaultValue = 0, MinValue = 0, MaxValue = 59)]
        public int StartMinute { get; set; }

        [Parameter("Session End Hour (winter UTC)", Group = "Session", DefaultValue = 20, MinValue = 0, MaxValue = 23)]
        public int EndHourUtc { get; set; }

        [Parameter("Session End Minute", Group = "Session", DefaultValue = 30, MinValue = 0, MaxValue = 59)]
        public int EndMinute { get; set; }

        [Parameter("DST Rule", Group = "Session", DefaultValue = MatrixDstRule.US)]
        public MatrixDstRule DstRule { get; set; }

        [Parameter("Flatten At EOD", Group = "Session", DefaultValue = true)]
        public bool FlattenAtEod { get; set; }

        [Parameter("Flatten Hour (winter UTC)", Group = "Session", DefaultValue = 20, MinValue = 0, MaxValue = 23)]
        public int FlattenHourUtc { get; set; }

        [Parameter("Flatten Minute", Group = "Session", DefaultValue = 45, MinValue = 0, MaxValue = 59)]
        public int FlattenMinute { get; set; }

        [Parameter("Max Trades per Session", Group = "Session", DefaultValue = 6, MinValue = 1, MaxValue = 50)]
        public int MaxTradesPerSession { get; set; }

        [Parameter("Daily Max Loss ($, 0=off)", Group = "Session", DefaultValue = 0.0, MinValue = 0.0)]
        public double DailyMaxLossMoney { get; set; }

        [Parameter("Max Consecutive SL", Group = "Session", DefaultValue = 3, MinValue = 1, MaxValue = 50)]
        public int MaxConsecutiveSl { get; set; }

        [Parameter("Cooldown After SL (minutes)", Group = "Session", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 1440.0)]
        public double CooldownAfterSlMinutes { get; set; }

        #endregion

        #region State

        private int _lastClosedIndex = -1;
        private int _lastIndIndex = -1;
        private bool _indReady;

        private double _emaF;
        private double _emaM;
        private double _emaS;
        private double _atr;
        private double _lastClose;
        private double _prevClose;
        private double _prevF;
        private double _prevM;
        private double _prevS;
        private int _upCount;
        private int _prevUpCount;

        private DateTime _sessionDate = DateTime.MinValue;
        private int _tradesToday;
        private double _realizedToday;
        private int _consecutiveSl;
        private DateTime _cooldownUntilUtc = DateTime.MinValue;
        private bool _eodFlattened;

        private DateTime _groupSessionDate = DateTime.MinValue;
        private bool _beApplied;
        private bool _groupHadSl;
        private readonly Dictionary<int, int> _tpLevel = new Dictionary<int, int>();

        #endregion

        #region Lifecycle

        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute15)
            {
                Print("[Matrix] This port is specified on M15. Attach it to an M15 chart, or change the timeframe guard.");
                Stop();
                return;
            }

            Positions.Closed += OnPositionClosed;
            RecoverOpenPositions();

            Print($"[Matrix] '{BotId}' {SymbolName} {TimeFrame} | EMA {EmaFast}/{EmaMid}/{EmaSlow} " +
                  $"| {EntryMode}/{EntryFilter} | R={SlAtrMultiple:F2}xATR{AtrPeriod} " +
                  $"clamp [{MinSlPips:F0},{MaxSlPips:F0}]p | TP {Tp1R:F2}/{Tp2R:F2}/{Tp3R:F2}R " +
                  $"| BE {BeAfterTp1} | session {StartHourUtc:00}:{StartMinute:00}-{EndHourUtc:00}:{EndMinute:00} UTC " +
                  $"({DstRule}) flatten {FlattenHourUtc:00}:{FlattenMinute:00} | " +
                  (FixedLots > 0 ? $"fixed {FixedLots:F2} lots" : $"risk {RiskPercent:F2}%/trade"));
        }

        protected override void OnTick()
        {
            RollSession(Server.Time);
            CheckEodFlatten();
        }

        protected override void OnBarClosed()
        {
            if (Bars.Count < 2) return;
            int index = Bars.Count - 2;
            if (index <= _lastClosedIndex) return;
            _lastClosedIndex = index;

            var bar = Bars.Last(1);
            RollSession(bar.OpenTime);

            if (!UpdateIndicators(index))
            {
                if (ShowLogs)
                    Print($"[Matrix] warming up: {index + 1} closed bars (need {WarmupBars})");
                return;
            }

            var open = OpenPositions();
            int signal = EvaluateSignal(index);
            if (ShowLogs) LogBar(bar, index, open.Length, signal);

            if (open.Length > 0)
            {
                if (ExitOnFlip && IsMatrixAgainstPosition())
                {
                    FlattenAll("Matrix flip");
                    return;
                }
                return;
            }

            if (!EnableTrading || !IsEntryWindow(bar.OpenTime)) return;
            if (signal == 0) return;
            if (!EntryAllowed()) return;

            double slDistance = StopDistancePrice();
            if (slDistance <= 0) return;
            OpenGroup(signal, slDistance);
        }

        protected override void OnStop()
        {
            Positions.Closed -= OnPositionClosed;
            Print($"[Matrix] Stopped | today trades={_tradesToday} realized=${_realizedToday:F2} " +
                  $"consecutiveSL={_consecutiveSl}");
        }

        #endregion

        #region Session clock

        private bool IsUsDst(DateTime utc)
        {
            DateTime start = NthSunday(utc.Year, 3, 2).AddHours(7);
            DateTime end = NthSunday(utc.Year, 11, 1).AddHours(6);
            return utc >= start && utc < end;
        }

        private static DateTime NthSunday(int year, int month, int n)
        {
            var first = new DateTime(year, month, 1);
            int offset = (7 - (int)first.DayOfWeek) % 7;
            return first.AddDays(offset + (n - 1) * 7);
        }

        /// <summary>Session-local clock: parameters are winter UTC, so during US DST the UTC
        /// timeline is shifted back one hour and the local clock time never moves.</summary>
        private DateTime SessionClock(DateTime utc)
        {
            if (DstRule == MatrixDstRule.None) return utc;
            return utc.AddHours(IsUsDst(utc) ? -1 : 0);
        }

        private DateTime FlattenTime(DateTime anyTimeInSession)
        {
            var local = SessionClock(anyTimeInSession);
            return local.Date.AddHours(FlattenHourUtc).AddMinutes(FlattenMinute);
        }

        private bool IsEntryWindow(DateTime barOpenUtc)
        {
            var t = SessionClock(barOpenUtc).TimeOfDay;
            var start = new TimeSpan(StartHourUtc, StartMinute, 0);
            var end = new TimeSpan(EndHourUtc, EndMinute, 0);
            if (end > start) return t >= start && t < end;
            return t >= start || t < end;   // overnight session
        }

        private void RollSession(DateTime utc)
        {
            var localDate = SessionClock(utc).Date;
            if (localDate == _sessionDate) return;

            if (_sessionDate != DateTime.MinValue && ShowLogs)
            {
                Print($"[Matrix] Session {_sessionDate:yyyy-MM-dd} | trades={_tradesToday} " +
                      $"realized=${_realizedToday:F2} consecutiveSL={_consecutiveSl}");
            }

            _sessionDate = localDate;
            _tradesToday = 0;
            _realizedToday = 0.0;
            _consecutiveSl = 0;
            _cooldownUntilUtc = DateTime.MinValue;
            _eodFlattened = false;
        }

        private void CheckEodFlatten()
        {
            var positions = OpenPositions();
            if (positions.Length == 0) return;

            var local = SessionClock(Server.Time);

            // A position that survived its own session (weekend, restart, holiday) is closed on
            // the first tick of the next session instead of being carried further.
            if (_groupSessionDate != DateTime.MinValue && local.Date != _groupSessionDate)
            {
                FlattenAll("EOD carry");
                return;
            }

            if (!FlattenAtEod || _eodFlattened) return;
            if (local >= FlattenTime(Server.Time))
            {
                FlattenAll("EOD");
                _eodFlattened = OpenPositions().Length == 0;
            }
        }

        #endregion

        #region Matrix indicators

        private int WarmupBars => Math.Max(EmaSlow * 2, 400);

        private double UpCount(double close, double f, double m, double s)
        {
            return (close > f ? 1 : 0) + (close > m ? 1 : 0) + (close > s ? 1 : 0);
        }

        private double EmaValue(int period, int lastIndex)
        {
            if (lastIndex < period - 1) return double.NaN;

            double sum = 0.0;
            for (int i = 0; i < period; i++) sum += Bars.ClosePrices[i];
            double ema = sum / period;
            double alpha = 2.0 / (period + 1.0);
            for (int i = period; i <= lastIndex; i++)
                ema = alpha * Bars.ClosePrices[i] + (1.0 - alpha) * ema;
            return ema;
        }

        private double TrueRange(int i)
        {
            double high = Bars.HighPrices[i];
            double low = Bars.LowPrices[i];
            double prevClose = Bars.ClosePrices[i - 1];
            return Math.Max(high - low, Math.Max(Math.Abs(high - prevClose), Math.Abs(low - prevClose)));
        }

        /// <summary>Wilder ATR seeded by the SMA of the first `period` true ranges, exactly as
        /// the Python reference (_atr_wilder).</summary>
        private double WilderAtr(int lastIndex)
        {
            if (lastIndex < AtrPeriod) return 0.0;

            double sum = 0.0;
            for (int i = 1; i <= AtrPeriod; i++) sum += TrueRange(i);
            double atr = sum / AtrPeriod;
            for (int i = AtrPeriod + 1; i <= lastIndex; i++)
                atr = (atr * (AtrPeriod - 1) + TrueRange(i)) / AtrPeriod;
            return atr;
        }

        /// <summary>Seeds once, then O(1) per closed bar. Re-seeding from bar 0 (and an SMA
        /// seed) is close enough to the Python first-close seed after the warmup window; the
        /// EMA memory decays the difference below one part in 10^3.</summary>
        private bool UpdateIndicators(int index)
        {
            if (index == _lastIndIndex) return _indReady;
            _lastIndIndex = index;

            if (!_indReady)
            {
                if (index < WarmupBars) return false;

                _emaF = EmaValue(EmaFast, index);
                _emaM = EmaValue(EmaMid, index);
                _emaS = EmaValue(EmaSlow, index);
                _prevF = EmaValue(EmaFast, index - 1);
                _prevM = EmaValue(EmaMid, index - 1);
                _prevS = EmaValue(EmaSlow, index - 1);
                _atr = WilderAtr(index);

                _lastClose = Bars.ClosePrices[index];
                _prevClose = Bars.ClosePrices[index - 1];
                _upCount = (int)UpCount(_lastClose, _emaF, _emaM, _emaS);
                _prevUpCount = (int)UpCount(_prevClose, _prevF, _prevM, _prevS);
                _indReady = true;
                return true;
            }

            _prevClose = _lastClose;
            _prevF = _emaF;
            _prevM = _emaM;
            _prevS = _emaS;
            _prevUpCount = _upCount;

            double close = Bars.ClosePrices[index];
            double aF = 2.0 / (EmaFast + 1.0);
            double aM = 2.0 / (EmaMid + 1.0);
            double aS = 2.0 / (EmaSlow + 1.0);
            _emaF = aF * close + (1.0 - aF) * _emaF;
            _emaM = aM * close + (1.0 - aM) * _emaM;
            _emaS = aS * close + (1.0 - aS) * _emaS;

            _atr = (_atr * (AtrPeriod - 1) + TrueRange(index)) / AtrPeriod;
            _lastClose = close;
            _upCount = (int)UpCount(close, _emaF, _emaM, _emaS);
            return true;
        }

        #endregion

        #region Signal

        private int EvaluateSignal(int index)
        {
            if (!_indReady) return 0;

            double close = Bars.ClosePrices[index];
            double low = Bars.LowPrices[index];
            double high = Bars.HighPrices[index];
            bool isLong = false;
            bool isShort = false;

            switch (EntryMode)
            {
                case MatrixEntryMode.Cross:
                    isLong = _prevClose <= _prevF && close > _emaF;
                    isShort = _prevClose >= _prevF && close < _emaF;
                    break;
                case MatrixEntryMode.Matrix3:
                    isLong = _upCount == 3 && _prevUpCount < 3;
                    isShort = _upCount == 0 && _prevUpCount > 0;
                    break;
                case MatrixEntryMode.Pullback:
                    isLong = _upCount >= 2 && low <= _emaF && close > _emaF;
                    isShort = _upCount <= 1 && high >= _emaF && close < _emaF;
                    break;
            }

            if (!(isLong || isShort)) return 0;
            if (isLong && Direction == MatrixDirection.ShortOnly) return 0;
            if (isShort && Direction == MatrixDirection.LongOnly) return 0;

            if (EntryFilter == MatrixEntryFilter.Mid)
            {
                if (isLong && close <= _emaM) return 0;
                if (isShort && close >= _emaM) return 0;
            }
            else if (EntryFilter == MatrixEntryFilter.Matrix2)
            {
                if (isLong && _upCount < 2) return 0;
                if (isShort && _upCount > 1) return 0;
            }

            return isLong ? 1 : -1;
        }

        private bool IsMatrixAgainstPosition()
        {
            foreach (var pos in OpenPositions())
            {
                if (pos.TradeType == TradeType.Buy && _upCount == 0) return true;
                if (pos.TradeType == TradeType.Sell && _upCount == 3) return true;
            }
            return false;
        }

        private bool EntryAllowed()
        {
            if (_tradesToday >= MaxTradesPerSession) return false;
            if (_consecutiveSl >= MaxConsecutiveSl) return false;
            if (Server.Time < _cooldownUntilUtc) return false;
            if (DailyMaxLossMoney > 0 && _realizedToday <= -DailyMaxLossMoney) return false;

            double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                if (ShowLogs) Print($"[Matrix] entry skipped: spread {spreadPips:F1}p > {MaxSpreadPips:F1}p");
                return false;
            }
            return true;
        }

        #endregion

        #region Execution

        private Position[] OpenPositions() => Positions.FindAll(BotId, SymbolName);

        private double StopDistancePrice()
        {
            if (_atr <= 0) return 0.0;
            double distance = _atr * SlAtrMultiple;
            double min = MinSlPips * Symbol.PipSize;
            double max = MaxSlPips * Symbol.PipSize;
            if (max > 0) distance = Math.Min(distance, max);
            if (min > 0) distance = Math.Max(distance, min);
            return distance;
        }

        private double UnitsForRisk(double stopDistance, out double riskMoney)
        {
            riskMoney = 0.0;
            double pipValue = Symbol.PipValue > 0 ? Symbol.PipValue : 1.0;
            double perUnitRisk = stopDistance * (pipValue / Symbol.PipSize);
            if (perUnitRisk <= 0) return 0.0;

            double units;
            if (FixedLots > 0)
            {
                units = FixedLots * Symbol.LotSize;
            }
            else
            {
                double budget = Account.Equity * (RiskPercent / 100.0);
                units = budget / perUnitRisk;
            }

            units = Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);
            double cap = Symbol.NormalizeVolumeInUnits(MaxLots * Symbol.LotSize, RoundingMode.Down);
            if (cap > 0) units = Math.Min(units, cap);

            if (units < Symbol.VolumeInUnitsMin)
            {
                // The broker minimum is the floor; the actual risk is logged and, when it
                // exceeds the intent by a lot, the operator sees it in the first trade line.
                units = Symbol.VolumeInUnitsMin;
            }
            if (units > Symbol.VolumeInUnitsMax) units = Symbol.VolumeInUnitsMax;

            riskMoney = units * perUnitRisk;
            return units;
        }

        private void OpenGroup(int side, double stopDistance)
        {
            double units = UnitsForRisk(stopDistance, out double riskMoney);
            if (units <= 0)
            {
                Print("[Matrix] entry refused: volume is not positive");
                return;
            }

            double slPips = stopDistance / Symbol.PipSize;
            if (slPips <= 0) return;

            var parts = SplitVolume(units, out var levels);
            var tradeType = side > 0 ? TradeType.Buy : TradeType.Sell;
            double[] tpR = { Tp1R, Tp2R, Tp3R };
            int opened = 0;

            for (int i = 0; i < parts.Count; i++)
            {
                int level = levels[i];
                double tpPips = slPips * tpR[level - 1];
                string comment = $"Matrix TP{level}";
                var result = ExecuteMarketOrder(tradeType, SymbolName, parts[i], BotId, slPips, tpPips, comment);
                if (result == null || !result.IsSuccessful || result.Position == null)
                {
                    Print($"[Matrix] {tradeType} TP{level} order failed: " +
                          $"{(result == null ? "no result" : result.Error.ToString())}");
                    continue;
                }

                _tpLevel[result.Position.Id] = level;
                opened++;
                Print($"[Matrix] OPEN {tradeType} TP{level} {parts[i] / Symbol.LotSize:F3} lots " +
                      $"#{result.Position.Id} @ {result.Position.EntryPrice:F2} " +
                      $"| SL {slPips:F0}p ({stopDistance:F2}) | TP {tpPips:F0}p ({tpR[level - 1]:F2}R)");
            }

            if (opened == 0) return;

            _tradesToday++;
            _groupSessionDate = _sessionDate;
            _groupHadSl = false;
            _beApplied = false;
            Print($"[Matrix] group {tradeType} {opened} part(s) | matrix {_upCount}/3 " +
                  $"({EntryMode}/{EntryFilter}) | risk ${riskMoney:F2} | trade {_tradesToday}/{MaxTradesPerSession}");
        }

        /// <summary>Splits the risk unit across the three TPs. One part when the broker minimum
        /// leaves no room for three, two when only two fit — mirroring the Python _split_qty.</summary>
        private List<double> SplitVolume(double total, out int[] levels)
        {
            double min = Symbol.VolumeInUnitsMin;
            var parts = new List<double>();
            var lv = new List<int>();

            double part = Symbol.NormalizeVolumeInUnits(total / 3.0, RoundingMode.Down);
            if (part >= min)
            {
                parts.Add(part);
                parts.Add(part);
                parts.Add(Symbol.NormalizeVolumeInUnits(total - 2.0 * part, RoundingMode.Down));
                lv.AddRange(new[] { 1, 2, 3 });
            }
            else
            {
                part = Symbol.NormalizeVolumeInUnits(total / 2.0, RoundingMode.Down);
                if (part >= min)
                {
                    parts.Add(part);
                    parts.Add(Symbol.NormalizeVolumeInUnits(total - part, RoundingMode.Down));
                    lv.AddRange(new[] { 1, 2 });
                }
                else
                {
                    parts.Add(total);
                    lv.Add(1);
                }
            }

            // Drop a zero/undersized tail instead of sending it to the broker.
            for (int i = parts.Count - 1; i >= 0; i--)
            {
                if (parts[i] >= min) continue;
                parts.RemoveAt(i);
                lv.RemoveAt(i);
            }

            levels = lv.ToArray();
            return parts;
        }

        private void ApplyBreakeven()
        {
            if (!BeAfterTp1) return;

            double offset = BeOffsetPips * Symbol.PipSize;
            foreach (var pos in OpenPositions())
            {
                double target = pos.TradeType == TradeType.Buy
                    ? pos.EntryPrice + offset
                    : pos.EntryPrice - offset;

                double? current = pos.StopLoss;
                bool improves = !current.HasValue
                    || (pos.TradeType == TradeType.Buy
                        ? target > current.Value + Symbol.TickSize / 2.0
                        : target < current.Value - Symbol.TickSize / 2.0);
                if (!improves) continue;

                var result = pos.ModifyStopLossPrice(target);
                if (result != null && result.IsSuccessful)
                {
                    _beApplied = true;
                    Print($"[Matrix] BE on #{pos.Id}: SL -> {target:F2} after TP1");
                }
                else
                {
                    Print($"[Matrix] BE failed on #{pos.Id}: " +
                          $"{(result == null ? "no result" : result.Error.ToString())}");
                }
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var pos = args.Position;
            if (pos.SymbolName != SymbolName || pos.Label != BotId) return;

            _realizedToday += pos.NetProfit;

            int level = 0;
            bool hadLevel = _tpLevel.TryGetValue(pos.Id, out level);
            _tpLevel.Remove(pos.Id);

            if (args.Reason == PositionCloseReason.TakeProfit)
            {
                _consecutiveSl = 0;
                if (hadLevel && level == 1) ApplyBreakeven();
            }
            else if (args.Reason == PositionCloseReason.StopLoss && !_beApplied)
            {
                _groupHadSl = true;
            }

            Print($"[Matrix] CLOSE #{pos.Id} {pos.TradeType} {(hadLevel ? $"TP{level}" : "")} " +
                  $"@ {pos.EntryPrice:F2} | {args.Reason} | pnl ${pos.NetProfit:F2}");

            if (OpenPositions().Length > 0) return;

            if (_groupHadSl)
            {
                _consecutiveSl++;
                if (CooldownAfterSlMinutes > 0)
                    _cooldownUntilUtc = Server.Time.AddMinutes(CooldownAfterSlMinutes);
            }

            _groupHadSl = false;
            _beApplied = false;
        }

        private void FlattenAll(string reason)
        {
            foreach (var pos in OpenPositions())
            {
                var result = ClosePosition(pos);
                if (result == null || !result.IsSuccessful)
                    Print($"[Matrix] flatten #{pos.Id} failed: " +
                          $"{(result == null ? "no result" : result.Error.ToString())}");
                else if (ShowLogs)
                    Print($"[Matrix] flattened #{pos.Id} ({reason})");
            }
        }

        /// <summary>Recreates the TP-level map and the session of the open group after a
        /// restart. The broker already holds the stops, so this is only bookkeeping.</summary>
        private void RecoverOpenPositions()
        {
            var positions = OpenPositions();
            if (positions.Length == 0) return;

            long minEntry = positions.Min(p => p.EntryTime.Ticks);
            _groupSessionDate = SessionClock(new DateTime(minEntry)).Date;

            foreach (var pos in positions)
            {
                if (!pos.StopLoss.HasValue || !pos.TakeProfit.HasValue) continue;
                double risk = Math.Abs(pos.EntryPrice - pos.StopLoss.Value);
                if (risk <= 0) continue;
                double reward = Math.Abs(pos.TakeProfit.Value - pos.EntryPrice);
                double r = reward / risk;
                int level = Math.Abs(r - Tp1R) < Math.Abs(r - Tp2R)
                    ? (Math.Abs(r - Tp1R) < Math.Abs(r - Tp3R) ? 1 : 3)
                    : (Math.Abs(r - Tp2R) < Math.Abs(r - Tp3R) ? 2 : 3);
                _tpLevel[pos.Id] = level;

                if (Math.Abs(pos.StopLoss.Value - pos.EntryPrice) <= Symbol.TickSize / 2.0)
                    _beApplied = true;
            }

            Print($"[Matrix] recovered {positions.Length} open position(s) of the {_groupSessionDate:yyyy-MM-dd} group");
        }

        #endregion

        #region Journal

        private void LogBar(Bar bar, int index, int openCount, int signal)
        {
            string signalText = signal > 0 ? "LONG" : signal < 0 ? "SHORT" : "-";
            Print($"[Matrix] {SessionClock(bar.OpenTime):HH:mm} close={bar.Close:F2} " +
                  $"matrix={_upCount}/3 E{EmaFast}={_emaF:F2} E{EmaMid}={_emaM:F2} E{EmaSlow}={_emaS:F2} " +
                  $"ATR={_atr / Symbol.PipSize:F0}p pos={openCount} signal={signalText} " +
                  $"trades={_tradesToday}/{MaxTradesPerSession} streakSL={_consecutiveSl}");
        }

        #endregion
    }
}
