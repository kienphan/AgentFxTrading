using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using cAlgo.API;
using cAlgo.API.Indicators;

namespace cAlgo.Robots
{
    public enum DstRule
    {
        None,
        Europe, // London: Lùi 1h từ Chủ Nhật cuối tháng 3 đến Chủ Nhật cuối tháng 10
        US      // New York: Lùi 1h từ Chủ Nhật thứ 2 tháng 3 đến Chủ Nhật đầu tháng 11
    }

    [Robot(AccessRights = AccessRights.FullAccess, TimeZone = TimeZones.UTC)]
    public class AiAgentBot : Robot
    {
        #region Parameters
        [Parameter("Bot ID", Group = "API", DefaultValue = "bot1")]
        public string BotId { get; set; }

        [Parameter("Agent API URL", Group = "API", DefaultValue = "http://127.0.0.1:8000/trade")]
        public string ApiUrl { get; set; }

        [Parameter("Account Label (optional)", Group = "API", DefaultValue = "")]
        public string AccountLabel { get; set; }

        // ---- TDI ----
        [Parameter("RSI Period", Group = "TDI", DefaultValue = 6, MinValue = 1)]
        public int RsiPeriod { get; set; }

        [Parameter("Red (Signal) Period", Group = "TDI", DefaultValue = 6, MinValue = 1)]
        public int RedPeriod { get; set; }

        // ---- Stochastic ----
        [Parameter("%K Period", Group = "Stochastic", DefaultValue = 6, MinValue = 1)]
        public int StochKPeriod { get; set; }

        [Parameter("%D Period", Group = "Stochastic", DefaultValue = 6, MinValue = 1)]
        public int StochDPeriod { get; set; }

        [Parameter("Slowing", Group = "Stochastic", DefaultValue = 4, MinValue = 1)]
        public int StochSlowing { get; set; }

        // ---- Entry Filters ----
        [Parameter("Max Bars After Cross", Group = "Entry", DefaultValue = 5, MinValue = 1)]
        public int MaxBarsAfterCross { get; set; }

        [Parameter("Min Angle Delta (0=off)", Group = "Entry", DefaultValue = 0.0, MinValue = 0, Step = 0.05)]
        public double MinAngleDelta { get; set; }

        [Parameter("Min Decisive Breakout (pips)", Group = "Entry", DefaultValue = 3.0, MinValue = 0, Step = 0.5)]
        public double MinDecisiveBreakoutPips { get; set; }

        // ---- Exit ----
        [Parameter("Flat Threshold", Group = "Exit", DefaultValue = 0.01, MinValue = 0.001)]
        public double FlatThreshold { get; set; }

        [Parameter("Checkmark Threshold", Group = "Exit", DefaultValue = 0.0, MinValue = 0)]
        public double CheckMarkThreshold { get; set; }

        [Parameter("Breakeven Trigger (pips)", Group = "Exit", DefaultValue = 5.0, MinValue = 0, Step = 0.5)]
        public double BreakevenTriggerPips { get; set; }

        [Parameter("Breakeven Offset (pips)", Group = "Exit", DefaultValue = 0.5, MinValue = 0, Step = 0.1)]
        public double BreakevenOffsetPips { get; set; }

        [Parameter("Trail Trigger (pips)", Group = "Exit", DefaultValue = 10.0, MinValue = 0, Step = 0.5)]
        public double TrailTriggerPips { get; set; }

        [Parameter("Trail Distance (pips)", Group = "Exit", DefaultValue = 5.0, MinValue = 0, Step = 0.5)]
        public double TrailDistancePips { get; set; }

        // ---- ORB ----
        [Parameter("ORB Start Hour (Winter UTC)", Group = "ORB", DefaultValue = 8)]
        public int OrbStartHour { get; set; }

        [Parameter("ORB Opening Range (minutes)", Group = "ORB", DefaultValue = 15, MinValue = 1)]
        public int OrbOpeningRangeMinutes { get; set; }

        [Parameter("Min OR Width (pips)", Group = "ORB", DefaultValue = 2.0, MinValue = 0, Step = 0.1)]
        public double MinOrWidthPips { get; set; }

        [Parameter("ORB Buffer (pips)", Group = "ORB", DefaultValue = 0.0, MinValue = 0)]
        public double OrbBufferPips { get; set; }

        [Parameter("Max Bars After Breakout", Group = "ORB", DefaultValue = 5, MinValue = 1)]
        public int MaxBarsAfterBreakout { get; set; }
        // ---- Session / EOD ----
        [Parameter("DST Rule (Auto-adjust UTC)", Group = "Session", DefaultValue = DstRule.Europe)]
        public DstRule SessionDstRule { get; set; }

        [Parameter("Session End Hour (Winter UTC, 0=off)", Group = "Session", DefaultValue = 17, MinValue = 0)]
        public int SessionEndHour { get; set; }

        [Parameter("Session End Minute", Group = "Session", DefaultValue = 0, MinValue = 0)]
        public int SessionEndMinute { get; set; }

        [Parameter("Session Name", Group = "Session", DefaultValue = "london")]
        public string SessionName { get; set; }

        // ---- Guardrails ----
        [Parameter("Min SL Distance (pips)", Group = "Guardrails", DefaultValue = 3.0, MinValue = 0, Step = 0.5)]
        public double MinSlPips { get; set; }

        [Parameter("Max SL Distance (pips)", Group = "Guardrails", DefaultValue = 30.0, MinValue = 0, Step = 0.5)]
        public double MaxSlPips { get; set; }

        [Parameter("Min TP Distance (pips)", Group = "Guardrails", DefaultValue = 3.0, MinValue = 0, Step = 0.5)]
        public double MinTpPips { get; set; }

