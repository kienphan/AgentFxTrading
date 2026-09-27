using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Text.Json;
using System.Text.Json.Serialization;
using cAlgo.API;

namespace cAlgo.Robots
{
    // Studies closed D1, H4 and H1 candles, then trades only on a closed M15 bar.
    // The server's risk gate must approve the prices. This robot never trades a live account.
    [Robot(AccessRights = AccessRights.FullAccess, TimeZone = TimeZones.UTC)]
    public class MtfChartBot : Robot
    {
        [Parameter("Bot ID", Group = "API", DefaultValue = "mtf-m15")]
        public string BotId { get; set; }

        [Parameter("Agent API URL", Group = "API", DefaultValue = "http://127.0.0.1:8000/trade/mtf")]
        public string ApiUrl { get; set; }

        [Parameter("D1 bars", Group = "Candles", DefaultValue = 100, MinValue = 20)]
        public int D1Bars { get; set; }

        [Parameter("H4 bars", Group = "Candles", DefaultValue = 150, MinValue = 20)]
        public int H4Bars { get; set; }

        [Parameter("H1 bars", Group = "Candles", DefaultValue = 200, MinValue = 20)]
        public int H1Bars { get; set; }

        [Parameter("M15 bars", Group = "Candles", DefaultValue = 250, MinValue = 20)]
        public int M15Bars { get; set; }

        [Parameter("Risk %", Group = "Risk", DefaultValue = 0.5, MinValue = 0.01, MaxValue = 0.5)]
        public double RiskPercent { get; set; }

        [Parameter("Min RR", Group = "Risk", DefaultValue = 2.0, MinValue = 2.0)]
        public double MinRr { get; set; }

        [Parameter("Max spread (pips)", Group = "Risk", DefaultValue = 40.0, MinValue = 0.1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max daily loss", Group = "Risk", DefaultValue = 500.0, MinValue = 1)]
        public double MaxDailyLoss { get; set; }

        [Parameter("Pending expiry (M15 bars)", Group = "Risk", DefaultValue = 8, MinValue = 1)]
        public int PendingMaxM15Bars { get; set; }

        private HttpClient _http;
        private Bars _d1;
        private Bars _h4;
        private Bars _h1;
        private bool _busy;
        private readonly HashSet<int> _closedByAgent = new HashSet<int>();

        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute15)
            {
                Print("MtfChartBot trades only on an M15 chart. Stop.");
                Stop();
                return;
            }
            if (Account.IsLive)
            {
                Print("Live account refused. Attach this cBot to a demo account.");
                Stop();
                return;
            }

            _http = new HttpClient { Timeout = TimeSpan.FromSeconds(45) };
            _d1 = MarketData.GetBars(TimeFrame.Daily);
            _h4 = MarketData.GetBars(TimeFrame.Hour4);
            _h1 = MarketData.GetBars(TimeFrame.Hour);
            Positions.Opened += OnPositionOpened;
            Positions.Closed += OnPositionClosed;
            Print($"MtfChartBot started on {SymbolName} M15. Decisions go to {ApiUrl}.");
        }

        protected override void OnBarClosed()
        {
            if (_busy || _http == null)
                return;
            if (Account.IsLive)
            {
                Print("Live account refused.");
                return;
            }

            _busy = true;
            try
            {
                ExpireStalePending();
                var payload = BuildPayload();
                var decision = RequestDecision(payload);
                if (decision == null || !decision.Approved)
                {
                    Print($"WAIT. {(decision == null ? "No decision." : decision.GateReason)}");
                    return;
                }
                ExecuteApproved(decision);
            }
            catch (Exception ex)
            {
                Print($"M15 decision failed closed: {ex.Message}");
            }
            finally
            {
                _busy = false;
            }
        }

        protected override void OnStop()
        {
            _http?.Dispose();
        }

        private Dictionary<string, object> BuildPayload()
        {
            double spreadPips = Symbol.PipSize > 0 ? Symbol.Spread / Symbol.PipSize : 0;
            return new Dictionary<string, object>
            {
                ["symbol"] = SymbolName,
                ["bot_id"] = BotId,
                ["bid"] = Symbol.Bid,
                ["ask"] = Symbol.Ask,
                ["spread_pips"] = spreadPips,
                ["max_spread_pips"] = MaxSpreadPips,
                ["min_rr"] = MinRr,
                ["pip_size"] = Symbol.PipSize,
                ["has_position"] = OwnPositions().Length > 0,
                ["has_pending"] = OwnPending().Length > 0,
                ["position"] = PositionState(),
                ["pending"] = PendingState(),
                ["is_live"] = Account.IsLive,
                ["daily_pnl"] = DailyClosedPnl(),
                ["max_daily_loss"] = MaxDailyLoss,
                ["d1"] = ClosedBars(_d1, D1Bars),
                ["h4"] = ClosedBars(_h4, H4Bars),
                ["h1"] = ClosedBars(_h1, H1Bars),
                ["m15"] = ClosedBars(Bars, M15Bars),
            };
        }

