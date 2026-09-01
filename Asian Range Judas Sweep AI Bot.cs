using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using cAlgo.API;
using cAlgo.API.Indicators;

namespace cAlgo.Robots
{
    public enum AiConnectionMode
    {
        Direct_OpenRouter_DashScope,
        Local_Python_Server
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class Asian_Range_Judas_Sweep_AI_Bot : Robot
    {
        [Parameter("SmartBot Template \nCreator: Nguyen van Cong\nTelegram:+84979404641\nEmail:nvcong89@live.com", DefaultValue = "Asian Range Judas Sweep AI Bot +84979404641")]
        public string Message { get; set; }

        #region Initial Setting
        [Parameter("Label", Group = "Initial Setting", DefaultValue = "Asian Range Judas Sweep AI Bot")]
        public string label { get; set; }

        #region AI Agent Parameters
        [Parameter("Direct AI Cloud API (OpenRouter/Qwen) ?", Group = "AI Agent Settings", DefaultValue = false)]
        public bool UseDirectAiApi { get; set; }

        public AiConnectionMode AiMode => UseDirectAiApi ? AiConnectionMode.Direct_OpenRouter_DashScope : AiConnectionMode.Local_Python_Server;

        [Parameter("Bot ID", Group = "AI Agent Settings", DefaultValue = "Asian Range Judas Sweep AI Bot")]
        public string BotId { get; set; }

        [Parameter("AI Endpoint URL", Group = "AI Agent Settings", DefaultValue = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions")]
        public string ApiUrl { get; set; }

        [Parameter("AI API Key (Bearer Token)", Group = "AI Agent Settings", DefaultValue = "")]
        public string AiApiKey { get; set; }

        [Parameter("AI Model Name", Group = "AI Agent Settings", DefaultValue = "qwen3.7-flash")]
        public string AiModelName { get; set; }

        [Parameter("AI Min Confidence (%)", Group = "AI Agent Settings", DefaultValue = 70.0, MinValue = 0.0, MaxValue = 100.0)]
        public double AiConfidenceThreshold { get; set; }

        [Parameter("AI Timeout (Seconds)", Group = "AI Agent Settings", DefaultValue = 300, MinValue = 5, MaxValue = 600)]
        public int aiTimeoutSeconds { get; set; }

        [Parameter("Enable Dashboard Telemetry", Group = "AI Agent Settings", DefaultValue = true)]
        public bool EnableDashboardTelemetry { get; set; }

        [Parameter("Dashboard Server URL", Group = "AI Agent Settings", DefaultValue = "http://127.0.0.1:8181")]
        public string DashboardServerUrl { get; set; }

        [Parameter("Account Label (optional)", Group = "AI Agent Settings", DefaultValue = "")]
        public string AccountLabel { get; set; }

        [Parameter("AI Gate Mode (Pre-filter: EMA cross â†’ AI entry)", Group = "AI Agent Settings", DefaultValue = true)]
        public bool UseAiGateMode { get; set; }

        [Parameter("AI SL Minimum Floor (pips, 0=disabled)", Group = "AI Agent Settings", DefaultValue = 200.0, MinValue = 0)]
        public double AiSlMinFloorPips { get; set; }
        #endregion

        [Parameter("Calculate on Bar Closed: ", Group = "Initial Setting", DefaultValue = true)]
        public bool _calculateOnBarClosed { get; set; }

        [Parameter("TradeType - BUY :", Group = "Initial Setting", DefaultValue = true)]
        public bool enableTradeTypeBuy { get; set; }

        [Parameter("TradeType - SELL : ", Group = "Initial Setting", DefaultValue = true)]
        public bool enableTradeTypeSELL { get; set; }

        [Parameter("Reverse Condition : ", Group = "Initial Setting", DefaultValue = false)]
        public bool reverseCondition { get; set; }

        [Parameter("Maximum number of orders allowed to be opened: ", Group = "Initial Setting", DefaultValue = 1)]
        public int maxPermittedOrder { get; set; }

        [Parameter("Show signal on Chart ? ", Group = "Initial Setting", DefaultValue = false)]
        public bool showSignal { get; set; }

        [Parameter("Show Indicator on Chart ? ", Group = "Initial Setting", DefaultValue = false)]
        public bool showIndicator { get; set; }

        [Parameter("Block reopening after manual close until close signal ? ", Group = "Initial Setting", DefaultValue = true)]
        public bool blockReopenUntilCloseSignal { get; set; }
        #endregion

        #region Strategy Parameters (Asian Range & Judas Sweep)
        [Parameter("Asian Session Start (UTC Hour)", Group = "Asian Range & Judas Sweep", DefaultValue = 0, MinValue = 0, MaxValue = 23)]
        public int asianStartHour { get; set; }

        [Parameter("Asian Session End (UTC Hour)", Group = "Asian Range & Judas Sweep", DefaultValue = 6, MinValue = 0, MaxValue = 23)]
        public int asianEndHour { get; set; }

        [Parameter("Min Asian Range (Pips)", Group = "Asian Range & Judas Sweep", DefaultValue = 50.0, MinValue = 10.0, MaxValue = 500.0)]
        public double minAsianRangePips { get; set; }

        [Parameter("Max Asian Range (Pips)", Group = "Asian Range & Judas Sweep", DefaultValue = 350.0, MinValue = 50.0, MaxValue = 1000.0)]
        public double maxAsianRangePips { get; set; }

        [Parameter("London Killzone Start (UTC Hour)", Group = "Asian Range & Judas Sweep", DefaultValue = 7, MinValue = 0, MaxValue = 23)]
        public int londonStartHour { get; set; }

        [Parameter("London Killzone End (UTC Hour)", Group = "Asian Range & Judas Sweep", DefaultValue = 10, MinValue = 0, MaxValue = 23)]
        public int londonEndHour { get; set; }

        [Parameter("NY Killzone Start (UTC Hour)", Group = "Asian Range & Judas Sweep", DefaultValue = 12, MinValue = 0, MaxValue = 23)]
        public int nyStartHour { get; set; }

        [Parameter("NY Killzone End (UTC Hour)", Group = "Asian Range & Judas Sweep", DefaultValue = 16, MinValue = 0, MaxValue = 23)]
        public int nyEndHour { get; set; }

        [Parameter("Judas Sweep Buffer (Pips)", Group = "Asian Range & Judas Sweep", DefaultValue = 15.0, MinValue = 1.0, MaxValue = 100.0)]
        public double sweepBufferPips { get; set; }

        [Parameter("Draw Asian Range Visuals", Group = "Asian Range & Judas Sweep", DefaultValue = true)]
        public bool drawAsianRangeVisuals { get; set; }

        [Parameter("Fast EMA Period", Group = "Strategy Indicators", DefaultValue = 9, MinValue = 1, MaxValue = 200)]
        public int fastEmaPeriod { get; set; }

        [Parameter("Slow EMA Period", Group = "Strategy Indicators", DefaultValue = 21, MinValue = 1, MaxValue = 500)]
        public int slowEmaPeriod { get; set; }

        [Parameter("Enable RSI Filter", Group = "Strategy Indicators", DefaultValue = false)]
        public bool enableRsiFilter { get; set; }

        [Parameter("RSI Period", Group = "Strategy Indicators", DefaultValue = 14, MinValue = 1, MaxValue = 100)]
        public int periodRSI { get; set; }

        [Parameter("RSI Overbought Level (Max for Buy)", Group = "Strategy Indicators", DefaultValue = 70.0, MinValue = 1.0, MaxValue = 100.0)]
        public double rsiOverbought { get; set; }

        [Parameter("RSI Oversold Level (Min for Sell)", Group = "Strategy Indicators", DefaultValue = 30.0, MinValue = 1.0, MaxValue = 100.0)]
        public double rsiOversold { get; set; }
        #endregion

        #region News Filter Parameters
        [Parameter("Enable Auto News Filter ?", Group = "News Filter", DefaultValue = true)]
        public bool enableNewsFilter { get; set; }

        [Parameter("Pause Before High News (Mins)", Group = "News Filter", DefaultValue = 30, MinValue = 5)]
        public int pauseBeforeNewsMins { get; set; }

        [Parameter("Pause After High News (Mins)", Group = "News Filter", DefaultValue = 30, MinValue = 5)]
        public int pauseAfterNewsMins { get; set; }

        [Parameter("Filter High Impact News Only", Group = "News Filter", DefaultValue = true)]
        public bool highImpactOnly { get; set; }

        [Parameter("Close Open Positions Before High News ?", Group = "News Filter", DefaultValue = false)]
        public bool closePositionsBeforeNews { get; set; }
        #endregion

        #region Setting Stop Loss and Take Profit
        private string _lastAgentReason = "";

        [Parameter("Enable StopLoss & TakeProfit to % ?", DefaultValue = true, Group = "Setting Stop Loss and Take Profit")]
        public bool SLTPpercentage { get; set; }

        [Parameter("Take Profit [%]", DefaultValue = 3.0, Group = "Setting Stop Loss and Take Profit", MinValue = 0.001)]
        public double takeprofitPercentage { get; set; }

        [Parameter("Stop Loss [%]", DefaultValue = 1.5, Group = "Setting Stop Loss and Take Profit", MinValue = 0.001)]
        public double stoplossPercentage { get; set; }

        [Parameter("Take Profit [pips]", DefaultValue = 300, Group = "Setting Stop Loss and Take Profit")]
        public double takeprofitPip { get; set; }

        [Parameter("Stop Loss [pips]", DefaultValue = 150, Group = "Setting Stop Loss and Take Profit")]
        public double stoplossPip { get; set; }
        #endregion

        #region Setting Trading Volume
        [Parameter("[Lots] Maximum allowed Volume per order", Group = "Setting Trading Volume", DefaultValue = 10)]
        public double maxVol { get; set; }

        [Parameter("Enable fixed Volume ?", Group = "Setting Trading Volume", DefaultValue = false)]
        public bool enableFixedVol { get; set; }

        [Parameter("[Lots] Fixed Volume: ", Group = "Setting Trading Volume", DefaultValue = 0.01)]
        public double _fixedVolLots { get; set; }

        [Parameter("Enable Volume by % risk of account ?", Group = "Setting Trading Volume", DefaultValue = true)]
        public bool _voltoAccount { get; set; }

        [Parameter("[%] Risk of account", Group = "Setting Trading Volume", DefaultValue = 10.0, MinValue = 0.1, MaxValue = 100, Step = 0.1)]
        public double riskFactor { get; set; }
        #endregion

        #region High-Watermark Equity Drawdown Protection (Circuit Breaker)
        [Parameter("Enable High-Watermark Equity Protection ?", Group = "Equity Protection (Circuit Breaker)", DefaultValue = false)]
        public bool enableEquityProtection { get; set; }

        [Parameter("Max Equity Drawdown Threshold (%)", Group = "Equity Protection (Circuit Breaker)", DefaultValue = 15.0, MinValue = 1.0, MaxValue = 80.0)]
        public double maxEquityDDPercent { get; set; }

        [Parameter("Risk Factor Reduction Ratio", Group = "Equity Protection (Circuit Breaker)", DefaultValue = 0.5, MinValue = 0.1, MaxValue = 1.0)]
        public double ddRiskReductionRatio { get; set; }
        #endregion

        #region Setting Trailing Stop Loss (TSL)
        [Parameter("Enable Trailing Stop", Group = "Setting Trailing Stop Loss (TSL)", DefaultValue = false)]
        public bool enableTrailingStop { get; set; }

        [Parameter("Trigger point of TSL (pips)", Group = "Setting Trailing Stop Loss (TSL)", DefaultValue = 300)]
        public double TrailingStopTrigger { get; set; }

        [Parameter("Distance of TSL (pips)", Group = "Setting Trailing Stop Loss (TSL)", DefaultValue = 150)]
        public double TrailingStopStep { get; set; }
        #endregion

        #region Setting Break Even Parameters
        [Parameter("Enable Moving Stoploss to break even price? ", Group = "Setting Break Even", DefaultValue = false)]
        public bool enableBreakEvenPrice { get; set; }

        [Parameter("Trigger point of break even [pips]", Group = "Setting Break Even", DefaultValue = 250)]
        public double breakEvenTrigger { get; set; }

        [Parameter("Enable Trailing Stop ? ", Group = "Setting Break Even", DefaultValue = false)]
        public bool enableTrailingStopFromBreakEven { get; set; }
        #endregion

        #region Setting DCA Parameters
        [Parameter("Enable DCA mode ?", Group = "Setting DCA", DefaultValue = false)]
        public bool dcaEnable { get; set; }

        [Parameter("Set SL,TP to first order ?", Group = "Setting DCA", DefaultValue = true)]
        public bool setSLTPtoFirsOrder { get; set; }

        [Parameter("Enable Averaging down ?", Group = "Setting DCA", DefaultValue = true)]
        public bool dcaDown { get; set; }

        [Parameter("Enable Averaging up ?", Group = "Setting DCA", DefaultValue = false)]
        public bool dcaUp { get; set; }

        [Parameter("Only close a deal when hit stoploss or takeprofit ?", Group = "Setting DCA", DefaultValue = false)]
        public bool dca_closeDealbySLTP { get; set; }

        [Parameter("DCA with fixed volume?", Group = "Setting DCA", DefaultValue = false)]
        public bool dcaEnableFixVol { get; set; }

        [Parameter("DCA with increment volume?", Group = "Setting DCA", DefaultValue = false)]
        public bool dcaEnableIncreaseVol { get; set; }

        [Parameter("DCA with double Volume?", Group = "Setting DCA", DefaultValue = true)]
        public bool dcaEnableDoubleVol { get; set; }

        [Parameter("Price range for DCA [pips]", Group = "Setting DCA", DefaultValue = 200)]
        public double dca_Distance { get; set; }

        [Parameter("Consider condition of Bot's strategy ?", Group = "Setting DCA", DefaultValue = false)]
        public bool dcaEnableBotCondition { get; set; }

        [Parameter("Close single order when profit reaches threshold?", Group = "Setting DCA", DefaultValue = false)]
        public bool dca_enableProfittoClose_singleOrder { get; set; }

        [Parameter("Profit threshold to close single order [usd]", Group = "Setting DCA", DefaultValue = 10)]
        public double dca_profittoClose_singleOder { get; set; }

        [Parameter("Close all orders when profit reaches threshold?", Group = "Setting DCA", DefaultValue = false)]
        public bool dca_enableProfittoClose { get; set; }

        [Parameter("Profit threshold to close all orders [usd]", Group = "Setting DCA", DefaultValue = 100)]
        public double profittoClose { get; set; }

        [Parameter("Close when profit pulls back from peak?", Group = "Setting DCA", DefaultValue = true)]
        public bool dcaPullBackToClose { get; set; }

        [Parameter("Pullback distance [pips]", Group = "Setting DCA", DefaultValue = 300)]
        public double dcaPullBackPips { get; set; }

        [Parameter("Enable profit percentage to close all ?", Group = "Setting DCA", DefaultValue = true)]
        public bool dcaProfitPercentageToCloseAll { get; set; }

        [Parameter("Profit percentage to close all [%]", Group = "Setting DCA", DefaultValue = 100)]
        public double dcaProfitPercent { get; set; }
        #endregion

        #region Telegram Integration
        [Parameter("Enable Telegram Alerts", Group = "Telegram Integration", DefaultValue = true)]
        public bool enableTelegramAlerts { get; set; }

        [Parameter("Telegram Bot Token", Group = "Telegram Integration", DefaultValue = "")]
        public string telegramBotToken { get; set; }

        [Parameter("Telegram Chat ID", Group = "Telegram Integration", DefaultValue = "")]
        public string telegramChatId { get; set; }

        [Parameter("Send Chart Screenshot on Signal", Group = "Telegram Integration", DefaultValue = false)]
        public bool sendChartScreenshot { get; set; }
        #endregion

        #region UI & Info Panel
        [Parameter("Show Info Panel on Chart", Group = "UI & Info Panel", DefaultValue = true)]
        public bool showInfoPanel { get; set; }

        [Parameter("Local Time UTC Offset (Hours)", Group = "UI & Info Panel", DefaultValue = 7, MinValue = -12, MaxValue = 14)]
        public double customUTCOffset { get; set; }
        #endregion

        #region In-Code Expiry & Licensing
        private readonly DateTime ExpiryDate = new DateTime(2026, 12, 30);
        private readonly DateTime StartDate = new DateTime(2024, 1, 1);
        private readonly bool Unlimited_License = true;
        private bool _isExpired = false;
        #endregion

        #region Fields
        private bool _waitingForCloseSignalBuy = false;
        private bool _waitingForCloseSignalSell = false;

        private bool TPhitBuy = false;
        private bool TPhitSell = false;

        private bool _buyCondition = false;
        private bool _sellCondition = false;
        private bool _closeBuyCondition = false;
        private bool _closeSellCondition = false;

        private DateTime _lastStopLossTime;
        private bool _isWaiting = false;

        private double _asianHigh = 0;
        private double _asianLow = 0;
        private double _asianRangePips = 0;
        private DateTime _asianSessionDate = DateTime.MinValue;
        private bool _highSwept = false;
        private bool _lowSwept = false;
        private string _activeKillzone = "Outside Killzones";

        private MovingAverage fastEma;
        private MovingAverage slowEma;
        private RelativeStrengthIndex rsi;
        private AverageTrueRange atr;

        private Bars _h1Bars;
        private Bars _h4Bars;
        private MovingAverage _h1FastEma;
        private MovingAverage _h1SlowEma;
        private RelativeStrengthIndex _h1Rsi;
        private MovingAverage _h4FastEma;
        private MovingAverage _h4SlowEma;
        private RelativeStrengthIndex _h4Rsi;

        private double takeprofit;
        private double stoploss;
        private double _calculatedVol = 0.01;

        private double _peakEquity = 0;
        private double _currentDrawdownPercent = 0;
        private bool _isCircuitBreakerActive = false;

        private Dictionary<int, bool> _movedToBreakEven = new Dictionary<int, bool>();

        private Position dcaStartPosition = null;
        private Position dcaEndPosition_down = null;
        private Position dcaEndPosition_up = null;
        private DateTime dcalastEntryTime;

        public class NewsEvent
        {
            public string Title { get; set; }
            public string Country { get; set; }
            public DateTime Date { get; set; }
            public string Impact { get; set; }
        }

        private readonly List<NewsEvent> _newsEvents = new List<NewsEvent>();
        private DateTime _lastNewsFetchTime = DateTime.MinValue;
        private DateTime _lastNewsFetchAttempt = DateTime.MinValue;
        #endregion

        #region AI Agent Fields
        private HttpClient _httpClient;
        private int _consecutiveAiFailures = 0;
        private DateTime _aiCooldownUntil = DateTime.MinValue;
        // Pre-filter gate state
        private string _allowedAiDirection = "NONE"; // "BUY", "SELL", "MANAGE_ONLY", "NONE"
        private string _traditionalSignal   = "NONE"; // "EMA_CROSS_BUY", "EMA_CROSS_SELL", "NONE"
        private int    _barsSinceCross      = 0;      // bars elapsed since last EMA cross
        private int    _lastCrossBarIndex   = -1;     // bar index of most recent cross
        #endregion

        #region Robot Events
        protected override void OnStart()
        {
            try { InitializeLicense(); } catch (Exception ex) { Print($"[License Init Warning] {ex.Message}"); }
            if (_isExpired) return;

            try { InitializeNewsFilter(); } catch (Exception ex) { Print($"[News Init Warning] {ex.Message}"); }
            try { InitializeRiskManagement(); } catch (Exception ex) { Print($"[Risk Init Warning] {ex.Message}"); }
            try { InitializeStrategyIndicators(); } catch (Exception ex) { Print($"[Indicators Init Warning] {ex.Message}"); }
            try { InitializeUI(); } catch (Exception ex) { Print($"[UI Init Warning] {ex.Message}"); }

            _httpClient = new HttpClient();
            _httpClient.Timeout = TimeSpan.FromSeconds(aiTimeoutSeconds > 0 ? aiTimeoutSeconds : 300);
            Print($"cBot Agent Template HTTP calls ENABLED in mode: {RunningMode} | AI Mode: {AiMode}");

            Positions.Closed += OnPositionsClosed;
            Positions.Opened += OnPositionsOpened;

            _lastStopLossTime = DateTime.MinValue;
            _isWaiting = false;
            _consecutiveAiFailures = 0;
            _aiCooldownUntil = DateTime.MinValue;

            if (AiMode == AiConnectionMode.Direct_OpenRouter_DashScope)
            {
                LogApiKeyResolution();
            }

            Print(label + " Started successfully. Bot is running...");

            _ = SendStateToAgentAsync();
        }

        protected override void OnBarClosed()
        {
            if (_isExpired) return;

            CheckNewsEvents();

            // Track Asian session range & golden killzones
            TrackAsianSession(Server.Time);
            bool inKillzone = IsGoldenKillzone(Server.Time, out _activeKillzone);

            // Evaluate Judas Sweep signals (respecting reverseCondition)
            CheckJudasSweep(out bool sweepBuy, out bool sweepSell, out string sweepSignal);
            bool rawBuy  = !reverseCondition ? sweepBuy  : sweepSell;
            bool rawSell = !reverseCondition ? sweepSell : sweepBuy;

            // ── Update pre-filter gate state ───────────────────────────────────
            if (rawBuy && !rawSell)
            {
                _allowedAiDirection = "BUY";
                _traditionalSignal  = sweepSignal;
                _lastCrossBarIndex  = Bars.Count - 1;
                _barsSinceCross     = 0;
            }
            else if (rawSell && !rawBuy)
            {
                _allowedAiDirection = "SELL";
                _traditionalSignal  = sweepSignal;
                _lastCrossBarIndex  = Bars.Count - 1;
                _barsSinceCross     = 0;
            }
            else if (_lastCrossBarIndex >= 0)
            {
                _barsSinceCross = Bars.Count - 1 - _lastCrossBarIndex;
            }
            else
            {
                _allowedAiDirection = "MANAGE_ONLY";
                _traditionalSignal  = "NONE";
            }
            // ───────────────────────────────────────────────────────────────────

            if (UseAiGateMode && _calculateOnBarClosed)
            {
                // GATE MODE: Traditional logic handles CLOSE signals only.
                // AI Agent is the sole authority for new ENTRY decisions.
                bool rawCloseBuy  = !reverseCondition ? closeBuyCondition()  : closeSellCondition();
                bool rawCloseSell = !reverseCondition ? closeSellCondition() : closeBuyCondition();

                if (rawCloseBuy && buyPositions(label).Length > 0)
                {
                    ClosePositions(label, TradeType.Buy);
                    _waitingForCloseSignalBuy = false;
                }
                if (rawCloseSell && sellPositions(label).Length > 0)
                {
                    ClosePositions(label, TradeType.Sell);
                    _waitingForCloseSignalSell = false;
                }
            }
            else if (!UseAiGateMode && _calculateOnBarClosed)
            {
                // LEGACY MODE: Traditional logic handles everything directly (backward compatible)
                _buyCondition       = rawBuy;
                _sellCondition      = rawSell;
                _closeBuyCondition  = !reverseCondition ? closeBuyCondition()  : closeSellCondition();
                _closeSellCondition = !reverseCondition ? closeSellCondition() : closeBuyCondition();
                createOrder();
                resetConditions();
            }

            ProcessBreakEvenLogic();
            ProcessDCALogic();
            UpdateUIPanel();

            if (_httpClient != null)
            {
                bool hasOpenPos   = Positions.FindAll(label, SymbolName).Length > 0;
                // Gate Mode: call AI only when cross is fresh (â‰¤3 bars) OR managing open positions
                bool gateOpen     = UseAiGateMode && _barsSinceCross <= 3 && _allowedAiDirection != "NONE";
                bool shouldCallAi = !UseAiGateMode || gateOpen || hasOpenPos;

                if (shouldCallAi)
                {
                    // Pass gate direction; use MANAGE_ONLY when only managing existing positions
                    string contextDir = (UseAiGateMode && hasOpenPos && !gateOpen) ? "MANAGE_ONLY" : _allowedAiDirection;
                    _ = SendStateToAgentAsync(contextDir);
                }
            }
        }

        protected override void OnTick()
        {
            if (_isExpired) return;

            if (enableTrailingStop) { TrailingStop(); }

            ProcessBreakEvenLogic();
            ProcessDCALogic();

            if (_isWaiting && Server.Time >= _lastStopLossTime.AddSeconds(30))
            {
                _isWaiting = false;
            }

            if (!_calculateOnBarClosed && _isWaiting == false)
            {
                if (!reverseCondition)
                {
                    _buyCondition = buyCondition();
                    _sellCondition = sellCondition();
                    _closeBuyCondition = closeBuyCondition();
                    _closeSellCondition = closeSellCondition();
                }
                else
                {
                    _buyCondition = sellCondition();
                    _sellCondition = buyCondition();
                    _closeBuyCondition = closeSellCondition();
                    _closeSellCondition = closeBuyCondition();
                }

                createOrder();
                resetConditions();
            }
        }

        private void resetConditions()
        {
            _buyCondition = false;
            _sellCondition = false;
            _closeBuyCondition = false;
            _closeSellCondition = false;
        }

        private void resetFlagsforManualClosed()
        {
            _waitingForCloseSignalBuy = false;
            _waitingForCloseSignalSell = false;
            _movedToBreakEven.Clear();
        }

        private void createOrder()
        {
            if (_closeBuyCondition && buyPositions(label).Length > 0)
            {
                ClosePositions(label, TradeType.Buy);
                _waitingForCloseSignalBuy = false;
            }

            if (_closeSellCondition && sellPositions(label).Length > 0)
            {
                ClosePositions(label, TradeType.Sell);
                _waitingForCloseSignalSell = false;
            }

            if (blockReopenUntilCloseSignal && (_waitingForCloseSignalBuy || _waitingForCloseSignalSell))
            {
                return;
            }

            if (_buyCondition && TPhitBuy == false)
            {
                if (enableTradeTypeBuy && buyPositions(label).Length < maxPermittedOrder)
                {
                    TimeSpan timeDifference = Server.Time - dcalastEntryTime;
                    if (timeDifference.TotalSeconds >= 2 && _isWaiting == false)
                    {
                        CalculateSLTP(TradeType.Buy, Symbol.Bid);

                        var result = ExecuteMarketOrder(TradeType.Buy, SymbolName, _calculatedVol, label, stoploss, takeprofit);
                        if (result.IsSuccessful)
                        {
                            dcalastEntryTime = result.Position.EntryTime;
                            _ = SendTelegramAlertAsync($"ðŸš€ <b>[{label}] BUY Position Opened</b>\nSymbol: {SymbolName}\nEntry: {result.Position.EntryPrice}\nSL: {stoploss:F1} pips\nTP: {takeprofit:F1} pips");
                            _ = CaptureAndSendChartScreenshotAsync($"Chart screenshot for BUY entry at {result.Position.EntryPrice}");
                        }
                    }
                }
            }

            if (_sellCondition && TPhitSell == false)
            {
                if (enableTradeTypeSELL && sellPositions(label).Length < maxPermittedOrder)
                {
                    TimeSpan timeDifference = Server.Time - dcalastEntryTime;
                    if (timeDifference.TotalSeconds >= 2 && _isWaiting == false)
                    {
                        CalculateSLTP(TradeType.Sell, Symbol.Ask);

                        var result = ExecuteMarketOrder(TradeType.Sell, SymbolName, _calculatedVol, label, stoploss, takeprofit);
                        if (result.IsSuccessful)
                        {
                            dcalastEntryTime = result.Position.EntryTime;
                            _ = SendTelegramAlertAsync($"ðŸš€ <b>[{label}] SELL Position Opened</b>\nSymbol: {SymbolName}\nEntry: {result.Position.EntryPrice}\nSL: {stoploss:F1} pips\nTP: {takeprofit:F1} pips");
                            _ = CaptureAndSendChartScreenshotAsync($"Chart screenshot for SELL entry at {result.Position.EntryPrice}");
                        }
                    }
                }
            }
        }

        private Position[] buyPositions(string label)
        {
            return Positions.FindAll(label, SymbolName, TradeType.Buy);
        }

        private Position[] sellPositions(string label)
        {
            return Positions.FindAll(label, SymbolName, TradeType.Sell);
        }

        private void ClosePositions(string label, TradeType tradeType)
        {
            foreach (var position in Positions.FindAll(label, SymbolName))
            {
                if (position.TradeType == tradeType)
                {
                    ClosePosition(position);
                }
            }
        }

        private void OnPositionsOpened(PositionOpenedEventArgs args)
        {
            Position openedPosition = args.Position;
            if (openedPosition.Label == label && _httpClient != null)
            {
                _ = ReportPositionOpen(openedPosition, stoplossPip, takeprofitPip, _lastAgentReason);
                _lastAgentReason = "";
                SendLiveTickTelemetry(force: true);
            }
            dcalastEntryTime = openedPosition.EntryTime;
            resetFlagsforManualClosed();
        }

        private void OnPositionsClosed(PositionClosedEventArgs args)
        {
            Position closedPosition = args.Position;
            if (closedPosition.Label != label) return;

            if (_httpClient != null)
            {
                _ = ReportPositionClosed(closedPosition, closedPosition.NetProfit);
                SendLiveTickTelemetry(force: true);
            }

            if (enableTelegramAlerts)
            {
                string icon = closedPosition.NetProfit >= 0 ? "ðŸ’°" : "ðŸ”»";
                string sign = closedPosition.NetProfit >= 0 ? "+$" : "-$";
                _ = SendTelegramAlertAsync($"{icon} <b>[{label}] Position Closed ({args.Reason})</b>\nSymbol: {SymbolName}\nSide: {closedPosition.TradeType}\nVolume: {closedPosition.VolumeInUnits / Symbol.LotSize:F2} lots\nNet Profit: <b>{sign}{Math.Abs(closedPosition.NetProfit):F2}</b> ({closedPosition.Pips:F1} pips)\nBalance: ${Account.Balance:F2} | Equity: ${Account.Equity:F2}");
            }

            if (args.Reason == PositionCloseReason.Closed)
            {
                if (closedPosition.TradeType == TradeType.Buy && _closeBuyCondition == false)
                {
                    _waitingForCloseSignalBuy = true;
                }
                else if (closedPosition.TradeType == TradeType.Sell && _closeSellCondition == false)
                {
                    _waitingForCloseSignalSell = true;
                }
                else
                {
                    resetFlagsforManualClosed();
                }
            }

            if (args.Reason == PositionCloseReason.TakeProfit)
            {
                if (closedPosition.TradeType == TradeType.Sell)
                {
                    TPhitBuy = false;
                    TPhitSell = true;
                }
                else
                {
                    TPhitBuy = true;
                    TPhitSell = false;
                }
                resetFlagsforManualClosed();
            }

            if (args.Reason == PositionCloseReason.StopLoss)
            {
                resetFlagsforManualClosed();
                _lastStopLossTime = Server.Time;
                _isWaiting = true;
            }
        }

        protected override void OnStop()
        {
            Print(label + " Stopped.");
        }
        #endregion

        #region License Management
        private void InitializeLicense()
        {
            if (Unlimited_License)
            {
                _isExpired = false;
                Print("[License] Running in Unlimited Mode.");
                return;
            }

            DateTime now = DateTime.UtcNow;
            if (now > ExpiryDate || now < StartDate)
            {
                _isExpired = true;
                Print($"[License Error] cBot license expired on {ExpiryDate:yyyy-MM-dd}. Stopping execution.");
                DrawExpiryNoticeOnChart();
                Stop();
            }
            else
            {
                _isExpired = false;
                TimeSpan remaining = ExpiryDate - now;
                Print($"[License Valid] License valid until {ExpiryDate:yyyy-MM-dd}. Remaining days: {remaining.TotalDays:F1}");
            }
        }

        private void DrawExpiryNoticeOnChart()
        {
            if (Chart != null)
            {
                Chart.DrawStaticText("ExpiryNotice", "âŒ BOT LICENSE EXPIRED! Contact Author: +84979404641", VerticalAlignment.Center, HorizontalAlignment.Center, Color.Red);
            }
        }
        #endregion

        #region Strategy Indicators & Signal Conditions (EMA Cross Strategy)
        private void InitializeStrategyIndicators()
        {
            fastEma = Indicators.MovingAverage(Bars.ClosePrices, fastEmaPeriod, MovingAverageType.Exponential);
            slowEma = Indicators.MovingAverage(Bars.ClosePrices, slowEmaPeriod, MovingAverageType.Exponential);
            rsi = Indicators.RelativeStrengthIndex(Bars.ClosePrices, periodRSI);
            atr = Indicators.AverageTrueRange(14, MovingAverageType.Exponential);

            try
            {
                _h1Bars = MarketData.GetBars(TimeFrame.Hour);
                if (_h1Bars != null)
                {
                    _h1FastEma = Indicators.MovingAverage(_h1Bars.ClosePrices, 9, MovingAverageType.Exponential);
                    _h1SlowEma = Indicators.MovingAverage(_h1Bars.ClosePrices, 21, MovingAverageType.Exponential);
                    _h1Rsi = Indicators.RelativeStrengthIndex(_h1Bars.ClosePrices, 14);
                }
            }
            catch (Exception ex)
            {
                Print($"[MTF Init Notice] H1 Bars initialization: {ex.Message}");
            }

            try
            {
                _h4Bars = MarketData.GetBars(TimeFrame.Hour4);
                if (_h4Bars != null)
                {
                    _h4FastEma = Indicators.MovingAverage(_h4Bars.ClosePrices, 9, MovingAverageType.Exponential);
                    _h4SlowEma = Indicators.MovingAverage(_h4Bars.ClosePrices, 21, MovingAverageType.Exponential);
                    _h4Rsi = Indicators.RelativeStrengthIndex(_h4Bars.ClosePrices, 14);
                }
            }
            catch (Exception ex)
            {
                Print($"[MTF Init Notice] H4 Bars initialization: {ex.Message}");
            }
        }

        private void TrackAsianSession(DateTime timeUtc)
        {
            DateTime date = timeUtc.Date;
            int hour = timeUtc.Hour;

            if (hour >= asianStartHour && hour < asianEndHour)
            {
                if (_asianSessionDate != date)
                {
                    _asianSessionDate = date;
                    _asianHigh = Bars.LastBar.High;
                    _asianLow = Bars.LastBar.Low;
                    _highSwept = false;
                    _lowSwept = false;
                    _asianRangePips = 0;
                }
                else
                {
                    if (Bars.LastBar.High > _asianHigh) _asianHigh = Bars.LastBar.High;
                    if (Bars.LastBar.Low < _asianLow) _asianLow = Bars.LastBar.Low;
                }
                _asianRangePips = Symbol.PipSize > 0 ? (_asianHigh - _asianLow) / Symbol.PipSize : 0;
            }

            if (drawAsianRangeVisuals && Chart != null && _asianHigh > 0 && _asianLow > 0)
            {
                Chart.DrawHorizontalLine("AsianHighLine", _asianHigh, Color.Red, 1, LineStyle.Lines);
                Chart.DrawHorizontalLine("AsianLowLine", _asianLow, Color.DodgerBlue, 1, LineStyle.Lines);
            }
        }

        private bool IsGoldenKillzone(DateTime timeUtc, out string killzoneName)
        {
            int hour = timeUtc.Hour;
            int min = timeUtc.Minute;
            if (hour >= londonStartHour && hour < londonEndHour)
            {
                killzoneName = "London Open Killzone";
                return true;
            }
            if ((hour == nyStartHour && min >= 30) || (hour > nyStartHour && hour < nyEndHour))
            {
                killzoneName = "New York Overlap Killzone";
                return true;
            }
            killzoneName = "Outside Killzones";
            return false;
        }

        private void CheckJudasSweep(out bool buySignal, out bool sellSignal, out string signalName)
        {
            buySignal = false;
            sellSignal = false;
            signalName = "NONE";

            if (!IsGoldenKillzone(Server.Time, out _)) return;
            if (_asianHigh <= 0 || _asianLow <= 0) return;
            if (_asianRangePips < minAsianRangePips || _asianRangePips > maxAsianRangePips) return;

            var lastBar = Bars.LastBar;
            double sweepBuffer = sweepBufferPips * Symbol.PipSize;

            // SELL Judas Sweep: Bar High spiked above Asian High + buffer, but closed back below Asian High
            if (lastBar.High >= (_asianHigh + sweepBuffer) && lastBar.Close <= _asianHigh)
            {
                if (!enableRsiFilter || rsi == null || rsi.Result.LastValue > rsiOversold)
                {
                    sellSignal = true;
                    _highSwept = true;
                    signalName = "JUDAS_SWEEP_SELL";
                }
            }
            // BUY Judas Sweep: Bar Low spiked below Asian Low - buffer, but closed back above Asian Low
            else if (lastBar.Low <= (_asianLow - sweepBuffer) && lastBar.Close >= _asianLow)
            {
                if (!enableRsiFilter || rsi == null || rsi.Result.LastValue < rsiOverbought)
                {
                    buySignal = true;
                    _lowSwept = true;
                    signalName = "JUDAS_SWEEP_BUY";
                }
            }
        }

        private bool buyCondition()
        {
            CheckJudasSweep(out bool buySignal, out _, out _);
            return buySignal;
        }

        private bool sellCondition()
        {
            CheckJudasSweep(out _, out bool sellSignal, out _);
            return sellSignal;
        }

        private bool closeBuyCondition()
        {
            try
            {
                if (fastEma == null || slowEma == null || fastEma.Result.Count < 2 || slowEma.Result.Count < 2) return false;
                return Functions.HasCrossedBelow(fastEma.Result, slowEma.Result, 0);
            }
            catch
            {
                return false;
            }
        }

        private bool closeSellCondition()
        {
            try
            {
                if (fastEma == null || slowEma == null || fastEma.Result.Count < 2 || slowEma.Result.Count < 2) return false;
                return Functions.HasCrossedAbove(fastEma.Result, slowEma.Result, 0);
            }
            catch
            {
                return false;
            }
        }
        #endregion

        #region Risk & Money Management
        private void InitializeRiskManagement()
        {
            _peakEquity = Account.Equity;
            _isCircuitBreakerActive = false;
        }

        private void UpdateEquityProtection()
        {
            if (Account.Equity > _peakEquity)
            {
                _peakEquity = Account.Equity;
            }

            if (_peakEquity > 0)
            {
                _currentDrawdownPercent = ((_peakEquity - Account.Equity) / _peakEquity) * 100.0;
            }
            else
            {
                _currentDrawdownPercent = 0.0;
            }

            if (enableEquityProtection)
            {
                if (!_isCircuitBreakerActive && _currentDrawdownPercent >= maxEquityDDPercent)
                {
                    _isCircuitBreakerActive = true;
                    Print($"[Circuit Breaker Triggered] Drawdown reached {_currentDrawdownPercent:F2}% >= {maxEquityDDPercent}%. Reducing Risk Factor by {ddRiskReductionRatio:P0}!");
                    _ = SendTelegramAlertAsync($"âš ï¸ <b>[Circuit Breaker Triggered]</b>\nBot: {label}\nCurrent DD: {_currentDrawdownPercent:F2}%\nThreshold: {maxEquityDDPercent}%\nRisk factor reduced by {ddRiskReductionRatio:P0}.");
                }
                else if (_isCircuitBreakerActive && _currentDrawdownPercent < (maxEquityDDPercent * 0.5))
                {
                    _isCircuitBreakerActive = false;
                    Print($"[Circuit Breaker Reset] Drawdown recovered to {_currentDrawdownPercent:F2}%. Restoring normal risk factor.");
                    _ = SendTelegramAlertAsync($"âœ… <b>[Circuit Breaker Reset]</b>\nBot: {label}\nCurrent DD: {_currentDrawdownPercent:F2}%. Normal risk restored.");
                }
            }
        }

        private double GetEffectiveRiskFactor()
        {
            UpdateEquityProtection();
            if (enableEquityProtection && _isCircuitBreakerActive)
            {
                return riskFactor * ddRiskReductionRatio;
            }
            return riskFactor;
        }

        private void CalculateSLTP(TradeType tradeType, double currentPrice)
        {
            if (!SLTPpercentage)
            {
                takeprofit = takeprofitPip;
                stoploss = stoplossPip;
                _calculatedVol = CalculateVolume(stoploss);
            }
            else
            {
                double effectiveRisk = GetEffectiveRiskFactor();
                _calculatedVol = CalculateVolumeFromPercentage(stoplossPercentage, effectiveRisk);

                double slMoney = Account.Equity * (stoplossPercentage / 100.0);
                double tpMoney = Account.Equity * (takeprofitPercentage / 100.0);

                stoploss = (slMoney / _calculatedVol) / Symbol.PipValue;
                takeprofit = (tpMoney / _calculatedVol) / Symbol.PipValue;
            }
        }

        private double CalculateVolume(double slPips)
        {
            if (enableFixedVol)
            {
                return Symbol.NormalizeVolumeInUnits(_fixedVolLots * Symbol.LotSize);
            }

            if (_voltoAccount)
            {
                double effectiveRisk = GetEffectiveRiskFactor();
                double riskAmount = Account.Equity * (effectiveRisk / 100.0);
                double lossPerUnit = slPips * Symbol.PipValue;
                if (lossPerUnit <= 0) lossPerUnit = Symbol.PipValue * 100.0;
                double volUnits = riskAmount / lossPerUnit;
                double normalized = Symbol.NormalizeVolumeInUnits(volUnits);

                double maxUnits = maxVol * Symbol.LotSize;
                if (normalized > maxUnits) normalized = maxUnits;
                if (normalized < Symbol.VolumeInUnitsMin) normalized = Symbol.VolumeInUnitsMin;
                if (normalized > Symbol.VolumeInUnitsMax) normalized = Symbol.VolumeInUnitsMax;

                return normalized;
            }

            return Symbol.NormalizeVolumeInUnits(_fixedVolLots * Symbol.LotSize);
        }

        private double CalculateVolumeFromPercentage(double slPercentage, double riskPercentage)
        {
            if (enableFixedVol)
            {
                return Symbol.NormalizeVolumeInUnits(_fixedVolLots * Symbol.LotSize);
            }

            double riskAmount = Account.Equity * (riskPercentage / 100.0);
            double slPriceDistance = Symbol.Ask * (slPercentage / 100.0);
            double lossPerUnit = (slPriceDistance / Symbol.PipSize) * Symbol.PipValue;
            if (lossPerUnit <= 0) lossPerUnit = Symbol.PipValue * 100.0;

            double targetUnits = riskAmount / lossPerUnit;
            double normalized = Symbol.NormalizeVolumeInUnits(targetUnits);

            double maxUnits = maxVol * Symbol.LotSize;
            if (normalized > maxUnits) normalized = maxUnits;
            if (normalized < Symbol.VolumeInUnitsMin) normalized = Symbol.VolumeInUnitsMin;
            if (normalized > Symbol.VolumeInUnitsMax) normalized = Symbol.VolumeInUnitsMax;

            return normalized;
        }
        #endregion

        #region Trailing Stop & Break Even
        private void TrailingStop()
        {
            var positions = Positions.FindAll(label, SymbolName);
            foreach (var pos in positions)
            {
                if (pos.TradeType == TradeType.Buy)
                {
                    double distance = (Symbol.Bid - pos.EntryPrice) / Symbol.PipSize;
                    if (distance >= TrailingStopTrigger)
                    {
                        double newSL = Symbol.Bid - TrailingStopStep * Symbol.PipSize;
                        if (pos.StopLoss == null || newSL > pos.StopLoss)
                        {
#pragma warning disable CS0618
                            ModifyPosition(pos, newSL, pos.TakeProfit);
#pragma warning restore CS0618
                        }
                    }
                }
                else if (pos.TradeType == TradeType.Sell)
                {
                    double distance = (pos.EntryPrice - Symbol.Ask) / Symbol.PipSize;
                    if (distance >= TrailingStopTrigger)
                    {
                        double newSL = Symbol.Ask + TrailingStopStep * Symbol.PipSize;
                        if (pos.StopLoss == null || newSL < pos.StopLoss)
                        {
#pragma warning disable CS0618
                            ModifyPosition(pos, newSL, pos.TakeProfit);
#pragma warning restore CS0618
                        }
                    }
                }
            }
        }

        private void ProcessBreakEvenLogic()
        {
            if (!enableBreakEvenPrice) return;

            var positions = Positions.FindAll(label, SymbolName);
            foreach (var pos in positions)
            {
                if (_movedToBreakEven.ContainsKey(pos.Id) && _movedToBreakEven[pos.Id]) continue;

                if (pos.TradeType == TradeType.Buy)
                {
                    double pipsGain = (Symbol.Bid - pos.EntryPrice) / Symbol.PipSize;
                    if (pipsGain >= breakEvenTrigger)
                    {
                        double newSL = pos.EntryPrice + (Symbol.PipSize * 2);
                        if (pos.StopLoss == null || newSL > pos.StopLoss)
                        {
#pragma warning disable CS0618
                            ModifyPosition(pos, newSL, pos.TakeProfit);
#pragma warning restore CS0618
                            _movedToBreakEven[pos.Id] = true;
                            Print($"[BreakEven] Buy position {pos.Id} moved SL to break-even.");
                        }
                    }
                }
                else if (pos.TradeType == TradeType.Sell)
                {
                    double pipsGain = (pos.EntryPrice - Symbol.Ask) / Symbol.PipSize;
                    if (pipsGain >= breakEvenTrigger)
                    {
                        double newSL = pos.EntryPrice - (Symbol.PipSize * 2);
                        if (pos.StopLoss == null || newSL < pos.StopLoss)
                        {
#pragma warning disable CS0618
                            ModifyPosition(pos, newSL, pos.TakeProfit);
#pragma warning restore CS0618
                            _movedToBreakEven[pos.Id] = true;
                            Print($"[BreakEven] Sell position {pos.Id} moved SL to break-even.");
                        }
                    }
                }
            }
        }
        #endregion

        #region DCA Logic
        private void ProcessDCALogic()
        {
            if (!dcaEnable) return;
            findEndDeal();
            checkToCloseDeal();

            var openPos = Positions.FindAll(label, SymbolName);
            if (openPos.Length == 0 || openPos.Length >= maxPermittedOrder) return;

            if (dcaStartPosition == null || dcaEndPosition_down == null || dcaEndPosition_up == null) return;

            if (dcaStartPosition.TradeType == TradeType.Buy)
            {
                if (dcaDown)
                {
                    double newEntryPrice = dcaEndPosition_down.EntryPrice - dca_Distance * Symbol.PipSize;
                    if (Math.Max(Symbol.Ask, Symbol.Bid) <= newEntryPrice)
                    {
                        double vol = dcaVolumeUnit();
                        var res = ExecuteMarketOrder(TradeType.Buy, SymbolName, vol, label, stoploss, takeprofit);
                        if (res.IsSuccessful) { findEndDeal(); }
                    }
                }
            }
            else if (dcaStartPosition.TradeType == TradeType.Sell)
            {
                if (dcaDown)
                {
                    double newEntryPrice = dcaEndPosition_down.EntryPrice + dca_Distance * Symbol.PipSize;
                    if (Math.Min(Symbol.Ask, Symbol.Bid) >= newEntryPrice)
                    {
                        double vol = dcaVolumeUnit();
                        var res = ExecuteMarketOrder(TradeType.Sell, SymbolName, vol, label, stoploss, takeprofit);
                        if (res.IsSuccessful) { findEndDeal(); }
                    }
                }
            }
        }

        private void findEndDeal()
        {
            var openPositions = Positions.FindAll(label, SymbolName);
            if (openPositions.Length == 0)
            {
                dcaStartPosition = null;
                dcaEndPosition_down = null;
                dcaEndPosition_up = null;
                return;
            }

            dcaStartPosition = openPositions[0];
            dcaEndPosition_down = openPositions[0];
            dcaEndPosition_up = openPositions[0];

            for (int i = 1; i < openPositions.Length; i++)
            {
                if (openPositions[i].EntryPrice < dcaEndPosition_down.EntryPrice)
                    dcaEndPosition_down = openPositions[i];

                if (openPositions[i].EntryPrice > dcaEndPosition_up.EntryPrice)
                    dcaEndPosition_up = openPositions[i];
            }
        }

        private double dcaVolumeUnit()
        {
            var openPositions = Positions.FindAll(label, SymbolName);
            if (openPositions.Length == 0) return _calculatedVol;

            double lastVol = openPositions[openPositions.Length - 1].VolumeInUnits;
            if (dcaEnableDoubleVol)
            {
                double dVol = lastVol * 2;
                return Symbol.NormalizeVolumeInUnits(dVol > maxVol * Symbol.LotSize ? maxVol * Symbol.LotSize : dVol);
            }
            if (dcaEnableIncreaseVol)
            {
                double incVol = lastVol + (_fixedVolLots * Symbol.LotSize);
                return Symbol.NormalizeVolumeInUnits(incVol > maxVol * Symbol.LotSize ? maxVol * Symbol.LotSize : incVol);
            }
            return lastVol;
        }

        private void checkToCloseDeal()
        {
            var openPositions = Positions.FindAll(label, SymbolName);
            if (openPositions.Length == 0) return;

            double totalNetProfit = openPositions.Sum(p => p.NetProfit);

            if (dca_enableProfittoClose && totalNetProfit >= profittoClose)
            {
                CloseAllPositions();
                return;
            }

            if (dcaProfitPercentageToCloseAll)
            {
                double targetProfit = Account.Equity * (dcaProfitPercent / 100.0);
                if (totalNetProfit >= targetProfit)
                {
                    CloseAllPositions();
                    return;
                }
            }
        }

        private void CloseAllPositions()
        {
            foreach (var pos in Positions.FindAll(label, SymbolName))
            {
                ClosePosition(pos);
            }
            resetFlagsforManualClosed();
        }
        #endregion

        #region News Filter Logic
        private void InitializeNewsFilter()
        {
            if (!enableNewsFilter) return;
            if (RunningMode != RunningMode.RealTime) return;

            FetchForexFactoryNews();
        }

        private void CheckNewsEvents()
        {
            if (!enableNewsFilter || RunningMode != RunningMode.RealTime) return;

            if (DateTime.UtcNow - _lastNewsFetchTime > TimeSpan.FromHours(6) && DateTime.UtcNow - _lastNewsFetchAttempt > TimeSpan.FromMinutes(5))
            {
                FetchForexFactoryNews();
            }

            DateTime now = DateTime.UtcNow;
            foreach (var item in _newsEvents)
            {
                if (highImpactOnly && item.Impact != "High") continue;

                if (now >= item.Date.AddMinutes(-pauseBeforeNewsMins) && now <= item.Date.AddMinutes(pauseAfterNewsMins))
                {
                    if (closePositionsBeforeNews)
                    {
                        CloseAllPositions();
                    }
                }
            }
        }

        private void FetchForexFactoryNews()
        {
            try
            {
                _lastNewsFetchAttempt = DateTime.UtcNow;
                string url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json";
#pragma warning disable SYSLIB0014
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)";
                req.Accept = "application/json";
                req.Timeout = 10000;

                using (var res = (HttpWebResponse)req.GetResponse())
                using (var stream = res.GetResponseStream())
                using (var reader = new StreamReader(stream))
                {
                    string json = reader.ReadToEnd();
                    ParseNewsJson(json);
                    _lastNewsFetchTime = DateTime.UtcNow;
                }
#pragma warning restore SYSLIB0014
            }
            catch (Exception ex)
            {
                Print($"[News Filter Error] JSON fetch failed: {ex.Message}. Attempting XML fallback.");
                TryFetchXmlFallback();
            }
        }

        private void TryFetchXmlFallback()
        {
            try
            {
                string url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml";
#pragma warning disable SYSLIB0014
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.UserAgent = "Mozilla/5.0";
                req.Timeout = 10000;

                using (var res = (HttpWebResponse)req.GetResponse())
                using (var stream = res.GetResponseStream())
                using (var reader = new StreamReader(stream))
                {
                    string xml = reader.ReadToEnd();
                    _lastNewsFetchTime = DateTime.UtcNow;
                    Print("[News Filter] XML Fallback news fetched successfully.");
                }
#pragma warning restore SYSLIB0014
            }
            catch (Exception ex)
            {
                Print($"[News Filter Error] XML fetch fallback failed: {ex.Message}");
            }
        }

        private void ParseNewsJson(string json)
        {
            try
            {
                using (var doc = JsonDocument.Parse(json))
                {
                    _newsEvents.Clear();
                    foreach (var element in doc.RootElement.EnumerateArray())
                    {
                        string title = element.GetProperty("title").GetString();
                        string country = element.GetProperty("country").GetString();
                        string impact = element.GetProperty("impact").GetString();
                        string dateStr = element.GetProperty("date").GetString();

                        if (DateTime.TryParse(dateStr, out DateTime newsDate))
                        {
                            _newsEvents.Add(new NewsEvent
                            {
                                Title = title,
                                Country = country,
                                Impact = impact,
                                Date = newsDate
                            });
                        }
                    }
                }
                Print($"[News Filter] Loaded {_newsEvents.Count} news events.");
            }
            catch (Exception ex)
            {
                Print($"[News Filter] JSON parsing error: {ex.Message}");
            }
        }
        #endregion

        #region Telegram Alerts
        public async Task SendTelegramAlertAsync(string message)
        {
            if (!enableTelegramAlerts || RunningMode != RunningMode.RealTime || string.IsNullOrWhiteSpace(telegramBotToken) || string.IsNullOrWhiteSpace(telegramChatId))
                return;

            try
            {
                using (var httpClient = new HttpClient())
                {
                    httpClient.Timeout = TimeSpan.FromSeconds(10);
                    string url = $"https://api.telegram.org/bot{telegramBotToken}/sendMessage?chat_id={telegramChatId}&text={Uri.EscapeDataString(message)}&parse_mode=HTML";
                    await httpClient.GetAsync(url);
                }
            }
            catch (Exception ex)
            {
                Print($"[Telegram Error] Failed to send text message: {ex.Message}");
            }
        }

        public async Task CaptureAndSendChartScreenshotAsync(string caption)
        {
            if (!enableTelegramAlerts || !sendChartScreenshot || RunningMode != RunningMode.RealTime || Chart == null) return;

            try
            {
                string tempPath = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "cTrader_template_screenshot.png");
                var chartshot = Chart.TakeChartshot();

                System.IO.File.WriteAllBytes(tempPath, chartshot);

                using (var httpClient = new HttpClient())
                {
                    httpClient.Timeout = TimeSpan.FromSeconds(15);
                    string url = $"https://api.telegram.org/bot{telegramBotToken}/sendPhoto";

                    using (var form = new MultipartFormDataContent())
                    {
                        form.Add(new StringContent(telegramChatId), "chat_id");
                        form.Add(new StringContent(caption), "caption");
                        form.Add(new StringContent("HTML"), "parse_mode");

                        var imageBytes = System.IO.File.ReadAllBytes(tempPath);
                        var imageContent = new ByteArrayContent(imageBytes);
                        imageContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("image/png");
                        form.Add(imageContent, "photo", System.IO.Path.GetFileName(tempPath));

                        await httpClient.PostAsync(url, form);
                    }
                }

                if (System.IO.File.Exists(tempPath))
                {
                    System.IO.File.Delete(tempPath);
                }
            }
            catch (Exception ex)
            {
                Print($"[Telegram Error] Failed to send chart screenshot: {ex.Message}");
            }
        }
        #endregion

        #region UI Panel Logic
        private void InitializeUI()
        {
            if (!showInfoPanel || Chart == null) return;
            UpdateUIPanel();
        }

        private void UpdateUIPanel()
        {
            if (!showInfoPanel || Chart == null) return;

            UpdateEquityProtection();

            DateTime utcNow = DateTime.UtcNow;
            DateTime localTime = utcNow.AddHours(customUTCOffset);

            string licenseStatus = Unlimited_License ? "Unlimited" : (ExpiryDate.ToString("yyyy-MM-dd"));
            string cbStatus = _isCircuitBreakerActive ? "ðŸ”´ CIRCUIT BREAKER (RISK CUT 50%)" : "ðŸŸ¢ NORMAL";
            double effRisk = GetEffectiveRiskFactor();

            string panelText = $"🤖 Asian Range Judas Sweep AI Bot (SMC)\n" +
                               $"------------------------------------\n" +
                               $"Symbol       : {SymbolName}\n" +
                               $"Time (UTC+{customUTCOffset:F0}) : {localTime:HH:mm:ss}\n" +
                               $"Killzone     : {_activeKillzone}\n" +
                               $"Asian Range  : {_asianHigh:F2} / {_asianLow:F2} ({_asianRangePips:F0} pips)\n" +
                               $"License      : {licenseStatus}\n" +
                               $"Peak Equity  : ${FormatAmount(_peakEquity)}\n" +
                               $"Current DD   : {_currentDrawdownPercent:F1}%\n" +
                               $"Protection   : {cbStatus}\n" +
                               $"Active Risk  : {effRisk:F2}% (Base: {riskFactor}%)\n" +
                               $"DCA Mode     : {(dcaEnable ? "ENABLED" : "DISABLED")}\n" +
                               $"BreakEven    : {(enableBreakEvenPrice ? $"ON ({breakEvenTrigger} pips)" : "OFF")}\n" +
                               $"Active Orders: {Positions.FindAll(label, SymbolName).Length}/{maxPermittedOrder}";

            Color textColor = _isCircuitBreakerActive ? Color.OrangeRed : Color.LimeGreen;
            Chart.DrawStaticText("AsianRangeJudasSweepInfoPanel", panelText, VerticalAlignment.Top, HorizontalAlignment.Right, textColor);
        }

        private string FormatAmount(double amount)
        {
            return amount.ToString("N2");
        }
        #endregion

        #region AI Agent Data Models
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

        public class BarData
        {
            public string time { get; set; }
            public double open { get; set; }
            public double high { get; set; }
            public double low { get; set; }
            public double close { get; set; }
            public double volume { get; set; }
        }

        public class StrategyData
        {
            public double tema1 { get; set; }
            public double tema2 { get; set; }
            public double rsi { get; set; }
            public double adx { get; set; }
            public double atr { get; set; }
            public double recent_high { get; set; }
            public double recent_low  { get; set; }
            // Asian Range & Judas Sweep fields
            public double asian_high { get; set; }
            public double asian_low { get; set; }
            public double asian_range_pips { get; set; }
            public string killzone_session { get; set; } = "NONE";
            // Gate context fields (pre-filter → AI alignment)
            public string bias_direction     { get; set; } = "NONE";
            public string traditional_signal { get; set; } = "NONE";
            public int    signal_window_bars { get; set; } = 0;
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

        public class SwingStructure
        {
            public double last_swing_high { get; set; }
            public string swing_high_type { get; set; }
            public double last_swing_low { get; set; }
            public string swing_low_type { get; set; }
            public double prev_swing_high { get; set; }
            public double prev_swing_low { get; set; }
            public string market_structure { get; set; }
        }

        public class TimeframeContext
        {
            public string timeframe { get; set; }
            public double fast_tema { get; set; }
            public double slow_tema { get; set; }
            public double rsi { get; set; }
            public string trend_bias { get; set; }
            public double high_35 { get; set; }
            public double low_35 { get; set; }
            public double close { get; set; }
            public SwingStructure swing_structure { get; set; }
        }

        public class MultiTimeframeData
        {
            public TimeframeContext current_tf { get; set; }
            public TimeframeContext h1_tf { get; set; }
            public TimeframeContext h4_tf { get; set; }
        }

        public class MarketSnapshot
        {
            public string request_id { get; set; }
            public string bot_id { get; set; }
            public string symbol { get; set; }
            public string timeframe { get; set; }
            public double ask { get; set; }
            public double bid { get; set; }
            public List<BarData> bars { get; set; }
            public StrategyData strategy { get; set; }
            public MultiTimeframeData multi_timeframe { get; set; }
            public PositionInfo position { get; set; }
            public List<ActivePosition> active_positions { get; set; }
            public List<HistoricalTrade> recent_history { get; set; }
            public string account_number { get; set; }
            public string account_type { get; set; }
            public string account_label { get; set; }
            public double account_balance { get; set; }
            public double account_equity { get; set; }
        }

        public class AgentDecision
        {
            public string request_id { get; set; }
            public string bot_id { get; set; }
            public string symbol { get; set; }
            public string timeframe { get; set; }
            public string action { get; set; }
            public double volume_lots { get; set; }
            public double sl_pips { get; set; }
            public double tp_pips { get; set; }
            public double new_sl_price { get; set; }
            public double new_tp_price { get; set; }
            public string reason { get; set; }
            public double confidence { get; set; }
        }
        #endregion

        #region AI Direct Configuration & Auto-Resolution
        private string _resolvedApiKey = "";
        private string _resolvedApiUrl = "";
        private string _resolvedAiModel = "";
        private string _apiKeySource = "";

        private void ResolveAiDirectConfig()
        {
            string defaultUrl = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions";
            string defaultModel = "qwen3.7-flash";

            _resolvedApiUrl = defaultUrl;
            _resolvedAiModel = defaultModel;
            _resolvedApiKey = "";
            _apiKeySource = "Not Found";

            var searchList = new List<string>
            {
                "API_key.env",
                System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "API_key.env"),
                System.IO.Path.Combine(System.IO.Directory.GetCurrentDirectory(), "API_key.env"),
                System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "API_key.env"),
                System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Documents", "GitHub", "cTrader-AI-Trading-Hub", "API_key.env"),
                System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Documents", "GitHub", "Agent_Gemini_Server", "API_key.env"),
                System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".gemini", "API_key.env")
            };

