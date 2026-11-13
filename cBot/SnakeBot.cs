// Snake Trading (Richard Dennis & William Eckhardt, 1983) as a single-instrument cBot.
//
// The rules implemented are the original breakout system, not a lookalike:
//
//   N         Wilder ATR(AtrPeriod) over CLOSED bars — the volatility unit.
//   Entry     Buy when price trades above the highest high of the last EntryChannelPeriod
//             closed bars; Sell below the lowest low. System 1 = 20 bars, System 2 = 55.
//   Stop      StopDistanceN x N below the unit's own fill (2N by default), never widened.
//   Pyramid   One extra unit every PyramidStepN x N the market moves our way, to MaxUnits.
//   Exit      The opposite ExitChannelPeriod extreme (S1: 10 bars, S2: 20), re-anchored
//             every closed bar. It trails with the trend and overtakes the 2N stop once the
//             move is old enough, which is where the system's edge actually lives.
//   Failsafe  System 1 only: after a winning group, skip the NEXT breakout in that same
//             direction (`FailsafeMode = Classic`) — one breakout episode, not a permanent
//             lock and not a single tick. The original rule, and the reason S1 does not
//             simply re-enter every 20-bar high.
//             A flag that never cleared would stop the bot for good after one win in each
//             direction: nothing would ever trade again to earn a loss and reset it.
//
// Every unit is sized so its 2N stop costs RiskPerUnitPercent of equity, hard-capped at
// MaxDollarRiskPerUnit. The broker's minimum lot can exceed that cap on a small account —
// then the entry is refused instead of sized up, the same guardrail FlowRsiBot uses.
//
// The bot is timeframe-agnostic: run a daily chart for the classic system. Channels and N
// are always measured on closed bars, so the forming bar can never move a level a live
// order depends on.
//
// It ignores the news, never asks the AI, and holds over the weekend by design — the exit
// is the channel, not the session. Run it on its own account: one gold unit's 2N stop is
// already wider than the intraday container fuse ($30/day), so the dashboard's daily-loss
// layers need their own ("account", <id>) and ("container", <bot id>) rows for this bot.
// See docs/snake.md for the XAUUSD recipe.

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
    public enum SnakeSystemPreset
    {
        System1_20_10,
        System2_55_20,
        Custom
    }

    public enum FailsafeModeType
    {
        Disabled,
        Classic,
        OnlyMajorWinners
    }

    public enum AllowedTradeDirection
    {
        Both,
        LongOnly,
        ShortOnly
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class SnakeBot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "Snake")]
        public string BotId { get; set; }

        [Parameter("Account Identifier", Group = "General", DefaultValue = "Snake-Standard")]
        public string AccountLabel { get; set; }

        [Parameter("Show Detailed Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        [Parameter("Enable Trading", Group = "General", DefaultValue = true)]
        public bool EnableTrading { get; set; }

        [Parameter("System Preset", Group = "Snake System", DefaultValue = SnakeSystemPreset.System1_20_10)]
        public SnakeSystemPreset SystemPreset { get; set; }

        [Parameter("Entry Channel (bars, S1=20 / S2=55)", Group = "Snake System", DefaultValue = 20, MinValue = 5, MaxValue = 200)]
        public int EntryChannelPeriod { get; set; }

        [Parameter("Exit Channel (bars, S1=10 / S2=20)", Group = "Snake System", DefaultValue = 10, MinValue = 3, MaxValue = 200)]
        public int ExitChannelPeriod { get; set; }

        [Parameter("Failsafe Mode", Group = "Snake System", DefaultValue = FailsafeModeType.Disabled)]
        public FailsafeModeType FailsafeMode { get; set; }

        [Parameter("Enable Macro Trend Filter", Group = "Macro Filter", DefaultValue = true)]
        public bool EnableMacroTrendFilter { get; set; }

        [Parameter("Macro Timeframe", Group = "Macro Filter", DefaultValue = "Daily")]
        public TimeFrame MacroTrendTimeFrame { get; set; }

        [Parameter("Macro EMA Period", Group = "Macro Filter", DefaultValue = 200, MinValue = 10, MaxValue = 500)]
        public int MacroEmaPeriod { get; set; }

        [Parameter("Allowed Direction", Group = "Execution Guard", DefaultValue = AllowedTradeDirection.Both)]
        public AllowedTradeDirection DirectionMode { get; set; }

        [Parameter("Max ATR Allowed ($)", Group = "Execution Guard", DefaultValue = 100.0, MinValue = 0.0)]
        public double MaxAtrAllowed { get; set; }

        [Parameter("Max Spread (pips)", Group = "Execution Guard", DefaultValue = 50.0, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Entry Buffer (pips)", Group = "Execution Guard", DefaultValue = 0.0, MinValue = 0.0)]
        public double EntryBufferPips { get; set; }

        [Parameter("N Period (Wilder ATR)", Group = "Volatility & Risk", DefaultValue = 20, MinValue = 5, MaxValue = 100)]
        public int AtrPeriod { get; set; }

        [Parameter("Stop Distance (x N)", Group = "Volatility & Risk", DefaultValue = 2.0, MinValue = 0.5, MaxValue = 10.0, Step = 0.1)]
        public double StopDistanceN { get; set; }

        [Parameter("Pyramid Step (x N)", Group = "Volatility & Risk", DefaultValue = 0.5, MinValue = 0.1, MaxValue = 5.0, Step = 0.1)]
        public double PyramidStepN { get; set; }

        [Parameter("Max Units", Group = "Volatility & Risk", DefaultValue = 4, MinValue = 1, MaxValue = 8)]
        public int MaxUnits { get; set; }

        [Parameter("Risk per Unit (% Equity)", Group = "Volatility & Risk", DefaultValue = 1.0, MinValue = 0.05, MaxValue = 5.0, Step = 0.05)]
        public double RiskPerUnitPercent { get; set; }

        [Parameter("Max Dollar Risk per Unit ($)", Group = "Volatility & Risk", DefaultValue = 250.0, MinValue = 1.0)]
        public double MaxDollarRiskPerUnit { get; set; }

        [Parameter("Agent API URL", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/trade")]
        public string ApiUrl { get; set; }

        [Parameter("Portfolio Report Endpoint", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/portfolio/report")]
        public string AiReportUrl { get; set; }

        [Parameter("Heartbeat Interval (seconds)", Group = "Reporting", DefaultValue = 30, MinValue = 5)]
        public int HeartbeatIntervalSeconds { get; set; }

        #endregion

        #region State

        // Levels are recomputed once per bar and reused for every tick of that bar: a daily
        // chart streams millions of ticks across a multi-year backtest.
        private int _levelsBarIndex = -1;
        private double _n;
        private double _entryHigh;
        private double _entryLow;
        private double _exitHigh;
        private double _exitLow;

        // The N each live unit was sized with. The 2N stop is measured from the unit's own
        // fill, and N moves every bar, so it has to be remembered per position.
        private readonly Dictionary<int, double> _unitN = new Dictionary<int, double>();

        // Failsafe memory: does the next breakout in this direction have to be skipped? It is
        // armed by a winning GROUP — all the units of one entry, which share the exit level,
        // so the group's total and not the last unit's P&L decides — and consumed by the one
        // signal it skips.
        private readonly Dictionary<TradeType, bool> _skipNextBreakout = new Dictionary<TradeType, bool>();
        // While true, the current breakout episode is the one the failsafe skipped; entries in
        // that direction stay blocked until price is back inside the entry channel. The old
        // flag-only version skipped one tick and re-entered on the next one.
        private readonly Dictionary<TradeType, bool> _failsafeBlocked = new Dictionary<TradeType, bool>();
        private readonly Dictionary<TradeType, double> _groupPnl = new Dictionary<TradeType, double>();
        private readonly Dictionary<TradeType, int> _groupUnitsCount = new Dictionary<TradeType, int>();
        private Bars _macroBars;
        private int _macroEmaLastClosed = -1;
        private int _macroEmaPeriod = -1;
        private double _macroEmaValue = double.NaN;

        private int _lastEntryBarIndex = -1;
        private int _skipLoggedBarIndex = -1;
        private bool _failsafeSeeded;

        private readonly HttpClient _httpClient = new HttpClient(new SocketsHttpHandler { PooledConnectionIdleTimeout = TimeSpan.FromSeconds(4) })
        {
            Timeout = TimeSpan.FromSeconds(60)
        };

        #endregion

        #region Lifecycle

        protected override void OnStart()
        {
            if (SystemPreset == SnakeSystemPreset.System1_20_10)
            {
                EntryChannelPeriod = 20;
                ExitChannelPeriod = 10;
            }
            else if (SystemPreset == SnakeSystemPreset.System2_55_20)
            {
                EntryChannelPeriod = 55;
                ExitChannelPeriod = 20;
            }

            Print($"[Snake] Initializing cBot '{BotId}' on {SymbolName} ({TimeFrame}) | " +
                  $"Preset: {SystemPreset} (Entry {EntryChannelPeriod} / Exit {ExitChannelPeriod}) | " +
                  $"N {AtrPeriod} bars | Direction: {DirectionMode} | MacroFilter: {EnableMacroTrendFilter} ({MacroTrendTimeFrame} EMA{MacroEmaPeriod}) | " +
                  $"Failsafe: {FailsafeMode}");

            if (EnableMacroTrendFilter)
            {
                try
                {
                    _macroBars = MarketData.GetBars(MacroTrendTimeFrame, SymbolName);
                }
                catch (Exception ex)
                {
                    Print($"[Snake] Warning: could not load macro timeframe {MacroTrendTimeFrame}: {ex.Message}");
                }
            }

            Positions.Closed += OnPositionClosed;

            SeedFailsafeFromHistory();
            SendAccountSync();

            // Heartbeat chạy trên Timer, độc lập tick (như DATX.DualRSI /
            // DATX.AgentBot): sàn nghỉ phiên không có tick thì hub vẫn thấy bot sống.
            if (RunningMode == RunningMode.RealTime)
                Timer.Start(TimeSpan.FromSeconds(Math.Max(5, HeartbeatIntervalSeconds)));
        }

        protected override void OnTick()
        {
            TryEvaluateEntry();
        }

        /// <summary>Heartbeat mỗi HeartbeatIntervalSeconds — POST /api/bots/{id}/heartbeat,
        /// fire-and-forget, không bao giờ throw (hub sập không được giết bot).</summary>
        protected override void OnTimer()
        {
            if (RunningMode != RunningMode.RealTime) return;
            SendHeartbeat();
        }

        protected override void OnBarClosed()
        {
            // Levels for the new bar, the trailing channel stop, then the entry check (a bar
            // can break a channel on the first tick after the close without another tick loop).
            // LastClosedIndex() resolves the bar that actually closed; see the contract below.
            UpdateStops();
            TryEvaluateEntry();
        }

        #endregion

        #region Snake maths

        // cTrader can already have the just-opened bar in Bars during OnBarClosed (the
        // 2026-09-23 restart audit saw it on half the bots; the old "verified 10/2026"
        // comment here was wrong for this API). A bar whose full span has not yet elapsed on
        // the server clock is still forming, so the closed one is the bar before it. This is
        // the same contract FlowRsiBot and AsianRangeJudasSweepBot use.
        private int LastClosedIndex => ClosedBarIndex();

        private int ClosedBarIndex()
        {
            int last = Bars.Count - 1;
            if (last > 0 && Bars[last].OpenTime + BarSpan() > Server.Time)
                return last - 1;
            return last;
        }

        private TimeSpan _barSpan = TimeSpan.Zero;

        /// <summary>Bar width, as the smallest gap between recent open times. Weekends and
        /// holidays only widen a gap, so the minimum is the chart timeframe.</summary>
        private TimeSpan BarSpan()
        {
            if (_barSpan > TimeSpan.Zero) return _barSpan;
            var span = TimeSpan.MaxValue;
            for (int i = Math.Max(1, Bars.Count - 50); i < Bars.Count; i++)
            {
                var gap = Bars[i].OpenTime - Bars[i - 1].OpenTime;
                if (gap > TimeSpan.Zero && gap < span) span = gap;
            }
            if (span == TimeSpan.MaxValue) return TimeSpan.FromDays(1);
            _barSpan = span;
            return span;
        }

        private bool HasWarmup => Bars.Count >= Math.Max(EntryChannelPeriod, AtrPeriod) + 3;

        private double TrueRange(int i)
        {
            double prevClose = Bars.ClosePrices[i - 1];
            double high = Bars.HighPrices[i];
            double low = Bars.LowPrices[i];
            return Math.Max(high - low, Math.Max(Math.Abs(high - prevClose), Math.Abs(low - prevClose)));
        }

        /// <summary>
        /// N = Wilder's ATR: an RMA of true range seeded by the SMA of the first `period`
        /// ranges. Re-seeded a fixed number of bars back so the value tracks the live market
        /// instead of the whole history; the RMA's memory is exponential, so a seed 200 bars
        /// back is already below one part in 10^4 of the result.
        /// </summary>
        private double WilderAtr(int period)
        {
            int last = LastClosedIndex;
            if (last < period) return 0.0;

            int warmup = Math.Max(period * 10, 200);
            int seedEnd = Math.Max(period, last - warmup);

            double seed = 0.0;
            for (int i = seedEnd - period + 1; i <= seedEnd; i++)
                seed += TrueRange(i);

            double rma = seed / period;
            for (int i = seedEnd + 1; i <= last; i++)
                rma = ((period - 1) * rma + TrueRange(i)) / period;

            return rma;
        }

        /// <summary>Highest high of the last `period` closed bars — the forming bar is excluded.</summary>
        private double HighestHigh(int period)
        {
            int last = LastClosedIndex;
            double highest = double.MinValue;
            for (int i = last - period + 1; i <= last; i++)
                if (Bars.HighPrices[i] > highest) highest = Bars.HighPrices[i];
            return highest;
        }

        private double LowestLow(int period)
        {
            int last = LastClosedIndex;
            double lowest = double.MaxValue;
            for (int i = last - period + 1; i <= last; i++)
                if (Bars.LowPrices[i] < lowest) lowest = Bars.LowPrices[i];
            return lowest;
        }

        private void EnsureLevels()
        {
            // Keyed on LastClosedIndex, NOT Bars.Count: during OnBarClosed the next
            // forming bar is not in the collection yet, so Count is still what it was
            // on the last tick of the previous bar — a Count-keyed cache would serve
            // levels that are one closed bar stale exactly when the bar-closed path
            // needs the fresh ones.
            if (_levelsBarIndex == LastClosedIndex) return;

            _n = WilderAtr(AtrPeriod);
            _entryHigh = HighestHigh(EntryChannelPeriod);
            _entryLow = LowestLow(EntryChannelPeriod);
            _exitHigh = HighestHigh(ExitChannelPeriod);
            _exitLow = LowestLow(ExitChannelPeriod);
            _levelsBarIndex = LastClosedIndex;
        }

        #endregion

        #region Entries

        private Position[] Units() => Positions.FindAll(BotId, SymbolName);

        private double CalculateMacroEma(int period)
        {
            if (_macroBars == null) return double.NaN;
            int lastClosed = _macroBars.Count - 2; // Last closed macro bar
            if (lastClosed < period) return double.NaN;
            if (lastClosed == _macroEmaLastClosed && period == _macroEmaPeriod)
                return _macroEmaValue;

            double alpha = 2.0 / (period + 1.0);
            double seed = 0.0;
            for (int i = 0; i < period; i++)
                seed += _macroBars.ClosePrices[i];
            double ema = seed / period;

            for (int i = period; i <= lastClosed; i++)
                ema = alpha * _macroBars.ClosePrices[i] + (1.0 - alpha) * ema;

            _macroEmaLastClosed = lastClosed;
            _macroEmaPeriod = period;
            _macroEmaValue = ema;
            return ema;
        }

        private bool MacroTrendBlocks(TradeType side)
        {
            if (!EnableMacroTrendFilter) return false;
            if (_macroBars == null) return false;

            double macroEma = CalculateMacroEma(MacroEmaPeriod);
            if (double.IsNaN(macroEma)) return false;

            int lastMacroIdx = _macroBars.Count - 2;
            if (lastMacroIdx < 0) return false;
            double macroClose = _macroBars.ClosePrices[lastMacroIdx];

            if (side == TradeType.Buy && macroClose < macroEma)
            {
                if (ShowLogs && _skipLoggedBarIndex != Bars.Count)
                {
                    _skipLoggedBarIndex = Bars.Count;
                    Print($"[Snake] Buy breakout skipped: Macro {MacroTrendTimeFrame} Close ({macroClose:F2}) < EMA{MacroEmaPeriod} ({macroEma:F2}).");
                }
                return true;
            }

            if (side == TradeType.Sell && macroClose > macroEma)
            {
                if (ShowLogs && _skipLoggedBarIndex != Bars.Count)
                {
                    _skipLoggedBarIndex = Bars.Count;
                    Print($"[Snake] Sell breakout skipped: Macro {MacroTrendTimeFrame} Close ({macroClose:F2}) > EMA{MacroEmaPeriod} ({macroEma:F2}).");
                }
                return true;
            }

            return false;
        }

        private void TryEvaluateEntry()
        {
            if (!EnableTrading) return;
            if (!HasWarmup) return;
            if (Bars.Count == _lastEntryBarIndex) return;      // one entry per bar, pyramid included

            EnsureLevels();
            if (_n <= 0) return;

            // Re-arm a skipped breakout only after price has come back inside the channel.
            if (_failsafeBlocked.TryGetValue(TradeType.Buy, out bool buyBlocked) && buyBlocked
                && Symbol.Ask <= _entryHigh)
                _failsafeBlocked[TradeType.Buy] = false;
            if (_failsafeBlocked.TryGetValue(TradeType.Sell, out bool sellBlocked) && sellBlocked
                && Symbol.Bid >= _entryLow)
                _failsafeBlocked[TradeType.Sell] = false;

            double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                if (ShowLogs) Print($"[Snake] Spread {spreadPips:F1} pips exceeds the {MaxSpreadPips:F1} pip guard.");
                return;
            }

            var units = Units();
            if (units.Length == 0)
            {
                if (MaxAtrAllowed > 0 && _n > MaxAtrAllowed)
                {
                    if (ShowLogs && _skipLoggedBarIndex != Bars.Count)
                    {
                        _skipLoggedBarIndex = Bars.Count;
                        Print($"[Snake] Entry blocked: ATR N ({_n:F2}) exceeds Max ATR Allowed ({MaxAtrAllowed:F2}).");
                    }
                    return;
                }

                double buffer = EntryBufferPips * Symbol.PipSize;

                if (Symbol.Ask > _entryHigh + buffer)
                {
                    if (DirectionMode == AllowedTradeDirection.ShortOnly) return;
                    if (MacroTrendBlocks(TradeType.Buy)) return;
                    if (FailsafeBlocks(TradeType.Buy)) return;
                    TryOpen(TradeType.Buy, $"breakout above {_entryHigh:F5} ({EntryChannelPeriod} bars)");
                }
                else if (Symbol.Bid < _entryLow - buffer)
                {
                    if (DirectionMode == AllowedTradeDirection.LongOnly) return;
                    if (MacroTrendBlocks(TradeType.Sell)) return;
                    if (FailsafeBlocks(TradeType.Sell)) return;
                    TryOpen(TradeType.Sell, $"breakout below {_entryLow:F5} ({EntryChannelPeriod} bars)");
                }
                return;
            }

            if (units.Length >= MaxUnits) return;

            TradeType side = units[0].TradeType;
            if (units.Any(u => u.TradeType != side)) return;   // never pyramid a hedged book

            double lastFill = side == TradeType.Buy
                ? units.Max(u => u.EntryPrice)
                : units.Min(u => u.EntryPrice);
            double step = PyramidStepN * _n;
            double nextLevel = side == TradeType.Buy ? lastFill + step : lastFill - step;

            if (side == TradeType.Buy ? Symbol.Ask >= nextLevel : Symbol.Bid <= nextLevel)
                TryOpen(side, $"pyramid {units.Length + 1}/{MaxUnits} at {nextLevel:F5} ({PyramidStepN}xN from {lastFill:F5})");
        }

        /// <summary>
        /// System 1's failsafe: the first breakout after a winning group in that direction is
        /// skipped, whatever happens to the ones after it. The skip is an episode: once the
        /// flag is consumed the direction stays blocked until price is back inside the entry
        /// channel. The old flag-only version skipped a single tick and re-entered on the next
        /// one. Logged once per bar so a tick-driven check cannot flood the log.
        /// </summary>
        private bool FailsafeBlocks(TradeType side)
        {
            if (FailsafeMode == FailsafeModeType.Disabled) return false;

            // Already inside the skipped breakout: keep blocking until price is back inside.
            if (_failsafeBlocked.TryGetValue(side, out bool blocked) && blocked) return true;

            if (!_skipNextBreakout.TryGetValue(side, out bool skip) || !skip) return false;
            _skipNextBreakout[side] = false;
            _failsafeBlocked[side] = true;

            if (ShowLogs && _skipLoggedBarIndex != Bars.Count)
            {
                _skipLoggedBarIndex = Bars.Count;
                Print($"[Snake] {side} breakout skipped by Failsafe ({FailsafeMode}, whole episode).");
            }
            return true;
        }

        private void TryOpen(TradeType side, string reason)
        {
            double stopDistance = StopDistanceN * _n;
            double slPips = stopDistance / Symbol.PipSize;

            double units = UnitsForRisk(slPips, out double risk);
            if (units <= 0)
            {
                Print($"[Snake] Entry REFUSED: {side} {SymbolName} at {slPips:F1}p. " +
                      $"The broker's minimum volume already risks more than ${MaxDollarRiskPerUnit:F2}.");
                return;
            }

            // ExecuteMarketOrder takes distances in pips, not prices, and no take profit:
            // Snake's exit is the channel, re-anchored on every closed bar.
            var result = ExecuteMarketOrder(side, SymbolName, units, BotId, slPips, null, BotId);
            if (!result.IsSuccessful)
            {
                Print($"[Snake] Order FAILED: {side} {units / Symbol.LotSize:F2} lots rejected: {result.Error}");
                return;
            }

            _lastEntryBarIndex = Bars.Count;
            _unitN[result.Position.Id] = _n;
            if (!_groupPnl.ContainsKey(side)) _groupPnl[side] = 0.0;

            int currentUnits = Units().Length;
            _groupUnitsCount[side] = Math.Max(_groupUnitsCount.TryGetValue(side, out int curMax) ? curMax : 0, currentUnits);

            Print($"[Snake] {side} {units / Symbol.LotSize:F2} lots #{result.Position.Id} at " +
                  $"{result.Position.EntryPrice:F5} | N {_n:F5} | stop {stopDistance / Symbol.PipSize:F1}p " +
                  $"(risk ${risk:F2}) | {reason}");

            ReportPositionOpen(result.Position, slPips, reason);
        }

        /// <summary>
        /// 1 unit risks RiskPerUnitPercent of equity, capped at MaxDollarRiskPerUnit. Returns 0
        /// when the broker minimum would break the dollar cap — refusing beats silently sizing
        /// a "1% unit" up to 3% on a small account.
        /// </summary>
        private double UnitsForRisk(double slPips, out double risk)
        {
            risk = 0.0;
            double equityRisk = Account.Equity * (RiskPerUnitPercent / 100.0);
            double budget = Math.Min(equityRisk, MaxDollarRiskPerUnit);
            double pipValue = Symbol.PipValue > 0 ? Symbol.PipValue : 1.0;
            if (slPips <= 0 || budget <= 0) return 0.0;

            double units = Symbol.NormalizeVolumeInUnits(budget / (slPips * pipValue), RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin) units = Symbol.VolumeInUnitsMin;
            if (units > Symbol.VolumeInUnitsMax) units = Symbol.VolumeInUnitsMax;

            risk = units * slPips * pipValue;
            return risk > MaxDollarRiskPerUnit ? 0.0 : units;
        }

        #endregion

        #region Exits

        /// <summary>
        /// The position's stop is the tighter of its own 2N stop and the opposite channel.
        /// Re-anchoring happens on closed bars only, and the stop is never widened: if a gap
        /// moves the channel against us, the old level stands.
        /// </summary>
        private void UpdateStops()
        {
            if (!HasWarmup) return;
            EnsureLevels();
            if (_n <= 0) return;

            foreach (var pos in Units())
            {
                double unitN = _unitN.TryGetValue(pos.Id, out double remembered) && remembered > 0
                    ? remembered
                    : _n;
                double initialStop = pos.TradeType == TradeType.Buy
                    ? pos.EntryPrice - StopDistanceN * unitN
                    : pos.EntryPrice + StopDistanceN * unitN;
                double channelStop = pos.TradeType == TradeType.Buy ? _exitLow : _exitHigh;
                double target = pos.TradeType == TradeType.Buy
                    ? Math.Max(initialStop, channelStop)
                    : Math.Min(initialStop, channelStop);

                double halfTick = Symbol.TickSize / 2.0;
                double? current = pos.StopLoss;
                bool tighter = !current.HasValue
                    || (pos.TradeType == TradeType.Buy
                        ? target > current.Value + halfTick
                        : target < current.Value - halfTick);
                if (!tighter) continue;

                var result = pos.ModifyStopLossPrice(target);
                if (!result.IsSuccessful)
                    Print($"[Snake] Could not move stop #{pos.Id} to {target:F5}: {result.Error}");
                else if (ShowLogs)
                    Print($"[Snake] Stop #{pos.Id} -> {target:F5} ({pos.TradeType} {ExitChannelPeriod}-bar channel)");
            }
        }

        /// <summary>
        /// Positions.Closed does not say whether the group is over, so the failsafe is fed
        /// per unit and only its total decides the next signal: the units of one entry share
        /// the exit level, and a pyramid's later units ride a worse fill.
        /// </summary>
        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            Position pos = args.Position;
            if (pos.SymbolName != SymbolName || pos.Label != BotId) return;

            try
            {
                double pnl = pos.NetProfit;
                _unitN.Remove(pos.Id);
                _groupPnl[pos.TradeType] = _groupPnl.TryGetValue(pos.TradeType, out double running)
                    ? running + pnl
                    : pnl;

                TradeType side = pos.TradeType;
                bool groupOver = Units().All(u => u.TradeType != side);
                if (groupOver)
                {
                    bool isWin = _groupPnl[side] > 0;
                    int maxUnitsReached = _groupUnitsCount.TryGetValue(side, out int uCount) ? uCount : 1;

                    _failsafeBlocked[side] = false;
                    if (FailsafeMode == FailsafeModeType.Classic)
                    {
                        _skipNextBreakout[side] = isWin;
                    }
                    else if (FailsafeMode == FailsafeModeType.OnlyMajorWinners)
                    {
                        _skipNextBreakout[side] = isWin && (maxUnitsReached >= 3);
                    }
                    else
                    {
                        _skipNextBreakout[side] = false;
                    }

                    if (ShowLogs)
                        Print($"[Snake] {side} group closed for ${_groupPnl[side]:F2} ({maxUnitsReached} units) — next {side} breakout " +
                              $"{(_skipNextBreakout[side] ? "will be skipped (failsafe)" : "will be taken")}.");

                    _groupPnl[side] = 0.0;
                    _groupUnitsCount[side] = 0;
                }

                ReportPositionClosed(pos, pnl, DescribeClose(args.Reason));
            }
            catch (Exception ex)
            {
                Print($"[Snake] OnPositionClosed error: {ex.Message}");
            }
        }

        private static string DescribeClose(PositionCloseReason reason)
        {
            switch (reason)
            {
                case PositionCloseReason.StopLoss: return "Channel / 2N stop";
                case PositionCloseReason.TakeProfit: return "Take profit";
                case PositionCloseReason.StopOut: return "Stop out";
                default: return "Closed";
            }
        }

        /// <summary>
        /// Best-effort recovery of the failsafe after a restart: History keeps the deals, but
        /// it does not say where one group ended and the next began, and a group that stopped
        /// out can lose on later units while its first unit wins. The sign of the last closed
        /// deal per direction is therefore the seed, wrong only for a restart mid-group, and
        /// then only by one skipped or taken signal.
        /// </summary>
        private void SeedFailsafeFromHistory()
        {
            if (_failsafeSeeded) return;
            _failsafeSeeded = true;
            try
            {
                var deals = History
                    .Where(h => h.SymbolName == SymbolName && h.Label == BotId)
                    .OrderBy(h => h.ClosingTime)
                    .ToList();
                foreach (var deal in deals)
                    _skipNextBreakout[deal.TradeType] = deal.NetProfit > 0;
                _failsafeBlocked[TradeType.Buy] = false;
                _failsafeBlocked[TradeType.Sell] = false;

                if (deals.Count > 0 && ShowLogs)
                    Print($"[Snake] Failsafe seeded from {deals.Count} historical deal(s).");
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Snake] Failsafe seed skipped: {ex.Message}");
            }
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
                if (ShowLogs) Print($"[Snake] Account sync failed: {ex.Message}");
            }
        }

        private void ReportPositionOpen(Position position, double slPips, string reason)
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
                    tp_pips = 0.0,
                    reason = reason,
                    entry_indicators = JsonSerializer.Serialize(new
                    {
                        n = Math.Round(_n, 5),
                        entry_channel = EntryChannelPeriod,
                        exit_channel = ExitChannelPeriod,
                        units_open = Units().Length,
                        spread = Math.Round(Symbol.Spread / Symbol.PipSize, 1),
                        system_preset = SystemPreset.ToString(),
                        macro_filter = EnableMacroTrendFilter,
                        direction_mode = DirectionMode.ToString(),
                        failsafe_mode = FailsafeMode.ToString()
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
                int pid = position.Id;
                Task.Run(async () =>
                {
                    string error = await PostReportAsync(url, json);
                    // Print là API cTrader — chỉ được gọi từ main thread; gọi từ
                    // continuation Task.Run crash cả cBot instance ("Unable to
                    // invoke target method in current thread", audit 11/10/2026).
                    if (error != null)
                        BeginInvokeOnMainThread(
                            () => Print($"[Snake] Failed to report open #{pid}: {error}"));
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Snake] ReportPositionOpen error: {ex.Message}");
            }
        }

        private void ReportPositionClosed(Position position, double pnl, string reason)
        {
            if (RunningMode != RunningMode.RealTime) return;
            try
            {
                // The spread right now is not the price the position closed at: a stop swept
                // by a spike would be booked at a price that never traded.
                double exitPrice = position.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                if (position.StopLoss.HasValue && position.TradeType == TradeType.Buy && Symbol.Bid <= position.StopLoss.Value)
                    exitPrice = position.StopLoss.Value;
                else if (position.StopLoss.HasValue && position.TradeType == TradeType.Sell && Symbol.Ask >= position.StopLoss.Value)
                    exitPrice = position.StopLoss.Value;

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
                    pnl = pnl,
                    pips = Math.Round(pnl / (position.VolumeInUnits * Symbol.PipValue), 1),
                    reason = reason,
                    entry_time = position.EntryTime.ToUniversalTime().ToString("o"),
                    exit_time = Server.Time.ToUniversalTime().ToString("o"),
                    account_number = Account.Number.ToString(CultureInfo.InvariantCulture),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = AccountLabel,
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                string json = JsonSerializer.Serialize(report);
                string url = ReportUrl();
                int pid = position.Id;
                Task.Run(async () =>
                {
                    string error = await PostReportAsync(url, json);
                    // Print là API cTrader — chỉ được gọi từ main thread (audit 11/10).
                    if (error != null)
                        BeginInvokeOnMainThread(
                            () => Print($"[Snake] Failed to report close #{pid}: {error}"));
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Snake] ReportPositionClosed error: {ex.Message}");
            }
        }

        // /portfolio/report is the server's only record of a trade, so transport errors and
        // non-2xx answers are retried; the server dedupes on ctrader_id.
        private static readonly int[] ReportRetryDelaysSeconds = { 2, 4, 8, 16 };

        /// <summary>Base URL của hub: ReportUrl() bỏ đuôi /portfolio/report.</summary>
        private string HubBaseUrl()
        {
            string url = ReportUrl();
            int cut = url.IndexOf("/portfolio/report", StringComparison.OrdinalIgnoreCase);
            if (cut >= 0) url = url.Substring(0, cut);
            return url.TrimEnd('/');
        }

        /// <summary>Heartbeat mỗi HeartbeatIntervalSeconds (audit 14/10) —
        /// POST /api/bots/{id}/heartbeat {bot_id, timestamp, status}.
        /// Fire-and-forget, KHÔNG retry, KHÔNG bao giờ throw: hub sập không
        /// được giết bot (P4). Khi bot treo (tick chết, feed đứng) heartbeat
        /// cũng đứng theo → watchdog bắt được verdict "hung" thay vì chỉ dựa
        /// PID sống.</summary>
        private void SendHeartbeat()
        {
            if (RunningMode != RunningMode.RealTime) return;
            try
            {
                var payload = new
                {
                    bot_id = BotId,
                    timestamp = (double)DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                    status = "ok",
                    detail = (string)null
                };
                string json = JsonSerializer.Serialize(payload);
                string url = HubBaseUrl() + "/api/bots/"
                             + Uri.EscapeDataString(BotId ?? "default") + "/heartbeat";
                Task.Run(async () =>
                {
                    try
                    {
                        using (var content = new StringContent(json, Encoding.UTF8, "application/json"))
                        using (var response = await _httpClient.PostAsync(url, content))
                        {
                            // 404 = hub chưa biết bot (chưa deploy trên dashboard) — bỏ qua.
                        }
                    }
                    catch
                    {
                        // P4: heartbeat là kênh phụ, mất kết nối không được throw.
                    }
                });
            }
            catch
            {
                // Serialize/EscapeDataString fail cũng im lặng — không giết bot.
            }
        }

        private async Task<string> PostReportAsync(string url, string json)
        {
            // Không đọc RunningMode ở đây: method chạy trên thread nền và
            // Robot property là API cTrader — caller đã chặn non-RealTime
            // trước khi spawn Task.Run (audit 11/10/2026).
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