        // The last index of a cTrader series is the bar that is still forming.
        private static List<Dictionary<string, object>> ClosedBars(Bars series, int count)
        {
            var rows = new List<Dictionary<string, object>>();
            if (series == null || series.Count < 2)
                return rows;
            int lastClosed = series.Count - 2;
            int start = Math.Max(0, lastClosed - count + 1);
            for (int i = start; i <= lastClosed; i++)
            {
                var bar = series[i];
                rows.Add(new Dictionary<string, object>
                {
                    ["t"] = bar.OpenTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
                    ["o"] = bar.Open,
                    ["h"] = bar.High,
                    ["l"] = bar.Low,
                    ["c"] = bar.Close,
                });
            }
            return rows;
        }

        private double DailyClosedPnl()
        {
            double total = 0;
            var day = Server.Time.Date;
            foreach (var trade in History)
            {
                if (trade.SymbolName == SymbolName && trade.ClosingTime.Date == day)
                    total += trade.NetProfit;
            }
            return total;
        }

        private MtfDecision RequestDecision(Dictionary<string, object> payload)
        {
            string json = JsonSerializer.Serialize(payload);
            var content = new StringContent(json, Encoding.UTF8, "application/json");
            HttpResponseMessage response;
            try
            {
                response = _http.PostAsync(ApiUrl, content).GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                Print($"AI request failed, no trade: {ex.Message}");
                return null;
            }
            string body = response.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            if (!response.IsSuccessStatusCode)
            {
                Print($"AI HTTP {(int)response.StatusCode}, no trade: {body}");
                return null;
            }
            return JsonSerializer.Deserialize<MtfDecision>(body, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
            });
        }

        private Position[] OwnPositions()
        {
            return Positions.FindAll(BotId, SymbolName);
        }

        private PendingOrder[] OwnPending()
        {
            var found = new List<PendingOrder>();
            foreach (var order in PendingOrders)
            {
                if (order.Label == BotId && order.SymbolName == SymbolName)
                    found.Add(order);
            }
            return found.ToArray();
        }

        private void ExpireStalePending()
        {
            foreach (var order in OwnPending())
            {
                int barsAlive = (int)((Server.Time - order.SubmittedTime).TotalMinutes / 15.0);
                if (barsAlive >= PendingMaxM15Bars)
                {
                    CancelPendingOrder(order);
                    JournalEvent("expiry", order.Comment, new Dictionary<string, object> { ["bars_alive"] = barsAlive });
                    Print($"Pending expired after {barsAlive} M15 bars.");
                }
            }
        }

        private Dictionary<string, object> PositionState()
        {
            var open = OwnPositions();
            if (open.Length == 0)
                return null;
            var pos = open[0];
            return new Dictionary<string, object>
            {
                ["side"] = pos.TradeType.ToString().ToUpperInvariant(),
                ["entry"] = pos.EntryPrice,
                ["stop_loss"] = pos.StopLoss ?? 0,
                ["take_profit"] = pos.TakeProfit ?? 0,
                ["pnl"] = pos.NetProfit,
            };
        }

        private Dictionary<string, object> PendingState()
        {
            var open = OwnPending();
            if (open.Length == 0)
                return null;
            var order = open[0];
            int barsAlive = (int)((Server.Time - order.SubmittedTime).TotalMinutes / 15.0);
            return new Dictionary<string, object>
            {
                ["side"] = order.TradeType.ToString().ToUpperInvariant(),
                ["order_type"] = order.OrderType.ToString().ToUpperInvariant(),
                ["entry"] = order.TargetPrice,
                ["stop_loss"] = order.StopLoss ?? 0,
                ["take_profit"] = order.TakeProfit ?? 0,
                ["bars_alive"] = barsAlive,
            };
        }