            try
            {
                var curDir = new System.IO.DirectoryInfo(System.IO.Directory.GetCurrentDirectory());
                for (int i = 0; i < 4 && curDir != null; i++)
                {
                    string candidate = System.IO.Path.Combine(curDir.FullName, "API_key.env");
                    if (!searchList.Contains(candidate)) searchList.Add(candidate);
                    curDir = curDir.Parent;
                }
            }
            catch { }

            string[] keyNames = new string[]
            {
                "APIKey",
                "API_KEY",
                "QWEN_API_KEY",
                "DASHSCOPE_API_KEY",
                "OPENROUTER_API_KEY",
                "AI_API_KEY",
                "OPENAI_API_KEY",
                "GEMINI_API_KEY",
                "DEEPSEEK_API_KEY"
            };

            string[] urlNames = new string[]
            {
                "OpenAI_compatible",
                "OPENAI_COMPATIBLE",
                "AI_ENDPOINT_URL",
                "API_URL",
                "OPENAI_BASE_URL",
                "BASE_URL"
            };

            string[] modelNames = new string[]
            {
                "Model",
                "MODEL",
                "AI_MODEL_NAME",
                "QWEN_MODEL"
            };

            foreach (var filePath in searchList)
            {
                try
                {
                    if (System.IO.File.Exists(filePath))
                    {
                        var lines = System.IO.File.ReadAllLines(filePath);
                        foreach (var line in lines)
                        {
                            var trimmed = line.Trim();
                            if (string.IsNullOrWhiteSpace(trimmed) || trimmed.StartsWith("#")) continue;

                            int eqIdx = trimmed.IndexOf('=');
                            if (eqIdx > 0)
                            {
                                string k = trimmed.Substring(0, eqIdx).Trim();
                                string v = trimmed.Substring(eqIdx + 1).Trim().Trim('"', '\'');

                                if (string.IsNullOrWhiteSpace(v)) continue;

                                if (string.IsNullOrEmpty(_resolvedApiKey))
                                {
                                    foreach (var targetKey in keyNames)
                                    {
                                        if (string.Equals(k, targetKey, StringComparison.OrdinalIgnoreCase))
                                        {
                                            _apiKeySource = $"API_key.env ({targetKey} @ {filePath})";
                                            _resolvedApiKey = v;
                                            break;
                                        }
                                    }
                                }

                                if (urlNames.Any(u => string.Equals(k, u, StringComparison.OrdinalIgnoreCase)))
                                {
                                    string formattedUrl = v.TrimEnd('/');
                                    if (!formattedUrl.EndsWith("/chat/completions", StringComparison.OrdinalIgnoreCase))
                                    {
                                        formattedUrl += "/chat/completions";
                                    }
                                    _resolvedApiUrl = formattedUrl;
                                }

                                if (modelNames.Any(m => string.Equals(k, m, StringComparison.OrdinalIgnoreCase)))
                                {
                                    _resolvedAiModel = v;
                                }
                            }
                        }

                        if (!string.IsNullOrEmpty(_resolvedApiKey)) break;
                    }
                }
                catch { }
            }

