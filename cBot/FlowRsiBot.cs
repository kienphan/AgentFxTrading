using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using System.Xml.Linq;
using cAlgo.API;
using cAlgo.API.Collections;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class FlowRSI : Robot
    {
        #region Enums
        public enum StopLossType
        {
            Technical_Swing,
            ATR_Multiplier,
            Fixed_Pips
        }

        public enum TakeProfitType
        {
            Technical_Liquidity,
            Risk_Reward_Ratio,
            Fixed_Pips
        }

        public enum BreakEvenTriggerMode
        {
            Risk_Reward_Ratio,
            Fixed_Pips
        }
        #endregion

        #region Parameters

        #region General & Licensing
        [Parameter("Bot ID", Group = "General", DefaultValue = "FlowRSI")]
        public string BotId { get; set; }

        [Parameter("Account Identifier", Group = "General", DefaultValue = "FlowRSI-Standard")]
        public string AccountLabel { get; set; }

        [Parameter("Show Detailed Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }
        #endregion

        #region Nested RSI Strategy
        [Parameter("Fast RSI Period", Group = "Nested RSI Engine", DefaultValue = 7, MinValue = 2, MaxValue = 50)]
        public int FastRsiPeriod { get; set; }

        [Parameter("Slow RSI Period", Group = "Nested RSI Engine", DefaultValue = 14, MinValue = 5, MaxValue = 100)]
        public int SlowRsiPeriod { get; set; }

        [Parameter("RSI Cross Lookback Bars", Group = "Nested RSI Engine", DefaultValue = 3, MinValue = 1, MaxValue = 10)]
        public int RsiCrossLookbackBars { get; set; }

        [Parameter("Bullish Cross Min (Oversold Floor)", Group = "Nested RSI Engine", DefaultValue = 25.0, MinValue = 10.0, MaxValue = 45.0)]
        public double RsiBullishCrossMin { get; set; }

        [Parameter("Bullish Cross Max (Rebound Ceiling)", Group = "Nested RSI Engine", DefaultValue = 50.0, MinValue = 35.0, MaxValue = 55.0)]
        public double RsiBullishCrossMax { get; set; }

        [Parameter("Bearish Cross Min (Exhaustion Floor)", Group = "Nested RSI Engine", DefaultValue = 50.0, MinValue = 45.0, MaxValue = 65.0)]
        public double RsiBearishCrossMin { get; set; }

        [Parameter("Bearish Cross Max (Overbought Ceiling)", Group = "Nested RSI Engine", DefaultValue = 75.0, MinValue = 55.0, MaxValue = 90.0)]
        public double RsiBearishCrossMax { get; set; }
        #endregion

        #region SMC / ICT Engine
        [Parameter("Enable SMC Structural Filter", Group = "SMC & ICT Market Structure", DefaultValue = true)]
        public bool EnableSmcFilter { get; set; }

        [Parameter("Swing Lookback (Fractal Period)", Group = "SMC & ICT Market Structure", DefaultValue = 5, MinValue = 2, MaxValue = 20)]
        public int SwingLookback { get; set; }

        [Parameter("Enable FVG Imbalance Detection", Group = "SMC & ICT Market Structure", DefaultValue = true)]
        public bool EnableFvgDetection { get; set; }

        [Parameter("Min FVG Size (pips)", Group = "SMC & ICT Market Structure", DefaultValue = 2.0, MinValue = 0.5)]
        public double FvgMinPips { get; set; }

        [Parameter("Enable Premium/Discount Filter", Group = "SMC & ICT Market Structure", DefaultValue = true)]
        public bool EnablePremiumDiscountFilter { get; set; }

        [Parameter("Equilibrium Threshold (50%)", Group = "SMC & ICT Market Structure", DefaultValue = 0.5, MinValue = 0.3, MaxValue = 0.7)]
        public double EquilibriumThreshold { get; set; }

        [Parameter("Enable Liquidity Sweep Filter", Group = "SMC & ICT Market Structure", DefaultValue = true)]
        public bool EnableLiquiditySweepFilter { get; set; }
        #endregion

        #region Risk Management Engine
        [Parameter("Risk per Trade (% Equity)", Group = "Risk Management", DefaultValue = 0.5, MinValue = 0.01, MaxValue = 10.0, Step = 0.1)]
        public double RiskPercentage { get; set; }

        [Parameter("Max Dollar Risk Per Trade ($)", Group = "Risk Management", DefaultValue = 50.0, MinValue = 5.0)]
        public double MaxRiskPerTradeMoney { get; set; }

        [Parameter("Stop Loss Mode", Group = "Risk Management", DefaultValue = StopLossType.Technical_Swing)]
        public StopLossType SlMode { get; set; }

        [Parameter("Take Profit Mode", Group = "Risk Management", DefaultValue = TakeProfitType.Risk_Reward_Ratio)]
        public TakeProfitType TpMode { get; set; }

        [Parameter("ATR Period", Group = "Risk Management", DefaultValue = 14, MinValue = 5)]
        public int AtrPeriod { get; set; }

        [Parameter("ATR SL Multiplier", Group = "Risk Management", DefaultValue = 1.5, MinValue = 0.5)]
        public double AtrSlMultiplier { get; set; }

        [Parameter("ATR TP Multiplier", Group = "Risk Management", DefaultValue = 3.0, MinValue = 1.0)]
        public double AtrTpMultiplier { get; set; }

        [Parameter("Target Risk-to-Reward Ratio", Group = "Risk Management", DefaultValue = 1.5, MinValue = 1.0)]
        public double TargetRiskReward { get; set; }
        [Parameter("Min SL Floor (pips)", Group = "Risk Management", DefaultValue = 15.0, MinValue = 5.0)]
        public double MinSlFloorPips { get; set; }

        [Parameter("Fixed SL Distance (pips)", Group = "Risk Management", DefaultValue = 30.0, MinValue = 5.0)]
        public double FixedSlPips { get; set; }

        [Parameter("Fixed TP Distance (pips)", Group = "Risk Management", DefaultValue = 60.0, MinValue = 10.0)]
        public double FixedTpPips { get; set; }

        [Parameter("Max Allowed Spread (pips)", Group = "Risk Management", DefaultValue = 30.0, MinValue = 0.5)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Positions Allowed", Group = "Risk Management", DefaultValue = 1, MinValue = 1, MaxValue = 5)]
        public int MaxPositionsAllowed { get; set; }

        [Parameter("Enable High-Watermark Circuit Breaker", Group = "Risk Management", DefaultValue = false)]
        public bool EnableHighWatermarkCut { get; set; }

        [Parameter("Max Daily Drawdown Threshold (%)", Group = "Risk Management", DefaultValue = 5.0, MinValue = 1.0)]
        public double HighWatermarkCutThreshold { get; set; }
        #endregion

        #region Position Protection (Anti-Drawdown & True Zero-Loss BE)
        [Parameter("Enable True Break-Even", Group = "Position Protection", DefaultValue = true)]
        public bool EnableBreakEven { get; set; }

        [Parameter("Break-Even Trigger Mode", Group = "Position Protection", DefaultValue = BreakEvenTriggerMode.Risk_Reward_Ratio)]
        public BreakEvenTriggerMode BeMode { get; set; }

[Parameter("Break-Even Trigger (R:R)", Group = "Position Protection", DefaultValue = 1.0, MinValue = 0.5)]
        public double BreakEvenTriggerRr { get; set; }

        [Parameter("Break-Even Trigger (pips)", Group = "Position Protection", DefaultValue = 20.0, MinValue = 5.0)]
        public double BreakEvenTriggerPips { get; set; }

        [Parameter("Min Break-Even Distance (pips)", Group = "Position Protection", DefaultValue = 10.0, MinValue = 1.0)]
        public double MinBreakEvenPips { get; set; }

        [Parameter("Zero-Loss Safety Buffer (pips)", Group = "Position Protection", DefaultValue = 0.5, MinValue = 0.1)]
        public double BreakEvenExtraPips { get; set; }

        [Parameter("Enable Gated Trailing Stop", Group = "Position Protection", DefaultValue = true)]
        public bool EnableTrailingStop { get; set; }

        // Must stay BELOW TargetRiskReward (1.5): with TpMode = Risk_Reward_Ratio the take
        // profit sits at that ratio, and lower still once the spread on the stop is paid, so
        // a trigger at the old 1.8 could never be reached before the trade closed at TP.
        [Parameter("Trailing Stop Trigger (R:R)", Group = "Position Protection", DefaultValue = 1.2, MinValue = 0.5)]
        public double TrailingStopTriggerRr { get; set; }

        [Parameter("Trailing Stop Distance (pips)", Group = "Position Protection", DefaultValue = 25.0, MinValue = 5.0)]
        public double TrailingStopDistancePips { get; set; }

        [Parameter("Partial Close at BE Ratio (0-1)", Group = "Position Protection", DefaultValue = 0.25, MinValue = 0.0, MaxValue = 1.0)]
        public double PartialCloseRatio { get; set; }
        #endregion

        #region Gemini AI Agent Bridge
        [Parameter("Enable AI Gate Mode", Group = "AI Agent Integration", DefaultValue = true)]
        public bool UseAiGateMode { get; set; }

        // Fail-closed by default: when the AI hub is unreachable, in cooldown or erroring,
        // the gate is what it claims to be and no order is placed. Turn this on only to
        // deliberately run technical-only entries while the hub is down.
        [Parameter("Allow Technical Fallback On AI Failure", Group = "AI Agent Integration", DefaultValue = false)]
        public bool AllowTechnicalFallbackOnAiFailure { get; set; }

        [Parameter("Agent API URL", Group = "AI Agent Integration", DefaultValue = "http://127.0.0.1:8000/trade")]
        public string ApiUrl { get; set; }

        [Parameter("Telemetry Endpoint", Group = "AI Agent Integration", DefaultValue = "http://127.0.0.1:8000/api/tick")]
        public string AiTelemetryUrl { get; set; }

        [Parameter("Portfolio Report Endpoint", Group = "AI Agent Integration", DefaultValue = "http://127.0.0.1:8000/portfolio/report")]
        public string AiReportUrl { get; set; }
        [Parameter("AI Minimum Confidence (%)", Group = "AI Agent Integration", DefaultValue = 65.0, MinValue = 50.0, MaxValue = 100.0)]
        public double AiConfidenceThreshold { get; set; }
        #endregion

        #region ForexFactory News Filter
        [Parameter("Enable News Filter", Group = "News Filter", DefaultValue = true)]
        public bool EnableNewsFilter { get; set; }

        [Parameter("Minutes Before News", Group = "News Filter", DefaultValue = 30, MinValue = 5)]
        public int MinsBeforeNews { get; set; }

        [Parameter("Minutes After News", Group = "News Filter", DefaultValue = 30, MinValue = 5)]
        public int MinsAfterNews { get; set; }

        [Parameter("Filter High Impact News", Group = "News Filter", DefaultValue = true)]
        public bool FilterHighImpact { get; set; }

        [Parameter("Filter Medium Impact News", Group = "News Filter", DefaultValue = false)]
        public bool FilterMediumImpact { get; set; }

        [Parameter("Target News Currencies (Empty = Auto)", Group = "News Filter", DefaultValue = "")]
        public string TargetNewsCurrencies { get; set; }
        #endregion

        #region Telegram Alerts
        [Parameter("Enable Telegram Alerts", Group = "Telegram Integration", DefaultValue = false)]
        public bool EnableTelegramAlerts { get; set; }

        [Parameter("Telegram Bot Token", Group = "Telegram Integration", DefaultValue = "")]
        public string TelegramBotToken { get; set; }

        [Parameter("Telegram Chat ID", Group = "Telegram Integration", DefaultValue = "")]
        public string TelegramChatId { get; set; }

        [Parameter("Send Chart Screenshot", Group = "Telegram Integration", DefaultValue = false)]
        public bool SendChartScreenshot { get; set; }

        [Parameter("Send AI ADJUST Alerts", Group = "Telegram Integration", DefaultValue = true)]
        public bool SendAiAdjustAlerts { get; set; }
        #endregion

        #endregion

        #region State Variables & Indicator References
        private readonly bool Unlimited_License = true; // CS0162 compliant
        private volatile bool _isStopped = false;
        private RelativeStrengthIndex _fastRsi;
        private RelativeStrengthIndex _slowRsi;
        private AverageTrueRange _atr;

        private readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };

        // AI Agent Safety Guard (aligned with Asian Range Judas Sweep AI Bot reference pattern)
        private int _isAgentQuerying = 0;
        private int _consecutiveAiFailures = 0;
        private DateTime _aiCooldownUntil = DateTime.MinValue;

        private readonly HashSet<int> _breakevenApplied = new HashSet<int>();
        private readonly Dictionary<int, double> _initialSlDistances = new Dictionary<int, double>();

        private double _highWatermarkEquity;
        private bool _circuitBreakerTriggered;
        private DateTime _lastCircuitBreakerResetDate = DateTime.MinValue;
        private DateTime _lastTickTelemetryTime = DateTime.MinValue;
        private DateTime _lastNewsFetchTime = DateTime.MinValue;
        private DateTime _nextAllowedDirectFetchTime = DateTime.MinValue;
        private readonly List<NewsEvent> _newsEvents = new List<NewsEvent>();

        private struct NewsEvent
        {
            public DateTime UtcTime;
            public string Currency;
            public string Impact;
            public string Title;
        }

        public class AgentDecision
        {
            public string request_id { get; set; }
            public string bot_id { get; set; }
            public string symbol { get; set; }
            public string timeframe { get; set; }
            public string action { get; set; } // BUY, SELL, HOLD, ADJUST, CLOSE_ALL
            public double? volume_lots { get; set; }
            public double? sl_pips { get; set; }
            public double? tp_pips { get; set; }
            public double? new_sl_price { get; set; }
            public double? new_tp_price { get; set; }
            public string reason { get; set; }
            public double confidence { get; set; }
        }

        public class StrategyData
        {
            public double tema1 { get; set; }
            public double tema2 { get; set; }
            public double rsi { get; set; }
            public double adx { get; set; }
            public double atr { get; set; }
            public double recent_high { get; set; }
            public double recent_low { get; set; }
            public string bias_direction { get; set; }
        }

        public class ActivePosition
        {
            public int id { get; set; }
            public string symbol { get; set; }
            public string trade_type { get; set; }
            public double volume { get; set; }
            public double entry_price { get; set; }
            public double sl { get; set; }
            public double tp { get; set; }
            public string entry_time { get; set; }
        }

        public class HistoricalTrade
        {
            public int position_id { get; set; }
            public string symbol { get; set; }
            public string trade_type { get; set; }
            public double volume { get; set; }
            public double entry_price { get; set; }
            public double exit_price { get; set; }
            public double pnl { get; set; }
            public string entry_time { get; set; }
            public string exit_time { get; set; }
        }

        public class PositionInfo
        {
            public int id { get; set; }
            public string type { get; set; }
            public double volume { get; set; }
            public double entry_price { get; set; }
            public double current_price { get; set; }
            public double pnl { get; set; }
            public double? sl { get; set; }
            public double? tp { get; set; }
            public double duration_minutes { get; set; }
        }

        public class MarketSnapshot
        {
            public string request_id { get; set; }
            public string bot_id { get; set; }
            public string symbol { get; set; }
            public string timeframe { get; set; }
            public double ask { get; set; }
            public double bid { get; set; }
            public double current_bid { get; set; }
            public double current_ask { get; set; }
            public double spread_pips { get; set; }
            public double pip_size { get; set; }
            public double pip_value { get; set; }
            public int digits { get; set; }
            public string account_number { get; set; }
            public string account_type { get; set; }
            public string account_label { get; set; }
            public double account_balance { get; set; }
            public double account_equity { get; set; }
            public double fast_rsi { get; set; }
            public double slow_rsi { get; set; }
            public string rsi_cross_signal { get; set; } // Bullish_Cross, Bearish_Cross, None
            public bool in_fvg_zone { get; set; }
            public string fvg_type { get; set; }
            public bool is_discount { get; set; }
            public bool is_premium { get; set; }
            public bool liquidity_swept { get; set; }
            public string swept_liquidity_type { get; set; }
            public double technical_sl_price { get; set; }
            public double technical_tp_price { get; set; }
            public double technical_risk_reward { get; set; }
            public string candidate_action { get; set; }
            public StrategyData strategy { get; set; }
            public PositionInfo position { get; set; }
            public List<ActivePosition> active_positions { get; set; } = new List<ActivePosition>();
            public List<HistoricalTrade> recent_history { get; set; } = new List<HistoricalTrade>();
            public List<BarInfo> bars { get; set; } = new List<BarInfo>();
        }

        public class ActivePositionInfo
        {
            public int id { get; set; }
            public string symbol { get; set; }
            public string trade_type { get; set; }
            public double volume { get; set; }
            public double volume_lots { get; set; }
            public double entry_price { get; set; }
            public double sl { get; set; }
            public double tp { get; set; }
            public double? stop_loss { get; set; }
            public double? take_profit { get; set; }
            public string entry_time { get; set; }
            public double net_profit { get; set; }
            public double pnl_pips { get; set; }
            public double duration_minutes { get; set; }
        }

        public class BarInfo
        {
            public string time { get; set; }
            public double open { get; set; }
            public double high { get; set; }
            public double low { get; set; }
            public double close { get; set; }
            public double volume { get; set; }
        }
        #endregion

        #region cTrader Lifecycle
        protected override void OnStart()
        {
            Print($"[FlowRSI] Initializing cBot '{BotId}' on {SymbolName} ({TimeFrame})");

            try
            {
                if (Unlimited_License)
                {
                    Print($"[FlowRSI] Commercial Unlimited License verified for Account #{Account.Number}.");
                }

                // 1. Initialize Indicators
                _fastRsi = Indicators.RelativeStrengthIndex(Bars.ClosePrices, FastRsiPeriod);
                _slowRsi = Indicators.RelativeStrengthIndex(Bars.ClosePrices, SlowRsiPeriod);
                _atr = Indicators.AverageTrueRange(AtrPeriod, MovingAverageType.Simple);

                // 2. Initialize Equity High-Watermark
                _highWatermarkEquity = Account.Equity;

                // 3. Initialize News Filter
                if (EnableNewsFilter && RunningMode == RunningMode.RealTime)
                {
                    Task.Run(FetchForexFactoryNewsAsync);
                }

                // 4. Register Position Events for Centralized Telegram & Portfolio Tracking
                Positions.Closed += OnPositionClosed;

                // 5. Test Notification
                if (EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                {
                    _ = SendTelegramMessageAsync($"🚀 <b>[FlowRSI cBot Started]</b>\nSymbol: <b>{SymbolName}</b>\nTimeFrame: <b>{TimeFrame}</b>\nEquity: <b>${Account.Equity:F2}</b>\nAI Gate Mode: <b>{UseAiGateMode}</b>");
                }

                Print($"[FlowRSI] Initialized Successfully! FastRSI({FastRsiPeriod}), SlowRSI({SlowRsiPeriod}), SMC Filter: {EnableSmcFilter}");

                // 6. Recover initial SL distances for positions that survived the restart
                _ = RestoreInitialSlDistances();

                // 7. Dispatch initial boot snapshot to AI Server
                if (UseAiGateMode && RunningMode == RunningMode.RealTime)
                {
                    var activePositions = GetBotPositions();
                    bool hasOpenPos = activePositions.Count > 0;

                    // Only dispatch when a position is already open: that path sends a
                    // MANAGE_ONLY snapshot and cannot enter. While flat, EvaluateStrategySignals
                    // would build an ENTRY candidate from the bar still FORMING at start-up
                    // (index = Count-1 is only the closed bar inside OnBarClosed), so a mid-bar
                    // cross that reverses by the close could be confirmed into a real trade on
                    // every restart. Entry evaluation resumes at the next bar close.
                    if (hasOpenPos)
                    {
                        EvaluateStrategySignals(hasOpenPos);
                    }
                    else
                    {
                        Print("[FlowRSI] Boot snapshot skipped: flat at start-up and the current bar is still forming. Entry evaluation resumes at the next bar close.");
                    }
                }
            }
            catch (Exception ex)
            {
                Print($"[FlowRSI Error on OnStart] {ex.Message}");
            }
        }

        protected override void OnTick()
        {
            try
            {
                // Manage Open Positions on every tick (Breakeven & Gated Trailing Stop)
                ManageExits();

                // High-Watermark Drawdown Circuit Breaker check
                CheckCircuitBreaker();

                // Stream Live Tick Telemetry to Web Hub (throttled every 5 seconds)
                if (RunningMode == RunningMode.RealTime && (DateTime.UtcNow - _lastTickTelemetryTime).TotalSeconds >= 5.0)
                {
                    _lastTickTelemetryTime = DateTime.UtcNow;
                    SendLiveTickTelemetry();
                }
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[OnTick Error] {ex.Message}");
            }
        }

        protected override void OnBarClosed()
        {
            try
            {
                // Check if news window active
                if (IsNewsSuspensionActive(out string newsReason))
                {
                    if (ShowLogs) Print($"[FlowRSI] Market entry suspended due to High-Impact News event window: {newsReason}");
                    return;
                }

                // Check Circuit Breaker
                if (_circuitBreakerTriggered)
                {
                    if (ShowLogs) Print("[FlowRSI] Circuit breaker active. Entries blocked.");
                    return;
                }

                // Check active bot positions
                var activePositions = GetBotPositions();
                bool hasOpenPos = activePositions.Count > 0;

                // Max Spread Check
                double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
                if (spreadPips > MaxSpreadPips)
                {
                    if (ShowLogs) Print($"[FlowRSI] Spread {spreadPips:F1} pips exceeds max allowed {MaxSpreadPips:F1} pips.");
                    return;
                }

                // Evaluate SMC Market Structure & Nested RSI Crossing (coordinates single-flight AI snapshot)
                EvaluateStrategySignals(hasOpenPos);
            }
            catch (Exception ex)
            {
                Print($"[OnBarClosed Error] {ex.Message}");
            }
        }

        protected override void OnStop()
        {
            _isStopped = true;
            Positions.Closed -= OnPositionClosed;
            Print($"[FlowRSI] cBot stopped on {SymbolName}.");
            _httpClient?.Dispose();
        }
        #endregion

        #region SMC / ICT Structure & Nested RSI Signal Analysis
        private void EvaluateStrategySignals(bool hasOpenPos)
        {
            int index = Bars.ClosePrices.Count - 1;
            if (index < 20) return;

            double fastRsiCurr = _fastRsi.Result[index];
            double slowRsiCurr = _slowRsi.Result[index];

            int lookback = Math.Max(1, RsiCrossLookbackBars);
            int startBar = Math.Max(1, index - lookback + 1);

            // 1. Nested RSI Crossing Detection over lookback window
            bool bullishRsiCross = false;
            bool bearishRsiCross = false;

            for (int k = startBar; k <= index; k++)
            {
                double fPrev = _fastRsi.Result[k - 1];
                double fCurr = _fastRsi.Result[k];
                double sPrev = _slowRsi.Result[k - 1];
                double sCurr = _slowRsi.Result[k];

                // Bullish Golden Cross: Fast crossed Slow from oversold/rebound zone (Slow RSI in [RsiBullishCrossMin, RsiBullishCrossMax])
                if (fPrev <= sPrev && fCurr > sCurr && sCurr >= RsiBullishCrossMin && sCurr <= RsiBullishCrossMax)
                {
                    bullishRsiCross = true;
                }

                // Bearish Death Cross: Fast crossed Slow from overbought/exhaustion zone (Slow RSI in [RsiBearishCrossMin, RsiBearishCrossMax])
                if (fPrev >= sPrev && fCurr < sCurr && sCurr >= RsiBearishCrossMin && sCurr <= RsiBearishCrossMax)
                {
                    bearishRsiCross = true;
                }
            }

            // Confirm momentum direction on current bar
            if (bullishRsiCross && fastRsiCurr <= slowRsiCurr) bullishRsiCross = false;
            if (bearishRsiCross && fastRsiCurr >= slowRsiCurr) bearishRsiCross = false;

            string rsiCrossSignal = bullishRsiCross ? "Bullish_Cross" : (bearishRsiCross ? "Bearish_Cross" : "None");

            // 2. SMC Structural Analysis: Swing High / Low (Fractals)
            double recentSwingHigh = GetRecentSwingHigh(index, SwingLookback);
            double recentSwingLow = GetRecentSwingLow(index, SwingLookback);
            double range = recentSwingHigh - recentSwingLow;
            double currentClose = Bars.ClosePrices[index];

            bool isDiscount = range > 0 && currentClose <= (recentSwingLow + range * EquilibriumThreshold);
            bool isPremium = range > 0 && currentClose >= (recentSwingLow + range * (1.0 - EquilibriumThreshold));

            // 3. FVG Imbalance Detection (3-candle pattern: Bar i-2 vs Bar i)
            bool inBullishFvg = false;
            bool inBearishFvg = false;
            if (EnableFvgDetection)
            {
                // Bullish FVG: Bar i-2 High < Bar i Low
                double fvgGapBull = Bars.LowPrices[index] - Bars.HighPrices[index - 2];
                if (fvgGapBull >= (FvgMinPips * Symbol.PipSize) && currentClose >= Bars.HighPrices[index - 2])
                    inBullishFvg = true;

                // Bearish FVG: Bar i-2 Low > Bar i High
                double fvgGapBear = Bars.LowPrices[index - 2] - Bars.HighPrices[index];
                if (fvgGapBear >= (FvgMinPips * Symbol.PipSize) && currentClose <= Bars.LowPrices[index - 2])
                    inBearishFvg = true;
            }

            // 4. Liquidity Sweep Detection across lookback window
            bool sweptSsl = false; // Swept Sell-Side Liquidity (below swing low and closed back above)
            bool sweptBsl = false; // Swept Buy-Side Liquidity (above swing high and closed back below)
            if (EnableLiquiditySweepFilter)
            {
                for (int k = startBar; k <= index; k++)
                {
                    if (Bars.LowPrices[k] < recentSwingLow && Bars.ClosePrices[k] > recentSwingLow)
                        sweptSsl = true;

                    if (Bars.HighPrices[k] > recentSwingHigh && Bars.ClosePrices[k] < recentSwingHigh)
                        sweptBsl = true;
                }
            }

            // 5. Synthesize Technical Candidate Entry
            bool buyCandidate = bullishRsiCross;
            bool sellCandidate = bearishRsiCross;

            if (EnableSmcFilter)
            {
                buyCandidate = buyCandidate && (isDiscount || inBullishFvg || sweptSsl);
                sellCandidate = sellCandidate && (isPremium || inBearishFvg || sweptBsl);
            }

            string candidateAction = "NONE";
            string signalReason = "None";
            double currentAtr = _atr.Result[index];
            double technicalSL = 0.0;
            double technicalTP = 0.0;
            double calculatedRr = TargetRiskReward;

            if (buyCandidate || sellCandidate)
            {
                candidateAction = buyCandidate ? "BUY" : "SELL";
                signalReason = $"NestedRSI-SMC ({rsiCrossSignal}, FVG:{(inBullishFvg || inBearishFvg)}, Sweep:{(sweptSsl || sweptBsl)}, Disc:{isDiscount}/Prem:{isPremium})";

                // 6. Calculate Technical SL and TP targets
                double effectiveMinSl = MinSlFloorPips > 0 ? MinSlFloorPips : 15.0;
                string symUpper = SymbolName.ToUpperInvariant();
                if (symUpper.Contains("XAU") || symUpper.Contains("GOLD"))
                    effectiveMinSl = Math.Max(effectiveMinSl, 150.0);
                else if (symUpper.Contains("JPY"))
                    effectiveMinSl = Math.Max(effectiveMinSl, 18.0);

                // Enforce ATR-based breathing room: at least 1.0 * ATR
                double atrInPips = currentAtr / Symbol.PipSize;
                effectiveMinSl = Math.Max(effectiveMinSl, atrInPips * 1.0);

                if (buyCandidate)
                {
                    double structuralSl = (recentSwingLow > 0 && recentSwingLow < Symbol.Bid) 
                        ? (recentSwingLow - 0.5 * currentAtr) 
                        : (Symbol.Bid - AtrSlMultiplier * currentAtr);

                    if (SlMode == StopLossType.ATR_Multiplier)
                        technicalSL = Symbol.Bid - AtrSlMultiplier * currentAtr;
                    else if (SlMode == StopLossType.Fixed_Pips)
                        technicalSL = Symbol.Bid - FixedSlPips * Symbol.PipSize;
                    else
                        technicalSL = structuralSl;

                    // Enforce calibrated minimum SL breathing room
                    double slDistance = Symbol.Bid - technicalSL;
                    if ((slDistance / Symbol.PipSize) < effectiveMinSl)
                    {
                        technicalSL = Symbol.Bid - (effectiveMinSl * Symbol.PipSize);
                        slDistance = Symbol.Bid - technicalSL;
                    }

                    double tpDistance = slDistance * TargetRiskReward;
                    technicalTP = Symbol.Ask + tpDistance;

                    // Technical liquidity mode can extend TP further, but NEVER compress it below target RR
                    if (TpMode == TakeProfitType.Technical_Liquidity && recentSwingHigh > Symbol.Ask)
                    {
                        double liquidityTpDistance = recentSwingHigh - Symbol.Ask;
                        if (liquidityTpDistance >= tpDistance)
                            technicalTP = recentSwingHigh;
                    }
                }
                else
                {
                    double structuralSl = (recentSwingHigh > 0 && recentSwingHigh > Symbol.Ask)
                        ? (recentSwingHigh + 0.5 * currentAtr)
                        : (Symbol.Ask + AtrSlMultiplier * currentAtr);

                    if (SlMode == StopLossType.ATR_Multiplier)
                        technicalSL = Symbol.Ask + AtrSlMultiplier * currentAtr;
                    else if (SlMode == StopLossType.Fixed_Pips)
                        technicalSL = Symbol.Ask + FixedSlPips * Symbol.PipSize;
                    else
                        technicalSL = structuralSl;

                    // Enforce calibrated minimum SL breathing room
                    double slDistance = technicalSL - Symbol.Ask;
                    if ((slDistance / Symbol.PipSize) < effectiveMinSl)
                    {
                        technicalSL = Symbol.Ask + (effectiveMinSl * Symbol.PipSize);
                        slDistance = technicalSL - Symbol.Ask;
                    }

                    double tpDistance = slDistance * TargetRiskReward;
                    technicalTP = Symbol.Bid - tpDistance;

                    // Technical liquidity mode can extend TP further, but NEVER compress it below target RR
                    if (TpMode == TakeProfitType.Technical_Liquidity && recentSwingLow > 0 && recentSwingLow < Symbol.Bid)
                    {
                        double liquidityTpDistance = Symbol.Bid - recentSwingLow;
                        if (liquidityTpDistance >= tpDistance)
                            technicalTP = recentSwingLow;
                    }
                }
                double riskDistPips = Math.Abs(Symbol.Bid - technicalSL) / Symbol.PipSize;
                double rewardDistPips = Math.Abs(technicalTP - Symbol.Bid) / Symbol.PipSize;
                calculatedRr = riskDistPips > 0 ? (rewardDistPips / riskDistPips) : TargetRiskReward;
            }

            // 7. Single-Flight Coordinated AI Snapshot Dispatch (Judas Pattern)
            bool shouldCallAi = (UseAiGateMode && RunningMode == RunningMode.RealTime);

            if (shouldCallAi)
            {
                string contextDir = hasOpenPos ? "MANAGE_ONLY" : candidateAction;
                _ = SendStateToAgentAsync(contextDir, technicalSL, technicalTP, signalReason, rsiCrossSignal, inBullishFvg || inBearishFvg, inBullishFvg ? "Bullish_FVG" : (inBearishFvg ? "Bearish_FVG" : "None"), isDiscount, isPremium, sweptSsl || sweptBsl, sweptSsl ? "SSL_Swept" : (sweptBsl ? "BSL_Swept" : "None"), calculatedRr);
            }
            else if (!UseAiGateMode && !hasOpenPos && (candidateAction == "BUY" || candidateAction == "SELL"))
            {
                // Direct Technical Execution (Backtesting or standalone mode)
                ExecuteTechnicalOrder(candidateAction, technicalSL, technicalTP, signalReason);
            }
        }

        private double GetRecentSwingHigh(int currentIndex, int lookback)
        {
            double maxHigh = 0.0;
            int start = Math.Max(0, currentIndex - lookback * 3);
            for (int i = start; i < currentIndex; i++)
            {
                if (Bars.HighPrices[i] > maxHigh)
                    maxHigh = Bars.HighPrices[i];
            }
            return maxHigh;
        }

        private double GetRecentSwingLow(int currentIndex, int lookback)
        {
            double minLow = double.MaxValue;
            int start = Math.Max(0, currentIndex - lookback * 3);
            for (int i = start; i < currentIndex; i++)
            {
                if (Bars.LowPrices[i] < minLow)
                    minLow = Bars.LowPrices[i];
            }
            return minLow == double.MaxValue ? 0.0 : minLow;
        }
        #endregion

        #region AI Agent Communication & Decision Execution (Judas Architecture)
        private async Task SendStateToAgentAsync(
            string allowedDirection,
            double technicalSL,
            double technicalTP,
            string signalReason,
            string rsiCrossSignal,
            bool inFvgZone,
            string fvgType,
            bool isDiscount,
            bool isPremium,
            bool liquiditySwept,
            string sweptLiquidityType,
            double technicalRr)
        {
            if (RunningMode != RunningMode.RealTime) return;
            if (Interlocked.CompareExchange(ref _isAgentQuerying, 1, 0) != 0)
            {
                if (ShowLogs) Print("[AI Agent] Previous evaluation still in-flight. Skipping overlapping AI query this bar.");
                return;
            }

            try
            {
                // Check Safety Guard Cooldown
                if (DateTime.UtcNow < _aiCooldownUntil)
                {
                    BeginInvokeOnMainThread(() =>
                    {
                        if (ShowLogs) Print($"[AI Agent Safety Guard] Cooldown active until {_aiCooldownUntil:HH:mm:ss} UTC. Direct AI query skipped.");
                        if (allowedDirection == "BUY" || allowedDirection == "SELL")
                        {
                            if (AllowTechnicalFallbackOnAiFailure)
                            {
                                Print($"[AI Gate Fallback] ⚠️ Technical fallback ENABLED - entering {allowedDirection} with NO AI approval (cooldown active).");
                                ExecuteTechnicalOrder(allowedDirection, technicalSL, technicalTP, signalReason);
                            }
                            else
                            {
                                Print($"[AI Gate Fail-Closed] {allowedDirection} candidate skipped: AI cooldown active until {_aiCooldownUntil:HH:mm:ss} UTC.");
                            }
                        }
                    });
                    return;
                }

                // ── 1. Synchronously pre-capture all cTrader COM/API objects on Main Thread ──
                int maxBars = Math.Min(35, Bars.Count);
                var barList = new List<BarInfo>();
                for (int i = 1; i <= maxBars; i++)
                {
                    int idx = Bars.Count - i;
                    barList.Add(new BarInfo
                    {
                        time = Bars.OpenTimes[idx].ToString("o"),
                        open = Bars.OpenPrices[idx],
                        high = Bars.HighPrices[idx],
                        low = Bars.LowPrices[idx],
                        close = Bars.ClosePrices[idx],
                        volume = Bars.TickVolumes[idx]
                    });
                }

                int index = Bars.ClosePrices.Count - 1;
                double fastRsiCurr = _fastRsi != null && _fastRsi.Result.Count > 1 ? _fastRsi.Result[index] : 50.0;
                double slowRsiCurr = _slowRsi != null && _slowRsi.Result.Count > 1 ? _slowRsi.Result[index] : 50.0;
                double currentAtr = _atr != null && _atr.Result.Count > 1 ? _atr.Result[index] : (Symbol.Spread * 3);
                double recentSwingHigh = GetRecentSwingHigh(index, SwingLookback);
                double recentSwingLow = GetRecentSwingLow(index, SwingLookback);

                var activePositionsList = new List<ActivePosition>();
                var botPositions = GetBotPositions();
                foreach (var p in botPositions)
                {
                    activePositionsList.Add(new ActivePosition
                    {
                        id = p.Id,
                        symbol = p.SymbolName,
                        trade_type = p.TradeType.ToString(),
                        volume = Math.Round(p.VolumeInUnits / Symbol.LotSize, 2),
                        entry_price = p.EntryPrice,
                        sl = p.StopLoss ?? 0.0,
                        tp = p.TakeProfit ?? 0.0,
                        entry_time = p.EntryTime.ToString("yyyy-MM-dd HH:mm:ss")
                    });
                }

                var recentHistoryList = new List<HistoricalTrade>();
                var recentCutoff = Server.Time.AddDays(-1);
                foreach (var hist in History.Where(h => (h.Label == BotId || h.Comment == BotId) && h.SymbolName == SymbolName && h.ClosingTime >= recentCutoff)
                                            .OrderByDescending(h => h.ClosingTime)
                                            .Take(5))
                {
                    recentHistoryList.Add(new HistoricalTrade
                    {
                        position_id = hist.PositionId,
                        symbol = hist.SymbolName,
                        trade_type = hist.TradeType.ToString(),
                        volume = Math.Round(hist.VolumeInUnits / Symbol.LotSize, 2),
                        entry_price = hist.EntryPrice,
                        exit_price = hist.ClosingPrice,
                        pnl = hist.NetProfit,
                        entry_time = hist.EntryTime.ToString("yyyy-MM-dd HH:mm:ss"),
                        exit_time = hist.ClosingTime.ToString("yyyy-MM-dd HH:mm:ss")
                    });
                }

                PositionInfo primaryPos = null;
                if (botPositions.Count > 0)
                {
                    var p = botPositions[0];
                    primaryPos = new PositionInfo
                    {
                        id = p.Id,
                        type = p.TradeType.ToString(),
                        volume = p.VolumeInUnits / Symbol.LotSize,
                        entry_price = p.EntryPrice,
                        current_price = p.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask,
                        pnl = p.NetProfit,
                        sl = p.StopLoss,
                        tp = p.TakeProfit,
                        duration_minutes = (Server.TimeInUtc - p.EntryTime).TotalMinutes
                    };
                }

                string currentRequestId = Guid.NewGuid().ToString("N");

                var snapshot = new MarketSnapshot
                {
                    request_id = currentRequestId,
                    bot_id = BotId,
                    symbol = SymbolName,
                    timeframe = TimeFrame.ToString(),
                    ask = Symbol.Ask,
                    bid = Symbol.Bid,
                    current_bid = Symbol.Bid,
                    current_ask = Symbol.Ask,
                    spread_pips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize,
                    pip_size = Symbol.PipSize,
                    pip_value = Symbol.PipValue,
                    digits = Symbol.Digits,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "Live" : "Demo",
                    account_label = AccountLabel,
                    account_balance = Math.Round(Account.Balance, 2),
                    account_equity = Math.Round(Account.Equity, 2),
                    fast_rsi = Math.Round(fastRsiCurr, 2),
                    slow_rsi = Math.Round(slowRsiCurr, 2),
                    rsi_cross_signal = rsiCrossSignal,
                    in_fvg_zone = inFvgZone,
                    fvg_type = fvgType,
                    is_discount = isDiscount,
                    is_premium = isPremium,
                    liquidity_swept = liquiditySwept,
                    swept_liquidity_type = sweptLiquidityType,
                    technical_sl_price = Math.Round(technicalSL, Symbol.Digits),
                    technical_tp_price = Math.Round(technicalTP, Symbol.Digits),
                    technical_risk_reward = Math.Round(technicalRr, 2),
                    candidate_action = allowedDirection,
                    bars = barList,
                    strategy = new StrategyData
                    {
                        tema1 = Math.Round(technicalSL, Symbol.Digits),
                        tema2 = Math.Round(technicalTP, Symbol.Digits),
                        rsi = Math.Round(slowRsiCurr, 2),
                        adx = 25.0,
                        atr = Math.Round(currentAtr, Symbol.Digits),
                        recent_high = Math.Round(recentSwingHigh, Symbol.Digits),
                        recent_low = Math.Round(recentSwingLow, Symbol.Digits),
                        bias_direction = allowedDirection
                    },
                    position = primaryPos,
                    active_positions = activePositionsList,
                    recent_history = recentHistoryList
                };

                // ── 2. Serialize JSON string on Main Thread before async dispatch ──
                string jsonPayload = JsonSerializer.Serialize(snapshot);

                // ── 3. Dispatch safe network query ──
                await AskAgentAsync(jsonPayload, currentRequestId, allowedDirection, technicalSL, technicalTP, signalReason);
            }
            catch (Exception ex)
            {
                BeginInvokeOnMainThread(() =>
                {
                    if (ShowLogs) Print($"[SendStateToAgentAsync Error] {ex.Message}");
                });
            }
            finally
            {
                Interlocked.Exchange(ref _isAgentQuerying, 0);
            }
        }

        private async Task AskAgentAsync(string jsonPayload, string expectedRequestId, string allowedDirection, double fallbackSL, double fallbackTP, string reason)
        {
            try
            {
                if (_httpClient == null) return;
                string localTargetUrl = ApiUrl;
                if (string.IsNullOrWhiteSpace(localTargetUrl) || !localTargetUrl.EndsWith("/trade", StringComparison.OrdinalIgnoreCase))
                {
                    var baseUri = !string.IsNullOrWhiteSpace(ApiUrl) ? ApiUrl.Replace("/trade", "").TrimEnd('/') : "http://127.0.0.1:8000";
                    localTargetUrl = $"{baseUri}/trade";
                }

                BeginInvokeOnMainThread(() =>
                {
                    if (ShowLogs) Print($"[AI Agent] Sending market snapshot for {SymbolName} ({TimeFrame}) [Req: {expectedRequestId.Substring(0, 8)}...] to {localTargetUrl}...");
                });

                var content = new StringContent(jsonPayload, Encoding.UTF8, "application/json");
                var response = await _httpClient.PostAsync(localTargetUrl, content);

                if (!response.IsSuccessStatusCode)
                {
                    string httpErr = $"HTTP {(int)response.StatusCode} {response.StatusCode} from {localTargetUrl}";
                    BeginInvokeOnMainThread(() =>
                    {
                        if (ShowLogs) Print($"[AI Hub HTTP Error] {response.StatusCode} from {localTargetUrl}");
                        HandleAiFailure(httpErr);
                        if (allowedDirection == "BUY" || allowedDirection == "SELL")
                        {
                            if (AllowTechnicalFallbackOnAiFailure)
                            {
                                Print($"[AI Gate Fallback] ⚠️ Technical fallback ENABLED - entering {allowedDirection} with NO AI approval ({httpErr}).");
                                ExecuteTechnicalOrder(allowedDirection, fallbackSL, fallbackTP, reason);
                            }
                            else
                            {
                                Print($"[AI Gate Fail-Closed] {allowedDirection} candidate skipped: {httpErr}.");
                            }
                        }
                    });
                    return;
                }

                string responseText = await response.Content.ReadAsStringAsync();
                var jsonOptions = new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true,
                    NumberHandling = JsonNumberHandling.AllowReadingFromString
                };
                var decision = JsonSerializer.Deserialize<AgentDecision>(responseText, jsonOptions);

                if (decision != null)
                {
                    _consecutiveAiFailures = 0;
                    BeginInvokeOnMainThread(() =>
                    {
                        ExecuteDecision(decision, expectedRequestId, allowedDirection, fallbackSL, fallbackTP, reason);
                    });
                }
                else
                {
                    BeginInvokeOnMainThread(() =>
                    {
                        HandleAiFailure("Failed to deserialize AgentDecision from AI Hub");
                    });
                }
            }
            catch (Exception ex)
            {
                string exErr = ex.Message;
                BeginInvokeOnMainThread(() =>
                {
                    if (ShowLogs) Print($"[AI Agent Bridge Error] {exErr}");
                    HandleAiFailure(exErr);
                    if (allowedDirection == "BUY" || allowedDirection == "SELL")
                    {
                        if (AllowTechnicalFallbackOnAiFailure)
                        {
                            Print($"[AI Gate Fallback] ⚠️ Technical fallback ENABLED - entering {allowedDirection} with NO AI approval ({exErr}).");
                            ExecuteTechnicalOrder(allowedDirection, fallbackSL, fallbackTP, reason);
                        }
                        else
                        {
                            Print($"[AI Gate Fail-Closed] {allowedDirection} candidate skipped: AI bridge error ({exErr}).");
                        }
                    }
                });
            }
        }

        private void HandleAiFailure(string errorMessage)
        {
            _consecutiveAiFailures++;
            if (ShowLogs) Print($"[AI Agent Warning] AI query failed ({_consecutiveAiFailures}/3): {errorMessage}");

            if (_consecutiveAiFailures >= 3)
            {
                _aiCooldownUntil = DateTime.UtcNow.AddMinutes(15);
                Print($"[AI Agent Safety Guard] 🚨 3 consecutive AI failures reached! Pausing AI evaluation for 15 minutes until {_aiCooldownUntil:HH:mm:ss} UTC.");
                if (EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                {
                    _ = SendTelegramMessageAsync($"🚨 <b>[FlowRSI AI Agent Safety Guard]</b>\nAI Hub encountered 3 consecutive failures!\nEvaluation suspended for 15 minutes until <b>{_aiCooldownUntil:HH:mm:ss} UTC</b>.\n<i>Last error: {errorMessage}</i>");
                }
            }
        }

        private void ExecuteDecision(AgentDecision decision, string expectedRequestId, string allowedDirection, double fallbackSL, double fallbackTP, string reason)
        {
            try
            {
                if (decision == null) return;

                // 1. Cross-Ticker & Cross-Instance Verification
                if (!string.IsNullOrEmpty(decision.symbol))
                {
                    bool symMatch = string.Equals(decision.symbol, SymbolName, StringComparison.OrdinalIgnoreCase) ||
                                   SymbolName.StartsWith(decision.symbol, StringComparison.OrdinalIgnoreCase) ||
                                   decision.symbol.StartsWith(SymbolName, StringComparison.OrdinalIgnoreCase) ||
                                   string.Equals(decision.symbol.Replace("/", ""), SymbolName.Replace("/", ""), StringComparison.OrdinalIgnoreCase);
                    if (!symMatch)
                    {
                        Print($"[Security Alert] Symbol mismatch! Expected '{SymbolName}', but received '{decision.symbol}'. Action DISCARDED!");
                        return;
                    }
                }

                // 2. Strict Correlation Request ID Verification
                if (!string.IsNullOrEmpty(expectedRequestId) && !string.IsNullOrEmpty(decision.request_id) && !string.Equals(decision.request_id, expectedRequestId, StringComparison.OrdinalIgnoreCase))
                {
                    Print($"[Security Alert] RequestID mismatch! Expected '{expectedRequestId}', but received '{decision.request_id}'. Action DISCARDED!");
                    return;
                }

                // 3. Timeframe Verification
                if (!string.IsNullOrEmpty(decision.timeframe))
                {
                    bool tfMatch = string.Equals(decision.timeframe, TimeFrame.Name, StringComparison.OrdinalIgnoreCase) ||
                                   string.Equals(decision.timeframe, TimeFrame.ToString(), StringComparison.OrdinalIgnoreCase);
                    if (!tfMatch)
                    {
                        Print($"[Security Alert] Timeframe mismatch! Expected '{TimeFrame}', but received '{decision.timeframe}'. Action DISCARDED!");
                        return;
                    }
                }

                // 4. Bot ID Verification
                if (!string.IsNullOrEmpty(decision.bot_id) && !string.IsNullOrEmpty(BotId) && !string.Equals(decision.bot_id, BotId, StringComparison.OrdinalIgnoreCase))
                {
                    Print($"[Security Alert] BotID mismatch! Expected '{BotId}', but received '{decision.bot_id}'. Action DISCARDED!");
                    return;
                }

                string action = (decision.action ?? "").Trim().ToUpperInvariant();
                if (ShowLogs) Print($"[AI Decision] Action: {action} | Conf: {decision.confidence:F1}% | Reason: {decision.reason}");

                if (action == "HOLD" || action == "NO_ACTION" || action == "NOACTION")
                {
                    return;
                }

                if (action == "CLOSE_ALL")
                {
                    foreach (var pos in GetBotPositions())
                    {
                        ClosePosition(pos);
                    }
                    Print($"[AI Emergency Exit] Closed all positions. Reason: {decision.reason}");
                    if (EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                    {
                        _ = SendTelegramMessageAsync($"🚨 <b>[FlowRSI AI Agent] CLOSE_ALL Executed</b>\nReason: {decision.reason}\nConfidence: {decision.confidence:F1}%");
                    }
                    return;
                }

                // Handle AI ADJUST
                if (action == "ADJUST")
                {
                    var openPositions = GetBotPositions();
                    if (openPositions.Count > 0)
                    {
                        foreach (var pos in openPositions)
                        {
                            double? targetSL = decision.new_sl_price;
                            double? targetTP = decision.new_tp_price;

                            if ((!targetSL.HasValue || targetSL.Value <= 0) && decision.sl_pips.HasValue && decision.sl_pips.Value > 0)
                            {
                                double slPrice = pos.TradeType == TradeType.Buy
                                    ? Symbol.Bid - decision.sl_pips.Value * Symbol.PipSize
                                    : Symbol.Ask + decision.sl_pips.Value * Symbol.PipSize;
                                targetSL = Math.Round(slPrice, Symbol.Digits);
                            }

                            if ((!targetTP.HasValue || targetTP.Value <= 0) && decision.tp_pips.HasValue && decision.tp_pips.Value > 0)
                            {
                                double tpPrice = pos.TradeType == TradeType.Buy
                                    ? Symbol.Ask + decision.tp_pips.Value * Symbol.PipSize
                                    : Symbol.Bid - decision.tp_pips.Value * Symbol.PipSize;
                                targetTP = Math.Round(tpPrice, Symbol.Digits);
                            }


                            // Strict One-Way Profit Ratchet: Only accept AI proposed SL if it improves/protects profit more than current SL
                            if (targetSL.HasValue && pos.StopLoss.HasValue)
                            {
                                if (pos.TradeType == TradeType.Buy && targetSL.Value < pos.StopLoss.Value)
                                {
                                    if (ShowLogs) Print($"[AI ADJUST Discarded SL] Proposed SL ({targetSL.Value:F2}) is <= current SL ({pos.StopLoss.Value:F2}) on BUY #{pos.Id}. Retaining existing SL.");
                                    targetSL = pos.StopLoss;
                                }
                                else if (pos.TradeType == TradeType.Sell && targetSL.Value > pos.StopLoss.Value)
                                {
                                    if (ShowLogs) Print($"[AI ADJUST Discarded SL] Proposed SL ({targetSL.Value:F2}) is >= current SL ({pos.StopLoss.Value:F2}) on SELL #{pos.Id}. Retaining existing SL.");
                                    targetSL = pos.StopLoss;
                                }
                            }

                            if (targetSL.HasValue || targetTP.HasValue)
                            {
                                SafeModifyPosition(pos, targetSL, targetTP, pos.HasTrailingStop, source: $"AI ADJUST ({decision.reason})");
                            }
                        }

                        if (SendAiAdjustAlerts && EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                        {
                            string slStr = decision.new_sl_price.HasValue && decision.new_sl_price.Value > 0 ? $"{decision.new_sl_price.Value:F2}" : $"{decision.sl_pips ?? 0:F0} pips";
                            string tpStr = decision.new_tp_price.HasValue && decision.new_tp_price.Value > 0 ? $"{decision.new_tp_price.Value:F2}" : $"{decision.tp_pips ?? 0:F0} pips";
                            _ = SendTelegramMessageAsync($"⚙️ <b>[FlowRSI AI Agent] ADJUST Evaluated</b>\n• Target SL: <b>{slStr}</b> | TP: <b>{tpStr}</b>\n• Positions: {openPositions.Count}\n• Reason: <i>{decision.reason}</i>");
                        }
                    }
                    return;
                }

                // If allowedDirection was MANAGE_ONLY, do NOT allow BUY or SELL
                if (allowedDirection == "MANAGE_ONLY")
                {
                    if (action == "BUY" || action == "SELL")
                    {
                        if (ShowLogs) Print($"[AI Position Eval] Received {action} during open position evaluation. Ignored (no new entries in management mode).");
                    }
                    return;
                }

                // The snapshot asked about one specific direction. An answer against it means
                // the decision was formed on a different setup than the one sent, and the
                // SL/TP we are holding belong to the opposite side of the market - which then
                // hits the wrong-side stop path in ExecuteTechnicalOrder. Refuse it.
                if ((allowedDirection == "BUY" || allowedDirection == "SELL") &&
                    (action == "BUY" || action == "SELL") &&
                    action != allowedDirection)
                {
                    Print($"[Security Alert] AI returned {action} against a {allowedDirection} candidate on {SymbolName}. Entry refused (SL/TP were computed for {allowedDirection}). Reason: {decision.reason}");
                    return;
                }

                // Handle BUY / SELL
                if (action == "BUY" || action == "SELL")
                {
                    if (decision.confidence < AiConfidenceThreshold)
                    {
                        if (ShowLogs) Print($"[AI Gate Rejected] Decision {action} confidence ({decision.confidence:F1}%) < threshold ({AiConfidenceThreshold}%).");
                        return;
                    }

                    // Check active positions limit
                    if (GetBotPositions().Count >= MaxPositionsAllowed)
                    {
                        if (ShowLogs) Print($"[AI Entry Blocked] Max positions limit ({MaxPositionsAllowed}) reached.");
                        return;
                    }

                    // News filter guard
                    if (IsNewsSuspensionActive(out string newsReason))
                    {
                        if (ShowLogs) Print($"[AI Entry Blocked] Market entry suspended due to High-Impact News event window: {newsReason}");
                        return;
                    }

                    // Circuit breaker guard
                    if (_circuitBreakerTriggered)
                    {
                        if (ShowLogs) Print("[AI Entry Blocked] Circuit breaker active. Entries blocked.");
                        return;
                    }

                    // AI confirms entry: compute 100% internal risk sizing (AI has 0% volume authority)
                    double finalSL = decision.new_sl_price.HasValue && decision.new_sl_price.Value > 0 ? decision.new_sl_price.Value : fallbackSL;
                    double finalTP = decision.new_tp_price.HasValue && decision.new_tp_price.Value > 0 ? decision.new_tp_price.Value : fallbackTP;

                    ExecuteTechnicalOrder(action, finalSL, finalTP, $"AI Confirmed ({decision.confidence:F0}%) - {decision.reason}");
                }
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ExecuteDecision Error] {ex.Message}");
            }
        }
        #endregion

        #region Order Execution & Dynamic Risk Management
        private void ExecuteTechnicalOrder(string action, double slPrice, double tpPrice, string reason)
        {
            TradeType tradeType = action == "BUY" ? TradeType.Buy : TradeType.Sell;
            double currentPrice = tradeType == TradeType.Buy ? Symbol.Ask : Symbol.Bid;
            double slDistancePips = Math.Abs(currentPrice - slPrice) / Symbol.PipSize;

            double effectiveMinSl = MinSlFloorPips > 0 ? MinSlFloorPips : 15.0;
            string symUpperExec = SymbolName.ToUpperInvariant();
            if (symUpperExec.Contains("XAU") || symUpperExec.Contains("GOLD"))
                effectiveMinSl = Math.Max(effectiveMinSl, 150.0);
            else if (symUpperExec.Contains("JPY"))
                effectiveMinSl = Math.Max(effectiveMinSl, 18.0);

            if (slDistancePips < effectiveMinSl)
            {
                slDistancePips = effectiveMinSl;
                if (tradeType == TradeType.Buy)
                    slPrice = Symbol.Bid - (effectiveMinSl * Symbol.PipSize);
                else
                    slPrice = Symbol.Ask + (effectiveMinSl * Symbol.PipSize);

                // Re-adjust TP if needed to preserve target RR
                double minTpDistance = effectiveMinSl * TargetRiskReward * Symbol.PipSize;
                if (tradeType == TradeType.Buy && (tpPrice - Symbol.Ask) < minTpDistance)
                    tpPrice = Symbol.Ask + minTpDistance;
                else if (tradeType == TradeType.Sell && (Symbol.Bid - tpPrice) < minTpDistance)
                    tpPrice = Symbol.Bid - minTpDistance;
            }

            // Dynamic Volume Calculation (100% computed by cBot Risk Engine)
            double targetUnits = CalculateDynamicVolumeInUnits(slDistancePips);

            // Pre-flight broker boundary checks
            double minStopBuffer = Math.Max(Symbol.Spread * 3, Symbol.TickSize * 10);

            // A stop on the WRONG SIDE of the market is not a boundary problem: it means the
            // direction and the stop disagree. slDistancePips is measured with Math.Abs, so
            // such a stop still produced a LARGE distance and sized the volume accordingly,
            // and the clamp below then cut the stop to ~3x spread. Refuse the order instead.
            if ((tradeType == TradeType.Buy && slPrice >= Symbol.Bid) ||
                (tradeType == TradeType.Sell && slPrice <= Symbol.Ask))
            {
                Print($"[Security Alert] Order REJECTED: {tradeType} with stop {slPrice:F5} on the wrong side of market (Bid {Symbol.Bid:F5} / Ask {Symbol.Ask:F5}). Reason: {reason}");
                return;
            }

            if (tradeType == TradeType.Buy && slPrice >= (Symbol.Bid - minStopBuffer))
                slPrice = Symbol.Bid - minStopBuffer - (Symbol.PipSize * 2);
            else if (tradeType == TradeType.Sell && slPrice <= (Symbol.Ask + minStopBuffer))
                slPrice = Symbol.Ask + minStopBuffer + (Symbol.PipSize * 2);

            // The clamp above can pull the stop closer than the distance targetUnits was
            // sized for, which multiplies the intended dollar risk. Re-size to the final stop.
            double clampedSlDistancePips = Math.Abs(currentPrice - slPrice) / Symbol.PipSize;
            if (Math.Abs(clampedSlDistancePips - slDistancePips) > 0.01)
            {
                if (ShowLogs) Print($"[Pre-flight Resize] Boundary check moved the stop: {slDistancePips:F1}p -> {clampedSlDistancePips:F1}p. Re-sizing volume to hold risk constant.");
                slDistancePips = clampedSlDistancePips;
                targetUnits = CalculateDynamicVolumeInUnits(slDistancePips);
            }

            // ExecuteMarketOrder(..., label, stopLossPips, takeProfitPips, comment) takes DISTANCES IN PIPS,
            // not absolute prices. Convert from the final SL/TP levels relative to the entry side
            // (Buy fills at Ask, Sell at Bid) so the broker places them exactly where we log them.
            double entryRefPrice = tradeType == TradeType.Buy ? Symbol.Ask : Symbol.Bid;
            double slPips = Math.Abs(entryRefPrice - slPrice) / Symbol.PipSize;
            double tpPips = Math.Abs(tpPrice - entryRefPrice) / Symbol.PipSize;

            if (targetUnits <= 0)
            {
                if (ShowLogs) Print($"[Order Skipped] Risk engine refused a volume for {tradeType} {SymbolName} at {slDistancePips:F1}p. Reason: {reason}");
                return;
            }

            var result = ExecuteMarketOrder(tradeType, SymbolName, targetUnits, BotId, slPips, tpPips, BotId);

            if (result.IsSuccessful)
            {
                _initialSlDistances[result.Position.Id] = Math.Abs(result.Position.EntryPrice - slPrice);
                Print($"[Order Executed] {tradeType} {targetUnits / Symbol.LotSize:F2} lots #{result.Position.Id} at {result.Position.EntryPrice:F5} | SL: {slPrice:F5}, TP: {tpPrice:F5} | Reason: {reason}");

                // Centralized Portfolio Hub Report (Triggers Telegram notification via server)
                ReportPositionOpen(result.Position, slDistancePips, Math.Abs(result.Position.EntryPrice - tpPrice) / Symbol.PipSize, reason);

                if (EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                {
                    string msg = $"🎯 <b>[FlowRSI Signal Executed]</b>\n• Lệnh: <b>{tradeType}</b>\n• Khối lượng: <b>{targetUnits / Symbol.LotSize:F2} lots</b>\n• Giá vào: <code>{result.Position.EntryPrice:F5}</code>\n• Stop Loss: <code>{slPrice:F5}</code>\n• Take Profit: <code>{tpPrice:F5}</code>\n• Lý do: <i>{reason}</i>\n• Tài khoản: <code>{Account.Number}</code> | Equity: <b>${Account.Equity:F2}</b>";
                    SendTelegramWithOptionalScreenshot(msg, SendChartScreenshot);
                }

                SendLiveTickTelemetry(force: true);
            }
            else
            {
                Print($"[Order Execution Failed] {tradeType} rejected: {result.Error}");
            }
        }

        private double CalculateDynamicVolumeInUnits(double slPips)
        {
            double equityRiskAmount = Account.Equity * (RiskPercentage / 100.0);
            double effectiveRiskAmount = Math.Min(equityRiskAmount, MaxRiskPerTradeMoney);

            double pipValue = Symbol.PipValue > 0 ? Symbol.PipValue : 1.0;
            double targetUnits = effectiveRiskAmount / (slPips * pipValue);

            double normalizedUnits = Symbol.NormalizeVolumeInUnits(targetUnits);
            if (normalizedUnits < Symbol.VolumeInUnitsMin) normalizedUnits = Symbol.VolumeInUnitsMin;
            if (normalizedUnits > Symbol.VolumeInUnitsMax) normalizedUnits = Symbol.VolumeInUnitsMax;

            // Clamping up to the broker minimum can blow straight past the configured cap on a
            // small account trading a large-minimum instrument (US30, BTC, indices) with a wide
            // technical stop. Refuse rather than place an order that breaks MaxRiskPerTradeMoney.
            double finalRisk = normalizedUnits * slPips * pipValue;
            if (finalRisk > MaxRiskPerTradeMoney)
            {
                Print($"[Guardrail] Entry REFUSED: broker minimum {normalizedUnits / Symbol.LotSize:F2} lots at {slPips:F1}p risks ${finalRisk:F2}, over MaxRiskPerTradeMoney (${MaxRiskPerTradeMoney:F2}).");
                return 0;
            }

            return normalizedUnits;
        }
        #endregion

        #region Position Exit Management & SafeModifyPosition Engine
        private void ManageExits()
        {
            foreach (var pos in GetBotPositions())
            {
                double pnlPips = (pos.TradeType == TradeType.Buy ? (Symbol.Bid - pos.EntryPrice) : (pos.EntryPrice - Symbol.Ask)) / Symbol.PipSize;
                double initialSlDist;
                if (_initialSlDistances.ContainsKey(pos.Id))
                {
                    initialSlDist = _initialSlDistances[pos.Id] / Symbol.PipSize;
                }
                else
                {
                    // No recorded distance (restart, and the server lookup found no match). The
                    // CURRENT stop is not the initial risk: on a position already moved to
                    // break-even it is ~0.5p, which would inflate currentRr ~50x and fire the
                    // trailing stop on the first tick. Floor it at the same minimum the entry
                    // sizing uses so R stays bounded.
                    double measured = pos.StopLoss.HasValue
                        ? Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / Symbol.PipSize
                        : 20.0;

                    double effectiveMinSl = MinSlFloorPips > 0 ? MinSlFloorPips : 15.0;
                    string symUpperRestore = SymbolName.ToUpperInvariant();
                    if (symUpperRestore.Contains("XAU") || symUpperRestore.Contains("GOLD"))
                        effectiveMinSl = Math.Max(effectiveMinSl, 150.0);
                    else if (symUpperRestore.Contains("JPY"))
                        effectiveMinSl = Math.Max(effectiveMinSl, 18.0);

                    initialSlDist = Math.Max(measured, effectiveMinSl);
                }

                if (initialSlDist <= 0) initialSlDist = 20.0;

                double currentRr = pnlPips / initialSlDist;
                bool isBeAchieved = IsBreakEvenAchieved(pos);

                // ── 1. True Zero-Loss Break-Even Move ──
                if (EnableBreakEven && !isBeAchieved)
                {
                    double minRequiredPips = MinBreakEvenPips > 0 ? MinBreakEvenPips : 10.0;
                    string symUpper = SymbolName.ToUpperInvariant();
                    if (symUpper.Contains("XAU") || symUpper.Contains("GOLD"))
                        minRequiredPips = Math.Max(minRequiredPips, 150.0);
                    else if (symUpper.Contains("JPY"))
                        minRequiredPips = Math.Max(minRequiredPips, 15.0);

                    bool beTriggered = BeMode == BreakEvenTriggerMode.Risk_Reward_Ratio 
                        ? (currentRr >= BreakEvenTriggerRr && pnlPips >= minRequiredPips) 
                        : (pnlPips >= Math.Max(BreakEvenTriggerPips, minRequiredPips));

                    if (beTriggered)
                    {
                        double zeroLossSL = GetZeroLossStopLossPrice(pos, extraBufferPips: BreakEvenExtraPips);
                        bool shouldMove = pos.TradeType == TradeType.Buy 
                            ? (!pos.StopLoss.HasValue || zeroLossSL > pos.StopLoss.Value)
                            : (!pos.StopLoss.HasValue || zeroLossSL < pos.StopLoss.Value);

                        if (shouldMove)
                        {
                            var res = SafeModifyPosition(pos, zeroLossSL, pos.TakeProfit, source: "BreakEven Move");
                            if (res != null && res.IsSuccessful)
                            {
                                _breakevenApplied.Add(pos.Id);

                                // Partial close at BE if configured
                                if (PartialCloseRatio > 0 && PartialCloseRatio < 1.0)
                                {
                                    double volToClose = Symbol.NormalizeVolumeInUnits(pos.VolumeInUnits * PartialCloseRatio);
                                    if (volToClose >= Symbol.VolumeInUnitsMin && (pos.VolumeInUnits - volToClose) >= Symbol.VolumeInUnitsMin)
                                    {
                                        double pnlBeforePartial = pos.NetProfit;
                                        var partialRes = ClosePosition(pos, volToClose);
                                        if (partialRes != null && partialRes.IsSuccessful)
                                        {
                                            // Prefer the booked deal - it carries commission and swap, which is
                                            // what "realised P&L" has to mean for the DB. History is not always
                                            // populated the instant the call returns, so fall back to the NetProfit
                                            // delta, i.e. the unrealised P&L the closed slice was carrying.
                                            double realizedPnl = pnlBeforePartial - pos.NetProfit;
                                            try
                                            {
                                                var partialHist = History.LastOrDefault(h => h.PositionId == pos.Id);
                                                if (partialHist != null) realizedPnl = partialHist.NetProfit;
                                            }
                                            catch { }
                                            Print($"[Partial Close] #{pos.Id} closed {volToClose / Symbol.LotSize:F2} lots at Break-Even.");
                                            ReportPartialClose(pos, volToClose / Symbol.LotSize, realizedPnl, "Partial close at Break-Even");
                                        }
                                        else if (ShowLogs)
                                        {
                                            Print($"[Partial Close Failed] #{pos.Id}: {partialRes?.Error}");
                                        }
                                    }
                                }
                                Print($"[BreakEven Achieved] #{pos.Id} SL -> {zeroLossSL:F5} (EstNet@SL=${CalculateEstimatedNetProfitAtSL(pos, zeroLossSL):F2})");
                            }
                        }
                    }
                }

                // ── 2. Gated Trailing Stop (Activates ONLY when profit >= TrailingStopTriggerRr) ──
                // CRITICAL FIX: Decouple from isBeAchieved!
                // At 1.0R, Break-Even moves SL to Entry + buffer to secure Zero-Loss.
                // The position MUST be granted breathing room to run and ride the trend above 1.0R.
                //
                // The trigger is clamped against THIS position's take profit first. With
                // TpMode = Risk_Reward_Ratio the target sits at TargetRiskReward, and lower once
                // the spread on the stop is paid (the ETHUSD entry of 2026-09-22 reported
                // 2891.3p / 4239.9p = 1.47R; BTCUSD 1.40R). A trigger at or above that is
                // unreachable -- the trade closes at TP before currentRr ever arrives -- which
                // silently turned every TrailingStopDistancePips in the preset table into dead
                // config. Arm partway to the target instead, still clear of the 1.0R BE move.
                const double armFractionOfTp = 0.8;
                double effectiveTrailTriggerRr = TrailingStopTriggerRr;
                if (pos.TakeProfit.HasValue && initialSlDist > 0)
                {
                    double tpRr = Math.Abs(pos.TakeProfit.Value - pos.EntryPrice) / Symbol.PipSize / initialSlDist;
                    if (tpRr > 0 && effectiveTrailTriggerRr >= tpRr)
                        effectiveTrailTriggerRr = tpRr * armFractionOfTp;
                }

                if (EnableTrailingStop && currentRr >= effectiveTrailTriggerRr)
                {
                    string symUp = SymbolName.ToUpperInvariant();
                    double minTrailDistPips = TrailingStopDistancePips;
                    if (symUp.Contains("XAU") || symUp.Contains("GOLD"))
                        minTrailDistPips = Math.Max(minTrailDistPips, 350.0); // min $3.50 for Gold
                    else if (symUp.Contains("JPY"))
                        minTrailDistPips = Math.Max(minTrailDistPips, 25.0);  // min 25 pips for JPY
                    else if (symUp.Contains("US30") || symUp.Contains("USTEC") || symUp.Contains("DE40") || symUp.Contains("UK100"))
                        minTrailDistPips = Math.Max(minTrailDistPips, 350.0); // min 350 pips for Indices
                    else
                        minTrailDistPips = Math.Max(minTrailDistPips, 20.0);  // min 20 pips for Forex

                    // Tiered Trailing: Normal trailing gives breathing room (100% of initial SL distance).
                    // Tier 2 (currentRr >= 2.5R): Tighten to 60% of initial SL distance to lock in profits.
                    double trailMultiplier = currentRr >= 2.5 ? 0.6 : 1.0;
                    double effectiveTrailDistPips = Math.Max(minTrailDistPips, initialSlDist * trailMultiplier);

                    double candidateTrailSL;
                    if (pos.TradeType == TradeType.Buy)
                    {
                        candidateTrailSL = Symbol.Bid - (effectiveTrailDistPips * Symbol.PipSize);
                        candidateTrailSL = GetZeroLossStopLossPrice(pos, candidateTrailSL, extraBufferPips: BreakEvenExtraPips);
                        if ((!pos.StopLoss.HasValue || candidateTrailSL > pos.StopLoss.Value) && candidateTrailSL < Symbol.Bid)
                        {
                            SafeModifyPosition(pos, candidateTrailSL, pos.TakeProfit, source: "Trailing Stop");
                        }
                    }
                    else
                    {
                        candidateTrailSL = Symbol.Ask + (effectiveTrailDistPips * Symbol.PipSize);
                        candidateTrailSL = GetZeroLossStopLossPrice(pos, candidateTrailSL, extraBufferPips: BreakEvenExtraPips);
                        if ((!pos.StopLoss.HasValue || candidateTrailSL < pos.StopLoss.Value) && candidateTrailSL > Symbol.Ask)
                        {
                            SafeModifyPosition(pos, candidateTrailSL, pos.TakeProfit, source: "Trailing Stop");
                        }
                    }
                }
            }
        }

        /// <summary>
        /// Calculates the estimated Net Profit/Loss ($) if the position closes at slPrice,
        /// including full round-trip commission and accumulated negative swap fees.
        /// Matches the estimated PnL shown by cTrader UI when dragging Stop Loss.
        /// </summary>
        public double CalculateEstimatedNetProfitAtSL(Position pos, double slPrice)
        {
            if (pos == null) return 0.0;

            double pipsDiff = pos.TradeType == TradeType.Buy
                ? (slPrice - pos.EntryPrice) / Symbol.PipSize
                : (pos.EntryPrice - slPrice) / Symbol.PipSize;

            double pipMonetaryValue = pos.VolumeInUnits * Symbol.PipValue;
            if (pipMonetaryValue <= 0) pipMonetaryValue = 1.0;

            double grossProfitAtSL = pipsDiff * pipMonetaryValue;
            double totalCommission = Math.Abs(pos.Commissions) * 2.0;
            double negativeSwap = pos.Swap < 0 ? Math.Abs(pos.Swap) : 0.0;
            double totalFees = totalCommission + negativeSwap;

            return grossProfitAtSL - totalFees;
        }

        /// <summary>
        /// Finds the minimum Stop Loss price required so that Estimated Net Profit >= 0 (Zero-Loss).
        /// If candidateSL results in estimatedNetProfit < 0, shifts candidateSL towards profit until >= 0.
        /// </summary>
        public double GetZeroLossStopLossPrice(Position pos, double? candidateSL = null, double extraBufferPips = 0.5)
        {
            if (pos == null) return 0.0;

            double totalCommission = Math.Abs(pos.Commissions) * 2.0;
            double negativeSwap = pos.Swap < 0 ? Math.Abs(pos.Swap) : 0.0;
            double totalFees = totalCommission + negativeSwap;

            double pipMonetaryValue = pos.VolumeInUnits * Symbol.PipValue;
            if (pipMonetaryValue <= 0) pipMonetaryValue = 1.0;

            double feePips = totalFees / pipMonetaryValue;
            double safeBufferPips = feePips + Math.Max(0.0, extraBufferPips);

            double zeroLossPrice = pos.TradeType == TradeType.Buy
                ? pos.EntryPrice + safeBufferPips * Symbol.PipSize
                : pos.EntryPrice - safeBufferPips * Symbol.PipSize;

            if (!candidateSL.HasValue)
                return zeroLossPrice;

            if (pos.TradeType == TradeType.Buy)
                return candidateSL.Value < zeroLossPrice ? zeroLossPrice : candidateSL.Value;
            else
                return candidateSL.Value > zeroLossPrice ? zeroLossPrice : candidateSL.Value;
        }

        private bool IsBreakEvenAchieved(Position pos)
        {
            if (pos == null) return false;
            if (_breakevenApplied.Contains(pos.Id)) return true;

            // Self-healing check across bot restarts
            if (pos.StopLoss.HasValue && CalculateEstimatedNetProfitAtSL(pos, pos.StopLoss.Value) >= 0)
            {
                _breakevenApplied.Add(pos.Id);
                return true;
            }
            return false;
        }

        private TradeResult SafeModifyPosition(Position pos, double? targetSL, double? targetTP, bool? hasTrailingStop = null, string source = "")
        {
            if (pos == null) return null;

            double currentBid = Symbol.Bid;
            double currentAsk = Symbol.Ask;
            double minStopBuffer = Math.Max(Symbol.Spread * 3, Symbol.TickSize * 10);
            bool isModifyingSL = targetSL.HasValue && (!pos.StopLoss.HasValue || Math.Abs(targetSL.Value - pos.StopLoss.Value) > 0.00001);

            // ── Scenario 2 (True Break-Even First-Move & Zero-Loss Ratchet - strictly for BreakEven Move) ──
            if (isModifyingSL && targetSL.HasValue && source == "BreakEven Move")
            {
                bool isBeAchieved = IsBreakEvenAchieved(pos);
                if (!isBeAchieved)
                {
                    double zeroLossSL = GetZeroLossStopLossPrice(pos, targetSL.Value, extraBufferPips: BreakEvenExtraPips);
                    if (pos.TradeType == TradeType.Buy && targetSL.Value < zeroLossSL)
                    {
                        if (ShowLogs) Print($"[SafeModify Zero-Loss Shift] Target SL {targetSL.Value:F5} shifted to Zero-Loss BE: {zeroLossSL:F5} (EstNet@SL=${CalculateEstimatedNetProfitAtSL(pos, zeroLossSL):F2})");
                        targetSL = zeroLossSL;
                    }
                    else if (pos.TradeType == TradeType.Sell && targetSL.Value > zeroLossSL)
                    {
                        if (ShowLogs) Print($"[SafeModify Zero-Loss Shift] Target SL {targetSL.Value:F5} shifted to Zero-Loss BE: {zeroLossSL:F5} (EstNet@SL=${CalculateEstimatedNetProfitAtSL(pos, zeroLossSL):F2})");
                        targetSL = zeroLossSL;
                    }
                }
            }

            // Strict One-Way Profit Ratchet: Never loosen Stop Loss (protects against risk expansion)
            double? finalSL = targetSL ?? pos.StopLoss;
            if (targetSL.HasValue && pos.StopLoss.HasValue)
            {
                if (pos.TradeType == TradeType.Buy && targetSL.Value < pos.StopLoss.Value)
                    finalSL = pos.StopLoss.Value;
                else if (pos.TradeType == TradeType.Sell && targetSL.Value > pos.StopLoss.Value)
                    finalSL = pos.StopLoss.Value;
            }

            double? finalTP = targetTP ?? pos.TakeProfit;
            bool finalHasTrailingStop = hasTrailingStop ?? pos.HasTrailingStop;

            // Broker Pre-flight minStopBuffer validation (Never forcibly close winning trades at market!)
            if (pos.TradeType == TradeType.Sell)
            {
                if (finalSL.HasValue && finalSL.Value <= (currentAsk + minStopBuffer))
                {
                    if (ShowLogs) Print($"[SafeModify Pre-flight] SELL #{pos.Id}: Candidate SL {finalSL.Value:F5} too close to Ask {currentAsk:F5} (buffer {minStopBuffer:F5}). Retaining existing SL.");
                    finalSL = pos.StopLoss;
                }
                if (finalTP.HasValue && finalTP.Value >= (currentBid - minStopBuffer)) finalTP = pos.TakeProfit;
                if (finalSL.HasValue && finalTP.HasValue && finalSL.Value <= finalTP.Value) finalTP = pos.TakeProfit;
            }
            else if (pos.TradeType == TradeType.Buy)
            {
                if (finalSL.HasValue && finalSL.Value >= (currentBid - minStopBuffer))
                {
                    if (ShowLogs) Print($"[SafeModify Pre-flight] BUY #{pos.Id}: Candidate SL {finalSL.Value:F5} too close to Bid {currentBid:F5} (buffer {minStopBuffer:F5}). Retaining existing SL.");
                    finalSL = pos.StopLoss;
                }
                if (finalTP.HasValue && finalTP.Value <= (currentAsk + minStopBuffer)) finalTP = pos.TakeProfit;
                if (finalSL.HasValue && finalTP.HasValue && finalSL.Value >= finalTP.Value) finalTP = pos.TakeProfit;
            }

            // Scenario 9 (Anti-Spam Filter)
            double threshold = 0.5 * Symbol.PipSize;
            bool slChanged = (finalSL.HasValue != pos.StopLoss.HasValue) ||
                             (finalSL.HasValue && pos.StopLoss.HasValue && Math.Abs(finalSL.Value - pos.StopLoss.Value) > threshold);
            bool tpChanged = (finalTP.HasValue != pos.TakeProfit.HasValue) ||
                             (finalTP.HasValue && pos.TakeProfit.HasValue && Math.Abs(finalTP.Value - pos.TakeProfit.Value) > threshold);
            bool trailingChanged = (finalHasTrailingStop != pos.HasTrailingStop);

            if (!slChanged && !tpChanged && !trailingChanged)
                return null;

            try
            {
#pragma warning disable CS0618
                var res = ModifyPosition(pos, finalSL, finalTP, finalHasTrailingStop);
#pragma warning restore CS0618

                if (res != null && res.IsSuccessful)
                {
                    if (finalSL.HasValue && CalculateEstimatedNetProfitAtSL(pos, finalSL.Value) >= 0)
                    {
                        _breakevenApplied.Add(pos.Id);
                    }
                    if (ShowLogs) Print($"[{source}] Position #{pos.Id} modified successfully -> SL: {finalSL:F5}, TP: {finalTP:F5}");
                    SendLiveTickTelemetry(force: true);
                }
                return res;
            }
            catch (Exception modEx)
            {
                if (ShowLogs) Print($"[SafeModify Error] {source} for #{pos.Id}: {modEx.Message}");
                return null;
            }
        }
        #endregion

        #region Circuit Breaker & Helpers
        private void CheckCircuitBreaker()
        {
            if (!EnableHighWatermarkCut)
            {
                _circuitBreakerTriggered = false;
                return;
            }

            DateTime currentDate = Server.Time.Date;
            if (currentDate > _lastCircuitBreakerResetDate)
            {
                _lastCircuitBreakerResetDate = currentDate;
                _highWatermarkEquity = Account.Equity;
                _circuitBreakerTriggered = false;
            }

            if (Account.Equity > _highWatermarkEquity)
            {
                _highWatermarkEquity = Account.Equity;
            }

            double currentDrawdownPercent = ((_highWatermarkEquity - Account.Equity) / _highWatermarkEquity) * 100.0;
            if (currentDrawdownPercent >= HighWatermarkCutThreshold && !_circuitBreakerTriggered)
            {
                _circuitBreakerTriggered = true;
                Print($"[Circuit Breaker Triggered] Max Drawdown reached {currentDrawdownPercent:F2}% (Threshold: {HighWatermarkCutThreshold:F1}%). Halving risk & closing open positions.");
                foreach (var pos in GetBotPositions())
                {
                    ClosePosition(pos);
                }
                if (EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                {
                    _ = SendTelegramMessageAsync($"🚨 <b>[FlowRSI Circuit Breaker Triggered]</b>\nMax Drawdown reached <b>{currentDrawdownPercent:F2}%</b>. Closed open positions to protect capital.");
                }
            }
        }

        private List<Position> GetBotPositions()
        {
            return Positions.FindAll(BotId, SymbolName).ToList();
        }
        #endregion

        #region ForexFactory News Filter
        private bool IsCurrencyAffected(string newsCountry)
        {
            if (string.IsNullOrWhiteSpace(newsCountry)) return false;
            string country = newsCountry.Trim().ToUpperInvariant();

            // 1. Manual user override if specified
            if (!string.IsNullOrWhiteSpace(TargetNewsCurrencies))
            {
                var customCurrs = TargetNewsCurrencies.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries);
                foreach (var c in customCurrs)
                {
                    if (string.Equals(c.Trim(), country, StringComparison.OrdinalIgnoreCase))
                        return true;
                }
                return false;
            }

            string sym = SymbolName.Trim().ToUpperInvariant();

            // 2. Direct Forex & Cross match (e.g. EUR, USD, GBP, JPY, AUD, CAD, CHF, NZD in EURUSD, GBPJPY, etc.)
            if (sym.Contains(country)) return true;

            // 3. Metals (Gold / Silver)
            if ((sym.Contains("XAU") || sym.Contains("GOLD") || sym.Contains("XAG") || sym.Contains("SILVER")) && country == "USD")
                return true;

            // 4. US Indices
            if ((sym.Contains("US30") || sym.Contains("DJ30") || sym.Contains("DOW") ||
                 sym.Contains("USTEC") || sym.Contains("NAS100") || sym.Contains("US100") || sym.Contains("NDX") || sym.Contains("NASDAQ") ||
                 sym.Contains("US500") || sym.Contains("SPX500") || sym.Contains("SP500")) && country == "USD")
                return true;

            // 5. European Indices
            if ((sym.Contains("DE40") || sym.Contains("GER40") || sym.Contains("GER30") || sym.Contains("DAX") ||
                 sym.Contains("F40") || sym.Contains("FRA40") || sym.Contains("CAC40") || sym.Contains("STOXX")) && country == "EUR")
                return true;

            // 6. UK Indices
            if ((sym.Contains("UK100") || sym.Contains("FTSE")) && country == "GBP")
                return true;

            // 7. Japan Indices
            if ((sym.Contains("JP225") || sym.Contains("JPN225") || sym.Contains("NIKKEI")) && country == "JPY")
                return true;

            // 8. Australia Indices
            if ((sym.Contains("AUS200") || sym.Contains("ASX200")) && country == "AUD")
                return true;

            // 9. Hong Kong / China
            if ((sym.Contains("HK50") || sym.Contains("HSI")) && (country == "HKD" || country == "CNY" || country == "USD"))
                return true;

            // 10. Commodities (Crude Oil)
            if ((sym.Contains("OIL") || sym.Contains("WTI") || sym.Contains("BRENT") || sym.Contains("XTI") || sym.Contains("XBR")) && (country == "USD" || country == "CAD"))
                return true;

            // 11. Crypto
            if ((sym.Contains("BTC") || sym.Contains("ETH") || sym.Contains("SOL")) && country == "USD")
                return true;

            return false;
        }

        private bool IsNewsSuspensionActive()
        {
            return IsNewsSuspensionActive(out _);
        }

        private bool IsNewsSuspensionActive(out string activeNewsReason)
        {
            activeNewsReason = string.Empty;
            if (!EnableNewsFilter || RunningMode != RunningMode.RealTime) return false;

            DateTime nowUtc = DateTime.UtcNow;
            lock (_newsEvents)
            {
                foreach (var ev in _newsEvents)
                {
                    if (!IsCurrencyAffected(ev.Currency)) continue;

                    if (nowUtc >= ev.UtcTime.AddMinutes(-MinsBeforeNews) && nowUtc <= ev.UtcTime.AddMinutes(MinsAfterNews))
                    {
                        activeNewsReason = $"[{ev.Currency}] {ev.Title} ({ev.UtcTime:HH:mm} UTC)";
                        return true;
                    }
                }
            }
            return false;
        }

        private async Task FetchForexFactoryNewsAsync()
        {
            if (RunningMode != RunningMode.RealTime) return;

            while (!_isStopped)
            {
                try
                {
                    // 1. Primary: Query Centralized Local News Hub (127.0.0.1:8000/api/news/raw)
                    string hubBase = !string.IsNullOrWhiteSpace(ApiUrl) ? ApiUrl.TrimEnd('/') : "http://127.0.0.1:8000";
                    if (hubBase.EndsWith("/trade", StringComparison.OrdinalIgnoreCase))
                    {
                        hubBase = hubBase.Substring(0, hubBase.Length - 6);
                    }
                    string hubUrl = $"{hubBase}/api/news/raw?range=thisweek";

                    bool fetchedFromHub = false;
                    try
                    {
                        using (var hubReq = new HttpRequestMessage(System.Net.Http.HttpMethod.Get, hubUrl))
                        {
                            hubReq.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
                            hubReq.Headers.Add("Accept", "application/json");

                            var hubResp = await _httpClient.SendAsync(hubReq);
                            if (hubResp.IsSuccessStatusCode)
                            {
                                string hubJson = await hubResp.Content.ReadAsStringAsync();
                                ParseNewsEventsFromJson(hubJson);
                                _lastNewsFetchTime = DateTime.UtcNow;
                                fetchedFromHub = true;
                                if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter] Loaded {_newsEvents.Count} high/medium events from Centralized News Hub."));
                            }
                        }
                    }
                    catch (Exception hubEx)
                    {
                        if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Hub Unavailable] {hubEx.Message}. Falling back to direct ForexFactory..."));
                    }

                    // 2. Direct ForexFactory Fallback with Rate-Limit (429) Protection
                    if (!fetchedFromHub)
                    {
                        if (DateTime.UtcNow < _nextAllowedDirectFetchTime)
                        {
                            if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter Rate-Limit Guard] Skipping direct fetch until {_nextAllowedDirectFetchTime:HH:mm:ss} UTC (429 backoff)."));
                        }
                        else
                        {
                            // Small random jitter (500ms - 2500ms)
                            await Task.Delay(new Random().Next(500, 2500));

                            string jsonUrl = "https://nfs.faireconomy.media/ff_calendar_thisweek.json";
                            string xmlUrl = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml";

                            bool jsonSuccess = false;
                            try
                            {
                                using (var req = new HttpRequestMessage(System.Net.Http.HttpMethod.Get, jsonUrl))
                                {
                                    req.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36");
                                    req.Headers.Add("Accept", "application/json");

                                    var resp = await _httpClient.SendAsync(req);
                                    if (resp.IsSuccessStatusCode)
                                    {
                                        string json = await resp.Content.ReadAsStringAsync();
                                        ParseNewsEventsFromJson(json);
                                        _lastNewsFetchTime = DateTime.UtcNow;
                                        jsonSuccess = true;
                                        if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter] Loaded {_newsEvents.Count} high/medium events via Direct JSON."));
                                    }
                                    else if ((int)resp.StatusCode == 429 || (int)resp.StatusCode == 403)
                                    {
                                        _nextAllowedDirectFetchTime = DateTime.UtcNow.AddMinutes(15);
                                        _lastNewsFetchTime = DateTime.UtcNow;
                                        if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter WARNING] ForexFactory returned HTTP {(int)resp.StatusCode}. Rate-limit backoff activated for 15 minutes."));
                                    }
                                }
                            }
                            catch (Exception ex)
                            {
                                if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter JSON Error] {ex.Message}. Falling back to XML..."));
                            }

                            // Fallback XML
                            if (!jsonSuccess && DateTime.UtcNow >= _nextAllowedDirectFetchTime)
                            {
                                try
                                {
#pragma warning disable SYSLIB0014
                                    var xmlReq = (HttpWebRequest)WebRequest.Create(xmlUrl);
                                    xmlReq.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";
                                    xmlReq.Accept = "application/xml";
                                    xmlReq.Timeout = 10000;

                                    using (var resp = await xmlReq.GetResponseAsync())
                                    using (var stream = resp.GetResponseStream())
                                    using (var reader = new StreamReader(stream))
                                    {
                                        string xmlContent = await reader.ReadToEndAsync();
                                        ParseNewsEventsFromXml(xmlContent);
                                        _lastNewsFetchTime = DateTime.UtcNow;
                                        if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter] Loaded {_newsEvents.Count} high/medium events via Direct XML."));
                                    }
#pragma warning restore SYSLIB0014
                                }
                                catch (WebException webEx)
                                {
                                    if (webEx.Response is HttpWebResponse httpResp && ((int)httpResp.StatusCode == 429 || (int)httpResp.StatusCode == 403))
                                    {
                                        _nextAllowedDirectFetchTime = DateTime.UtcNow.AddMinutes(15);
                                        if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter WARNING] ForexFactory XML returned HTTP {(int)httpResp.StatusCode}. Backoff activated for 15 minutes."));
                                    }
                                    _lastNewsFetchTime = DateTime.UtcNow;
                                }
                                catch (Exception xmlEx)
                                {
                                    if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter XML Failed] {xmlEx.Message}"));
                                    _lastNewsFetchTime = DateTime.UtcNow;
                                }
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[News Filter Error] {ex.Message}"));
                }

                // Poll news every 15 minutes (alleviates server load & rate-limiting)
                await Task.Delay(TimeSpan.FromMinutes(15));
            }
        }

        private void ParseNewsEventsFromJson(string json)
        {
            using (var doc = JsonDocument.Parse(json))
            {
                lock (_newsEvents)
                {
                    _newsEvents.Clear();
                    foreach (var el in doc.RootElement.EnumerateArray())
                    {
                        string impact = el.GetProperty("impact").GetString() ?? "";
                        bool isHigh = impact.Equals("High", StringComparison.OrdinalIgnoreCase);
                        bool isMed = impact.Equals("Medium", StringComparison.OrdinalIgnoreCase);

                        if ((FilterHighImpact && isHigh) || (FilterMediumImpact && isMed))
                        {
                            string dateStr = el.GetProperty("date").GetString() ?? "";
                            if (DateTime.TryParse(dateStr, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal, out var eventTime))
                            {
                                _newsEvents.Add(new NewsEvent
                                {
                                    UtcTime = eventTime,
                                    Currency = el.GetProperty("country").GetString() ?? "",
                                    Impact = impact,
                                    Title = el.GetProperty("title").GetString() ?? ""
                                });
                            }
                        }
                    }
                }
            }
        }

        // ForexFactory's XML feed writes US Eastern times with NO offset, so they can only be
        // converted with an explicit zone. Windows and IANA ids are both tried: .NET on Linux
        // resolves IANA natively and Windows ids via ICU, and neither is guaranteed present.
        private static TimeZoneInfo ResolveEasternTimeZone()
        {
            foreach (var id in new[] { "America/New_York", "Eastern Standard Time" })
            {
                try { return TimeZoneInfo.FindSystemTimeZoneById(id); }
                catch { }
            }
            return null;
        }

        private static readonly TimeZoneInfo _easternTz = ResolveEasternTimeZone();

        private void ParseNewsEventsFromXml(string xml)
        {
            if (_easternTz == null)
            {
                Print("[News Filter WARNING] US Eastern time zone unavailable; ForexFactory XML times cannot be converted to UTC. XML fallback skipped (hub/JSON paths unaffected).");
                return;
            }

            var xdoc = XDocument.Parse(xml);
            lock (_newsEvents)
            {
                _newsEvents.Clear();
                foreach (var item in xdoc.Descendants("event"))
                {
                    string impact = item.Element("impact")?.Value ?? "";
                    bool isHigh = impact.Equals("High", StringComparison.OrdinalIgnoreCase);
                    bool isMed = impact.Equals("Medium", StringComparison.OrdinalIgnoreCase);

                    if ((FilterHighImpact && isHigh) || (FilterMediumImpact && isMed))
                    {
                        string dateStr = item.Element("date")?.Value ?? "";
                        string timeStr = item.Element("time")?.Value ?? "";
                        // Parse as wall-clock Eastern (no offset in the feed), then convert.
                        if (DateTime.TryParse($"{dateStr} {timeStr}", CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsedEastern))
                        {
                            var parsed = TimeZoneInfo.ConvertTimeToUtc(
                                DateTime.SpecifyKind(parsedEastern, DateTimeKind.Unspecified), _easternTz);
                            _newsEvents.Add(new NewsEvent
                            {
                                UtcTime = parsed,
                                Currency = item.Element("country")?.Value ?? "",
                                Impact = impact,
                                Title = item.Element("title")?.Value ?? ""
                            });
                        }
                    }
                }
            }
        }
        #endregion

        #region Telegram Alerts & Dashboard Telemetry
        private void SendTelegramWithOptionalScreenshot(string message, bool captureScreenshot)
        {
            if (!EnableTelegramAlerts || RunningMode != RunningMode.RealTime) return;

            if (captureScreenshot)
            {
                // Chart API is only safe to touch on the main thread. Positions.Closed and other
                // event handlers are not guaranteed to run there, so the capture is explicitly
                // marshaled via BeginInvokeOnMainThread regardless of the calling thread.
                BeginInvokeOnMainThread(() =>
                {
                    byte[] shot = null;
                    try { if (Chart != null) shot = Chart.TakeChartshot(); } catch { shot = null; }
                    _ = SendTelegramMessageAsync(message, shot);
                });
            }
            else
            {
                _ = SendTelegramMessageAsync(message, null);
            }
        }

        private async Task SendTelegramMessageAsync(string message, byte[] screenshotData = null)
        {
            if (!EnableTelegramAlerts || string.IsNullOrWhiteSpace(TelegramBotToken) || string.IsNullOrWhiteSpace(TelegramChatId) || RunningMode != RunningMode.RealTime)
                return;

            try
            {
                if (screenshotData != null)
                {
                    using (var form = new MultipartFormDataContent())
                    {
                        form.Add(new StringContent(TelegramChatId), "chat_id");
                        form.Add(new StringContent(message), "caption");
                        form.Add(new StringContent("HTML"), "parse_mode");

                        var imageContent = new ByteArrayContent(screenshotData);
                        imageContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("image/png");
                        form.Add(imageContent, "photo", "chartshot.png");

                        string photoUrl = $"https://api.telegram.org/bot{TelegramBotToken}/sendPhoto";
                        await _httpClient.PostAsync(photoUrl, form);
                        return;
                    }
                }

                string url = $"https://api.telegram.org/bot{TelegramBotToken}/sendMessage";
                var payload = new
                {
                    chat_id = TelegramChatId,
                    text = message,
                    parse_mode = "HTML"
                };

                var content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");
                await _httpClient.PostAsync(url, content);
            }
            catch (Exception ex)
            {
                if (ShowLogs) BeginInvokeOnMainThread(() => Print($"[Telegram Error] {ex.Message}"));
            }
        }

        private void SendLiveTickTelemetry(bool force = false)
        {
            if (RunningMode != RunningMode.RealTime) return;
            if (_httpClient == null) return;
            if (!force && (DateTime.UtcNow - _lastTickTelemetryTime).TotalSeconds < 10.0) return;
            _lastTickTelemetryTime = DateTime.UtcNow;

            try
            {
                var posList = new List<object>();
                foreach (var p in Positions.FindAll(BotId, SymbolName))
                {
                    posList.Add(new
                    {
                        id = p.Id,
                        symbol = p.SymbolName,
                        side = p.TradeType.ToString(),
                        volume = p.VolumeInUnits / Symbol.LotSize,
                        entry_price = p.EntryPrice,
                        current_price = p.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask,
                        net_profit = p.NetProfit,
                        pips = p.Pips,
                        sl_price = p.StopLoss,
                        tp_price = p.TakeProfit,
                        label = p.Label
                    });
                }

                var telemetry = new
                {
                    bot_id = BotId,
                    account_number = Account.Number.ToString(),
                    symbol = SymbolName,
                    bid = Symbol.Bid,
                    ask = Symbol.Ask,
                    equity = Account.Equity,
                    balance = Account.Balance,
                    positions = posList
                };

                string tickUrl = !string.IsNullOrEmpty(AiTelemetryUrl) ? AiTelemetryUrl : ApiUrl.Replace("/trade", "/api/tick");
                var json = JsonSerializer.Serialize(telemetry);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                Task.Run(async () =>
                {
                    try
                    {
                        await _httpClient.PostAsync(tickUrl, content);
                    }
                    catch { }
                });
            }
            catch
            {
                // ignore background telemetry failures
            }
        }

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            BeginInvokeOnMainThread(() =>
            {
                try
                {
                    var pos = args.Position;
                    if (pos == null) return;
                    if (pos.Label != BotId && pos.Comment != BotId) return;
                    if (pos.SymbolName != SymbolName) return;

                    // The live spread when this handler happens to run is not the price the
                    // position closed at: a stop swept by a spike that snaps back would be
                    // reported at a price that never traded. Prefer the booked deal.
                    double exitPrice = pos.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                    if (args.Reason == PositionCloseReason.TakeProfit && pos.TakeProfit.HasValue)
                        exitPrice = pos.TakeProfit.Value;
                    else if (args.Reason == PositionCloseReason.StopLoss && pos.StopLoss.HasValue)
                        exitPrice = pos.StopLoss.Value;

                    try
                    {
                        var hist = History.FirstOrDefault(h => h.PositionId == pos.Id);
                        if (hist != null && hist.ClosingPrice > 0)
                            exitPrice = hist.ClosingPrice;
                    }
                    catch { }

                    double pnl = pos.NetProfit;
                    string reason = args.Reason.ToString();

                    if (args.Reason == PositionCloseReason.StopLoss)
                    {
                        if (pnl > 0.5)
                            reason = "StopLoss (Trailing Profit-Lock)";
                        else if (IsBreakEvenAchieved(pos) && pnl >= -0.5)
                            reason = "StopLoss (Break-Even Zero-Loss)";
                        else
                        {
                            var holdingSeconds = (DateTime.UtcNow - pos.EntryTime.ToUniversalTime()).TotalSeconds;
                            reason = holdingSeconds < 180 ? "StopLoss (Initial Spike Sweep)" : "StopLoss (Initial Structural Reversal)";
                        }
                    }
                    else if (args.Reason == PositionCloseReason.TakeProfit)
                    {
                        reason = "TakeProfit (Target Hit)";
                    }
                    else if (args.Reason == PositionCloseReason.Closed)
                    {
                        reason = pnl >= 0 ? "Closed (Take Profit Early)" : "Closed (Cut Loss Early)";
                    }

                    ReportPositionClosed(pos, pnl, reason, exitPrice, pos.Pips);

                    if (EnableTelegramAlerts && RunningMode == RunningMode.RealTime)
                    {
                        string emoji = pnl >= 0 ? "🟢" : "🔴";
                        string msg = $"🏁 <b>[FlowRSI Position Closed]</b>\n• Lệnh: <b>{pos.TradeType}</b> #{pos.Id}\n• PnL: <b>{emoji} ${pnl:F2}</b> ({pos.Pips:+0.0;-0.0;0.0} pips)\n• Lý do: <code>{reason}</code>\n• Tài khoản: <code>{Account.Number}</code> | Equity: <b>${Account.Equity:F2}</b>";
                        SendTelegramWithOptionalScreenshot(msg, SendChartScreenshot);
                    }

                    SendLiveTickTelemetry(force: true);
                }
                catch (Exception ex)
                {
                    if (ShowLogs) Print($"[OnPositionClosed Error] {ex.Message}");
                }
            });
        }

        private void ReportPositionOpen(Position position, double slPips, double tpPips, string reason = "")
        {
            if (RunningMode != RunningMode.RealTime || _httpClient == null || position == null) return;
            try
            {
                // Synchronously pre-capture all values on the Main Thread
                int posId = position.Id;
                string posSymbol = position.SymbolName;
                string posSide = position.TradeType.ToString();
                double posLots = position.VolumeInUnits / Symbol.LotSize;
                double entryPrice = position.EntryPrice;
                double? slPrice = position.StopLoss;
                double? tpPrice = position.TakeProfit;
                string entryTimeStr = position.EntryTime.ToUniversalTime().ToString("o");

                string accNum = Account.Number.ToString();
                string accType = Account.IsLive ? "live" : "demo";
                string accLabel = Account.BrokerName;
                double accBalance = Account.Balance;
                double accEquity = Account.Equity;

                var indObj = new
                {
                    fast_rsi = _fastRsi != null && _fastRsi.Result.Count > 0 ? Math.Round(_fastRsi.Result.Last(1), 2) : 0.0,
                    slow_rsi = _slowRsi != null && _slowRsi.Result.Count > 0 ? Math.Round(_slowRsi.Result.Last(1), 2) : 0.0,
                    atr = _atr != null && _atr.Result.Count > 0 ? Math.Round(_atr.Result.Last(1), 4) : 0.0,
                    spread = Math.Round(Symbol.Spread / Symbol.PipSize, 1)
                };
                string indJson = JsonSerializer.Serialize(indObj);

                var report = new
                {
                    ctrader_id = posId,
                    bot_id = BotId,
                    action = "open",
                    symbol = posSymbol,
                    side = posSide,
                    volume = posLots,
                    entry_price = entryPrice,
                    sl_price = slPrice,
                    tp_price = tpPrice,
                    sl_pips = Math.Round(slPips, 1),
                    tp_pips = Math.Round(tpPips, 1),
                    reason = string.IsNullOrWhiteSpace(reason) ? "Technical / AI Entry" : reason,
                    entry_indicators = indJson,
                    entry_time = entryTimeStr,
                    account_number = accNum,
                    account_type = accType,
                    account_label = accLabel,
                    account_balance = accBalance,
                    account_equity = accEquity
                };

                var json = JsonSerializer.Serialize(report);
                var reportUrl = !string.IsNullOrWhiteSpace(AiReportUrl)
                    ? AiReportUrl.Trim()
                    : ApiUrl.Replace("/trade", "/portfolio/report");

                Task.Run(async () =>
                {
                    try
                    {
                        var content = new StringContent(json, Encoding.UTF8, "application/json");
                        await _httpClient.PostAsync(reportUrl, content);
                        if (ShowLogs)
                        {
                            BeginInvokeOnMainThread(() =>
                            {
                                Print($"[Portfolio Hub] Reported position open: #{posId} {posSide} {posSymbol}");
                            });
                        }
                    }
                    catch (Exception ex)
                    {
                        if (ShowLogs)
                        {
                            BeginInvokeOnMainThread(() =>
                            {
                                Print($"[Portfolio Hub] Failed to report position open: {ex.Message}");
                            });
                        }
                    }
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ReportPositionOpen Error] {ex.Message}");
            }
        }

        // _initialSlDistances lives in RAM and is lost on restart. The server already holds
        // the stop distance reported at entry, so recover it rather than re-deriving R from
        // whatever stop the position carries now - which for a position already at break-even
        // is near zero and inflates currentRr by an order of magnitude.
        private async Task RestoreInitialSlDistances()
        {
            if (RunningMode != RunningMode.RealTime || _httpClient == null) return;
            try
            {
                var baseUri = !string.IsNullOrWhiteSpace(AiReportUrl)
                    ? AiReportUrl.Replace("/portfolio/report", "").TrimEnd('/')
                    : ApiUrl.Replace("/trade", "").TrimEnd('/');
                string url = $"{baseUri}/portfolio/open-positions?bot_id={Uri.EscapeDataString(BotId)}&account_number={Account.Number}";

                var response = await _httpClient.GetAsync(url);
                if (!response.IsSuccessStatusCode) return;
                string body = await response.Content.ReadAsStringAsync();

                BeginInvokeOnMainThread(() =>
                {
                    try
                    {
                        using (var doc = JsonDocument.Parse(body))
                        {
                            if (!doc.RootElement.TryGetProperty("positions", out var rows)) return;

                            int restored = 0;
                            foreach (var pos in GetBotPositions())
                            {
                                foreach (var row in rows.EnumerateArray())
                                {
                                    string rowSymbol = row.TryGetProperty("symbol", out var sym) ? sym.GetString() : null;
                                    string rowSide = row.TryGetProperty("side", out var sd) ? sd.GetString() : null;
                                    if (!string.Equals(rowSymbol, pos.SymbolName, StringComparison.OrdinalIgnoreCase)) continue;
                                    if (!string.Equals(rowSide, pos.TradeType.ToString(), StringComparison.OrdinalIgnoreCase)) continue;

                                    // Match on entry price so this stays correct if MaxPositionsAllowed > 1.
                                    if (!row.TryGetProperty("entry_price", out var ep) || ep.ValueKind != JsonValueKind.Number) continue;
                                    if (Math.Abs(ep.GetDouble() - pos.EntryPrice) > Symbol.PipSize) continue;

                                    if (!row.TryGetProperty("sl_pips", out var sp) || sp.ValueKind != JsonValueKind.Number) continue;
                                    double slPipsRestored = sp.GetDouble();
                                    if (slPipsRestored <= 0) continue;

                                    _initialSlDistances[pos.Id] = slPipsRestored * Symbol.PipSize;
                                    restored++;
                                    break;
                                }
                            }

                            if (restored > 0) Print($"[FlowRSI] Restored initial SL distance for {restored} open position(s) after restart.");
                        }
                    }
                    catch (Exception ex)
                    {
                        if (ShowLogs) Print($"[RestoreInitialSlDistances Parse Error] {ex.Message}");
                    }
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[RestoreInitialSlDistances Error] {ex.Message}");
            }
        }

        // Positions.Closed does NOT fire on a partial close, so nothing else reports it.
        // Without this the profit taken at break-even never reaches the DB: the row keeps
        // its original volume and the final close only carries the remainder's P&L.
        private void ReportPartialClose(Position position, double closedLots, double realizedPnl, string reason = "")
        {
            if (RunningMode != RunningMode.RealTime || _httpClient == null || position == null) return;
            try
            {
                int posId = position.Id;
                string posSymbol = position.SymbolName;
                string posSide = position.TradeType.ToString();
                double remainingLots = position.VolumeInUnits / Symbol.LotSize;

                var report = new
                {
                    ctrader_id = posId,
                    bot_id = BotId,
                    action = "partial_close",
                    symbol = posSymbol,
                    side = posSide,
                    closed_volume = Math.Round(closedLots, 2),
                    remaining_volume = Math.Round(remainingLots, 2),
                    realized_pnl = Math.Round(realizedPnl, 2),
                    reason = string.IsNullOrWhiteSpace(reason) ? "Partial close at Break-Even" : reason,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = Account.BrokerName,
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                var json = JsonSerializer.Serialize(report);
                var reportUrl = !string.IsNullOrWhiteSpace(AiReportUrl)
                    ? AiReportUrl.Trim()
                    : ApiUrl.Replace("/trade", "/portfolio/report");

                Task.Run(async () =>
                {
                    try
                    {
                        var content = new StringContent(json, Encoding.UTF8, "application/json");
                        await _httpClient.PostAsync(reportUrl, content);
                        if (ShowLogs)
                        {
                            BeginInvokeOnMainThread(() =>
                            {
                                Print($"[Portfolio Hub] Reported partial close: #{posId} {closedLots:F2} lots for {realizedPnl:F2}, {remainingLots:F2} lots remaining");
                            });
                        }
                    }
                    catch (Exception ex)
                    {
                        if (ShowLogs)
                        {
                            string err = ex.Message;
                            BeginInvokeOnMainThread(() => Print($"[Portfolio Hub Error] Partial close report failed: {err}"));
                        }
                    }
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ReportPartialClose Error] {ex.Message}");
            }
        }

        private void ReportPositionClosed(Position position, double pnl, string reason = "", double? exitPrice = null, double? pips = null)
        {
            if (RunningMode != RunningMode.RealTime || _httpClient == null || position == null) return;
            try
            {
                // Synchronously pre-capture all values on the Main Thread
                int posId = position.Id;
                string posSymbol = position.SymbolName;
                string posSide = position.TradeType.ToString();
                double posLots = position.VolumeInUnits / Symbol.LotSize;
                double entryPrice = position.EntryPrice;
                double resolvedExitPrice = exitPrice ?? (position.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask);
                double resolvedPips = pips ?? position.Pips;
                string entryTimeStr = position.EntryTime.ToUniversalTime().ToString("o");
                string exitTimeStr = DateTime.UtcNow.ToString("o");

                string accNum = Account.Number.ToString();
                string accType = Account.IsLive ? "live" : "demo";
                string accLabel = Account.BrokerName;
                double accBalance = Account.Balance;
                double accEquity = Account.Equity;

                var report = new
                {
                    ctrader_id = posId,
                    bot_id = BotId,
                    action = "close",
                    symbol = posSymbol,
                    side = posSide,
                    volume = posLots,
                    entry_price = entryPrice,
                    exit_price = resolvedExitPrice,
                    pnl = pnl,
                    pips = Math.Round(resolvedPips, 1),
                    reason = string.IsNullOrWhiteSpace(reason) ? "Closed" : reason,
                    entry_time = entryTimeStr,
                    exit_time = exitTimeStr,
                    account_number = accNum,
                    account_type = accType,
                    account_label = accLabel,
                    account_balance = accBalance,
                    account_equity = accEquity
                };

                var json = JsonSerializer.Serialize(report);
                var reportUrl = !string.IsNullOrWhiteSpace(AiReportUrl)
                    ? AiReportUrl.Trim()
                    : ApiUrl.Replace("/trade", "/portfolio/report");

                Task.Run(async () =>
                {
                    try
                    {
                        var content = new StringContent(json, Encoding.UTF8, "application/json");
                        await _httpClient.PostAsync(reportUrl, content);
                        if (ShowLogs)
                        {
                            BeginInvokeOnMainThread(() =>
                            {
                                Print($"[Portfolio Hub] Reported position closed: #{posId} {posSide} PnL: {pnl:F2}");
                            });
                        }
                    }
                    catch (Exception ex)
                    {
                        if (ShowLogs)
                        {
                            BeginInvokeOnMainThread(() =>
                            {
                                Print($"[Portfolio Hub] Failed to report position closed: {ex.Message}");
                            });
                        }
                    }
                });
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[ReportPositionClosed Error] {ex.Message}");
            }
        }
        #endregion
    }
}
