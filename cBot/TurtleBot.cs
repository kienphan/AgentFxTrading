// Turtle Trading (Richard Dennis & William Eckhardt, 1983) as a single-instrument cBot.
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
//             direction — one signal, not a permanent lock (EnableFailsafeFilter). The
//             original rule, and the reason S1 does not simply re-enter every 20-bar high.
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
// See docs/turtle.md for the XAUUSD recipe.

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
    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class TurtleBot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "Turtle")]
        public string BotId { get; set; }

        [Parameter("Account Identifier", Group = "General", DefaultValue = "Turtle-Standard")]
        public string AccountLabel { get; set; }

        [Parameter("Show Detailed Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        [Parameter("Enable Trading", Group = "General", DefaultValue = true)]
        public bool EnableTrading { get; set; }

        [Parameter("Entry Channel (bars, S1=20 / S2=55)", Group = "Turtle System", DefaultValue = 20, MinValue = 5, MaxValue = 200)]
        public int EntryChannelPeriod { get; set; }

        [Parameter("Exit Channel (bars, S1=10 / S2=20)", Group = "Turtle System", DefaultValue = 10, MinValue = 3, MaxValue = 200)]
        public int ExitChannelPeriod { get; set; }

        [Parameter("System 1 Failsafe Filter", Group = "Turtle System", DefaultValue = true)]
        public bool EnableFailsafeFilter { get; set; }

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

        [Parameter("Max Spread (pips)", Group = "Execution Guard", DefaultValue = 50.0, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Entry Buffer (pips)", Group = "Execution Guard", DefaultValue = 0.0, MinValue = 0.0)]
        public double EntryBufferPips { get; set; }

        [Parameter("Agent API URL", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/trade")]
        public string ApiUrl { get; set; }

        [Parameter("Portfolio Report Endpoint", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/portfolio/report")]
        public string AiReportUrl { get; set; }

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
        private readonly Dictionary<TradeType, double> _groupPnl = new Dictionary<TradeType, double>();

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
            Print($"[Turtle] Initializing cBot '{BotId}' on {SymbolName} ({TimeFrame}) | " +
                  $"Entry {EntryChannelPeriod} / Exit {ExitChannelPeriod} / N {AtrPeriod} bars");

            Positions.Closed += OnPositionClosed;

            SeedFailsafeFromHistory();
            SendAccountSync();
        }

        protected override void OnTick()
        {
            TryEvaluateEntry();
        }

        protected override void OnBarClosed()
        {
            // Levels for the new bar, the trailing channel stop, then the entry check (a bar
            // can break a channel on the first tick after the close without another tick loop).
            UpdateStops();
            TryEvaluateEntry();
        }

        #endregion

        #region Turtle maths

        /// <summary>The bar index of the last CLOSED bar (Count-1 is the one still forming).</summary>
        private int LastClosedIndex => Bars.Count - 2;

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
            if (_levelsBarIndex == Bars.Count) return;

            _n = WilderAtr(AtrPeriod);
            _entryHigh = HighestHigh(EntryChannelPeriod);
            _entryLow = LowestLow(EntryChannelPeriod);
            _exitHigh = HighestHigh(ExitChannelPeriod);
            _exitLow = LowestLow(ExitChannelPeriod);
            _levelsBarIndex = Bars.Count;
        }

        #endregion

        #region Entries

        private Position[] Units() => Positions.FindAll(BotId, SymbolName);

        private void TryEvaluateEntry()
        {
            if (!EnableTrading) return;
            if (!HasWarmup) return;
            if (Bars.Count == _lastEntryBarIndex) return;      // one entry per bar, pyramid included

            EnsureLevels();
            if (_n <= 0) return;

            double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                if (ShowLogs) Print($"[Turtle] Spread {spreadPips:F1} pips exceeds the {MaxSpreadPips:F1} pip guard.");
                return;
            }

            var units = Units();
            if (units.Length == 0)
            {
                double buffer = EntryBufferPips * Symbol.PipSize;

                if (Symbol.Ask > _entryHigh + buffer)
                {
                    if (FailsafeBlocks(TradeType.Buy)) return;
                    TryOpen(TradeType.Buy, $"breakout above {_entryHigh:F5} ({EntryChannelPeriod} bars)");
                }
                else if (Symbol.Bid < _entryLow - buffer)
                {
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
        /// skipped, whatever happens to the ones after it. The flag is consumed here, so the
        /// skip costs exactly one signal. Logged once per bar so a tick-driven check cannot
        /// flood the log.
        /// </summary>
        private bool FailsafeBlocks(TradeType side)
        {
            if (!EnableFailsafeFilter) return false;
            if (!_skipNextBreakout.TryGetValue(side, out bool skip) || !skip) return false;
            _skipNextBreakout[side] = false;

            if (ShowLogs && _skipLoggedBarIndex != Bars.Count)
            {
                _skipLoggedBarIndex = Bars.Count;
                Print($"[Turtle] {side} breakout skipped: the last {side} group was a winner (System 1 failsafe, one signal).");
            }
            return true;
        }

        private void TryOpen(TradeType side, string reason)
        {
            double entryRef = side == TradeType.Buy ? Symbol.Ask : Symbol.Bid;
            double stopDistance = StopDistanceN * _n;
            double slPips = stopDistance / Symbol.PipSize;

            double units = UnitsForRisk(slPips, out double risk);
            if (units <= 0)
            {
                Print($"[Turtle] Entry REFUSED: {side} {SymbolName} at {slPips:F1}p. " +
                      $"The broker's minimum volume already risks more than ${MaxDollarRiskPerUnit:F2}.");
                return;
            }

            // ExecuteMarketOrder takes distances in pips, not prices, and no take profit:
            // Turtle's exit is the channel, re-anchored on every closed bar.
            var result = ExecuteMarketOrder(side, SymbolName, units, BotId, slPips, null, BotId);
            if (!result.IsSuccessful)
            {
                Print($"[Turtle] Order FAILED: {side} {units / Symbol.LotSize:F2} lots rejected: {result.Error}");
                return;
            }

            _lastEntryBarIndex = Bars.Count;
            _unitN[result.Position.Id] = _n;
            if (!_groupPnl.ContainsKey(side)) _groupPnl[side] = 0.0;

            Print($"[Turtle] {side} {units / Symbol.LotSize:F2} lots #{result.Position.Id} at " +
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

            double units = Symbol.NormalizeVolumeInUnits(budget / (slPips * pipValue));
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
                    Print($"[Turtle] Could not move stop #{pos.Id} to {target:F5}: {result.Error}");
                else if (ShowLogs)
                    Print($"[Turtle] Stop #{pos.Id} -> {target:F5} ({pos.TradeType} {ExitChannelPeriod}-bar channel)");
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
                    _skipNextBreakout[side] = _groupPnl[side] > 0;
                    if (ShowLogs)
                        Print($"[Turtle] {side} group closed for ${_groupPnl[side]:F2} — next {side} breakout " +
                              $"{(_skipNextBreakout[side] ? "will be skipped (failsafe)" : "will be taken")}.");
                    _groupPnl[side] = 0.0;
                }

                ReportPositionClosed(pos, pnl, DescribeClose(args.Reason));
            }
            catch (Exception ex)
            {
                Print($"[Turtle] OnPositionClosed error: {ex.Message}");
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

                if (deals.Count > 0 && ShowLogs)
                    Print($"[Turtle] Failsafe seeded from {deals.Count} historical deal(s).");
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Turtle] Failsafe seed skipped: {ex.Message}");
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
                if (ShowLogs) Print($"[Turtle] Account sync failed: {ex.Message}");
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
                        spread = Math.Round(Symbol.Spread / Symbol.PipSize, 1)
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
                    if (error != null) Print($"[Turtle] Failed to report open #{position.Id}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Turtle] ReportPositionOpen error: {ex.Message}");
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
                Task.Run(async () =>
                {
                    string error = await PostReportAsync(url, json);
                    if (error != null) Print($"[Turtle] Failed to report close #{position.Id}: {error}");
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Turtle] ReportPositionClosed error: {ex.Message}");
            }
        }

        // /portfolio/report is the server's only record of a trade, so transport errors and
        // non-2xx answers are retried; the server dedupes on ctrader_id.
        private static readonly int[] ReportRetryDelaysSeconds = { 2, 4, 8, 16 };

        private async Task<string> PostReportAsync(string url, string json)
        {
            if (RunningMode != RunningMode.RealTime) return null;
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