            if (string.IsNullOrEmpty(_resolvedApiKey))
            {
                foreach (var targetKey in keyNames)
                {
                    try
                    {
                        string envVal = Environment.GetEnvironmentVariable(targetKey);
                        if (!string.IsNullOrWhiteSpace(envVal))
                        {
                            _apiKeySource = $"Environment Variable ({targetKey})";
                            _resolvedApiKey = envVal.Trim().Trim('"', '\'');
                            break;
                        }
                    }
                    catch { }
                }
            }

            if (!string.IsNullOrWhiteSpace(AiApiKey))
            {
                _apiKeySource = "cBot UI Parameter";
                _resolvedApiKey = AiApiKey.Trim();
            }

            // ONLY override _resolvedApiUrl from ApiUrl parameter if ApiUrl is an explicit external cloud URL (NOT a local python server /trade endpoint)
            if (!string.IsNullOrWhiteSpace(ApiUrl) && 
                !ApiUrl.Contains("127.0.0.1") && 
                !ApiUrl.Contains("localhost") && 
                !ApiUrl.EndsWith("/trade", StringComparison.OrdinalIgnoreCase) && 
                !string.Equals(ApiUrl, "https://openrouter.ai/api/v1/chat/completions", StringComparison.OrdinalIgnoreCase) && 
                !string.Equals(ApiUrl, defaultUrl, StringComparison.OrdinalIgnoreCase))
            {
                string formattedUrl = ApiUrl.Trim().TrimEnd('/');
                if (!formattedUrl.EndsWith("/chat/completions", StringComparison.OrdinalIgnoreCase))
                {
                    formattedUrl += "/chat/completions";
                }
                _resolvedApiUrl = formattedUrl;
            }