        private void ExecuteApproved(MtfDecision decision)
        {
            if (Account.IsLive)
            {
                Print("Live account refused.");
                return;
            }

            string action = decision.Decision == null ? "WAIT" : decision.Decision.Action ?? "WAIT";
            if (action == "CLOSE_POSITION")
            {
                foreach (var pos in OwnPositions())
                {
                    _closedByAgent.Add(pos.Id);
                    ClosePosition(pos);
                }
                Print("Position closed.");
                return;
            }
            if (action == "CANCEL_PENDING_ORDER")
            {
                foreach (var order in OwnPending())
                {
                    CancelPendingOrder(order);
                    JournalEvent("cancel", order.Comment, new Dictionary<string, object> { ["by"] = "agent" });
                }
                Print("Pending order cancelled.");
                return;
            }
            if (action == "HOLD_POSITION" || action == "WAIT")
            {
                Print(action);
                return;
            }
            if (OwnPositions().Length > 0 || OwnPending().Length > 0)
            {
                Print("Exposure already exists. No second order.");
                return;
            }

            bool buy = action.StartsWith("BUY");
            var side = buy ? TradeType.Buy : TradeType.Sell;
            double entry = decision.Decision.Entry;
            double slPips = Math.Abs(entry - decision.Decision.StopLoss) / Symbol.PipSize;
            double tpPips = Math.Abs(decision.Decision.TakeProfit - entry) / Symbol.PipSize;
            if (slPips <= 0 || tpPips <= 0 || tpPips / slPips < MinRr)
            {
                Print("Stop, target or reward is not acceptable. No trade.");
                return;
            }

            double volume = VolumeForRisk(slPips);
            if (volume <= 0)
                return;

            // The server journaled the thesis under this id before answering; the order comment
            // carries it so the fill and the close land on the same journal record.
            string clientId = decision.ClientOrderId ?? "";
            TradeResult result;
            if (action.EndsWith("LIMIT"))
                result = PlaceLimitOrder(side, SymbolName, volume, entry, BotId, slPips, tpPips, null, clientId);
            else if (action.EndsWith("STOP"))
                result = PlaceStopOrder(side, SymbolName, volume, entry, BotId, slPips, tpPips, null, clientId);
            else if (action.EndsWith("MARKET"))
                result = ExecuteMarketOrder(side, SymbolName, volume, BotId, slPips, tpPips, clientId);
            else
            {
                Print($"Unhandled action {action}. No trade.");
                return;
            }

            if (!result.IsSuccessful)
            {
                Print($"Order rejected: {result.Error}");
                JournalEvent("error", clientId, new Dictionary<string, object>
                {
                    ["error"] = result.Error?.ToString() ?? "unknown",
                    ["action"] = action,
                    ["volume_units"] = volume,
                });
                return;
            }
            Print($"{action} sent. Entry {entry}, SL {slPips:F1} pips, TP {tpPips:F1} pips.");
        }

        private double VolumeForRisk(double slPips)
        {
            double pipValue = Symbol.PipValue > 0 ? Symbol.PipValue : 0;
            if (pipValue <= 0)
            {
                Print("PipValue is missing. No trade.");
                return 0;
            }
            double riskAmount = Account.Equity * (RiskPercent / 100.0);
            double rawUnits = riskAmount / (slPips * pipValue);
            double volume = Symbol.NormalizeVolumeInUnits(rawUnits, RoundingMode.Down);
            if (volume < Symbol.VolumeInUnitsMin)
            {
                Print($"Volume {volume} is below the broker minimum. Not rounding up.");
                return 0;
            }
            if (volume > Symbol.VolumeInUnitsMax)
                volume = Symbol.NormalizeVolumeInUnits(Symbol.VolumeInUnitsMax, RoundingMode.Down);

            double actualRisk = volume * slPips * pipValue;
            if (actualRisk > riskAmount + 0.01)
            {
                Print($"Sized risk {actualRisk:F2} exceeds {riskAmount:F2}. No trade.");
                return 0;
            }
            return volume;
        }