        [Parameter("Max TP Distance (pips)", Group = "Guardrails", DefaultValue = 100.0, MinValue = 0, Step = 0.5)]
        public double MaxTpPips { get; set; }

        [Parameter("Max Loss Streak", Group = "Guardrails", DefaultValue = 3, MinValue = 0)]
        public int MaxLossStreak { get; set; }

        [Parameter("Loss Streak Threshold ($)", Group = "Guardrails", DefaultValue = -50.0, MinValue = -10000, Step = 5)]
        public double LossStreakThreshold { get; set; }

        [Parameter("Bias Flip Exit", Group = "Guardrails", DefaultValue = true)]
        public bool BiasFlipExit { get; set; }

        [Parameter("Max Giveback (pips, 0=off)", Group = "Guardrails", DefaultValue = 0.0, MinValue = 0, Step = 0.5)]
        public double MaxGivebackPips { get; set; }

        [Parameter("Show Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }
        #endregion

        #region Fields
        private HttpClient _httpClient;

        // ---- Data Models ----
        public class BarData
        {
            public string ha_color { get; set; }
            public double tdi_green { get; set; }
            public double tdi_red { get; set; }
            public double stoch_k { get; set; }
            public double stoch_d { get; set; }
        }

        public class TmsSignals
        {
            public string bias { get; set; }
            public int bars_since_cross { get; set; }
            public string cross_direction { get; set; }
            public bool cross_up { get; set; }
            public bool cross_down { get; set; }
            public bool ha_turned_green { get; set; }
            public bool ha_turned_red { get; set; }
            public bool stoch_bull { get; set; }
            public bool stoch_bear { get; set; }
            public bool angle_ok_long { get; set; }
            public bool angle_ok_short { get; set; }
            public bool within_window { get; set; }
            public bool long_entry { get; set; }
            public bool short_entry { get; set; }
            public bool exit_long { get; set; }
            public bool exit_short { get; set; }
            public string exit_reason { get; set; }
            public string tdi_level { get; set; }
            // TF Green State (current chart TF momentum)
            public double green_tf_value { get; set; }
            public double green_tf_slope { get; set; }  // positive = rising, negative = falling
        }

        public class OrbData
        {
            public double or_high { get; set; }
            public double or_low { get; set; }
            public double or_mid { get; set; }
            public double or_width { get; set; }
            public bool or_complete { get; set; }
            public string breakout_direction { get; set; }
            public double breakout_price { get; set; }
            public double breakout_distance_pips { get; set; }
            public int bars_since_breakout { get; set; }
            public bool in_entry_window { get; set; }
            public bool is_decisive { get; set; }
            public string price_position { get; set; }
        }

        public class PositionInfo
        {
            public string side { get; set; }
            public double entry_price { get; set; }
            public double unrealized_pnl { get; set; }
            public double unrealized_pnl_pips { get; set; }
            public double mfe_pips { get; set; }
            public double giveback_pips { get; set; }
            public double sl_price { get; set; }
            public double tp_price { get; set; }
            public int bars_held { get; set; }
        }

        public class SessionInfo
        {
            public string session_name { get; set; }
            public string phase { get; set; }  // "pre", "active", "ending", "closed"
            public int minutes_to_end { get; set; }
            public bool is_trading_time { get; set; }
        }

        public class MarketSnapshot
        {
            public string bot_id { get; set; }
            public string symbol { get; set; }
            public string timeframe { get; set; }
            public double ask { get; set; }
            public double bid { get; set; }
            public List<BarData> bars { get; set; }
            public TmsSignals tms { get; set; }
            public OrbData orb { get; set; }
            public PositionInfo position { get; set; }
            public SessionInfo session { get; set; }
            public int loss_streak { get; set; }
            public double day_pnl { get; set; }
            public int trades_today { get; set; }
            
            public string account_number { get; set; }
            public string account_type { get; set; }
            public string account_label { get; set; }
            public double account_balance { get; set; }
            public double account_equity { get; set; }
        }

        public class AgentDecision
        {
            public string action { get; set; }  // BUY, SELL, CLOSE_ALL, HOLD, ADJUST
            public double volume_lots { get; set; }
            public double sl_pips { get; set; }
            public double tp_pips { get; set; }
            public double new_sl_pips { get; set; }  // for ADJUST action
            public string reason { get; set; }
        }

        // ---- Indicator Storage ----
        private IndicatorDataSeries _haOpen, _haHigh, _haLow, _haClose;
        private IndicatorDataSeries _rsiSeries, _redSeries;
        private IndicatorDataSeries _rawK, _kSeries, _dSeries;
        private double _avgGain, _avgLoss, _gainSum, _lossSum;
        private int _lastProcessedIndex = -1;

        // ---- TMS Signal Tracking ----
        private int _lastCrossBar = -1;
        private int _lastCrossDir = 0;

        // ---- ORB State ----
        private double _orHigh = double.MinValue;
        private double _orLow = double.MaxValue;
        private bool _orComplete;
        private string _breakoutDir;
        private double _breakoutPrice;
        private int _breakoutBar = -1;
        private string _lastOrbDate;

        // ---- Loss Streak Tracking ----
        private int _lossStreak = 0;
        private int _lastClosedTradeDay = -1;
        private double _dayPnl = 0;
        private int _tradesToday = 0;

        // ---- Position Memory (MFE tracking) ----
        private Dictionary<int, double> _positionMfe = new Dictionary<int, double>();
        private Dictionary<int, int> _positionEntryBar = new Dictionary<int, int>();

        // ---- Exit Management ----
        private HashSet<int> _breakevenApplied = new HashSet<int>();
        #endregion

        private object GetAccountPayload()
        {
            return new
            {
                account_number = Account.Number.ToString(),
                account_type = Account.IsLive ? "live" : "demo",
                account_label = string.IsNullOrWhiteSpace(AccountLabel) ? null : AccountLabel.Trim(),
                account_balance = Account.Balance,
                account_equity = Account.Equity
            };
        }

        protected override void OnStart()
        {
            _httpClient = new HttpClient();

            _haOpen = CreateDataSeries();
            _haHigh = CreateDataSeries();
            _haLow = CreateDataSeries();
            _haClose = CreateDataSeries();

            _rsiSeries = CreateDataSeries();
            _redSeries = CreateDataSeries();
            _rawK = CreateDataSeries();
            _kSeries = CreateDataSeries();
            _dSeries = CreateDataSeries();

            if (ShowLogs) Print($"AiAgentBot started | TF={TimeFrame.Name} | Session={SessionName}");
        }

        protected override void OnTick()
        {
            // Update MFE for all open positions every tick
            UpdatePositionMemory();

            // Auto exit management (breakeven + trailing)
            ManageExits();

            // Max giveback guard
            CheckMaxGiveback();

            // Session end close
            CheckSessionEnd();
        }

        protected override void OnBarClosed()
        {
            int index = Bars.Count - 1;
            if (index <= _lastProcessedIndex) return;

            for (int i = _lastProcessedIndex + 1; i <= index; i++)
            {
                UpdateHeikinAshi(i);
                UpdateTdi(i);
                UpdateStoch(i);
            }
            _lastProcessedIndex = index;

            if (index < 2) return;

            UpdateOrb(index);
            UpdateLossStreak();

            var snapshot = new MarketSnapshot
            {
                bot_id = BotId,
                symbol = SymbolName,
                timeframe = TimeFrame.Name,
                ask = Symbol.Ask,
                bid = Symbol.Bid,
                loss_streak = _lossStreak,
                day_pnl = Math.Round(_dayPnl, 2),
                trades_today = _tradesToday,
                account_number = Account.Number.ToString(),
                account_type = Account.IsLive ? "live" : "demo",
                account_label = string.IsNullOrWhiteSpace(AccountLabel) ? null : AccountLabel.Trim(),
                account_balance = Account.Balance,
                account_equity = Account.Equity,
                bars = new List<BarData>
                {
                    GetBarData(index),
                    GetBarData(index - 1),
                    GetBarData(index - 2)
                },
                tms = GetTmsSignals(index),
                orb = GetOrbData(index),
                position = GetPositionInfo(index),
                session = GetSessionInfo()
            };

            string jsonPayload = JsonSerializer.Serialize(snapshot);
            _ = AskAgentAsync(jsonPayload);
        }

        private BarData GetBarData(int idx)
        {
            bool isGreen = _haClose[idx] > _haOpen[idx];
            return new BarData
            {
                ha_color = isGreen ? "Green" : "Red",
                tdi_green = double.IsNaN(_rsiSeries[idx]) ? 50 : Math.Round(_rsiSeries[idx], 2),
                tdi_red = double.IsNaN(_redSeries[idx]) ? 50 : Math.Round(_redSeries[idx], 2),
                stoch_k = double.IsNaN(_kSeries[idx]) ? 50 : Math.Round(_kSeries[idx], 2),
                stoch_d = double.IsNaN(_dSeries[idx]) ? 50 : Math.Round(_dSeries[idx], 2)
            };
        }

        // ==========================================
        // TMS SIGNAL COMPUTATION
        // ==========================================

        private TmsSignals GetTmsSignals(int i)
        {
            double g = _rsiSeries[i];
            double g1 = _rsiSeries[i - 1];
            double g2 = i >= 2 ? _rsiSeries[i - 2] : g1;
            double r = _redSeries[i];
            double r1 = _redSeries[i - 1];

            double k = _kSeries[i];
            double d = _dSeries[i];

            if (double.IsNaN(g) || double.IsNaN(r))
            {
                return new TmsSignals { bias = "NEUTRAL", tdi_level = "neutral", green_tf_value = 50, green_tf_slope = 0 };
            }

            // HA color changes
            bool haGreen = _haClose[i] > _haOpen[i];
            bool haGreenP1 = _haClose[i - 1] > _haOpen[i - 1];
            bool haGreenP2 = i >= 2 ? (_haClose[i - 2] > _haOpen[i - 2]) : haGreenP1;
            bool haTurnedGreen = (haGreen && !haGreenP1) || (haGreenP1 && !haGreenP2);
            bool haTurnedRed = (!haGreen && haGreenP1) || (!haGreenP1 && haGreenP2);

            // TDI cross detection
            bool crossUp = g1 <= r1 && g > r;
            bool crossDn = g1 >= r1 && g < r;

            if (crossUp) { _lastCrossBar = i; _lastCrossDir = 1; }
            else if (crossDn) { _lastCrossBar = i; _lastCrossDir = -1; }

            int barsSinceCross = _lastCrossBar >= 0 ? i - _lastCrossBar : 999;

            bool stochBull = k > d;
            bool stochBear = k < d;

            bool angleOkLong = IsGoodAngle(g, g1, g2, isLong: true);
            bool angleOkShort = IsGoodAngle(g, g1, g2, isLong: false);

            bool withinWindow = barsSinceCross >= 1 && barsSinceCross <= MaxBarsAfterCross;

            bool longEntry = _lastCrossDir == 1 && withinWindow && haTurnedGreen && stochBull && angleOkLong;
            bool shortEntry = _lastCrossDir == -1 && withinWindow && haTurnedRed && stochBear && angleOkShort;

            bool exitLong, exitShort;
            string exitReason;
            CheckExit(g, g1, g2, out exitLong, out exitShort, out exitReason);

            string tdiLevel = "neutral";
            if (g < 32) tdiLevel = "oversold";
            else if (g > 68) tdiLevel = "overbought";

            string bias = _lastCrossDir == 1 ? "BULLISH" : _lastCrossDir == -1 ? "BEARISH" : "NEUTRAL";
            string crossDir = _lastCrossDir == 1 ? "up" : _lastCrossDir == -1 ? "down" : null;

            // TF Green State: current value + slope (momentum direction)
            double greenTfValue = Math.Round(g, 2);
            double greenTfSlope = Math.Round(g - g1, 3);

            return new TmsSignals
            {
                bias = bias,
                bars_since_cross = barsSinceCross,
                cross_direction = crossDir,
                cross_up = crossUp,
                cross_down = crossDn,
                ha_turned_green = haTurnedGreen,
                ha_turned_red = haTurnedRed,
                stoch_bull = stochBull,
                stoch_bear = stochBear,
                angle_ok_long = angleOkLong,
                angle_ok_short = angleOkShort,
                within_window = withinWindow,
                long_entry = longEntry,
                short_entry = shortEntry,
                exit_long = exitLong,
                exit_short = exitShort,
                exit_reason = exitReason,
                tdi_level = tdiLevel,
                green_tf_value = greenTfValue,
                green_tf_slope = greenTfSlope
            };
        }

        // ==========================================
        // ORB LOGIC
        // ==========================================

        private void UpdateOrb(int index)
        {
            var barTime = Bars[index].OpenTime; // Note: cAlgo Bars.OpenTime is usually in the broker's time zone or UTC depending on platform settings. Assuming UTC.
            string currentDate = barTime.ToString("yyyy-MM-dd");

            if (_lastOrbDate != currentDate)
            {
                _orHigh = double.MinValue;
                _orLow = double.MaxValue;
                _orComplete = false;
                _breakoutDir = null;
                _breakoutPrice = 0;
                _breakoutBar = -1;
                _lastOrbDate = currentDate;
            }

            // Tự động điều chỉnh giờ mở cửa theo DST (Mùa hè/Mùa đông)
            int adjustedStartHour = GetAdjustedHour(barTime, OrbStartHour, SessionDstRule);

            int barHour = barTime.Hour;
            int barMinute = barTime.Minute;
            int orEndMinute = adjustedStartHour * 60 + OrbOpeningRangeMinutes;
            int orEndHour = orEndMinute / 60;
            orEndMinute = orEndMinute % 60;

            int barTotalMinutes = barHour * 60 + barMinute;
            int orStartMinutes = adjustedStartHour * 60;

            if (barTotalMinutes >= orStartMinutes && barTotalMinutes < (adjustedStartHour * 60 + OrbOpeningRangeMinutes))
            {
                if (Bars[index].High > _orHigh) _orHigh = Bars[index].High;
                if (Bars[index].Low < _orLow) _orLow = Bars[index].Low;
            }

            // Check if OR window has passed
            if (barTotalMinutes >= (adjustedStartHour * 60 + OrbOpeningRangeMinutes) && !_orComplete && _orHigh > double.MinValue)
            {
                double orWidthPips = (_orHigh - _orLow) / Symbol.PipSize;
                if (orWidthPips >= MinOrWidthPips)
                    _orComplete = true;
            }

            if (_orComplete && _breakoutDir == null && _orHigh > double.MinValue)
            {
                double buffer = OrbBufferPips * Symbol.PipSize;
                double currentClose = Bars[index].Close;

                if (currentClose > _orHigh + buffer)
                {
                    _breakoutDir = "up";
                    _breakoutPrice = currentClose;
                    _breakoutBar = index;
                }
                else if (currentClose < _orLow - buffer)
                {
                    _breakoutDir = "down";
                    _breakoutPrice = currentClose;
                    _breakoutBar = index;
                }
            }
        }
        private bool IsGoodAngle(double g, double g1, double g2, bool isLong)
        {
            if (MinAngleDelta <= 0) return true;
            if (isLong) return (g - g2) >= MinAngleDelta;
            else return (g2 - g) >= MinAngleDelta;
        }

        private void CheckExit(double g, double g1, double g2, out bool exitLong, out bool exitShort, out string reason)
        {
            bool flat = Math.Abs(g - g1) < FlatThreshold;
            bool hookUp = g1 < g2 && g > g1;
            bool hookDn = g1 > g2 && g < g1;
            bool checkUp = hookUp && (g - g2) >= CheckMarkThreshold;
            bool checkDn = hookDn && (g2 - g) >= CheckMarkThreshold;

            exitLong = flat || hookDn || checkDn;
            exitShort = flat || hookUp || checkUp;

            reason = "";
            if (flat) reason = "flat";
            else if (checkDn || checkUp) reason = "checkmark";
            else if (hookDn || hookUp) reason = "hook";
        }

        // ==========================================
        // ORB LOGIC
        // ==========================================

        private void UpdateOrb(int index)
        {
            var barTime = Bars[index].OpenTime;
            string currentDate = barTime.ToString("yyyy-MM-dd");

            if (_lastOrbDate != currentDate)
            {
                _orHigh = double.MinValue;
                _orLow = double.MaxValue;
                _orComplete = false;
                _breakoutDir = null;
                _breakoutPrice = 0;
                _breakoutBar = -1;
                _lastOrbDate = currentDate;
            }

            int barTotalMinutes = barTime.Hour * 60 + barTime.Minute;
            int orStartMinutes = OrbStartHour * 60;
            int orEndMinutes = OrbStartHour * 60 + OrbOpeningRangeMinutes;

            if (barTotalMinutes >= orStartMinutes && barTotalMinutes < orEndMinutes)
            {
                if (Bars[index].High > _orHigh) _orHigh = Bars[index].High;
                if (Bars[index].Low < _orLow) _orLow = Bars[index].Low;
            }

            if (barTotalMinutes >= orEndMinutes && !_orComplete && _orHigh > double.MinValue)
            {
                double orWidthPips = (_orHigh - _orLow) / Symbol.PipSize;
                if (orWidthPips >= MinOrWidthPips)
                    _orComplete = true;
            }

            if (_orComplete && _breakoutDir == null && _orHigh > double.MinValue)
            {
                double buffer = OrbBufferPips * Symbol.PipSize;
                double currentClose = Bars[index].Close;

                if (currentClose > _orHigh + buffer)
                {
                    _breakoutDir = "up";
                    _breakoutPrice = currentClose;
                    _breakoutBar = index;
                }
                else if (currentClose < _orLow - buffer)
                {
                    _breakoutDir = "down";
                    _breakoutPrice = currentClose;
                    _breakoutBar = index;
                }
            }
        }

        private OrbData GetOrbData(int index)
        {
            int barsSince = _breakoutBar >= 0 ? index - _breakoutBar : 0;
            double currentPrice = (Symbol.Ask + Symbol.Bid) / 2.0;
            double buffer = OrbBufferPips * Symbol.PipSize;

            // Breakout distance in pips
            double breakoutDistPips = 0;
            if (_breakoutDir == "up" && _orHigh > double.MinValue)
                breakoutDistPips = (currentPrice - _orHigh) / Symbol.PipSize;
            else if (_breakoutDir == "down" && _orLow < double.MaxValue)
                breakoutDistPips = (_orLow - currentPrice) / Symbol.PipSize;

            bool isDecisive = breakoutDistPips >= MinDecisiveBreakoutPips;

            string pricePos = "inside";
            if (_orHigh > double.MinValue)
            {
                if (currentPrice > _orHigh + buffer) pricePos = "above";
                else if (currentPrice < _orLow - buffer) pricePos = "below";
            }

            return new OrbData
            {
                or_high = _orHigh > double.MinValue ? Math.Round(_orHigh, Symbol.Digits) : 0,
                or_low = _orLow < double.MaxValue ? Math.Round(_orLow, Symbol.Digits) : 0,
                or_mid = (_orHigh > double.MinValue && _orLow < double.MaxValue) ? Math.Round((_orHigh + _orLow) / 2, Symbol.Digits) : 0,
                or_width = (_orHigh > double.MinValue && _orLow < double.MaxValue) ? Math.Round(_orHigh - _orLow, Symbol.Digits) : 0,
                or_complete = _orComplete,
                breakout_direction = _breakoutDir,
                breakout_price = _breakoutPrice > 0 ? Math.Round(_breakoutPrice, Symbol.Digits) : 0,
                breakout_distance_pips = Math.Round(breakoutDistPips, 1),
                bars_since_breakout = barsSince,
                in_entry_window = barsSince >= 0 && barsSince <= MaxBarsAfterBreakout,
                is_decisive = isDecisive,
                price_position = pricePos
            };
        }

        // ==========================================
        // POSITION MEMORY (MFE / Giveback)
        // ==========================================

        private void UpdatePositionMemory()
        {
            // Clean up closed positions
            var closedIds = new List<int>();
            foreach (var kvp in _positionMfe)
            {
                bool found = false;
                foreach (var pos in Positions)
                {
                    if (pos.Id == kvp.Key) { found = true; break; }
                }
                if (!found) closedIds.Add(kvp.Key);
            }
            foreach (var id in closedIds)
            {
                _positionMfe.Remove(id);
                _positionEntryBar.Remove(id);
                _breakevenApplied.Remove(id);
            }

            // Update MFE for open positions
            foreach (var pos in Positions)
            {
                double pnlPips = GetPnlPips(pos);
                if (!_positionMfe.ContainsKey(pos.Id))
                {
                    _positionMfe[pos.Id] = pnlPips;
                    _positionEntryBar[pos.Id] = Bars.Count - 1;
                }
                else if (pnlPips > _positionMfe[pos.Id])
                {
                    _positionMfe[pos.Id] = pnlPips;
                }
            }
        }

        private double GetPnlPips(Position pos)
        {
            if (pos.TradeType == TradeType.Buy)
                return (Symbol.Bid - pos.EntryPrice) / Symbol.PipSize;
            else
                return (pos.EntryPrice - Symbol.Ask) / Symbol.PipSize;
        }

        private PositionInfo GetPositionInfo(int index)
        {
            if (Positions.Count == 0) return null;

            var pos = Positions[0];
            double pnlPips = GetPnlPips(pos);
            double mfe = _positionMfe.ContainsKey(pos.Id) ? _positionMfe[pos.Id] : 0;
            double giveback = Math.Max(0, mfe - pnlPips);
            int barsHeld = _positionEntryBar.ContainsKey(pos.Id) ? index - _positionEntryBar[pos.Id] : 0;

            return new PositionInfo
            {
                side = pos.TradeType == TradeType.Buy ? "BUY" : "SELL",
                entry_price = pos.EntryPrice,
                unrealized_pnl = Math.Round(pos.NetProfit, 2),
                unrealized_pnl_pips = Math.Round(pnlPips, 1),
                mfe_pips = Math.Round(mfe, 1),
                giveback_pips = Math.Round(giveback, 1),
                sl_price = pos.StopLoss ?? 0,
                tp_price = pos.TakeProfit ?? 0,
                bars_held = barsHeld
            };
        }

        // ==========================================
        // EXIT MANAGEMENT (Breakeven + Trailing)
        // ==========================================

        private void ManageExits()
        {
            foreach (var pos in Positions)
            {
                double pnlPips = GetPnlPips(pos);

                // Breakeven: move SL to entry + offset when profit >= trigger
                if (BreakevenTriggerPips > 0 && pnlPips >= BreakevenTriggerPips && !_breakevenApplied.Contains(pos.Id))
                {
                    double beSl = pos.EntryPrice + (pos.TradeType == TradeType.Buy ? 1 : -1) * BreakevenOffsetPips * Symbol.PipSize;

                    bool shouldMove = pos.TradeType == TradeType.Buy
                        ? (pos.StopLoss == null || beSl > pos.StopLoss.Value)
                        : (pos.StopLoss == null || beSl < pos.StopLoss.Value);

                    if (shouldMove)
                    {
                        pos.ModifyStopLossPrice(beSl);
                        _breakevenApplied.Add(pos.Id);
                        if (ShowLogs) Print($"[BE] Pos#{pos.Id} SL → {beSl:F5} (pnl={pnlPips:F1}p)");
                    }
                }

                // Trailing: trail SL when profit >= trail trigger
                if (TrailTriggerPips > 0 && pnlPips >= TrailTriggerPips && pos.StopLoss != null)
                {
                    double trailSl;
                    if (pos.TradeType == TradeType.Buy)
                    {
                        trailSl = Symbol.Bid - TrailDistancePips * Symbol.PipSize;
                        if (trailSl > pos.StopLoss.Value && trailSl < Symbol.Bid)
                            pos.ModifyStopLossPrice(trailSl);
                    }
                    else
                    {
                        trailSl = Symbol.Ask + TrailDistancePips * Symbol.PipSize;
                        if (trailSl < pos.StopLoss.Value && trailSl > Symbol.Ask)
                            pos.ModifyStopLossPrice(trailSl);
                    }
                }
            }
        }

        private void CheckMaxGiveback()
        {
            if (MaxGivebackPips <= 0) return;

            foreach (var pos in Positions)
            {
                if (!_positionMfe.ContainsKey(pos.Id)) continue;
                double mfe = _positionMfe[pos.Id];
                if (mfe <= 0) continue;

                double pnlPips = GetPnlPips(pos);
                double giveback = mfe - pnlPips;

                if (giveback >= MaxGivebackPips)
                {
                    pos.Close();
                    if (ShowLogs) Print($"[Giveback] Pos#{pos.Id} closed: gave back {giveback:F1}pips (MFE={mfe:F1}p, now={pnlPips:F1}p)");
                }
            }
        }

        private SessionInfo GetSessionInfo()
        {
            var now = Server.TimeInUtc;  // Use explicit UTC
            int nowMinutes = now.Hour * 60 + now.Minute;

            int adjustedStartHour = GetAdjustedHour(now, OrbStartHour, SessionDstRule);
            int adjustedEndHour = GetAdjustedHour(now, SessionEndHour, SessionDstRule);

            int sessionStart = adjustedStartHour * 60;
            int sessionEnd = adjustedEndHour * 60 + SessionEndMinute;

            // If session end is 0, use a default 9-hour session from start
            if (SessionEndHour == 0) sessionEnd = sessionStart + 540;

            // Handle overnight session crossing midnight
            bool isOvernight = sessionStart > sessionEnd;
            bool isActive;
            
            if (isOvernight)
                isActive = nowMinutes >= sessionStart || nowMinutes < sessionEnd;
            else
                isActive = nowMinutes >= sessionStart && nowMinutes < sessionEnd;

            int minutesToEnd = isActive ? (isOvernight && nowMinutes >= sessionStart ? (1440 - nowMinutes + sessionEnd) : sessionEnd - nowMinutes) : 0;
            bool isEnding = isActive && minutesToEnd <= 15;

            string phase;
            if (!isActive && (isOvernight ? (nowMinutes >= sessionEnd && nowMinutes < sessionStart) : nowMinutes < sessionStart)) phase = "pre";
            else if (isActive && !isEnding) phase = "active";
            else if (isEnding) phase = "ending";
            else phase = "closed";

            return new SessionInfo
            {
                session_name = SessionName,
                phase = phase,
                minutes_to_end = Math.Max(0, minutesToEnd),
                is_trading_time = isActive
            };
        }

        private void CheckSessionEnd()
        {
            if (SessionEndHour == 0) return;
            if (Positions.Count == 0) return;

            var now = Server.TimeInUtc;
            int nowMinutes = now.Hour * 60 + now.Minute;
            int adjustedEndHour = GetAdjustedHour(now, SessionEndHour, SessionDstRule);
            int sessionEnd = adjustedEndHour * 60 + SessionEndMinute;

            // Need a tolerance or exact check. If it's exactly session end or within 1 min after
            if (nowMinutes == sessionEnd)
            {
                foreach (var pos in Positions)
                {
                    pos.Close();
                    if (ShowLogs) Print($"[EOD] Pos#{pos.Id} closed at session end");
                }
            }
        }

        // ==========================================
        // LOSS STREAK TRACKING
        // ==========================================

        private void UpdateLossStreak()
        {
            int today = Server.Time.DayOfYear;
            if (_lastClosedTradeDay != today && _lastClosedTradeDay != -1)
            {
                _lossStreak = 0;
                _dayPnl = 0;
                _tradesToday = 0;
            }
            _lastClosedTradeDay = today;
        }

        protected override void OnPositionClosed(PositionClosedEventArgs args)
        {
            double pnl = args.Position.NetProfit;
            _dayPnl += pnl;
            _tradesToday++;

            if (pnl < LossStreakThreshold)
            {
                _lossStreak++;
                if (ShowLogs) Print($"[Loss] Streak: {_lossStreak} | PnL: {pnl:F2}");
            }
            else
            {
                _lossStreak = 0;
            }

            // Report to portfolio manager
            _ = ReportPositionClosed(args.Position, pnl);
        }

        // ==========================================
        // HTTP & EXECUTION
        // ==========================================

        private async Task AskAgentAsync(string jsonPayload)
        {
            try
            {
                var content = new StringContent(jsonPayload, Encoding.UTF8, "application/json");
                var response = await _httpClient.PostAsync(ApiUrl, content);

                if (!response.IsSuccessStatusCode)
                {
                    Print($"[HTTP Error] {response.StatusCode}");
                    return;
                }

                var resultJson = await response.Content.ReadAsStringAsync();
                var decision = JsonSerializer.Deserialize<AgentDecision>(resultJson);

                BeginInvokeOnMainThread(() => ExecuteDecision(decision));
            }
            catch (Exception ex)
            {
                Print($"[Error] {ex.Message}");
            }
        }

        private async Task ReportPositionOpen(Position position, double slPips, double tpPips)
        {
            try
            {
                var reportUrl = ApiUrl.Replace("/trade", "/portfolio/report");
                var report = new
                {
                    bot_id = BotId,
                    action = "open",
                    symbol = SymbolName,
                    side = position.TradeType.ToString(),
                    volume = position.VolumeInUnits / Symbol.LotSize,
                    entry_price = position.EntryPrice,
                    sl_pips = slPips,
                    tp_pips = tpPips,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = string.IsNullOrWhiteSpace(AccountLabel) ? null : AccountLabel.Trim(),
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                var json = JsonSerializer.Serialize(report);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                await _httpClient.PostAsync(reportUrl, content);
                
                if (ShowLogs) Print($"[Portfolio] Reported position open: {position.TradeType} {SymbolName}");
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Portfolio] Failed to report position open: {ex.Message}");
            }
        }

        private async Task ReportPositionClosed(Position position, double pnl)
        {
            try
            {
                var reportUrl = ApiUrl.Replace("/trade", "/portfolio/report");
                var report = new
                {
                    bot_id = BotId,
                    action = "close",
                    symbol = SymbolName,
                    exit_price = position.EntryPrice, // Will be updated with actual exit price
                    pnl = pnl,
                    account_number = Account.Number.ToString(),
                    account_type = Account.IsLive ? "live" : "demo",
                    account_label = string.IsNullOrWhiteSpace(AccountLabel) ? null : AccountLabel.Trim(),
                    account_balance = Account.Balance,
                    account_equity = Account.Equity
                };

                var json = JsonSerializer.Serialize(report);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                await _httpClient.PostAsync(reportUrl, content);
                
                if (ShowLogs) Print($"[Portfolio] Reported position closed: {position.TradeType} {SymbolName}, PnL: {pnl:F2}");
            }
            catch (Exception ex)
            {
                if (ShowLogs) Print($"[Portfolio] Failed to report position closed: {ex.Message}");
            }
        }

        private void ExecuteDecision(AgentDecision decision)
        {
            if (ShowLogs)
                Print($"Agent: {decision.action} | {decision.reason}");

            // Guardrail: block entry if loss streak exceeded
            if (_lossStreak >= MaxLossStreak && MaxLossStreak > 0 &&
                (decision.action == "BUY" || decision.action == "SELL"))
            {
                if (ShowLogs) Print($"[Guardrail] Blocked: loss streak={_lossStreak}");
                return;
            }

            // Guardrail: block entry if not in trading session
            var session = GetSessionInfo();
            if (!session.is_trading_time && (decision.action == "BUY" || decision.action == "SELL"))
            {
                if (ShowLogs) Print($"[Guardrail] Blocked: session={session.phase}");
                return;
            }

            // Guardrail: bias alignment check
            if (BiasFlipExit && Positions.Count > 0)
            {
                var pos = Positions[0];
                string bias = GetTmsSignals(Bars.Count - 1).bias;
                bool againstBias = (pos.TradeType == TradeType.Buy && bias == "BEARISH") ||
                                   (pos.TradeType == TradeType.Sell && bias == "BULLISH");
                if (againstBias && decision.action == "HOLD")
                {
                    pos.Close();
                    if (ShowLogs) Print($"[BiasFlip] Pos#{pos.Id} closed: bias flipped to {bias}");
                    return;
                }
            }

            // CLOSE_ALL
            if (decision.action == "CLOSE_ALL")
            {
                foreach (var pos in Positions) pos.Close();
                return;
            }

            if (decision.action == "HOLD" || decision.action == "NONE") return;

            // Entry
            if (Positions.Count > 0) return;
            if (decision.action != "BUY" && decision.action != "SELL") return;

            // Apply guardrails to SL/TP
            double slPips = Math.Max(MinSlPips, Math.Min(MaxSlPips, decision.sl_pips));
            double tpPips = Math.Max(MinTpPips, Math.Min(MaxTpPips, decision.tp_pips));

            double volume = Symbol.NormalizeVolumeInUnits(decision.volume_lots * Symbol.LotSize);
            if (volume < Symbol.VolumeInUnitsMin) volume = Symbol.VolumeInUnitsMin;

            var tradeType = decision.action == "BUY" ? TradeType.Buy : TradeType.Sell;

            // Guardrail: don't enter against ORB breakout direction
            if (_breakoutDir != null)
            {
                if (tradeType == TradeType.Buy && _breakoutDir == "down")
                {
                    if (ShowLogs) Print("[Guardrail] Blocked: BUY against ORB down breakout");
                    return;
                }
                if (tradeType == TradeType.Sell && _breakoutDir == "up")
                {
                    if (ShowLogs) Print("[Guardrail] Blocked: SELL against ORB up breakout");
                    return;
                }
            }

            var result = ExecuteMarketOrder(tradeType, SymbolName, volume, "AI_Agent", slPips, tpPips);
            if (result != null && result.IsSuccessful)
            {
                if (ShowLogs)
                    Print($"[Entry] {tradeType} {volume} units | SL={slPips}p TP={tpPips}p");
                
                // Report position to portfolio manager
                _ = ReportPositionOpen(result.Position, slPips, tpPips);
            }
        }

        // ==========================================
        // INDICATOR CALCULATIONS
        // ==========================================

        private void UpdateHeikinAshi(int i)
        {
            if (i == 0)
                _haOpen[i] = (Bars.OpenPrices[0] + Bars.ClosePrices[0]) / 2;
            else
                _haOpen[i] = (_haOpen[i - 1] + _haClose[i - 1]) / 2;

            _haClose[i] = (Bars.OpenPrices[i] + Bars.HighPrices[i] + Bars.LowPrices[i] + Bars.ClosePrices[i]) / 4;
            _haHigh[i] = Math.Max(Bars.HighPrices[i], Math.Max(_haOpen[i], _haClose[i]));
            _haLow[i] = Math.Min(Bars.LowPrices[i], Math.Min(_haOpen[i], _haClose[i]));
        }

        private void UpdateTdi(int index)
        {
            double close = _haClose[index];
            if (index == 0)
            {
                _gainSum = _lossSum = _avgGain = _avgLoss = 0;
                _rsiSeries[index] = double.NaN;
                _redSeries[index] = double.NaN;
                return;
            }

            double delta = close - _haClose[index - 1];
            double gain = Math.Max(delta, 0);
            double loss = Math.Max(-delta, 0);

            if (index <= RsiPeriod)
            {
                _gainSum += gain;
                _lossSum += loss;
                if (index < RsiPeriod)
                    _rsiSeries[index] = double.NaN;
                else
                {
                    _avgGain = _gainSum / RsiPeriod;
                    _avgLoss = _lossSum / RsiPeriod;
                    _rsiSeries[index] = RsiFromAvg();
                }
            }
            else
            {
                _avgGain = (_avgGain * (RsiPeriod - 1) + gain) / RsiPeriod;
                _avgLoss = (_avgLoss * (RsiPeriod - 1) + loss) / RsiPeriod;
                _rsiSeries[index] = RsiFromAvg();
            }

            int warmup = RsiPeriod + RedPeriod - 1;
            if (!double.IsNaN(_rsiSeries[index]) && index >= warmup)
            {
                double sum = 0;
                for (int j = index - RedPeriod + 1; j <= index; j++)
                    sum += _rsiSeries[j];
                _redSeries[index] = sum / RedPeriod;
            }
            else
            {
                _redSeries[index] = double.NaN;
            }
        }

        private double RsiFromAvg()
        {
            if (_avgLoss == 0) return 100;
            return 100 - 100 / (1 + _avgGain / _avgLoss);
        }

        private void UpdateStoch(int index)
        {
            double lowest = double.MaxValue, highest = double.MinValue;
            for (int i = index - StochKPeriod + 1; i <= index; i++)
            {
                if (i < 0) continue;
                if (_haLow[i] < lowest) lowest = _haLow[i];
                if (_haHigh[i] > highest) highest = _haHigh[i];
            }

            _rawK[index] = highest > lowest ? 100 * (_haClose[index] - lowest) / (highest - lowest) : 50;

            if (index >= StochSlowing - 1)
            {
                double sum = 0;
                for (int i = index - StochSlowing + 1; i <= index; i++) sum += _rawK[i];
                _kSeries[index] = sum / StochSlowing;
            }
            else _kSeries[index] = double.NaN;

            int dWarmup = StochSlowing + StochDPeriod - 2;
            if (index >= dWarmup)
            {
                double sum = 0;
                for (int i = index - StochDPeriod + 1; i <= index; i++) sum += _kSeries[i];
                _dSeries[index] = sum / StochDPeriod;
            }
            else _dSeries[index] = double.NaN;
        }

        // ==========================================
        // DST (Daylight Saving Time) HELPERS
        // ==========================================

        private int GetAdjustedHour(DateTime timeUtc, int baseHour, DstRule rule)
        {
            if (rule == DstRule.None || baseHour == 0) return baseHour;

            bool isDst = false;
            int y = timeUtc.Year;

            if (rule == DstRule.US)
            {
                // US: Từ Chủ nhật thứ 2 của tháng 3 đến Chủ nhật đầu tiên của tháng 11
                DateTime start = GetNthSunday(y, 3, 2).AddHours(7); // 2:00 AM EST = 7:00 AM UTC
                DateTime end = GetNthSunday(y, 11, 1).AddHours(6);  // 2:00 AM EDT = 6:00 AM UTC
                isDst = timeUtc >= start && timeUtc < end;
            }
            else if (rule == DstRule.Europe)
            {
                // Europe: Từ Chủ nhật cuối cùng của tháng 3 đến Chủ nhật cuối cùng của tháng 10
                DateTime start = GetLastSunday(y, 3).AddHours(1); // 1:00 AM UTC
                DateTime end = GetLastSunday(y, 10).AddHours(1);  // 1:00 AM UTC
                isDst = timeUtc >= start && timeUtc < end;
            }

            // Vào mùa hè (DST), giờ địa phương tiến lên 1 tiếng.
            // Để giữ nguyên giờ mở cửa địa phương (vd 8:00 AM London), giờ UTC phải LÙI lại 1 tiếng (thành 7:00 AM UTC).
            int adjustedHour = isDst ? baseHour - 1 : baseHour;
            
            // Handle negative hour (cross midnight backwards)
            if (adjustedHour < 0) adjustedHour += 24;
            
            return adjustedHour;
        }

        private DateTime GetNthSunday(int year, int month, int n)
        {
            DateTime firstDay = new DateTime(year, month, 1);
            int offset = (7 - (int)firstDay.DayOfWeek) % 7;
            return firstDay.AddDays(offset + (n - 1) * 7);
        }

        private DateTime GetLastSunday(int year, int month)
        {
            DateTime lastDay = new DateTime(year, month, DateTime.DaysInMonth(year, month));
            int offset = (int)lastDay.DayOfWeek;
            return lastDay.AddDays(-offset);
        }
    }
}