            if (!string.IsNullOrWhiteSpace(AiModelName) && !string.Equals(AiModelName, "qwen/qwen-2.5-72b-instruct", StringComparison.OrdinalIgnoreCase) && !string.Equals(AiModelName, defaultModel, StringComparison.OrdinalIgnoreCase))
            {
                _resolvedAiModel = AiModelName.Trim();
            }
        }

        private string ResolveApiKey()
        {
            if (string.IsNullOrEmpty(_resolvedApiKey))
            {
                ResolveAiDirectConfig();
            }
            return _resolvedApiKey;
        }

        private string GetMaskedApiKey(string key)
        {
            if (string.IsNullOrWhiteSpace(key)) return "[EMPTY]";
            if (key.Length <= 8) return "***";
            return $"{key.Substring(0, Math.Min(6, key.Length))}***...{key.Substring(Math.Max(0, key.Length - 4))}";
        }

        private void LogApiKeyResolution()
        {
            ResolveAiDirectConfig();
            if (AiMode == AiConnectionMode.Direct_OpenRouter_DashScope)
            {
                if (!string.IsNullOrWhiteSpace(_resolvedApiKey))
                {
                    Print($"[AI Auth] âœ… Direct AI Ready | Model: {_resolvedAiModel} | Endpoint: {_resolvedApiUrl} | Key: \"{GetMaskedApiKey(_resolvedApiKey)}\" | Source: {_apiKeySource}");
                }
                else
                {
                    Print("[AI Auth] âš ï¸ No API Key found in UI parameters, API_key.env, or Environment Variables. Direct AI queries will be blocked until a valid key is provided.");
                }
            }
        }
        #endregion