        private void OnPositionOpened(PositionOpenedEventArgs args)
        {
            var pos = args.Position;
            if (pos.SymbolName != SymbolName || args.Position.Label != BotId)
                return;
            double slPips = pos.StopLoss.HasValue ? Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / Symbol.PipSize : 0;
            double tpPips = pos.TakeProfit.HasValue ? Math.Abs(pos.TakeProfit.Value - pos.EntryPrice) / Symbol.PipSize : 0;
            PostAsync("/portfolio/report", new
            {
                ctrader_id = pos.Id,
                bot_id = BotId,
                action = "open",
                symbol = SymbolName,
                side = pos.TradeType.ToString(),
                volume = pos.VolumeInUnits / Symbol.LotSize,
                entry_price = pos.EntryPrice,
                sl_price = pos.StopLoss,
                tp_price = pos.TakeProfit,
                sl_pips = Math.Round(slPips, 1),
                tp_pips = Math.Round(tpPips, 1),
                account_number = Account.Number.ToString(),
                account_type = Account.IsLive ? "live" : "demo",
                account_balance = Account.Balance,
                account_equity = Account.Equity,
            });
            JournalEvent("fill", pos.Comment, new Dictionary<string, object>
            {
                ["position_id"] = pos.Id,
                ["side"] = pos.TradeType.ToString().ToUpperInvariant(),
                ["fill_entry"] = pos.EntryPrice,
                ["stop"] = pos.StopLoss,
                ["take_profit"] = pos.TakeProfit,
                ["volume_units"] = pos.VolumeInUnits,
                ["risk_pct"] = RiskPercent,
                ["balance"] = Account.Balance,
            });
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var pos = args.Position;
            if (pos.SymbolName != SymbolName || args.Position.Label != BotId)
                return;
            bool byAgent = _closedByAgent.Remove(pos.Id);
            double? exit = FinalClosingPrice(pos);
            string reason = args.Reason == PositionCloseReason.TakeProfit ? "take_profit"
                : args.Reason == PositionCloseReason.StopLoss ? "stop_loss"
                : args.Reason == PositionCloseReason.StopOut ? "margin_close"
                : "manual";
            PostAsync("/portfolio/report", new
            {
                ctrader_id = pos.Id,
                bot_id = BotId,
                action = "close",
                symbol = SymbolName,
                exit_price = exit ?? 0,
                pnl = pos.NetProfit,
                close_reason = byAgent ? "Agent CLOSE_POSITION" : args.Reason.ToString(),
                sl_price = pos.StopLoss,
                tp_price = pos.TakeProfit,
                account_number = Account.Number.ToString(),
                account_type = Account.IsLive ? "live" : "demo",
                account_balance = Account.Balance,
                account_equity = Account.Equity,
            });
            JournalEvent("close", pos.Comment, new Dictionary<string, object>
            {
                ["position_id"] = pos.Id,
                ["fill_entry"] = pos.EntryPrice,
                ["fill_exit"] = exit,
                ["stop"] = pos.StopLoss,
                ["take_profit"] = pos.TakeProfit,
                ["volume_units"] = pos.VolumeInUnits,
                ["gross"] = pos.GrossProfit,
                ["commission"] = pos.Commissions,
                ["swap"] = pos.Swap,
                ["net"] = pos.NetProfit,
                ["exit_reason"] = reason,
                ["closed_by"] = byAgent ? "agent" : (reason == "manual" ? "user" : "broker"),
            });
        }

        // The deal carries the real exit price; the position only knows its levels.
        private double? FinalClosingPrice(Position pos)
        {
            try
            {
                var deal = History
                    .Where(h => h.PositionId == pos.Id)
                    .OrderByDescending(h => h.ClosingTime)
                    .FirstOrDefault();
                if (deal != null && deal.ClosingPrice > 0)
                    return deal.ClosingPrice;
            }
            catch { }
            return null;
        }

        private void JournalEvent(string name, string clientId, Dictionary<string, object> fields)
        {
            var body = new Dictionary<string, object>(fields)
            {
                ["event"] = name,
                ["client_order_id"] = clientId ?? "",
                ["bot_id"] = BotId,
                ["symbol"] = SymbolName,
                ["account_type"] = Account.IsLive ? "live" : "demo",
            };
            PostAsync("/trade/mtf/event", body);
        }

        // Reporting never blocks or breaks trading: it runs off the main thread and only logs a failure.
        private void PostAsync(string path, object body)
        {
            if (_http == null)
                return;
            string url = Endpoint(path);
            string json = JsonSerializer.Serialize(body);
            Task.Run(async () =>
            {
                try
                {
                    var content = new StringContent(json, Encoding.UTF8, "application/json");
                    var response = await _http.PostAsync(url, content);
                    if (!response.IsSuccessStatusCode)
                        BeginInvokeOnMainThread(() => Print($"Report to {path} failed: HTTP {(int)response.StatusCode}"));
                }
                catch (Exception ex)
                {
                    BeginInvokeOnMainThread(() => Print($"Report to {path} failed: {ex.Message}"));
                }
            });
        }

        private string Endpoint(string path)
        {
            int cut = ApiUrl.IndexOf("/trade/mtf", StringComparison.Ordinal);
            string root = cut >= 0 ? ApiUrl.Substring(0, cut) : ApiUrl.TrimEnd('/');
            return root + path;
        }

        private class MtfDecision
        {
            [JsonPropertyName("approved")]
            public bool Approved { get; set; }

            [JsonPropertyName("client_order_id")]
            public string ClientOrderId { get; set; }

            [JsonPropertyName("gate_reason")]
            public string GateReason { get; set; }

            [JsonPropertyName("decision")]
            public DecisionBody Decision { get; set; }
        }

        private class DecisionBody
        {
            [JsonPropertyName("action")]
            public string Action { get; set; }

            [JsonPropertyName("entry")]
            public double Entry { get; set; }

            [JsonPropertyName("stop_loss")]
            public double StopLoss { get; set; }

            [JsonPropertyName("take_profit")]
            public double TakeProfit { get; set; }
        }
    }
}