        #region AI Agent Decision Parser
        private static double ParseJsonDouble(JsonElement root, string propName, double defaultVal = 0)
        {
            if (!root.TryGetProperty(propName, out var prop)) return defaultVal;
            if (prop.ValueKind == JsonValueKind.Number && prop.TryGetDouble(out var d)) return d;
            if (prop.ValueKind == JsonValueKind.String && double.TryParse(prop.GetString(), System.Globalization.NumberStyles.Any, System.Globalization.CultureInfo.InvariantCulture, out var ds)) return ds;
            return defaultVal;
        }

        private AgentDecision ParseAiDecision(string rawText, string requestId, string sym, string tf)
        {
            if (string.IsNullOrWhiteSpace(rawText)) return null;
            string cleanText = rawText.Trim();

            // 1. Try finding markdown JSON block ```json ... ```
            var match = Regex.Match(cleanText, @"```(?:json)?\s*(\{.*?\})\s*```", RegexOptions.Singleline);
            string jsonStr = match.Success ? match.Groups[1].Value : cleanText;

            // 2. Try finding raw JSON object if markdown block was absent
            if (!match.Success)
            {
                var braceMatch = Regex.Match(cleanText, @"(\{[\s\S]*\})");
                if (braceMatch.Success) jsonStr = braceMatch.Groups[1].Value;
            }

            try
            {
                using (var doc = JsonDocument.Parse(jsonStr))
                {
                    var root = doc.RootElement;
                    string action = root.TryGetProperty("action", out var actProp) ? actProp.GetString() : "HOLD";
                    if (string.IsNullOrWhiteSpace(action)) action = "HOLD";
                    action = action.Trim().ToUpperInvariant();

                    double vol = ParseJsonDouble(root, "volume_lots", 0.01);
                    double sl = ParseJsonDouble(root, "sl_pips", 0);
                    double tp = ParseJsonDouble(root, "tp_pips", 0);
                    double newSl = ParseJsonDouble(root, "new_sl_price", 0);
                    double newTp = ParseJsonDouble(root, "new_tp_price", 0);
                    double conf = ParseJsonDouble(root, "confidence", 80.0);
                    string reason = root.TryGetProperty("reason", out var rProp) ? rProp.GetString() : "Decision generated by AI Agent";

                    return new AgentDecision
                    {
                        request_id = requestId,
                        bot_id = BotId,
                        symbol = sym,
                        timeframe = tf,
                        action = action,
                        volume_lots = vol,
                        sl_pips = sl,
                        tp_pips = tp,
                        new_sl_price = newSl,
                        new_tp_price = newTp,
                        reason = reason,
                        confidence = conf
                    };
                }
            }
            catch (Exception ex)
            {
                Print($"[AI Parse Error] Failed to parse JSON: {ex.Message} | Raw text: {cleanText.Substring(0, Math.Min(120, cleanText.Length))}");
                return null;
            }
        }
        #endregion

        #region AI Agent Communication
        private int _isAgentQuerying = 0;

        private SwingStructure DetectSwingStructure(Bars tfBars)
        {
            if (tfBars == null || tfBars.Count < 10)
            {
                return new SwingStructure
                {
                    last_swing_high = 0,
                    swing_high_type = "N/A",
                    last_swing_low = 0,
                    swing_low_type = "N/A",
                    prev_swing_high = 0,
                    prev_swing_low = 0,
                    market_structure = "SIDEWAYS"
                };
            }

            var swingHighs = new List<double>();
            var swingLows = new List<double>();

            int count = tfBars.Count;
            int lookback = Math.Min(60, count - 3);
            for (int i = 2; i <= lookback; i++)
            {
                int idx = count - 1 - i;
                if (idx < 2) break;

                double high = tfBars.HighPrices[idx];
                if (high > tfBars.HighPrices[idx - 1] && high > tfBars.HighPrices[idx - 2] &&
                    high >= tfBars.HighPrices[idx + 1] && high >= tfBars.HighPrices[idx + 2])
                {
                    if (swingHighs.Count == 0 || Math.Abs(swingHighs[swingHighs.Count - 1] - high) > (Symbol.PipSize * 10))
                    {
                        swingHighs.Add(high);
                    }
                }

                double low = tfBars.LowPrices[idx];
                if (low < tfBars.LowPrices[idx - 1] && low < tfBars.LowPrices[idx - 2] &&
                    low <= tfBars.LowPrices[idx + 1] && low <= tfBars.LowPrices[idx + 2])
                {
                    if (swingLows.Count == 0 || Math.Abs(swingLows[swingLows.Count - 1] - low) > (Symbol.PipSize * 10))
                    {
                        swingLows.Add(low);
                    }
                }

                if (swingHighs.Count >= 2 && swingLows.Count >= 2) break;
            }

            int count20 = Math.Min(20, count);
            double lastSh = swingHighs.Count > 0 ? swingHighs[0] : tfBars.HighPrices.Maximum(count20);
            double prevSh = swingHighs.Count > 1 ? swingHighs[1] : lastSh;
            string shType = lastSh > prevSh ? "HH" : (lastSh < prevSh ? "LH" : "EH");

            double lastSl = swingLows.Count > 0 ? swingLows[0] : tfBars.LowPrices.Minimum(count20);
            double prevSl = swingLows.Count > 1 ? swingLows[1] : lastSl;
            string slType = lastSl > prevSl ? "HL" : (lastSl < prevSl ? "LL" : "EL");

            string structType = "SIDEWAYS";
            if (shType == "HH" && slType == "HL") structType = "BULLISH_HH_HL";
            else if (shType == "LH" && slType == "LL") structType = "BEARISH_LH_LL";
            else if (shType == "HH" && slType == "LL") structType = "EXPANDING_CHOCH";
            else if (shType == "LH" && slType == "HL") structType = "CONTRACTING_RANGE";

            return new SwingStructure
            {
                last_swing_high = Math.Round(lastSh, Symbol.Digits),
                swing_high_type = shType,
                last_swing_low = Math.Round(lastSl, Symbol.Digits),
                swing_low_type = slType,
                prev_swing_high = Math.Round(prevSh, Symbol.Digits),
                prev_swing_low = Math.Round(prevSl, Symbol.Digits),
                market_structure = structType
            };
        }

        private TimeframeContext BuildTimeframeContext(string tfName, Bars tfBars, MovingAverage fastMa, MovingAverage slowMa, RelativeStrengthIndex rsiInd)
        {
            if (tfBars == null || tfBars.Count == 0)
            {
                return new TimeframeContext
                {
                    timeframe = tfName,
                    trend_bias = "NEUTRAL",
                    fast_tema = 0,
                    slow_tema = 0,
                    rsi = 50,
                    high_35 = 0,
                    low_35 = 0,
                    close = 0,
                    swing_structure = new SwingStructure()
                };
            }

            double fVal = fastMa != null && fastMa.Result.Count > 0 ? fastMa.Result.LastValue : tfBars.ClosePrices.LastValue;
            double sVal = slowMa != null && slowMa.Result.Count > 0 ? slowMa.Result.LastValue : tfBars.ClosePrices.LastValue;
            double rVal = rsiInd != null && rsiInd.Result.Count > 0 ? rsiInd.Result.LastValue : 50.0;
            double cVal = tfBars.ClosePrices.LastValue;

            int count35 = Math.Min(35, tfBars.Count);
            double h35 = tfBars.HighPrices.Maximum(count35);
            double l35 = tfBars.LowPrices.Minimum(count35);

            string bias = "NEUTRAL";
            if (fVal > sVal && cVal > sVal) bias = "BULLISH";
            else if (fVal < sVal && cVal < sVal) bias = "BEARISH";
            else bias = "SIDEWAYS";

            var swingStruct = DetectSwingStructure(tfBars);

            return new TimeframeContext
            {
                timeframe = tfName,
                trend_bias = bias,
                fast_tema = Math.Round(fVal, Symbol.Digits),
                slow_tema = Math.Round(sVal, Symbol.Digits),
                rsi = Math.Round(rVal, 1),
                high_35 = Math.Round(h35, Symbol.Digits),
                low_35 = Math.Round(l35, Symbol.Digits),
                close = Math.Round(cVal, Symbol.Digits),
                swing_structure = swingStruct
            };
        }

        private string BuildDualModePrompt(MarketSnapshot snapshot, StrategyData stratData, List<BarData> barList)
        {
            double spreadPips = Math.Round((snapshot.ask - snapshot.bid) / Symbol.PipSize, 1);
            double atrPips = Symbol.PipSize > 0 ? Math.Round(stratData.atr / Symbol.PipSize, 0) : 0;
            int openPosCount = snapshot.active_positions != null ? snapshot.active_positions.Count : 0;
            bool hasOpenPositions = openPosCount > 0 || snapshot.position != null;

            // 1. Format 50 chronological bars (increased from 35 for richer Price Action context)
            int barCount = Math.Min(50, barList.Count);
            var chronologicalBars = barList.Take(barCount).Reverse().ToList();
            var barLines = new List<string>();
            for (int i = 0; i < chronologicalBars.Count; i++)
            {
                var b = chronologicalBars[i];
                int barIdx = -(chronologicalBars.Count - 1 - i);
                barLines.Add($"Bar[{barIdx}]: O={b.open:F2}, H={b.high:F2}, L={b.low:F2}, C={b.close:F2}, V={b.volume:F0}");
            }
            string barsFormatted = string.Join("\n", barLines);

            // 2. Format recent trade history (last 24h, max 5 trades) with Session Performance summary
            string historyFormatted = "No recent trades in the last 24h.";
            if (snapshot.recent_history != null && snapshot.recent_history.Count > 0)
            {
                double totalPnl = snapshot.recent_history.Sum(h => h.pnl);
                int winCount = snapshot.recent_history.Count(h => h.pnl > 0);
                int lossCount = snapshot.recent_history.Count(h => h.pnl < 0);
                string summaryHeader = $"[Session Performance: 24h PnL = {(totalPnl >= 0 ? "+" : "")}${totalPnl:F2} | Wins: {winCount}, Losses: {lossCount}]";

                var histLines = snapshot.recent_history.Select(h =>
                    $"  - {h.trade_type} {h.volume:F2} lots @ {h.entry_price:F2} -> Exit {h.exit_price:F2} | PnL: {(h.pnl >= 0 ? "+" : "")}${h.pnl:F2} | Closed: {h.exit_time}"
                );
                historyFormatted = summaryHeader + "\n" + string.Join("\n", histLines);
            }
            string mtfSummary = "Current Timeframe Only";
            if (snapshot.multi_timeframe != null)
            {
                var cur = snapshot.multi_timeframe.current_tf;
                var h1 = snapshot.multi_timeframe.h1_tf;
                var h4 = snapshot.multi_timeframe.h4_tf;
                var lines = new List<string>();
                foreach (var item in new[] { (cur, $"Current ({cur?.timeframe ?? "M15"})"), (h1, "Higher TF (H1)"), (h4, "Major TF (H4)") })
                {
                    var tfCtx = item.Item1;
                    var label = item.Item2;
                    if (tfCtx != null)
                    {
                        string swStr = "";
                        if (tfCtx.swing_structure != null)
                        {
                            var sw = tfCtx.swing_structure;
                            swStr = $" | Swings: High={sw.last_swing_high} ({sw.swing_high_type}), Low={sw.last_swing_low} ({sw.swing_low_type}), PrevH={sw.prev_swing_high}, PrevL={sw.prev_swing_low} [Struct: {sw.market_structure}]";
                        }
                        lines.Add($"- {label}: Bias={tfCtx.trend_bias} | FastMA={tfCtx.fast_tema} | SlowMA={tfCtx.slow_tema} | RSI={tfCtx.rsi}{swStr}");
                    }
                }
                if (lines.Count > 0) mtfSummary = string.Join("\n", lines);
            }

            if (!hasOpenPositions)
            {
                // === NEW ENTRY DISCOVERY MODE ===
                return $@"You are a World-Class Institutional Forex Specialist & Quantitative Trader using SMART MONEY CONCEPTS (SMC) & Asian Range Judas Sweep.

=== NEW ENTRY DISCOVERY MODE ===
The cBot currently HAS NO OPEN POSITIONS. Your mission is to analyze the Asian Range Liquidity Sweep and identify high-probability Sniper entries.

=== 1. MARKET SNAPSHOT ===
- Symbol: {snapshot.symbol} | Timeframe: {snapshot.timeframe}
- Current Market Prices: Ask={snapshot.ask}, Bid={snapshot.bid} | Spread: {spreadPips:F1} pips
- Account: Balance=${snapshot.account_balance:F2} | Equity=${snapshot.account_equity:F2}

=== 2. ASIAN RANGE & JUDAS SWEEP GATE CONTEXT ===
- Asian Session Range (00:00 - 06:00 UTC): High={stratData.asian_high:F2} | Low={stratData.asian_low:F2} | Range={stratData.asian_range_pips:F0} pips
- Active Killzone Window: {stratData.killzone_session}
- Gate Signal Trigger: {stratData.traditional_signal} (Bias: {stratData.bias_direction})
- Bars Since Sweep: {stratData.signal_window_bars} bar(s)
⚠️ CONSTRAINT:
  - Gate=BUY -> Price swept Asian Low & rejected back up. You MAY ONLY suggest 'BUY' or 'HOLD'. NEVER 'SELL'.
  - Gate=SELL -> Price swept Asian High & rejected back down. You MAY ONLY suggest 'SELL' or 'HOLD'. NEVER 'BUY'.
  - Gate=MANAGE_ONLY -> Do NOT open new positions. Only 'ADJUST', 'HOLD', or 'CLOSE_ALL'.
  - Bars Since Sweep > 3 -> Signal is STALE. Strongly prefer 'HOLD'.
  - volume_lots -> Always output 0. Volume is controlled by the cBot risk engine.

=== 3. MULTI-TIMEFRAME TREND BIAS (M15 + H1 + H4) ===
{mtfSummary}

=== 4. TECHNICAL INDICATORS & SWINGS ===
- Fast EMA: {stratData.tema1:F2} | Slow EMA: {stratData.tema2:F2}
- RSI (14): {stratData.rsi:F1} | ATR (14 Volatility): {atrPips:F0} pips
- Major Swing High (BSL / Resistance): {stratData.recent_high:F2}
- Major Swing Low (SSL / Support): {stratData.recent_low:F2}

=== 5. RECENT OHLCV CANDLE SEQUENCE (Last {barCount} bars, chronological) ===
{barsFormatted}

=== 6. RECENT TRADE HISTORY (Last 24h, Max 5 trades) ===
{historyFormatted}

=== 7. SMART MONEY CONCEPTS (SMC) & JUDAS SWEEP RULES ===
1. Judas Swing Reversal: Price fakeouts above Asian High or below Asian Low during London/NY Killzones, sweeps liquidity (BSL/SSL), and rejects back inside range.
2. Entry Confirmation: Validated Order Block, Fair Value Gap (FVG), or pinbar rejection on M15.
3. Technical SL & TP: Place SL safely beyond the sweep extreme spike (min floor 200 pips); TP targeted at opposing Asian Range boundary (Asian Low for SELL, Asian High for BUY) or target liquidity pool. For XAUUSD, $1.00 move = 100 pips.

=== 8. VALID ACTIONS ===
- BUY: Validated Bullish Judas Sweep (Asian Low fakeout) + Order Block bounce.
- SELL: Validated Bearish Judas Sweep (Asian High fakeout) + Order Block rejection.
- HOLD: Choppy consolidation inside Asian Range, no sweep, or conflicting HTF bias.

Reply strictly with JSON object.";
            }
            else
            {
                // === ACTIVE POSITION MANAGEMENT MODE ===
                var posLines = new List<string>();
                if (snapshot.position != null)
                {
                    posLines.Add($"- Primary Position: {snapshot.position.type} {snapshot.position.volume:F2} lots @ Entry={snapshot.position.entry_price} | CurrentPrice={snapshot.position.current_price} | PnL=${snapshot.position.pnl:F2} | SL={snapshot.position.sl} | TP={snapshot.position.tp} | Duration={snapshot.position.duration_minutes:F1} mins");
                }
                if (snapshot.active_positions != null)
                {
                    foreach (var p in snapshot.active_positions)
                    {
                        posLines.Add($"- Position ID {p.id}: {p.trade_type} {p.volume:F2} lots @ Entry={p.entry_price} | SL={p.sl} | TP={p.tp} | Opened={p.entry_time}");
                    }
                }
                string runningPositionsStr = posLines.Count > 0 ? string.Join("\n", posLines) : "No position details.";

                return $@"You are a World-Class Institutional Forex Specialist & Quantitative Risk Manager using SMART MONEY CONCEPTS (SMC) & Price Action.

=== ACTIVE POSITION MANAGEMENT MODE ===
The cBot currently HAS OPEN POSITIONS in the order book. Your PRIMARY MISSION is to EVALUATE AND MANAGE THESE EXISTING POSITIONS (Protect capital, lock in profits, adjust SL/TP, or exit safely).

=== 1. ACTIVE ORDER BOOK SNAPSHOT ===
- Symbol: {snapshot.symbol} | Timeframe: {snapshot.timeframe}
- Current Market Prices: Ask={snapshot.ask}, Bid={snapshot.bid} | Spread: {spreadPips:F1} pips
- Account: Balance=${snapshot.account_balance:F2} | Equity=${snapshot.account_equity:F2}
- Running Positions:
{runningPositionsStr}

=== 2. TRADITIONAL STRATEGY GATE â€” MANDATORY CONSTRAINT ===
- Gate Direction: {stratData.bias_direction}
- Signal Type: {stratData.traditional_signal}
- Bars Since Cross: {stratData.signal_window_bars} bar(s)
âš ï¸ CONSTRAINT:
  - Gate=MANAGE_ONLY â†’ Focus on managing existing positions. Do NOT open new ones.
  - volume_lots â†’ Always output 0. Volume is controlled by the cBot risk engine.

=== 3. MULTI-TIMEFRAME TREND BIAS (M15 + H1 + H4) ===
{mtfSummary}

=== 4. TECHNICAL INDICATORS & SWINGS ===
- Fast EMA: {stratData.tema1:F2} | Slow EMA: {stratData.tema2:F2}
- RSI (14): {stratData.rsi:F1} | ATR (14 Volatility): {atrPips:F0} pips
- Major Swing High (Resistance): {stratData.recent_high:F2}
- Major Swing Low (Support): {stratData.recent_low:F2}

=== 5. RECENT OHLCV CANDLE SEQUENCE (Last {barCount} bars, chronological) ===
{barsFormatted}

=== 6. POSITION MANAGEMENT EVALUATION RULES ===
1. Trend & Structure Health: Check if current structure still favors the open position.
2. Action Decisions:
   - HOLD: Position healthy and progressing towards TP.
   - ADJUST: Move SL to Break-Even (when in >= 1:1 RR profit) or Trailing Stop behind new Order Block. Specify new_sl_price and/or new_tp_price (or sl_pips/tp_pips).
   - CLOSE_ALL: Emergency exit if major opposing CHoCH reversal occurs against the position.
   - BUY / SELL: Scale-in ONLY if trend is extremely strong with fresh unmitigated Order Block.

Reply strictly with JSON object.";
            }
        }

        private async Task SendStateToAgentAsync(string allowedDirection = "NONE")
        {
            if (RunningMode != RunningMode.RealTime) return;
            if (Interlocked.CompareExchange(ref _isAgentQuerying, 1, 0) != 0) return;

            try
            {
                // Check Safety Guard Cooldown
                if (Server.Time < _aiCooldownUntil)
                {
                    Print($"[AI Agent Safety Guard] Cooldown active until {_aiCooldownUntil:HH:mm:ss} UTC. Direct AI query skipped.");
                    return;
                }

                var barList = new List<BarData>();
                int maxBars = Math.Min(50, Bars.Count);
                for (int i = 1; i <= maxBars; i++)
                {
                    int index = Bars.Count - i;
                    barList.Add(new BarData
                    {
                        time = Bars.OpenTimes[index].ToString("o"),
                        open = Bars.OpenPrices[index],
                        high = Bars.HighPrices[index],
                        low = Bars.LowPrices[index],
                        close = Bars.ClosePrices[index],
                        volume = Bars.TickVolumes[index]
                    });
                }

                double recentHigh = Bars.HighPrices.Maximum(35);
                double recentLow = Bars.LowPrices.Minimum(35);

                var stratData = new StrategyData
                {
                    tema1 = fastEma != null && fastEma.Result.Count > 0 ? fastEma.Result.LastValue : 0,
                    tema2 = slowEma != null && slowEma.Result.Count > 0 ? slowEma.Result.LastValue : 0,
                    rsi = rsi != null && rsi.Result.Count > 0 ? rsi.Result.LastValue : 0,
                    adx = 0,
                    atr = atr != null && atr.Result.Count > 0 ? atr.Result.LastValue : 0,
                    recent_high = recentHigh,
                    recent_low = recentLow,
                    asian_high = _asianHigh,
                    asian_low = _asianLow,
                    asian_range_pips = _asianRangePips,
                    killzone_session = _activeKillzone,
                    bias_direction = _allowedAiDirection,
                    traditional_signal = _traditionalSignal,
                    signal_window_bars = _barsSinceCross
                };

                var curTfContext = BuildTimeframeContext(TimeFrame.Name, Bars, fastEma, slowEma, rsi);
                var h1TfContext = BuildTimeframeContext("H1", _h1Bars, _h1FastEma, _h1SlowEma, _h1Rsi);
                var h4TfContext = BuildTimeframeContext("H4", _h4Bars, _h4FastEma, _h4SlowEma, _h4Rsi);

                var mtfData = new MultiTimeframeData
                {
                    current_tf = curTfContext,
                    h1_tf = h1TfContext,
                    h4_tf = h4TfContext
                };

                var activePositionsList = new List<ActivePosition>();
                foreach (var pos in Positions.FindAll(label, SymbolName))
                {
                    activePositionsList.Add(new ActivePosition
                    {
                        id = pos.Id,
                        symbol = pos.SymbolName,
                        trade_type = pos.TradeType.ToString(),
                        volume = Math.Round(pos.VolumeInUnits / Symbol.LotSize, 2),
                        entry_price = pos.EntryPrice,
                        sl = pos.StopLoss ?? 0,
                        tp = pos.TakeProfit ?? 0,
                        entry_time = pos.EntryTime.ToString("yyyy-MM-dd HH:mm:ss")
                    });
                }

                var recentHistoryList = new List<HistoricalTrade>();
                var recentTime = Server.Time.AddDays(-1);
                foreach (var hist in History.Where(h => h.Label == label && h.SymbolName == SymbolName && h.ClosingTime >= recentTime)
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

                string currentRequestId = Guid.NewGuid().ToString("N");

                var snapshot = new MarketSnapshot
                {
                    request_id = currentRequestId,
                    bot_id = BotId,
                    symbol = SymbolName,
                    timeframe = TimeFrame.Name,
                    ask = Symbol.Ask,
                    bid = Symbol.Bid,
                    bars = barList,
                    strategy = stratData,
                    multi_timeframe = mtfData,
                    active_positions = activePositionsList,
                    recent_history = recentHistoryList,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = string.IsNullOrWhiteSpace(AccountLabel) ? Account.BrokerName : $"{Account.BrokerName} ({AccountLabel.Trim()})",
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                var positions = Positions.FindAll(label, SymbolName);
                if (positions.Length > 0)
                {
                    var p = positions[0];
                    snapshot.position = new PositionInfo
                    {
                        id = p.Id,
                        type = p.TradeType.ToString(),
                        volume = p.VolumeInUnits / Symbol.LotSize,
                        entry_price = p.EntryPrice,
                        current_price = p.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask,
                        pnl = p.NetProfit,
                        sl = p.StopLoss,
                        tp = p.TakeProfit,
                        duration_minutes = (Server.Time - p.EntryTime).TotalMinutes
                    };
                }

                if (AiMode == AiConnectionMode.Direct_OpenRouter_DashScope)
                {
                    await QueryDirectQwenApiAsync(snapshot, stratData, barList, currentRequestId);
                }
                else
                {
                    var json = JsonSerializer.Serialize(snapshot);
                    await AskAgentAsync(json, currentRequestId);
                }
            }
            catch (Exception ex)
            {
                Print($"[Agent Error] Failed to process market state: {ex.Message}");
            }
            finally
            {
                Interlocked.Exchange(ref _isAgentQuerying, 0);
            }
        }

        private async Task QueryDirectQwenApiAsync(MarketSnapshot snapshot, StrategyData stratData, List<BarData> barList, string expectedRequestId)
        {
            try
            {
                if (_httpClient == null || snapshot == null) return;

                ResolveAiDirectConfig();
                string effectiveKey = _resolvedApiKey;
                if (string.IsNullOrWhiteSpace(effectiveKey))
                {
                    HandleAiFailure("No valid AI API Key found in parameters, API_key.env, or Environment Variables.");
                    return;
                }

                string targetUrl = _resolvedApiUrl;
                string model = _resolvedAiModel;

                Print($"[Qwen AI Direct] Querying {model} via {targetUrl} [Req: {expectedRequestId.Substring(0, 8)}...] (Auth: {_apiKeySource})...");

                string systemPrompt = "You are an elite Algorithmic Trading AI Co-Pilot for cTrader. Analyze the real-time market snapshot and output strictly valid JSON format with keys: \"action\" (\"BUY\"|\"SELL\"|\"HOLD\"|\"ADJUST\"|\"CLOSE_ALL\"), \"volume_lots\" (number), \"sl_pips\" (number), \"tp_pips\" (number), \"new_sl_price\" (number), \"new_tp_price\" (number), \"confidence\" (number between 0 and 100), \"reason\" (concise technical rationale). Output NO markdown explanations outside the JSON object.";

                string userPrompt = BuildDualModePrompt(snapshot, stratData, barList);

                var payloadObj = new
                {
                    model = model,
                    messages = new object[]
                    {
                        new { role = "system", content = systemPrompt },
                        new { role = "user", content = userPrompt }
                    },
                    temperature = 0.2,
                    response_format = new { type = "json_object" }
                };

                string jsonPayload = JsonSerializer.Serialize(payloadObj);
                var request = new HttpRequestMessage(System.Net.Http.HttpMethod.Post, targetUrl)
                {
                    Content = new StringContent(jsonPayload, Encoding.UTF8, "application/json")
                };

                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", effectiveKey);

                var response = await _httpClient.SendAsync(request);
                if (!response.IsSuccessStatusCode)
                {
                    string errContent = await response.Content.ReadAsStringAsync();
                    HandleAiFailure($"HTTP {(int)response.StatusCode} {response.ReasonPhrase}: {errContent}");
                    return;
                }

                string responseBody = await response.Content.ReadAsStringAsync();
                AgentDecision decision = null;

                using (var doc = JsonDocument.Parse(responseBody))
                {
                    if (doc.RootElement.TryGetProperty("choices", out var choices) && choices.GetArrayLength() > 0)
                    {
                        var msg = choices[0].GetProperty("message");
                        if (msg.TryGetProperty("content", out var contentProp))
                        {
                            string contentStr = contentProp.GetString();
                            decision = ParseAiDecision(contentStr, expectedRequestId, snapshot.symbol, snapshot.timeframe);
                        }
                    }
                }

                if (decision != null)
                {
                    _consecutiveAiFailures = 0; // Reset Safety Guard on success

                    // Confidence check
                    if (decision.confidence < AiConfidenceThreshold && (decision.action == "BUY" || decision.action == "SELL"))
                    {
                        Print($"[Qwen AI Notice] Confidence {decision.confidence:F1}% is below threshold {AiConfidenceThreshold:F1}%. Action adjusted to HOLD.");
                        decision.action = "HOLD";
                        decision.reason = $"Confidence {decision.confidence:F1}% < {AiConfidenceThreshold:F1}%. {decision.reason}";
                    }

                    BeginInvokeOnMainThread(() => ExecuteDecision(decision, expectedRequestId));

                    // Async Fire-and-forget Dashboard Telemetry
                    if (EnableDashboardTelemetry && !string.IsNullOrWhiteSpace(DashboardServerUrl))
                    {
                        _ = DispatchDashboardTelemetryAsync(snapshot, decision);
                    }
                }
                else
                {
                    HandleAiFailure("Invalid or empty JSON returned in choices[0].message.content");
                }
            }
            catch (Exception ex)
            {
                HandleAiFailure(ex.Message);
            }
        }

        private void HandleAiFailure(string errorMessage)
        {
            _consecutiveAiFailures++;
            Print($"[AI Agent Warning] Direct AI Query failed ({_consecutiveAiFailures}/3): {errorMessage}");

            if (_consecutiveAiFailures >= 3)
            {
                _aiCooldownUntil = DateTime.UtcNow.AddMinutes(15);
                Print($"[AI Agent Safety Guard] ðŸš¨ 3 consecutive AI failures reached! Pausing AI evaluation for 15 minutes until {_aiCooldownUntil:HH:mm:ss} UTC.");
                _ = SendTelegramAlertAsync($"ðŸš¨ <b>[AI Agent Safety Guard]</b>\nQwen AI API encountered 3 consecutive failures!\nBot evaluation is suspended for 15 minutes until <b>{_aiCooldownUntil:HH:mm:ss} UTC</b>.\n<i>Last error: {errorMessage}</i>");
            }
        }

        private async Task DispatchDashboardTelemetryAsync(MarketSnapshot snapshot, AgentDecision decision)
        {
            try
            {
                if (_httpClient == null || !EnableDashboardTelemetry || string.IsNullOrWhiteSpace(DashboardServerUrl) || snapshot == null) return;
                var baseUri = DashboardServerUrl.TrimEnd('/');
                var url = $"{baseUri}/api/tick";

                var payload = new
                {
                    bot_id = snapshot.bot_id,
                    account_number = snapshot.account_number,
                    symbol = snapshot.symbol,
                    bid = snapshot.bid,
                    ask = snapshot.ask,
                    equity = snapshot.account_equity,
                    balance = snapshot.account_balance,
                    decision = decision,
                    snapshot = snapshot
                };

                var json = JsonSerializer.Serialize(payload);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                await _httpClient.PostAsync(url, content);
            }
            catch { }
        }

        private async Task AskAgentAsync(string jsonPayload, string expectedRequestId)
        {
            try
            {
                if (_httpClient == null) return;
                string localTargetUrl = ApiUrl;
                if (string.IsNullOrWhiteSpace(localTargetUrl) || !localTargetUrl.EndsWith("/trade", StringComparison.OrdinalIgnoreCase))
                {
                    var baseUri = !string.IsNullOrWhiteSpace(DashboardServerUrl) ? DashboardServerUrl.TrimEnd('/') : "http://127.0.0.1:8181";
                    localTargetUrl = $"{baseUri}/trade";
                }

                Print($"[AI Agent] Sending market snapshot for {SymbolName} ({TimeFrame.Name}) [Req: {expectedRequestId.Substring(0, 8)}...] to {localTargetUrl}...");
                var content = new StringContent(jsonPayload, Encoding.UTF8, "application/json");
                var response = await _httpClient.PostAsync(localTargetUrl, content);

                if (!response.IsSuccessStatusCode)
                {
                    Print($"[Agent HTTP Error] {response.StatusCode} from {localTargetUrl}");
                    HandleAiFailure($"HTTP {(int)response.StatusCode} from {localTargetUrl}");
                    return;
                }

                var resultJson = await response.Content.ReadAsStringAsync();
                var jsonOptions = new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true,
                    NumberHandling = JsonNumberHandling.AllowReadingFromString
                };
                var decision = JsonSerializer.Deserialize<AgentDecision>(resultJson, jsonOptions);

                if (decision != null)
                {
                    _consecutiveAiFailures = 0;
                    BeginInvokeOnMainThread(() => ExecuteDecision(decision, expectedRequestId));
                }
                else
                {
                    HandleAiFailure("Failed to deserialize AgentDecision from local server");
                }
            }
            catch (Exception ex)
            {
                HandleAiFailure($"Communication failed to {ApiUrl}: {ex.Message}");
            }
        }

        private async Task ReportPositionOpen(Position position, double slPips, double tpPips, string reason = "")
        {
            try
            {
                if (_httpClient == null) return;
                var baseUri = (AiMode == AiConnectionMode.Direct_OpenRouter_DashScope && !string.IsNullOrWhiteSpace(DashboardServerUrl))
                    ? DashboardServerUrl.TrimEnd('/')
                    : ApiUrl.Replace("/trade", "");
                var reportUrl = $"{baseUri}/portfolio/report";

                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = BotId,
                    action = "open",
                    symbol = SymbolName,
                    side = position.TradeType.ToString(),
                    volume = position.VolumeInUnits / Symbol.LotSize,
                    entry_price = position.EntryPrice,
                    sl_pips = slPips,
                    tp_pips = tpPips,
                    reason = reason,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = string.IsNullOrWhiteSpace(AccountLabel) ? Account.BrokerName : $"{Account.BrokerName} ({AccountLabel.Trim()})",
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                var json = JsonSerializer.Serialize(report);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                await _httpClient.PostAsync(reportUrl, content);
            }
            catch (Exception ex)
            {
                Print($"[Agent Portfolio] Failed to report position open: {ex.Message}");
            }
        }

        private async Task ReportPositionClosed(Position position, double pnl)
        {
            try
            {
                if (_httpClient == null) return;
                var baseUri = (AiMode == AiConnectionMode.Direct_OpenRouter_DashScope && !string.IsNullOrWhiteSpace(DashboardServerUrl))
                    ? DashboardServerUrl.TrimEnd('/')
                    : ApiUrl.Replace("/trade", "");
                var reportUrl = $"{baseUri}/portfolio/report";

                var report = new
                {
                    ctrader_id = position.Id,
                    bot_id = BotId,
                    action = "close",
                    symbol = SymbolName,
                    exit_price = position.EntryPrice,
                    pnl = pnl,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = string.IsNullOrWhiteSpace(AccountLabel) ? Account.BrokerName : $"{Account.BrokerName} ({AccountLabel.Trim()})",
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                var json = JsonSerializer.Serialize(report);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                await _httpClient.PostAsync(reportUrl, content);
            }
            catch (Exception ex)
            {
                Print($"[Agent Portfolio] Failed to report position closed: {ex.Message}");
            }
        }

        private DateTime _lastTickTelemetryTime = DateTime.MinValue;

        private void SendLiveTickTelemetry(bool force = false)
        {
            if (RunningMode != RunningMode.RealTime) return;
            if (_httpClient == null) return;
            if (!force) return; // Periodic tick disabled to eliminate CPU overhead; only send on order events

            try
            {
                // Synchronously capture all cTrader COM/API objects on the Main Thread
                var posList = new List<object>();
                foreach (var p in Positions)
                {
                    posList.Add(new
                    {
                        id = p.Id,
                        side = p.TradeType.ToString(),
                        volume = p.VolumeInUnits / Symbol.LotSize,
                        entry_price = p.EntryPrice,
                        net_profit = p.NetProfit,
                        pips = p.Pips
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

                var baseUri = (AiMode == AiConnectionMode.Direct_OpenRouter_DashScope && !string.IsNullOrWhiteSpace(DashboardServerUrl))
                    ? DashboardServerUrl.TrimEnd('/')
                    : (ApiUrl.Contains("/trade") ? ApiUrl.Replace("/trade", "") : "http://127.0.0.1:8181");
                var tickUrl = $"{baseUri}/api/tick";

                var json = JsonSerializer.Serialize(telemetry);

                // Run only the HTTP network request in the background task
                Task.Run(async () =>
                {
                    try
                    {
                        var content = new StringContent(json, Encoding.UTF8, "application/json");
                        await _httpClient.PostAsync(tickUrl, content);
                    }
                    catch { }
                });
            }
            catch (Exception ex)
            {
                Print($"[Tick Telemetry Error] {ex.Message}");
            }
        }

        private void ExecuteDecision(AgentDecision decision, string expectedRequestId = "")
        {
            try
            {
                if (decision == null) return;

                // 1. Robust Cross-Ticker & Cross-Instance Verification
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

                // 3. Robust Timeframe Verification
                if (!string.IsNullOrEmpty(decision.timeframe))
                {
                    bool tfMatch = string.Equals(decision.timeframe, TimeFrame.Name, StringComparison.OrdinalIgnoreCase) ||
                                   string.Equals(decision.timeframe, TimeFrame.ToString(), StringComparison.OrdinalIgnoreCase) ||
                                   decision.timeframe.Replace(" ", "").Equals(TimeFrame.Name.Replace(" ", ""), StringComparison.OrdinalIgnoreCase);
                    if (!tfMatch)
                    {
                        Print($"[Security Alert] Timeframe mismatch! Expected '{TimeFrame.Name}', but received '{decision.timeframe}'. Action DISCARDED!");
                        return;
                    }
                }

                // 4. Robust Bot ID Verification
                if (!string.IsNullOrEmpty(decision.bot_id) && !string.IsNullOrEmpty(BotId) && !string.Equals(decision.bot_id, BotId, StringComparison.OrdinalIgnoreCase))
                {
                    Print($"[Security Alert] BotID mismatch! Expected '{BotId}', but received '{decision.bot_id}'. Action DISCARDED!");
                    return;
                }

                string action = (decision.action ?? "").Trim().ToUpperInvariant();
                Print($"[AI Decision] Action: {action} | Symbol: {SymbolName} | Confidence: {decision.confidence:F1}% | Reason: {decision.reason}");

                if (action == "CLOSE_ALL")
                {
                    Print($"[AI Agent Action] Executing CLOSE_ALL on all positions. Reason: {decision.reason}");
                    CloseAllPositions();
                    _ = SendTelegramAlertAsync($"🚨 <b>[AI Agent] CLOSE_ALL Executed</b>\nReason: {decision.reason}\nConfidence: {decision.confidence:F1}%");
                    return;
                }

                if (action == "ADJUST")
                {
                    var openPos = Positions.FindAll(label, SymbolName);
                    if (openPos.Length > 0)
                    {
                        foreach (var pos in openPos)
                        {
                            double? targetSL = null;
                            if (decision.new_sl_price > 0)
                            {
                                targetSL = Math.Round(decision.new_sl_price, Symbol.Digits);
                            }
                            else if (decision.sl_pips > 0 && !pos.StopLoss.HasValue)
                            {
                                double slPrice = pos.TradeType == TradeType.Buy 
                                    ? pos.EntryPrice - decision.sl_pips * Symbol.PipSize 
                                    : pos.EntryPrice + decision.sl_pips * Symbol.PipSize;
                                targetSL = Math.Round(slPrice, Symbol.Digits);
                            }

                            double? targetTP = null;
                            if (decision.new_tp_price > 0)
                            {
                                targetTP = Math.Round(decision.new_tp_price, Symbol.Digits);
                            }
                            else if (decision.tp_pips > 0 && !pos.TakeProfit.HasValue)
                            {
                                double tpPrice = pos.TradeType == TradeType.Buy 
                                    ? pos.EntryPrice + decision.tp_pips * Symbol.PipSize 
                                    : pos.EntryPrice - decision.tp_pips * Symbol.PipSize;
                                targetTP = Math.Round(tpPrice, Symbol.Digits);
                            }

                            double currentAsk = Symbol.Ask;
                            double currentBid = Symbol.Bid;
                            double minStopBuffer = Math.Max(Symbol.Spread * 3, Symbol.PipSize * 20);

                            // ── 1. SELL Position Intelligent TP/SL Analysis ──
                            if (pos.TradeType == TradeType.Sell)
                            {
                                // A. Smart Auto-Mapping: If targetTP is between Entry and Market (currentAsk < targetTP < pos.EntryPrice)
                                // Geometrically on a SELL order, a price between Entry and Market is a POSITIVE TRAILING STOP LOSS!
                                if (targetTP.HasValue && targetTP.Value > (currentAsk + minStopBuffer) && targetTP.Value < pos.EntryPrice)
                                {
                                    Print($"[AI Smart Auto-Mapping] Detected targetTP {targetTP.Value:F2} is between Entry ({pos.EntryPrice:F2}) and Market ({currentAsk:F2}) on SELL #{pos.Id}. Re-mapping to Positive Trailing SL to lock profit!");
                                    targetSL = targetTP.Value;
                                    targetTP = pos.TakeProfit; // Preserve original TP target
                                }

                                // B. Genuine Take Profit Reached: Target TP is at or above current market price
                                if (targetTP.HasValue && targetTP.Value >= (currentBid - minStopBuffer))
                                {
                                    Print($"[AI Agent TP Reached] Target TP {targetTP.Value:F2} reached/within buffer of current price (Bid: {currentBid:F2}, Ask: {currentAsk:F2}). Closing SELL position #{pos.Id} to lock profit!");
                                    ClosePosition(pos);
                                    _ = SendTelegramAlertAsync($"🎯 <b>[AI Agent] Take Profit Reached!</b>\nTarget TP {targetTP.Value:F2} reached at current price {currentBid:F2}.\nClosed SELL position #{pos.Id} to lock profit.\nReason: {decision.reason}");
                                    continue;
                                }

                                // C. Stop Loss Handling (Hybrid Protection Engine)
                                if (targetSL.HasValue && targetSL.Value <= (currentAsk + minStopBuffer))
                                {
                                    bool inProfit = currentAsk < pos.EntryPrice;
                                    if (inProfit)
                                    {
                                        // Case 1: Trade is in profit -> Close position immediately to lock remaining gains!
                                        Print($"[AI Smart SL Exit] SELL #{pos.Id} is in profit ($+{pos.NetProfit:F2}) and proposed SL {targetSL.Value:F2} is breached by current price (Ask: {currentAsk:F2}). Closing position immediately to lock profit!");
                                        ClosePosition(pos);
                                        _ = SendTelegramAlertAsync($"🎯 <b>[AI Agent] Profit Lock Exit!</b>\nSELL #{pos.Id} closed at {currentAsk:F2} (Net Profit: ${pos.NetProfit:F2}) as trailing SL was breached.\nReason: {decision.reason}");
                                        continue;
                                    }
                                    else
                                    {
                                        // Case 2: Trade is in drawdown -> Retain original SL to prevent premature stop-out and broker rejection
                                        Print($"[AI Smart SL Notice] SELL #{pos.Id} is in drawdown and proposed SL {targetSL.Value:F2} is within current market price (Ask: {currentAsk:F2}). Retaining original safe SL ({pos.StopLoss}) to allow trade room to breathe.");
                                        targetSL = pos.StopLoss;
                                    }
                                }
                            }
                            // ── 2. BUY Position Intelligent TP/SL Analysis ──
                            else if (pos.TradeType == TradeType.Buy)
                            {
                                // A. Smart Auto-Mapping: If targetTP is between Entry and Market (pos.EntryPrice < targetTP < currentBid - minStopBuffer)
                                // Geometrically on a BUY order, a price between Entry and Market is a POSITIVE TRAILING STOP LOSS!
                                if (targetTP.HasValue && targetTP.Value < (currentBid - minStopBuffer) && targetTP.Value > pos.EntryPrice)
                                {
                                    Print($"[AI Smart Auto-Mapping] Detected targetTP {targetTP.Value:F2} is between Entry ({pos.EntryPrice:F2}) and Market ({currentBid:F2}) on BUY #{pos.Id}. Re-mapping to Positive Trailing SL to lock profit!");
                                    targetSL = targetTP.Value;
                                    targetTP = pos.TakeProfit; // Preserve original TP target
                                }

                                // B. Genuine Take Profit Reached: Target TP is at or below current market price
                                if (targetTP.HasValue && targetTP.Value <= (currentAsk + minStopBuffer))
                                {
                                    Print($"[AI Agent TP Reached] Target TP {targetTP.Value:F2} reached/within buffer of current price (Ask: {currentAsk:F2}, Bid: {currentBid:F2}). Closing BUY position #{pos.Id} to lock profit!");
                                    ClosePosition(pos);
                                    _ = SendTelegramAlertAsync($"🎯 <b>[AI Agent] Take Profit Reached!</b>\nTarget TP {targetTP.Value:F2} reached at current price {currentAsk:F2}.\nClosed BUY position #{pos.Id} to lock profit.\nReason: {decision.reason}");
                                    continue;
                                }

                                // C. Stop Loss Handling (Hybrid Protection Engine)
                                if (targetSL.HasValue && targetSL.Value >= (currentBid - minStopBuffer))
                                {
                                    bool inProfit = currentBid > pos.EntryPrice;
                                    if (inProfit)
                                    {
                                        // Case 1: Trade is in profit -> Close position immediately to lock remaining gains!
                                        Print($"[AI Smart SL Exit] BUY #{pos.Id} is in profit ($+{pos.NetProfit:F2}) and proposed SL {targetSL.Value:F2} is breached by current price (Bid: {currentBid:F2}). Closing position immediately to lock profit!");
                                        ClosePosition(pos);
                                        _ = SendTelegramAlertAsync($"🎯 <b>[AI Agent] Profit Lock Exit!</b>\nBUY #{pos.Id} closed at {currentBid:F2} (Net Profit: ${pos.NetProfit:F2}) as trailing SL was breached.\nReason: {decision.reason}");
                                        continue;
                                    }
                                    else
                                    {
                                        // Case 2: Trade is in drawdown -> Retain original SL to prevent premature stop-out and broker rejection
                                        Print($"[AI Smart SL Notice] BUY #{pos.Id} is in drawdown and proposed SL {targetSL.Value:F2} is within current market price (Bid: {currentBid:F2}). Retaining original safe SL ({pos.StopLoss}) to allow trade room to breathe.");
                                        targetSL = pos.StopLoss;
                                    }
                                }
                            }

                            // ── 3. Modify Position with Boundary Validation ──
                            if (targetSL.HasValue || targetTP.HasValue)
                            {
                                double? finalSL = targetSL ?? pos.StopLoss;
                                double? finalTP = targetTP ?? pos.TakeProfit;

                                bool slChanged = (finalSL.HasValue && (!pos.StopLoss.HasValue || Math.Abs(finalSL.Value - pos.StopLoss.Value) > (Symbol.PipSize * 0.5)));
                                bool tpChanged = (finalTP.HasValue && (!pos.TakeProfit.HasValue || Math.Abs(finalTP.Value - pos.TakeProfit.Value) > (Symbol.PipSize * 0.5)));

                                if (!slChanged && !tpChanged)
                                {
                                    Print($"[AI Agent ADJUST Notice] Position #{pos.Id} SL/TP unchanged (SL: {pos.StopLoss}, TP: {pos.TakeProfit}). No modification needed.");
                                    continue;
                                }

                                bool isSlValid = !finalSL.HasValue || (pos.TradeType == TradeType.Buy ? finalSL.Value < (currentBid - minStopBuffer) : finalSL.Value > (currentAsk + minStopBuffer));
                                bool isTpValid = !finalTP.HasValue || (pos.TradeType == TradeType.Buy ? finalTP.Value > (currentAsk + minStopBuffer) : finalTP.Value < (currentBid - minStopBuffer));

                                if (isSlValid && isTpValid)
                                {
#pragma warning disable CS0618
                                    ModifyPosition(pos, finalSL, finalTP);
#pragma warning restore CS0618
                                    Print($"[AI Agent ADJUST] Position #{pos.Id} updated -> SL: {finalSL}, TP: {finalTP}. Reason: {decision.reason}");
                                }
                                else
                                {
                                    Print($"[AI Agent ADJUST Notice] Stop distance too close for #{pos.Id} (SL: {finalSL}, TP: {finalTP} vs Bid: {currentBid:F2}, Ask: {currentAsk:F2}, MinBuffer: {minStopBuffer:F2}). Skipped modification.");
                                }
                            }
                        }
                        _ = SendTelegramAlertAsync($"⚙️ <b>[AI Agent] ADJUST Evaluated</b>\nEvaluated SL/TP on {openPos.Length} position(s).\nReason: {decision.reason}");
                    }
                    return;
                }

                if (action == "HOLD")
                {
                    Print($"[AI Agent HOLD] Market in equilibrium or position healthy. Reason: {decision.reason}");
                    return;
                }

                if (action != "BUY" && action != "SELL") return;

                if (Positions.FindAll(label, SymbolName).Length >= maxPermittedOrder)
                {
                    Print($"[AI Agent Notice] Action {action} received, but max permitted orders ({maxPermittedOrder}) reached.");
                    return;
                }

                var tradeType = action == "BUY" ? TradeType.Buy : TradeType.Sell;

                // Calculate SL/TP in Pips from exact structural price if available
                double slPips = 0;
                if (decision.new_sl_price > 0)
                {
                    slPips = tradeType == TradeType.Buy 
                        ? Math.Max(0, Math.Round((Symbol.Ask - decision.new_sl_price) / Symbol.PipSize, 1))
                        : Math.Max(0, Math.Round((decision.new_sl_price - Symbol.Bid) / Symbol.PipSize, 1));
                }
                if (slPips <= 0) slPips = decision.sl_pips;
                if (slPips <= 0) slPips = stoplossPip;

                double tpPips = 0;
                if (decision.new_tp_price > 0)
                {
                    tpPips = tradeType == TradeType.Buy 
                        ? Math.Max(0, Math.Round((decision.new_tp_price - Symbol.Ask) / Symbol.PipSize, 1))
                        : Math.Max(0, Math.Round((Symbol.Bid - decision.new_tp_price) / Symbol.PipSize, 1));
                }
                if (tpPips <= 0) tpPips = decision.tp_pips;
                if (tpPips <= 0) tpPips = takeprofitPip;

                // ── Safety Guard: Dynamic ATR & Minimum SL Floor (Anti-Stop-Hunt) ─────────
                double currentAtrPips = (atr != null && atr.Result.Count > 0 && Symbol.PipSize > 0) 
                    ? Math.Round(atr.Result.LastValue / Symbol.PipSize, 0) 
                    : 0;
                double effectiveMinFloor = Math.Max(AiSlMinFloorPips, currentAtrPips > 0 ? Math.Round(currentAtrPips * 0.8, 0) : 200.0);
                if (slPips > 0 && slPips < effectiveMinFloor)
                {
                    Print($"[AI Safety Guard] AI suggested SL={slPips:F0} pips is too tight (< ATR Floor {effectiveMinFloor:F0} pips). Clamped to {effectiveMinFloor:F0} pips to prevent stop hunting.");
                    slPips = effectiveMinFloor;
                }

                // ── Volume Authority: Always use internal risk management (ignore AI volume_lots) ─
                // Calculate volume dynamically based on actual AI slPips and account equity risk
                double volume;
                if (enableFixedVol)
                {
                    volume = Symbol.NormalizeVolumeInUnits(_fixedVolLots * Symbol.LotSize);
                }
                else
                {
                    double effectiveRisk = GetEffectiveRiskFactor();
                    double riskAmount = Account.Equity * (effectiveRisk / 100.0);
                    double effectiveSlPips = slPips > 0 ? slPips : stoplossPip;
                    if (effectiveSlPips <= 0) effectiveSlPips = 200.0;
                    double lossPerUnit = effectiveSlPips * Symbol.PipValue;
                    if (lossPerUnit <= 0) lossPerUnit = Symbol.PipValue * 100.0;
                    double targetUnits = riskAmount / lossPerUnit;
                    volume = Symbol.NormalizeVolumeInUnits(targetUnits);
                    Print($"[AI Risk Sizing] Equity=${Account.Equity:F2} | Risk={effectiveRisk:F1}% (${riskAmount:F2}) | AI SL={effectiveSlPips:F0} pips | PipVal=${Symbol.PipValue:F4} | Loss/Unit=${lossPerUnit:F2} | TargetUnits={targetUnits:F2} => Order Vol: {volume / Symbol.LotSize:F2} Lots ({volume} units)");
                }

                double maxUnits = maxVol * Symbol.LotSize;
                if (volume > maxUnits) volume = maxUnits;
                if (volume < Symbol.VolumeInUnitsMin) volume = Symbol.VolumeInUnitsMin;
                if (volume > Symbol.VolumeInUnitsMax) volume = Symbol.VolumeInUnitsMax;

                _lastAgentReason = decision.reason;
                var result = ExecuteMarketOrder(tradeType, SymbolName, volume, label, slPips > 0 ? slPips : (double?)null, tpPips > 0 ? tpPips : (double?)null);
                if (result.IsSuccessful)
                {
                    Print($"[AI Agent SUCCESS] Market order {tradeType} {volume / Symbol.LotSize:F2} lots placed successfully @ {result.Position.EntryPrice}! SL: {slPips} pips, TP: {tpPips} pips.");
                    _ = SendTelegramAlertAsync($"ðŸš€ <b>[AI Agent] {action} Executed</b>\nSymbol: {SymbolName}\nVolume: {volume / Symbol.LotSize:F2} lots | SL: {slPips} pips | TP: {tpPips} pips\nConfidence: {decision.confidence:F1}%\nReason: {decision.reason}");
                }
                else
                {
                    Print($"[AI Agent Order FAILED] Error: {result.Error}");
                }
            }
            catch (Exception ex)
            {
                Print($"[AI Decision Execution Error] {ex.Message}");
            }
        }
        #endregion
    }
}

