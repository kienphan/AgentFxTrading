// OrderFlowBot: the order-flow setups from "Order Flow" (Volume Clusters, Multiple Nodes,
// Stacked Imbalances, Volume Profile HVNs) traded on the first test after a breakout.
//
// What the book asks for, and what a cBot can actually see
// ---------------------------------------------------------
// cAlgo gives a cBot quote ticks with Bid, Ask and Time only: no trade size and no traded
// volume. For FX/CFD the platform's own footprint is built from the same quotes, so this bot
// uses one tick as one unit of volume and classifies it with the footprint quote rule:
//
//   ask rises -> buyer lifted the offer: +1 ask volume at the ask price
//   bid falls -> seller hit the bid:      +1 bid volume at the bid price
//   both      -> spread widened:          one print on each side
//   neither   -> quote correction:        no volume
//
// That makes Setups #1 (volume clusters), #2 (multiple nodes) and #4 (stacked imbalances)
// implementable. Setup #3 (Trades Filter, ">= 25 lots") is not: a Tick carries no trade size.
// "Big Limit Orders" would need historical Level II, which cAlgo does not expose; the bot only
// reads the optional live DOM in the probe, never here. The bot therefore trades the three rule
// setups plus a session Volume-Profile HVN zone (section III), with the book's order-flow
// confirmations (delta, absorption, cumulative-delta divergence) available as filters.
//
// The setup, in the book's order
// ------------------------------
// 1. Form a zone from a finished 30m footprint bar:
//      Cluster      contiguous cells >= ClusterFactor x the bar's median cell volume
//      MultiNode    the POC repeats on consecutive bars (consecutive HVNs on one level)
//      Stacked      >= StackMinLevels adjacent levels with a diagonal imbalance >= StackRatio
//      Profile      the top HVN of the last ProfileBars (refreshed every ProfileRefreshBars)
// 2. Wait for the breakout: BreakoutBars finished bars entirely above (long) or below (short)
//    the zone. A bar that touches the zone before that invalidates it.
// 3. On the first touch back into the zone, enter in the direction of the approach (above ->
//    long, below -> short; a stacked buy zone is support, a stacked sell zone is resistance).
//    One entry per zone. Confirmations (delta / absorption / divergence) are optional and are
//    required only for Profile zones by default.
// 4. SL: behind the far edge of the zone (Area), behind the next low-volume pocket (LowVolume)
//    or a fixed distance (Fixed). TP: just before the next HVN above/below, with a MinRR gate.
// 5. Manage: break-even at BreakEvenR, ATR trail after TrailStartR, and an early exit when the
//    5m footprint shows delta and a stacked imbalance against the position.
//
// Warm-up and restarts
// --------------------
// A 30m footprint needs WarmupBars finished bars. On start the bot seeds them from the ticks the
// server already has (SeedHistory / SeedLoadCalls); bars whose first test happened before the
// bot started are marked consumed during that replay, so a restart cannot trade an old test.
//
// Reporting
// ---------
// Live/demo only (RunningMode.RealTime): account sync, open and close reports go to
// /portfolio/report with ctrader_id, and open positions are re-reported on start so a restart
// repairs a missed open. Backtests keep every URL disabled; they are pure rules.
//
// Backtest needs tick data: `--data-mode=ticks`. With M1 bars there are no ticks, the bot logs
// NO_TICKS and does nothing.
//
// Log codes: SEED, WARMUP, READY, NO_TICKS, ZONE, ARM, TOUCH, SEED_TOUCH, SKIP, ENTRY, EXIT,
// RESUME, BE, TRAIL, OF_EXIT, REPORT_ERROR.

using System;
using System.Collections.Generic;
using System.Diagnostics;
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
    public enum OrderFlowSlMode
    {
        Area,
        LowVolume,
        Fixed
    }

    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class OrderFlowBot : Robot
    {
        #region Parameters

        [Parameter("Bot ID (position label)", Group = "General", DefaultValue = "ORDERFLOW")]
        public string BotId { get; set; }

        [Parameter("Account Label", Group = "General", DefaultValue = "OrderFlow")]
        public string AccountLabel { get; set; }

        [Parameter("Dry Run (log signals, send no orders)", Group = "General", DefaultValue = false)]
        public bool DryRun { get; set; }

        [Parameter("Show Logs", Group = "General", DefaultValue = true)]
        public bool ShowLogs { get; set; }

        // ---- Footprint ----------------------------------------------------------------------
        [Parameter("Footprint Period (minutes)", Group = "Footprint", DefaultValue = 30, MinValue = 5, MaxValue = 240)]
        public int PeriodMinutes { get; set; }

        [Parameter("Warm-up Bars", Group = "Footprint", DefaultValue = 96, MinValue = 50, MaxValue = 2000)]
        public int WarmupBars { get; set; }

        [Parameter("Max M30 Bars Kept", Group = "Footprint", DefaultValue = 300, MinValue = 100, MaxValue = 5000)]
        public int MaxBarsKept { get; set; }

        [Parameter("Seed From Tick History", Group = "Footprint", DefaultValue = true)]
        public bool SeedHistory { get; set; }

        [Parameter("Seed Load Calls", Group = "Footprint", DefaultValue = 5, MinValue = 0, MaxValue = 100)]
        public int SeedLoadCalls { get; set; }

        [Parameter("Seed Zone Bars", Group = "Footprint", DefaultValue = 96, MinValue = 0, MaxValue = 2000)]
        public int SeedZoneBars { get; set; }

        [Parameter("Seed Max Ticks", Group = "Footprint", DefaultValue = 3000000, MinValue = 10000, MaxValue = 50000000)]
        public int SeedMaxTicks { get; set; }

        // Warm-up is the expensive part: 96 H1 footprint bars can only be rebuilt from ticks, and
        // the broker serves ~half a day of them. Persisting the built bars + zones means a restart
        // (watchdog, reboot, plan change) resumes instead of starting the count again.
        // Blank disables persistence. Live only — backtests never read or write state.
        [Parameter("State Directory (blank=off)", Group = "Footprint", DefaultValue = "/root/ctrader_data/orderflow_state")]
        public string StateDir { get; set; }

        // A gap longer than this throws the saved state away: the missing bars would leave the
        // EMA/ATR series non-contiguous, and stale zones would be traded as if they were fresh.
        [Parameter("Max State Gap (hours)", Group = "Footprint", DefaultValue = 48, MinValue = 1, MaxValue = 720)]
        public int MaxStateGapHours { get; set; }

        [Parameter("EMA Fast", Group = "Footprint", DefaultValue = 20, MinValue = 5, MaxValue = 200)]
        public int EmaFastPeriod { get; set; }

        [Parameter("EMA Slow", Group = "Footprint", DefaultValue = 50, MinValue = 10, MaxValue = 500)]
        public int EmaSlowPeriod { get; set; }

        [Parameter("ATR Period", Group = "Footprint", DefaultValue = 14, MinValue = 5, MaxValue = 100)]
        public int AtrPeriod { get; set; }

        [Parameter("Rejection Wick Factor", Group = "Footprint", DefaultValue = 2.0, MinValue = 0.5, MaxValue = 10.0, Step = 0.1)]
        public double RejectionWickFactor { get; set; }

        // ---- Zones --------------------------------------------------------------------------
        [Parameter("Use Volume Clusters", Group = "Zones", DefaultValue = true)]
        public bool UseClusters { get; set; }

        [Parameter("Cluster Factor (x median cell)", Group = "Zones", DefaultValue = 2.0, MinValue = 1.1, MaxValue = 10.0, Step = 0.1)]
        public double ClusterFactor { get; set; }

        [Parameter("Cluster Min Levels", Group = "Zones", DefaultValue = 2, MinValue = 2, MaxValue = 30)]
        public int ClusterMinLevels { get; set; }

        [Parameter("Cluster Min Volume Share", Group = "Zones", DefaultValue = 0.15, MinValue = 0.01, MaxValue = 1.0, Step = 0.01)]
        public double ClusterMinVolumeShare { get; set; }

        [Parameter("Cluster Requires Trend/Rejection", Group = "Zones", DefaultValue = true)]
        public bool RequireTrendForClusters { get; set; }

        [Parameter("Use Multiple Nodes", Group = "Zones", DefaultValue = true)]
        public bool UseMultiNodes { get; set; }

        [Parameter("Multi-Node Tolerance (ticks)", Group = "Zones", DefaultValue = 1, MinValue = 0, MaxValue = 10)]
        public int MultiNodeToleranceTicks { get; set; }

        [Parameter("Multi-Node Min Bars", Group = "Zones", DefaultValue = 2, MinValue = 2, MaxValue = 10)]
        public int MultiNodeMinBars { get; set; }

        [Parameter("Use Stacked Imbalances", Group = "Zones", DefaultValue = true)]
        public bool UseStacked { get; set; }

        [Parameter("Stack Ratio (x, 3=300%)", Group = "Zones", DefaultValue = 3.0, MinValue = 1.2, MaxValue = 10.0, Step = 0.1)]
        public double StackRatio { get; set; }

        [Parameter("Stack Min Levels", Group = "Zones", DefaultValue = 3, MinValue = 3, MaxValue = 30)]
        public int StackMinLevels { get; set; }

        [Parameter("Use Profile Zones", Group = "Zones", DefaultValue = true)]
        public bool UseProfileZones { get; set; }

        [Parameter("Profile Bars", Group = "Zones", DefaultValue = 48, MinValue = 6, MaxValue = 500)]
        public int ProfileBars { get; set; }

        [Parameter("Profile Refresh Bars", Group = "Zones", DefaultValue = 12, MinValue = 1, MaxValue = 100)]
        public int ProfileRefreshBars { get; set; }

        [Parameter("Profile Width (ticks)", Group = "Zones", DefaultValue = 2, MinValue = 0, MaxValue = 20)]
        public int ProfileWidthTicks { get; set; }

        [Parameter("Profile Confirmations", Group = "Zones", DefaultValue = 1, MinValue = 0, MaxValue = 3)]
        public int ProfileConfirmations { get; set; }

        [Parameter("Max Zone Width (ticks)", Group = "Zones", DefaultValue = 40, MinValue = 3, MaxValue = 500)]
        public int MaxZoneTicks { get; set; }

        [Parameter("Zone Max Age (bars)", Group = "Zones", DefaultValue = 400, MinValue = 20, MaxValue = 10000)]
        public int ZoneMaxAgeBars { get; set; }

        [Parameter("Max Active Zones", Group = "Zones", DefaultValue = 20, MinValue = 2, MaxValue = 100)]
        public int MaxActiveZones { get; set; }

        // ---- Entry --------------------------------------------------------------------------
        [Parameter("Breakout Bars", Group = "Entry", DefaultValue = 2, MinValue = 1, MaxValue = 10)]
        public int BreakoutBars { get; set; }

        [Parameter("Touch Tolerance (ticks)", Group = "Entry", DefaultValue = 1, MinValue = 0, MaxValue = 20)]
        public int TouchToleranceTicks { get; set; }

        [Parameter("Confirmations For Setups", Group = "Entry", DefaultValue = 1, MinValue = 0, MaxValue = 3)]
        public int ConfirmationsSetups { get; set; }

        [Parameter("Max Spread (pips)", Group = "Entry", DefaultValue = 50.0, MinValue = 0.1, MaxValue = 10000.0, Step = 0.1)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Max Trades Per Day", Group = "Entry", DefaultValue = 3, MinValue = 1, MaxValue = 100)]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Session Start Hour (UTC, 0=all day)", Group = "Entry", DefaultValue = 0, MinValue = 0, MaxValue = 24)]
        public int TradeStartHourUtc { get; set; }

        [Parameter("Session End Hour (UTC, 0=all day)", Group = "Entry", DefaultValue = 0, MinValue = 0, MaxValue = 24)]
        public int TradeEndHourUtc { get; set; }

        // ---- Risk ---------------------------------------------------------------------------
        [Parameter("Risk Sizing Enabled", Group = "Risk", DefaultValue = true)]
        public bool RiskSizingEnabled { get; set; }

        [Parameter("Risk per Trade (% Balance)", Group = "Risk", DefaultValue = 0.2, MinValue = 0.01, MaxValue = 10.0, Step = 0.01)]
        public double RiskPerTradePercent { get; set; }

        [Parameter("Fixed Lots (0=broker min)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 1000.0, Step = 0.01)]
        public double FixedLots { get; set; }

        [Parameter("Max Lots per Trade", Group = "Risk", DefaultValue = 5.0, MinValue = 0.01, MaxValue = 1000.0, Step = 0.01)]
        public double MaxAllowedLots { get; set; }

        [Parameter("Max Dollar Risk per Trade ($)", Group = "Risk", DefaultValue = 100.0, MinValue = 1.0, MaxValue = 100000.0)]
        public double MaxDollarRiskPerTrade { get; set; }

        [Parameter("Stop Loss Mode", Group = "Risk", DefaultValue = OrderFlowSlMode.Area)]
        public OrderFlowSlMode SlMode { get; set; }

        [Parameter("SL Buffer (x ATR)", Group = "Risk", DefaultValue = 0.25, MinValue = 0.01, MaxValue = 5.0, Step = 0.01)]
        public double SlBufferAtr { get; set; }

        [Parameter("Fixed SL (pips)", Group = "Risk", DefaultValue = 20.0, MinValue = 1.0, MaxValue = 10000.0, Step = 0.1)]
        public double FixedSlPips { get; set; }

        [Parameter("Low-Volume Pocket Fraction", Group = "Risk", DefaultValue = 0.2, MinValue = 0.01, MaxValue = 1.0, Step = 0.01)]
        public double LowVolumeFraction { get; set; }

        [Parameter("Min SL (pips)", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 1000.0, Step = 0.1)]
        public double MinSlPips { get; set; }

        [Parameter("Min Reward/Risk", Group = "Risk", DefaultValue = 1.5, MinValue = 0.2, MaxValue = 20.0, Step = 0.1)]
        public double MinRR { get; set; }

        [Parameter("TP Next HVN", Group = "Risk", DefaultValue = true)]
        public bool TpNextHvn { get; set; }

        [Parameter("TP HVN Factor (x median)", Group = "Risk", DefaultValue = 1.5, MinValue = 0.5, MaxValue = 20.0, Step = 0.1)]
        public double TpHvnFactor { get; set; }

        [Parameter("TP Buffer (pips)", Group = "Risk", DefaultValue = 1.0, MinValue = 0.0, MaxValue = 1000.0, Step = 0.1)]
        public double TpBufferPips { get; set; }

        [Parameter("Break-even at R (0=off)", Group = "Management", DefaultValue = 0.0, MinValue = 0.0, MaxValue = 10.0, Step = 0.1)]
        public double BreakEvenR { get; set; }

        [Parameter("Trail Start at R", Group = "Management", DefaultValue = 2.0, MinValue = 0.0, MaxValue = 20.0, Step = 0.1)]
        public double TrailStartR { get; set; }

        [Parameter("Trail ATR Multiple (M5)", Group = "Management", DefaultValue = 2.5, MinValue = 0.1, MaxValue = 10.0, Step = 0.1)]
        public double TrailAtrMult { get; set; }

        [Parameter("Use ATR Trail", Group = "Management", DefaultValue = false)]
        public bool UseAtrTrail { get; set; }

        [Parameter("Order-Flow Exit Bars (M5)", Group = "Management", DefaultValue = 2, MinValue = 1, MaxValue = 20)]
        public int OrderFlowExitBars { get; set; }

        [Parameter("Use Order-Flow Exit", Group = "Management", DefaultValue = true)]
        public bool UseOrderFlowExit { get; set; }

        // ---- Confirmations ------------------------------------------------------------------
        [Parameter("Use Delta Confirmation", Group = "Confirmations", DefaultValue = true)]
        public bool UseDeltaConfirm { get; set; }

        [Parameter("Use Absorption Confirmation", Group = "Confirmations", DefaultValue = true)]
        public bool UseAbsorption { get; set; }

        [Parameter("Absorption Factor (x cell)", Group = "Confirmations", DefaultValue = 2.0, MinValue = 0.5, MaxValue = 20.0, Step = 0.1)]
        public double AbsorptionFactor { get; set; }

        [Parameter("Absorption Lookback (M5 bars)", Group = "Confirmations", DefaultValue = 3, MinValue = 1, MaxValue = 30)]
        public int AbsorptionBars { get; set; }

        [Parameter("Confirm Delta Bars (M5)", Group = "Confirmations", DefaultValue = 3, MinValue = 1, MaxValue = 30)]
        public int ConfirmBars { get; set; }

        [Parameter("Use Cumulative Delta Divergence", Group = "Confirmations", DefaultValue = true)]
        public bool UseDivergence { get; set; }

        [Parameter("Divergence Lookback (M1 bars)", Group = "Confirmations", DefaultValue = 20, MinValue = 5, MaxValue = 200)]
        public int DivergenceLookback { get; set; }

        // ---- Reporting ----------------------------------------------------------------------
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

        #region State types

        private sealed class Cell
        {
            public int Bid;
            public int Ask;
            public int Total => Bid + Ask;
        }

        private sealed class TickBar
        {
            public readonly TimeSpan Period;
            public DateTime Start;
            public bool HasData;
            public Dictionary<long, Cell> Cells = new Dictionary<long, Cell>();
            public double Open, High, Low, Close;
            public int Ticks;

            public TickBar(TimeSpan period)
            {
                Period = period;
            }

            public void Reset(DateTime start, double open)
            {
                Start = start;
                Open = High = Low = Close = open;
                Ticks = 0;
                Cells = new Dictionary<long, Cell>();
                HasData = true;
            }
        }

        private sealed class FootprintBar
        {
            public DateTime Start;
            public double Open, High, Low, Close;
            public Dictionary<long, Cell> Cells = new Dictionary<long, Cell>();
            public int Ticks;
            public long TotalBid, TotalAsk;
            public int Levels, MaxCell;
            public long PocLevel;
            public int PocVol;
            public double MedianCell;
            public double CumulativeDelta;
            public double Delta => TotalAsk - TotalBid;
        }

        private enum ZoneKind
        {
            Cluster,
            MultiNode,
            StackedBuy,
            StackedSell,
            Profile
        }

        private enum ZoneBias
        {
            None,
            Long,
            Short,
            Both
        }

        private sealed class Zone
        {
            public int Id;
            public ZoneKind Kind;
            public ZoneBias Bias;
            public double Low, High, Anchor;
            public DateTime CreatedBar;
            public int AgeBars;
            public int ArmBars;
            public int TouchCount;
            public bool Armed;
            public bool Consumed;
            public bool Invalid;
            public string InvalidReason;
            public int ConfirmationsRequired;
            public ZoneBias ArmSide;
            public string Note;
        }

        private sealed class StackRun
        {
            public bool Buy;
            public long LowLevel;
            public long HighLevel;
            public int Length;
            public int Volume;
            public long AnchorLevel;
            public int AnchorVolume;
        }

        #endregion

        #region Fields

        private Ticks _ticks;
        private TickBar _m30;
        private TickBar _m5;
        private TickBar _m1;

        private readonly List<FootprintBar> _m30Bars = new List<FootprintBar>();
        private readonly List<FootprintBar> _m5Bars = new List<FootprintBar>();
        private readonly List<FootprintBar> _m1Bars = new List<FootprintBar>();
        private readonly List<Zone> _zones = new List<Zone>();
        private int _nextZoneId;

        private bool _seeding;
        private bool _warmupReady;
        private bool _warmupLogged;
        private bool _noTicksLogged;
        private int _warmupNeeded;
        private long _m30BarCount;

        private double _prevBid, _prevAsk;
        private bool _hasPrevTick;
        private double _lastBidPrice, _lastAskPrice;
        private bool _hasLastPrice;

        private double _emaFast, _emaSlow;
        private bool _hasEmaFast, _hasEmaSlow;
        private double _atr, _atr5;
        private bool _hasAtr, _hasAtr5;
        private double _prevClose30, _prevClose5;
        private bool _hasPrevClose30, _hasPrevClose5;

        private double _cumDelta;
        private DateTime _cdDate;

        private long _lastPocLevel = long.MinValue;
        private int _pocRun;
        private long _pocLow, _pocHigh;

        private DateTime _tradeDate;
        private int _tradesToday;

        private readonly Dictionary<int, double> _initialSlPips = new Dictionary<int, double>();
        private readonly Dictionary<int, string> _closeReasons = new Dictionary<int, string>();
        private readonly HashSet<int> _beMoved = new HashSet<int>();
        private int _activePositionId;
        private DateTime _lastModifyTime;

        private const int StateVersion = 1;
        private string _stateFile;
        private DateTime _restoredThroughUtc = DateTime.MinValue;
        private DateTime _lastStateSaveUtc = DateTime.MinValue;

        // uvicorn closes an idle keep-alive connection after 5 s; retire pooled ones before it does.
        private readonly HttpClient _httpClient = new HttpClient(new SocketsHttpHandler
        {
            PooledConnectionIdleTimeout = TimeSpan.FromSeconds(4)
        })
        {
            Timeout = TimeSpan.FromSeconds(60)
        };

        #endregion

        #region Lifecycle

        protected override void OnStart()
        {
            _warmupNeeded = Math.Min(WarmupBars, MaxBarsKept);
            _tradeDate = Server.Time.Date;
            _cdDate = Server.Time.Date;

            _m30 = new TickBar(TimeSpan.FromMinutes(PeriodMinutes));
            _m5 = new TickBar(TimeSpan.FromMinutes(5));
            _m1 = new TickBar(TimeSpan.FromMinutes(1));

            _ticks = MarketData.GetTicks(SymbolName);
            _ticks.Tick += OnTickEvent;

            Positions.Closed += OnPositionClosed;
            SendAccountSync();
            ResumeOpenPositions();
            ReconcileClosedDealsFromHistory();

            Print("OF|start|{0}|{1}|period={2}m|warmup={3}|breakout={4}|stack={5:F1}x{6}|cluster={7:F1}x{8}|" +
                  "risk={9}|sl={10}|minRR={11:F1}|profile={12}",
                BotId, SymbolName, PeriodMinutes, _warmupNeeded, BreakoutBars, StackRatio, StackMinLevels,
                ClusterFactor, ClusterMinLevels,
                RiskSizingEnabled ? $"{RiskPerTradePercent:F2}%/trade" : $"fixed {FixedLots:F2} lots",
                SlMode, MinRR, UseProfileZones ? $"on({ProfileConfirmations} confirm)" : "off");

            TryRestoreState();
            SeedFromHistory();

            if (_m30Bars.Count == 0 && _ticks.Count == 0 && !_noTicksLogged)
            {
                _noTicksLogged = true;
                Print("NO_TICKS|{0}|backtest needs --data-mode=ticks; live needs a tick feed", SymbolName);
            }
        }

        protected override void OnStop()
        {
            SaveState();
            Positions.Closed -= OnPositionClosed;
            if (_ticks != null)
                _ticks.Tick -= OnTickEvent;
            _httpClient.Dispose();
            Print("OF|stop|{0}|{1}", BotId, SymbolName);
        }

        private void OnTickEvent(TicksTickEventArgs args)
        {
            if (_seeding)
                return;
            ProcessTick(args.Ticks.LastTick);
        }

        #endregion

        #region State persistence

        private sealed class StateCell
        {
            public long L { get; set; }
            public int B { get; set; }
            public int A { get; set; }
        }

        private sealed class StateBar
        {
            public string T { get; set; }
            public double O { get; set; }
            public double H { get; set; }
            public double L { get; set; }
            public double C { get; set; }
            public int Ticks { get; set; }
            public long TotalBid { get; set; }
            public long TotalAsk { get; set; }
            public int Levels { get; set; }
            public int MaxCell { get; set; }
            public long PocLevel { get; set; }
            public int PocVol { get; set; }
            public double MedianCell { get; set; }
            public double CumulativeDelta { get; set; }
            public List<StateCell> Cells { get; set; }
        }

        private sealed class StateZone
        {
            public int Id { get; set; }
            public string Kind { get; set; }
            public string Bias { get; set; }
            public double Low { get; set; }
            public double High { get; set; }
            public double Anchor { get; set; }
            public string CreatedBar { get; set; }
            public int AgeBars { get; set; }
            public int ArmBars { get; set; }
            public int TouchCount { get; set; }
            public bool Armed { get; set; }
            public bool Consumed { get; set; }
            public int ConfirmationsRequired { get; set; }
            public string ArmSide { get; set; }
            public string Note { get; set; }
        }

        private sealed class StateDoc
        {
            public int Version { get; set; }
            public string Bot { get; set; }
            public string Symbol { get; set; }
            public int Period { get; set; }
            public string SavedUtc { get; set; }
            public long BarCount { get; set; }
            public bool WarmupReady { get; set; }
            public double Atr { get; set; }
            public bool HasAtr { get; set; }
            public double EmaFast { get; set; }
            public double EmaSlow { get; set; }
            public double PrevClose { get; set; }
            public bool HasPrevClose { get; set; }
            public int NextZoneId { get; set; }
            public List<StateBar> Bars { get; set; }
            public List<StateZone> Zones { get; set; }
        }

        private string StateFilePath()
        {
            if (string.IsNullOrWhiteSpace(StateDir)) return null;
            var name = new StringBuilder();
            foreach (char c in SymbolName + "_" + PeriodMinutes + "m_" + BotId)
                name.Append(char.IsLetterOrDigit(c) || c == '-' || c == '_' || c == '.' ? c : '_');
            return System.IO.Path.Combine(StateDir.Trim(), name + ".json");
        }

        /// <summary>
        /// Restores the closed footprint bars, the indicator state and the live zone list that a
        /// previous process left on disk. Live only: a backtest must stay a pure replay, otherwise
        /// a stale file would silently change its result.
        /// </summary>
        private void TryRestoreState()
        {
            _stateFile = StateFilePath();
            if (_stateFile == null)
            {
                Print("STATE|off");
                return;
            }
            if (RunningMode != RunningMode.RealTime)
            {
                _stateFile = null;      // never read or write state during a backtest
                return;
            }
            if (!System.IO.File.Exists(_stateFile))
            {
                Print("STATE|none|{0}", _stateFile);
                return;
            }

            try
            {
                var doc = JsonSerializer.Deserialize<StateDoc>(System.IO.File.ReadAllText(_stateFile));
                if (doc == null)
                {
                    Print("STATE|discard|unreadable|{0}", _stateFile);
                    return;
                }
                if (doc.Version != StateVersion)
                {
                    Print("STATE|discard|version={0}|expected={1}", doc.Version, StateVersion);
                    return;
                }
                if (!string.Equals(doc.Symbol, SymbolName, StringComparison.OrdinalIgnoreCase) || doc.Period != PeriodMinutes)
                {
                    Print("STATE|discard|mismatch|{0}|{1}m", doc.Symbol, doc.Period);
                    return;
                }

                DateTime savedUtc = DateTime.MinValue;
                if (!string.IsNullOrEmpty(doc.SavedUtc))
                {
                    DateTime.TryParse(doc.SavedUtc, CultureInfo.InvariantCulture,
                        DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out savedUtc);
                }
                double ageHours = savedUtc == DateTime.MinValue
                    ? double.MaxValue
                    : (Server.Time.ToUniversalTime() - savedUtc).TotalHours;
                if (ageHours > MaxStateGapHours)
                {
                    Print("STATE|discard|stale|age_h={0:F1}|max={1}", ageHours, MaxStateGapHours);
                    return;
                }

                var bars = new List<FootprintBar>();
                foreach (var sb in doc.Bars ?? new List<StateBar>())
                {
                    DateTime start;
                    if (sb.T == null || !DateTime.TryParse(sb.T, CultureInfo.InvariantCulture,
                            DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out start))
                        continue;
                    var bar = new FootprintBar
                    {
                        Start = start, Open = sb.O, High = sb.H, Low = sb.L, Close = sb.C,
                        Ticks = sb.Ticks, TotalBid = sb.TotalBid, TotalAsk = sb.TotalAsk,
                        Levels = sb.Levels, MaxCell = sb.MaxCell, PocLevel = sb.PocLevel,
                        PocVol = sb.PocVol, MedianCell = sb.MedianCell, CumulativeDelta = sb.CumulativeDelta
                    };
                    foreach (var sc in sb.Cells ?? new List<StateCell>())
                        bar.Cells[sc.L] = new Cell { Bid = sc.B, Ask = sc.A };
                    bars.Add(bar);
                }
                if (bars.Count == 0)
                {
                    Print("STATE|discard|no_bars");
                    return;
                }
                bars.Sort((a, b) => a.Start.CompareTo(b.Start));

                _m30Bars.Clear();
                foreach (var bar in bars)
                    AddBar(_m30Bars, bar, MaxBarsKept);

                // Indicator values ride along; replaying them from the bars would re-seed the EMAs
                // and drift away from what the warm-up actually computed.
                _atr = doc.Atr;
                _hasAtr = doc.HasAtr;
                _emaFast = doc.EmaFast;
                _emaSlow = doc.EmaSlow;
                _prevClose30 = doc.PrevClose;
                _hasPrevClose30 = doc.HasPrevClose;
                _hasEmaFast = doc.WarmupReady;
                _hasEmaSlow = doc.WarmupReady;
                _m30BarCount = doc.BarCount;
                _nextZoneId = doc.NextZoneId;

                int restoredZones = 0;
                _zones.Clear();
                foreach (var sz in doc.Zones ?? new List<StateZone>())
                {
                    ZoneKind kind;
                    ZoneBias bias;
                    if (!Enum.TryParse(sz.Kind, out kind) || !Enum.TryParse(sz.Bias, out bias)) continue;
                    DateTime createdBar;
                    DateTime.TryParse(sz.CreatedBar ?? "", CultureInfo.InvariantCulture,
                        DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out createdBar);
                    ZoneBias armSide;
                    if (!Enum.TryParse(sz.ArmSide ?? "", out armSide)) armSide = ZoneBias.None;

                    _zones.Add(new Zone
                    {
                        Id = sz.Id, Kind = kind, Bias = bias, Low = sz.Low, High = sz.High, Anchor = sz.Anchor,
                        CreatedBar = createdBar, AgeBars = sz.AgeBars, ArmBars = sz.ArmBars,
                        TouchCount = sz.TouchCount, Armed = sz.Armed, Consumed = sz.Consumed,
                        ConfirmationsRequired = sz.ConfirmationsRequired, ArmSide = armSide, Note = sz.Note
                    });
                    restoredZones++;
                }

                _restoredThroughUtc = _m30Bars[_m30Bars.Count - 1].Start.AddMinutes(PeriodMinutes);
                _warmupReady = doc.WarmupReady && _m30Bars.Count >= _warmupNeeded;
                if (_warmupReady)
                {
                    _warmupLogged = true;
                    Print("READY|bars={0}|atr={1}|ema={2}/{3}|restored", _m30Bars.Count, F(_atr), F(_emaFast), F(_emaSlow));
                }

                Print("STATE|loaded|bars={0}|zones={1}|barCount={2}|ready={3}|age_min={4:F0}",
                    _m30Bars.Count, restoredZones, _m30BarCount, _warmupReady, ageHours * 60.0);
            }
            catch (Exception ex)
            {
                Print("STATE|load_error|{0}", OneLine(ex.Message));
            }
        }

        /// <summary>Writes the bars, indicators and zones atomically (tmp + move).</summary>
        private void SaveState(bool force = false)
        {
            if (_stateFile == null || RunningMode != RunningMode.RealTime) return;
            if (_m30Bars.Count == 0) return;

            // One write per closed bar is plenty; the entry hook also calls this.
            if (!force && (DateTime.UtcNow - _lastStateSaveUtc).TotalSeconds < 5) return;

            try
            {
                var doc = new StateDoc
                {
                    Version = StateVersion, Bot = BotId, Symbol = SymbolName, Period = PeriodMinutes,
                    SavedUtc = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                    BarCount = _m30BarCount, WarmupReady = _warmupReady,
                    Atr = _atr, HasAtr = _hasAtr, EmaFast = _emaFast, EmaSlow = _emaSlow,
                    PrevClose = _prevClose30, HasPrevClose = _hasPrevClose30,
                    NextZoneId = _nextZoneId,
                    Bars = new List<StateBar>(_m30Bars.Count),
                    Zones = new List<StateZone>(_zones.Count)
                };

                foreach (var bar in _m30Bars)
                {
                    var sb = new StateBar
                    {
                        T = bar.Start.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture),
                        O = bar.Open, H = bar.High, L = bar.Low, C = bar.Close, Ticks = bar.Ticks,
                        TotalBid = bar.TotalBid, TotalAsk = bar.TotalAsk, Levels = bar.Levels,
                        MaxCell = bar.MaxCell, PocLevel = bar.PocLevel, PocVol = bar.PocVol,
                        MedianCell = bar.MedianCell, CumulativeDelta = bar.CumulativeDelta,
                        Cells = new List<StateCell>(bar.Cells.Count)
                    };
                    foreach (var kv in bar.Cells)
                        sb.Cells.Add(new StateCell { L = kv.Key, B = kv.Value.Bid, A = kv.Value.Ask });
                    doc.Bars.Add(sb);
                }

                foreach (var zone in _zones)
                {
                    if (zone.Invalid) continue;         // dead zones are not worth restoring
                    doc.Zones.Add(new StateZone
                    {
                        Id = zone.Id, Kind = zone.Kind.ToString(), Bias = zone.Bias.ToString(),
                        Low = zone.Low, High = zone.High, Anchor = zone.Anchor,
                        CreatedBar = zone.CreatedBar.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture),
                        AgeBars = zone.AgeBars, ArmBars = zone.ArmBars, TouchCount = zone.TouchCount,
                        Armed = zone.Armed, Consumed = zone.Consumed,
                        ConfirmationsRequired = zone.ConfirmationsRequired,
                        ArmSide = zone.ArmSide.ToString(), Note = zone.Note
                    });
                }

                System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_stateFile));
                string tmp = _stateFile + ".tmp";
                System.IO.File.WriteAllText(tmp, JsonSerializer.Serialize(doc));
                if (System.IO.File.Exists(_stateFile)) System.IO.File.Delete(_stateFile);
                System.IO.File.Move(tmp, _stateFile);
                _lastStateSaveUtc = DateTime.UtcNow;

                if (ShowLogs)
                    Print("STATE|saved|bars={0}|zones={1}", doc.Bars.Count, doc.Zones.Count);
            }
            catch (Exception ex)
            {
                Print("STATE|save_error|{0}", OneLine(ex.Message));
            }
        }

        #endregion

        #region Seed and tick processing

        /// <summary>
        /// Replays the last WarmupBars worth of server ticks through the same builders and zone
        /// detectors the live path uses. A zone whose first test already happened is consumed
        /// here, never traded on restart.
        /// </summary>
        private void SeedFromHistory()
        {
            if (!SeedHistory)
            {
                Print("SEED|disabled");
                return;
            }

            var watch = Stopwatch.StartNew();
            for (int i = 0; i < SeedLoadCalls; i++)
            {
                int loaded;
                try
                {
                    loaded = _ticks.LoadMoreHistory();
                }
                catch (Exception ex)
                {
                    Print("SEED_ERROR|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
                    break;
                }
                if (loaded <= 0)
                    break;
                if (_ticks.Count >= SeedMaxTicks)
                    break;
            }

            int total = _ticks.Count;
            if (total < 2)
            {
                Print("SEED|empty|ticks={0}|load_ms={1}", total, watch.ElapsedMilliseconds);
                return;
            }

            DateTime last = _ticks[total - 1].Time;
            // Warm-up bars feed the EMAs/ATR; the extra SeedZoneBars are replayed so the bot starts
            // with zones formed before it came up, not only with the bar that completed warm-up.
            DateTime cutoff = last - TimeSpan.FromMinutes((_warmupNeeded + SeedZoneBars + 10L) * PeriodMinutes);
            int start = LowerBound(cutoff);
            if (_restoredThroughUtc != DateTime.MinValue)
            {
                int covered = LowerBound(_restoredThroughUtc);
                if (covered > start)
                {
                    if (ShowLogs)
                        Print("SEED|skip_before_state|from={0:o}|ticks={1}", _restoredThroughUtc, covered - start);
                    start = covered;
                }
            }
            if (total - start > SeedMaxTicks)
                start = total - SeedMaxTicks;

            _seeding = true;
            try
            {
                for (int i = start; i < total; i++)
                    ProcessTick(_ticks[i]);
            }
            finally
            {
                _seeding = false;
            }

            Print("SEED|ticks={0}|from={1:o}|to={2:o}|bars={3}|load_ms={4}",
                total - start, _ticks[start].Time, last, _m30Bars.Count, watch.ElapsedMilliseconds);

            if (!_warmupReady)
                Print("WARMUP|bars={0}|need={1}|live bars will finish the warm-up", _m30Bars.Count, _warmupNeeded);

            // The replay can add dozens of bars inside a few seconds, so the per-bar throttle above
            // would leave only the first one on disk. Persist the whole rebuilt set here.
            SaveState(force: true);
        }

        private int LowerBound(DateTime cutoff)
        {
            int lo = 0;
            int hi = _ticks.Count - 1;
            while (lo < hi)
            {
                int mid = (lo + hi) / 2;
                if (_ticks[mid].Time < cutoff)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

        private void ProcessTick(Tick t)
        {
            double prevBid = _lastBidPrice;
            double prevAsk = _lastAskPrice;
            bool hadLast = _hasLastPrice;

            int side = Classify(t);

            FootprintBar closed30 = AddTickToBar(_m30, t, side);
            if (closed30 != null)
                OnM30Closed(closed30);

            FootprintBar closed5 = AddTickToBar(_m5, t, side);
            if (closed5 != null)
                OnM5Closed(closed5);

            FootprintBar closed1 = AddTickToBar(_m1, t, side);
            if (closed1 != null)
                OnM1Closed(closed1);

            if (hadLast && _warmupReady)
                CheckTouches(t.Bid, t.Ask, prevBid, prevAsk, t.Time);

            if (!_seeding)
                ManageOpenPosition(t);

            _lastBidPrice = t.Bid;
            _lastAskPrice = t.Ask;
            _hasLastPrice = true;
        }

        /// <summary>
        /// 0 neutral, 1 buy at ask, 2 sell at bid, 3 both (spread widened). One tick is one unit
        /// of footprint volume; the pair of quotes identifies the side that moved.
        /// </summary>
        private int Classify(Tick t)
        {
            if (!_hasPrevTick)
            {
                _hasPrevTick = true;
                _prevBid = t.Bid;
                _prevAsk = t.Ask;
                return 0;
            }

            double dAsk = t.Ask - _prevAsk;
            double dBid = t.Bid - _prevBid;
            bool buy = dAsk > 1e-9;
            bool sell = dBid < -1e-9;

            int side;
            if (buy && sell)
                side = 3;
            else if (buy)
                side = 1;
            else if (sell)
                side = 2;
            else
                side = 0;

            _prevBid = t.Bid;
            _prevAsk = t.Ask;
            return side;
        }

        private FootprintBar AddTickToBar(TickBar bar, Tick t, int side)
        {
            DateTime target = Floor(t.Time, bar.Period);
            FootprintBar closed = null;

            if (!bar.HasData)
            {
                bar.Reset(target, t.Bid);
            }
            else if (target > bar.Start)
            {
                closed = FinalizeBar(bar);
                bar.Reset(target, t.Bid);
            }

            bar.Ticks++;
            if (t.Bid > bar.High) bar.High = t.Bid;
            if (t.Bid < bar.Low) bar.Low = t.Bid;
            bar.Close = t.Bid;

            if (side == 3)
            {
                AddCell(bar.Cells, LevelOf(t.Ask), 0, 1);
                AddCell(bar.Cells, LevelOf(t.Bid), 1, 0);
            }
            else if (side == 1)
            {
                AddCell(bar.Cells, LevelOf(t.Ask), 0, 1);
            }
            else if (side == 2)
            {
                AddCell(bar.Cells, LevelOf(t.Bid), 1, 0);
            }

            return closed;
        }

        private FootprintBar FinalizeBar(TickBar bar)
        {
            var fb = new FootprintBar
            {
                Start = bar.Start,
                Open = bar.Open,
                High = bar.High,
                Low = bar.Low,
                Close = bar.Close,
                Cells = bar.Cells,
                Ticks = bar.Ticks
            };
            ComputeMetrics(fb);
            return fb;
        }

        private static void ComputeMetrics(FootprintBar fb)
        {
            fb.Levels = fb.Cells.Count;
            long bid = 0, ask = 0;
            int max = 0;
            long poc = 0;
            int pocVol = 0;
            var volumes = new List<int>(fb.Cells.Count);

            foreach (KeyValuePair<long, Cell> kv in fb.Cells)
            {
                bid += kv.Value.Bid;
                ask += kv.Value.Ask;
                int total = kv.Value.Total;
                volumes.Add(total);
                if (total > max)
                {
                    max = total;
                    poc = kv.Key;
                    pocVol = total;
                }
            }

            fb.TotalBid = bid;
            fb.TotalAsk = ask;
            fb.MaxCell = max;
            fb.PocLevel = poc;
            fb.PocVol = pocVol;

            if (volumes.Count > 0)
            {
                volumes.Sort();
                fb.MedianCell = volumes[volumes.Count / 2];
            }
        }

        #endregion

        #region Bar close handlers

        private void OnM30Closed(FootprintBar bar)
        {
            // After a state restore the seed replay can walk over bars already on disk.
            if (_m30Bars.Count > 0 && bar.Start <= _m30Bars[_m30Bars.Count - 1].Start) return;

            _m30BarCount++;
            AddBar(_m30Bars, bar, MaxBarsKept);
            UpdateIndicators(bar);

            if (_m30Bars.Count < _warmupNeeded)
            {
                if (!_warmupLogged)
                {
                    _warmupLogged = true;
                    Print("WARMUP|bars={0}|need={1}", _m30Bars.Count, _warmupNeeded);
                }
                SaveState();
                return;
            }

            if (!_warmupReady)
            {
                _warmupReady = true;
                Print("READY|bars={0}|atr={1}|ema={2}/{3}", _m30Bars.Count, F(_atr), F(_emaFast), F(_emaSlow));
            }

            DetectZones(bar);
            UpdateZones(bar);
            PruneZones();
            SaveState();
        }

        private void OnM5Closed(FootprintBar bar)
        {
            AddBar(_m5Bars, bar, 200);
            UpdateAtr5(bar);

            if (!_seeding && _warmupReady && UseOrderFlowExit)
                TryOrderFlowExit();
        }

        private void OnM1Closed(FootprintBar bar)
        {
            AddBar(_m1Bars, bar, 300);

            if (bar.Start.Date != _cdDate.Date)
            {
                _cdDate = bar.Start.Date;
                _cumDelta = 0;
            }

            _cumDelta += bar.Delta;
            bar.CumulativeDelta = _cumDelta;
        }

        private static void AddBar(List<FootprintBar> bars, FootprintBar bar, int max)
        {
            bars.Add(bar);
            while (bars.Count > max)
                bars.RemoveAt(0);
        }

        private void UpdateIndicators(FootprintBar bar)
        {
            if (_hasPrevClose30)
            {
                double tr = Math.Max(bar.High - bar.Low,
                    Math.Max(Math.Abs(bar.High - _prevClose30), Math.Abs(bar.Low - _prevClose30)));
                _atr = _hasAtr ? _atr + (tr - _atr) / AtrPeriod : tr;
                _hasAtr = true;
            }
            else
            {
                _atr = bar.High - bar.Low;
                _hasAtr = true;
                _hasPrevClose30 = true;
            }

            _emaFast = _hasEmaFast ? _emaFast + 2.0 / (EmaFastPeriod + 1) * (bar.Close - _emaFast) : bar.Close;
            _emaSlow = _hasEmaSlow ? _emaSlow + 2.0 / (EmaSlowPeriod + 1) * (bar.Close - _emaSlow) : bar.Close;
            _hasEmaFast = true;
            _hasEmaSlow = true;
            _prevClose30 = bar.Close;
        }

        private void UpdateAtr5(FootprintBar bar)
        {
            if (_hasPrevClose5)
            {
                double tr = Math.Max(bar.High - bar.Low,
                    Math.Max(Math.Abs(bar.High - _prevClose5), Math.Abs(bar.Low - _prevClose5)));
                _atr5 = _hasAtr5 ? _atr5 + (tr - _atr5) / AtrPeriod : tr;
                _hasAtr5 = true;
            }
            else
            {
                _atr5 = bar.High - bar.Low;
                _hasAtr5 = true;
                _hasPrevClose5 = true;
            }
            _prevClose5 = bar.Close;
        }

        #endregion

        #region Zone detection

        private void DetectZones(FootprintBar bar)
        {
            if (UseClusters)
                DetectClusters(bar);
            if (UseMultiNodes)
                DetectMultiNodes(bar);
            if (UseStacked)
                DetectStacked(bar);
            if (UseProfileZones)
                DetectProfile(bar);
        }

        /// <summary>Volume Clusters: contiguous cells at least ClusterFactor x the bar's median cell.</summary>
        private void DetectClusters(FootprintBar bar)
        {
            if (bar.Cells.Count == 0 || bar.MedianCell <= 0)
                return;

            ZoneBias bias = ClusterBias(bar);
            if (bias == ZoneBias.None)
                return;

            double threshold = ClusterFactor * bar.MedianCell;
            var levels = new List<long>(bar.Cells.Keys);
            levels.Sort();

            var found = new List<long[]>(); // low, high, volume, poc, pocVol
            long runLow = 0, runHigh = 0, runVol = 0, runPoc = 0;
            int runPocVol = 0;
            bool inRun = false;

            foreach (long level in levels)
            {
                Cell cell = bar.Cells[level];
                bool hot = cell.Total >= threshold;

                if (hot)
                {
                    if (!inRun)
                    {
                        inRun = true;
                        runLow = level;
                        runVol = 0;
                        runPoc = level;
                        runPocVol = 0;
                    }
                    runHigh = level;
                    runVol += cell.Total;
                    if (cell.Total > runPocVol)
                    {
                        runPocVol = cell.Total;
                        runPoc = level;
                    }
                }
                else if (inRun)
                {
                    if (runHigh - runLow + 1 >= ClusterMinLevels)
                        found.Add(new[] { runLow, runHigh, runVol, runPoc, runPocVol });
                    inRun = false;
                }
            }

            if (inRun && runHigh - runLow + 1 >= ClusterMinLevels)
                found.Add(new[] { runLow, runHigh, runVol, runPoc, runPocVol });

            if (found.Count == 0)
                return;

            double barVolume = bar.TotalBid + bar.TotalAsk;
            foreach (long[] run in found.OrderByDescending(r => r[2]).Take(2))
            {
                if (barVolume <= 0 || run[2] / barVolume < ClusterMinVolumeShare)
                    continue;

                CreateZone(ZoneKind.Cluster, bias, run[0], run[1], run[3],
                    ConfirmationsSetups,
                    string.Format(CultureInfo.InvariantCulture, "vol={0}/{1:F0}%", run[2], 100.0 * run[2] / barVolume));
            }
        }

        private ZoneBias ClusterBias(FootprintBar bar)
        {
            bool bullTrend = _hasEmaFast && _hasEmaSlow && _emaFast > _emaSlow && bar.Close >= _emaSlow;
            bool bearTrend = _hasEmaFast && _hasEmaSlow && _emaFast < _emaSlow && bar.Close <= _emaSlow;

            double range = bar.High - bar.Low;
            if (range <= 0)
                return ZoneBias.None;

            double body = Math.Abs(bar.Close - bar.Open);
            double lowerWick = Math.Min(bar.Open, bar.Close) - bar.Low;
            double upperWick = bar.High - Math.Max(bar.Open, bar.Close);
            double wickFloor = Math.Max(body, range * 0.1);

            bool bullRejection = lowerWick >= RejectionWickFactor * wickFloor && bar.Close >= bar.Low + 0.6 * range;
            bool bearRejection = upperWick >= RejectionWickFactor * wickFloor && bar.Close <= bar.High - 0.6 * range;

            if (bullTrend || bullRejection)
                return ZoneBias.Long;
            if (bearTrend || bearRejection)
                return ZoneBias.Short;

            return RequireTrendForClusters ? ZoneBias.None : ZoneBias.Both;
        }

        /// <summary>Multiple Nodes: consecutive bars whose POC sits on the same level.</summary>
        private void DetectMultiNodes(FootprintBar bar)
        {
            if (bar.Cells.Count == 0)
                return;

            long poc = bar.PocLevel;

            if (_lastPocLevel != long.MinValue && Math.Abs(poc - _lastPocLevel) <= MultiNodeToleranceTicks)
            {
                _pocRun++;
                _pocLow = Math.Min(_pocLow, poc);
                _pocHigh = Math.Max(_pocHigh, poc);
            }
            else
            {
                _pocRun = 1;
                _pocLow = poc;
                _pocHigh = poc;
            }

            _lastPocLevel = poc;

            if (_pocRun != MultiNodeMinBars)
                return;

            long low = Math.Max(0, _pocLow - MultiNodeToleranceTicks);
            long high = _pocHigh + MultiNodeToleranceTicks;
            CreateZone(ZoneKind.MultiNode, ZoneBias.Both, low, high, poc, ConfirmationsSetups,
                string.Format(CultureInfo.InvariantCulture, "poc_run={0}", _pocRun));
        }

        /// <summary>Stacked Imbalances: a diagonal bid/ask run of at least StackMinLevels.</summary>
        private void DetectStacked(FootprintBar bar)
        {
            if (bar.Cells.Count == 0)
                return;

            List<StackRun> runs = FindStackRuns(bar.Cells, StackRatio, StackMinLevels);
            if (runs.Count == 0)
                return;

            StackRun buy = runs.Where(r => r.Buy).OrderByDescending(r => r.Length).ThenByDescending(r => r.Volume).FirstOrDefault();
            StackRun sell = runs.Where(r => !r.Buy).OrderByDescending(r => r.Length).ThenByDescending(r => r.Volume).FirstOrDefault();

            if (buy != null)
            {
                CreateZone(ZoneKind.StackedBuy, ZoneBias.Long, buy.LowLevel, buy.HighLevel, buy.AnchorLevel,
                    ConfirmationsSetups,
                    string.Format(CultureInfo.InvariantCulture, "stack={0}/vol={1}", buy.Length, buy.Volume));
            }

            if (sell != null)
            {
                CreateZone(ZoneKind.StackedSell, ZoneBias.Short, sell.LowLevel, sell.HighLevel, sell.AnchorLevel,
                    ConfirmationsSetups,
                    string.Format(CultureInfo.InvariantCulture, "stack={0}/vol={1}", sell.Length, sell.Volume));
            }
        }

        /// <summary>Session Volume Profile: the top HVN of the last ProfileBars, refreshed periodically.</summary>
        private void DetectProfile(FootprintBar bar)
        {
            if (ProfileRefreshBars <= 0 || _m30BarCount % ProfileRefreshBars != 0)
                return;

            Dictionary<long, Cell> profile = AggregateProfile(ProfileBars);
            if (profile.Count == 0)
                return;

            long hvn = 0;
            int hvnVol = -1;
            foreach (KeyValuePair<long, Cell> kv in profile)
            {
                if (kv.Value.Total > hvnVol)
                {
                    hvnVol = kv.Value.Total;
                    hvn = kv.Key;
                }
            }

            if (hvnVol <= 0)
                return;

            long low = Math.Max(0, hvn - ProfileWidthTicks);
            long high = hvn + ProfileWidthTicks;
            CreateZone(ZoneKind.Profile, ZoneBias.Both, low, high, hvn, ProfileConfirmations,
                string.Format(CultureInfo.InvariantCulture, "hvn_vol={0}", hvnVol));
        }

        private Zone CreateZone(ZoneKind kind, ZoneBias bias, long lowLevel, long highLevel, long anchorLevel,
            int confirmations, string note)
        {
            if (lowLevel > highLevel)
            {
                long swap = lowLevel;
                lowLevel = highLevel;
                highLevel = swap;
            }

            if (highLevel - lowLevel > MaxZoneTicks)
                return null;

            double low = PriceOf(lowLevel);
            double high = PriceOf(highLevel);

            foreach (Zone existing in _zones)
            {
                if (existing.Invalid || existing.Consumed || existing.Kind != kind)
                    continue;
                if (low <= existing.High + Symbol.TickSize && high >= existing.Low - Symbol.TickSize)
                    return null;
            }

            if (_zones.Count >= MaxActiveZones)
            {
                PruneZones();
                if (_zones.Count >= MaxActiveZones)
                    return null;
            }

            var zone = new Zone
            {
                Id = ++_nextZoneId,
                Kind = kind,
                Bias = bias,
                Low = low,
                High = high,
                Anchor = PriceOf(anchorLevel),
                CreatedBar = _m30Bars.Count > 0 ? _m30Bars[_m30Bars.Count - 1].Start : Server.Time,
                ConfirmationsRequired = Math.Max(0, confirmations),
                Note = note
            };

            _zones.Add(zone);
            Print("ZONE|{0}|{1}|{2}|{3}|{4}|{5}|{6}|confirm={7}|{8}",
                zone.Id, kind, bias, F(low), F(high), F(zone.Anchor), F(_atr),
                zone.ConfirmationsRequired, note);
            return zone;
        }

        private List<StackRun> FindStackRuns(Dictionary<long, Cell> cells, double ratio, int minLevels)
        {
            var runs = new List<StackRun>();
            if (cells.Count == 0)
                return runs;

            var levels = new List<long>(cells.Keys);
            levels.Sort();

            int buyLen = 0, sellLen = 0, buyVol = 0, sellVol = 0, buyPocVol = 0, sellPocVol = 0;
            long buyLow = 0, buyHigh = 0, sellLow = 0, sellHigh = 0, buyPoc = 0, sellPoc = 0;
            long prev = long.MinValue;

            foreach (long level in levels)
            {
                if (prev != long.MinValue && level != prev + 1)
                {
                    FlushRun(runs, true, buyLen, buyLow, buyHigh, buyVol, buyPoc, buyPocVol, minLevels);
                    FlushRun(runs, false, sellLen, sellLow, sellHigh, sellVol, sellPoc, sellPocVol, minLevels);
                    buyLen = sellLen = buyVol = sellVol = 0;
                }

                Cell cell = cells[level];
                Cell up, down;
                cells.TryGetValue(level + 1, out up);
                cells.TryGetValue(level - 1, out down);

                bool buy = cell.Ask > 0 && up != null && up.Bid > 0 && cell.Ask >= ratio * up.Bid;
                bool sell = cell.Bid > 0 && down != null && down.Ask > 0 && cell.Bid >= ratio * down.Ask;

                if (buy)
                {
                    if (buyLen == 0)
                    {
                        buyLow = level;
                        buyVol = 0;
                        buyPoc = level;
                        buyPocVol = 0;
                    }
                    buyHigh = level;
                    buyLen++;
                    buyVol += cell.Ask;
                    if (cell.Ask > buyPocVol)
                    {
                        buyPocVol = cell.Ask;
                        buyPoc = level;
                    }
                }
                else
                {
                    FlushRun(runs, true, buyLen, buyLow, buyHigh, buyVol, buyPoc, buyPocVol, minLevels);
                    buyLen = 0;
                    buyVol = 0;
                }

                if (sell)
                {
                    if (sellLen == 0)
                    {
                        sellLow = level;
                        sellVol = 0;
                        sellPoc = level;
                        sellPocVol = 0;
                    }
                    sellHigh = level;
                    sellLen++;
                    sellVol += cell.Bid;
                    if (cell.Bid > sellPocVol)
                    {
                        sellPocVol = cell.Bid;
                        sellPoc = level;
                    }
                }
                else
                {
                    FlushRun(runs, false, sellLen, sellLow, sellHigh, sellVol, sellPoc, sellPocVol, minLevels);
                    sellLen = 0;
                    sellVol = 0;
                }

                prev = level;
            }

            FlushRun(runs, true, buyLen, buyLow, buyHigh, buyVol, buyPoc, buyPocVol, minLevels);
            FlushRun(runs, false, sellLen, sellLow, sellHigh, sellVol, sellPoc, sellPocVol, minLevels);
            return runs;
        }

        private static void FlushRun(List<StackRun> runs, bool buy, int length, long low, long high,
            int volume, long anchor, int anchorVolume, int minLevels)
        {
            if (length < minLevels)
                return;

            runs.Add(new StackRun
            {
                Buy = buy,
                LowLevel = low,
                HighLevel = high,
                Length = length,
                Volume = volume,
                AnchorLevel = anchor,
                AnchorVolume = anchorVolume
            });
        }

        #endregion

        #region Zone arming and first test

        private void UpdateZones(FootprintBar bar)
        {
            double invalidationBuffer = Math.Max(_atr * 0.1, Symbol.TickSize * 2);

            foreach (Zone zone in _zones)
            {
                if (zone.Invalid || zone.Consumed)
                {
                    zone.AgeBars++;
                    continue;
                }
                if (bar.Start <= zone.CreatedBar)
                    continue;

                zone.AgeBars++;
                if (zone.AgeBars > ZoneMaxAgeBars)
                {
                    Invalidate(zone, "expired");
                    continue;
                }

                bool above = bar.Low > zone.High;
                bool below = bar.High < zone.Low;
                bool inside = !above && !below;

                if (!zone.Armed)
                {
                    if (zone.Bias == ZoneBias.Long)
                    {
                        if (above)
                            zone.ArmBars++;
                        else
                        {
                            Invalidate(zone, "returned before breakout");
                            continue;
                        }
                    }
                    else if (zone.Bias == ZoneBias.Short)
                    {
                        if (below)
                            zone.ArmBars++;
                        else
                        {
                            Invalidate(zone, "returned before breakout");
                            continue;
                        }
                    }
                    else
                    {
                        if (above)
                        {
                            if (zone.ArmSide == ZoneBias.Short)
                            {
                                Invalidate(zone, "crossed to the other side");
                                continue;
                            }
                            zone.ArmSide = ZoneBias.Long;
                            zone.ArmBars++;
                        }
                        else if (below)
                        {
                            if (zone.ArmSide == ZoneBias.Long)
                            {
                                Invalidate(zone, "crossed to the other side");
                                continue;
                            }
                            zone.ArmSide = ZoneBias.Short;
                            zone.ArmBars++;
                        }
                        else
                        {
                            Invalidate(zone, "returned before breakout");
                            continue;
                        }
                    }

                    if (zone.ArmBars < BreakoutBars)
                        continue;

                    if (zone.Bias == ZoneBias.Long)
                        zone.ArmSide = ZoneBias.Long;
                    else if (zone.Bias == ZoneBias.Short)
                        zone.ArmSide = ZoneBias.Short;

                    zone.Armed = true;
                    Print("ARM|{0}|{1}|{2}|bars={3}|low={4}|high={5}",
                        zone.Id, zone.Kind, zone.ArmSide, zone.ArmBars, F(zone.Low), F(zone.High));
                    continue;
                }

                // Armed: a close through the far side means the level failed before the pullback.
                if (inside)
                {
                    Invalidate(zone, "first test missed or crossed");
                    continue;
                }

                if (zone.ArmSide == ZoneBias.Long && bar.Close < zone.Low - invalidationBuffer)
                {
                    Invalidate(zone, "closed below zone");
                    continue;
                }
                if (zone.ArmSide == ZoneBias.Short && bar.Close > zone.High + invalidationBuffer)
                {
                    Invalidate(zone, "closed above zone");
                    continue;
                }
            }
        }

        private void Invalidate(Zone zone, string reason)
        {
            if (zone.Invalid)
                return;
            zone.Invalid = true;
            zone.InvalidReason = reason;
            Print("ZONE_INVALID|{0}|{1}|age={2}", zone.Id, reason, zone.AgeBars);
        }

        private void PruneZones()
        {
            for (int i = _zones.Count - 1; i >= 0; i--)
            {
                Zone zone = _zones[i];
                if ((zone.Invalid || zone.Consumed) && zone.AgeBars > 20)
                    _zones.RemoveAt(i);
            }

            while (_zones.Count > MaxActiveZones * 2)
            {
                int index = _zones.FindIndex(z => z.Invalid || z.Consumed);
                _zones.RemoveAt(index >= 0 ? index : 0);
            }
        }

        private void CheckTouches(double bid, double ask, double prevBid, double prevAsk, DateTime time)
        {
            if (_zones.Count == 0)
                return;

            double tol = TouchToleranceTicks * Symbol.TickSize;

            foreach (Zone zone in _zones.ToList())
            {
                if (!zone.Armed || zone.Consumed || zone.Invalid)
                    continue;

                bool fromAbove = prevBid > zone.High + tol && bid <= zone.High + tol && bid >= zone.Low - tol;
                bool fromBelow = prevAsk < zone.Low - tol && ask >= zone.Low - tol && ask <= zone.High + tol;

                // A Both zone locks the side the breakout happened on: a pullback from the other
                // side means price crossed the zone, which is already the first test.
                ZoneBias direction = zone.Bias == ZoneBias.Both ? zone.ArmSide : zone.Bias;

                if (direction == ZoneBias.Long)
                {
                    if (fromBelow)
                    {
                        Invalidate(zone, "tested from the wrong side");
                        continue;
                    }
                    if (!fromAbove)
                        continue;
                }
                else if (direction == ZoneBias.Short)
                {
                    if (fromAbove)
                    {
                        Invalidate(zone, "tested from the wrong side");
                        continue;
                    }
                    if (!fromBelow)
                        continue;
                }
                else
                {
                    continue;
                }

                zone.TouchCount++;
                Print("TOUCH|{0}|{1}|{2}|bid={3}|ask={4}|first={5}",
                    zone.Id, zone.Kind, fromAbove ? "above" : "below", F(bid), F(ask), zone.TouchCount == 1);

                zone.Consumed = true;

                if (_seeding)
                {
                    Print("SEED_TOUCH|{0}|first test already happened before the bot started", zone.Id);
                    continue;
                }

                TradeType side = fromAbove ? TradeType.Buy : TradeType.Sell;
                TryEnter(zone, side, fromAbove ? ask : bid, time);
            }
        }

        #endregion

        #region Entry

        private void TryEnter(Zone zone, TradeType side, double entry, DateTime time)
        {
            if (!_warmupReady)
                return;

            if (Positions.FindAll(BotId, SymbolName).Length > 0)
            {
                LogSkip(zone, "busy");
                return;
            }

            if (!InSession(time))
            {
                LogSkip(zone, "outside_session");
                return;
            }

            ResetDaily(time);
            if (_tradesToday >= MaxTradesPerDay)
            {
                LogSkip(zone, "daily_limit");
                return;
            }

            double spreadPips = (Symbol.Ask - Symbol.Bid) / Symbol.PipSize;
            if (spreadPips > MaxSpreadPips)
            {
                LogSkip(zone, $"spread {F(spreadPips)} > {F(MaxSpreadPips)}");
                return;
            }

            int confirmations = CountConfirmations(zone, side);
            if (confirmations < zone.ConfirmationsRequired)
            {
                LogSkip(zone, $"confirmations {confirmations}/{zone.ConfirmationsRequired}");
                return;
            }

            double sl, tp, slPips, tpPips;
            string reason;
            if (!ComputeProtection(zone, side, entry, out sl, out tp, out slPips, out tpPips, out reason))
            {
                LogSkip(zone, reason);
                return;
            }

            double dollarRisk;
            string refusal;
            double volume = VolumeForRisk(slPips, out dollarRisk, out refusal);
            if (volume <= 0)
            {
                LogSkip(zone, refusal ?? "volume_refused");
                return;
            }

            // The comment carries the initial stop in pips: after a break-even move the position's
            // SL is no longer the risk it was opened with, and a restart must not re-derive R from it.
            string comment = string.Format(CultureInfo.InvariantCulture, "OF|{0}|{1}|{2:F1}",
                zone.Kind, zone.Id, slPips);

            if (DryRun)
            {
                Print("ENTRY_DRY|{0}|{1}|{2}|entry={3}|sl={4}|tp={5}|slPips={6}|tpPips={7}|vol={8}|risk={9}|conf={10}",
                    zone.Id, zone.Kind, side, F(entry), F(sl), F(tp), F(slPips), F(tpPips),
                    F(volume / Symbol.LotSize), F(dollarRisk), confirmations);
                return;
            }

            TradeResult result = ExecuteMarketOrder(side, SymbolName, volume, BotId,
                Math.Round(slPips, 1), Math.Round(tpPips, 1), comment);

            if (result == null || !result.IsSuccessful || result.Position == null)
            {
                Print("ENTRY_FAIL|{0}|{1}|{2}", zone.Id, side,
                    result != null ? result.Error.ToString() : "no result");
                return;
            }

            Position position = result.Position;
            _initialSlPips[position.Id] = slPips;
            _activePositionId = position.Id;
            _beMoved.Remove(position.Id);
            _tradesToday++;

            Print("ENTRY|{0}|{1}|{2}|{3}|p={4}|entry={5}|sl={6}|tp={7}|slPips={8}|tpPips={9}|vol={10}|risk={11}|conf={12}|spread={13}",
                zone.Id, zone.Kind, side, zone.Armed ? zone.ArmSide : zone.Bias,
                position.Id, F(position.EntryPrice), F(sl), F(tp), F(slPips), F(tpPips),
                F(volume / Symbol.LotSize), F(dollarRisk), confirmations, F(spreadPips));

            ReportPositionOpen(position, slPips, tpPips,
                $"{zone.Kind} first test after {BreakoutBars}-bar breakout", zone, confirmations);
            SaveState();
        }

        private void LogSkip(Zone zone, string reason)
        {
            Print("SKIP|{0}|{1}|{2}", zone.Id, zone.Kind, reason);
        }

        private bool InSession(DateTime time)
        {
            if (TradeStartHourUtc == TradeEndHourUtc)
                return true;
            if (TradeStartHourUtc < TradeEndHourUtc)
                return time.Hour >= TradeStartHourUtc && time.Hour < TradeEndHourUtc;
            return time.Hour >= TradeStartHourUtc || time.Hour < TradeEndHourUtc;
        }

        private void ResetDaily(DateTime time)
        {
            if (time.Date == _tradeDate)
                return;
            _tradeDate = time.Date;
            _tradesToday = 0;
        }

        private int CountConfirmations(Zone zone, TradeType side)
        {
            if (zone.ConfirmationsRequired <= 0)
                return 0;

            if (!UseDeltaConfirm && !UseAbsorption && !UseDivergence)
                return zone.ConfirmationsRequired;

            int count = 0;
            if (UseDeltaConfirm && DeltaConfirms(side))
                count++;
            if (UseAbsorption && AbsorptionConfirms(zone))
                count++;
            if (UseDivergence && (side == TradeType.Buy ? BullishDivergence() : BearishDivergence()))
                count++;
            return count;
        }

        private bool DeltaConfirms(TradeType side)
        {
            int n = Math.Min(ConfirmBars, _m5Bars.Count);
            if (n <= 0)
                return false;

            double sum = 0;
            for (int i = _m5Bars.Count - n; i < _m5Bars.Count; i++)
                sum += _m5Bars[i].Delta;

            return side == TradeType.Buy ? sum > 0 : sum < 0;
        }

        /// <summary>
        /// Absorption: inside the zone, both sides printed heavily at the same prices compared
        /// with the bar's median cell. The book's "both bid and ask spike at the same price".
        /// </summary>
        private bool AbsorptionConfirms(Zone zone)
        {
            int n = Math.Min(AbsorptionBars, _m5Bars.Count);
            if (n <= 0)
                return false;

            int zoneLevels = Math.Max(1, (int)Math.Round((zone.High - zone.Low) / Symbol.TickSize) + 1);
            int hits = 0;

            for (int i = _m5Bars.Count - n; i < _m5Bars.Count; i++)
            {
                FootprintBar bar = _m5Bars[i];
                if (bar.MedianCell <= 0)
                    continue;

                long bid = 0, ask = 0;
                foreach (KeyValuePair<long, Cell> kv in bar.Cells)
                {
                    double price = PriceOf(kv.Key);
                    if (price < zone.Low || price > zone.High)
                        continue;
                    bid += kv.Value.Bid;
                    ask += kv.Value.Ask;
                }

                double threshold = AbsorptionFactor * bar.MedianCell * zoneLevels;
                if (bid > 0 && ask > 0 && bid + ask >= threshold)
                    hits++;
            }

            return hits >= 1;
        }

        private bool BullishDivergence()
        {
            if (_m1Bars.Count < DivergenceLookback + 2)
                return false;

            int start = _m1Bars.Count - DivergenceLookback;
            double minLow = double.MaxValue;
            double cdAtLow = 0;

            for (int i = start; i < _m1Bars.Count - 1; i++)
            {
                if (_m1Bars[i].Low < minLow)
                {
                    minLow = _m1Bars[i].Low;
                    cdAtLow = _m1Bars[i].CumulativeDelta;
                }
            }

            FootprintBar last = _m1Bars[_m1Bars.Count - 1];
            return last.Low <= minLow + Symbol.TickSize && last.CumulativeDelta > cdAtLow;
        }

        private bool BearishDivergence()
        {
            if (_m1Bars.Count < DivergenceLookback + 2)
                return false;

            int start = _m1Bars.Count - DivergenceLookback;
            double maxHigh = double.MinValue;
            double cdAtHigh = 0;

            for (int i = start; i < _m1Bars.Count - 1; i++)
            {
                if (_m1Bars[i].High > maxHigh)
                {
                    maxHigh = _m1Bars[i].High;
                    cdAtHigh = _m1Bars[i].CumulativeDelta;
                }
            }

            FootprintBar last = _m1Bars[_m1Bars.Count - 1];
            return last.High >= maxHigh - Symbol.TickSize && last.CumulativeDelta < cdAtHigh;
        }

        #endregion

        #region Protection and sizing

        private bool ComputeProtection(Zone zone, TradeType side, double entry,
            out double sl, out double tp, out double slPips, out double tpPips, out string reason)
        {
            sl = 0;
            tp = 0;
            slPips = 0;
            tpPips = 0;
            reason = null;

            if (!_hasAtr || _atr <= 0)
            {
                reason = "no_atr";
                return false;
            }

            double buffer = Math.Max(SlBufferAtr * _atr, Symbol.Spread + 2 * Symbol.TickSize);

            if (SlMode == OrderFlowSlMode.Area)
            {
                sl = side == TradeType.Buy ? zone.Low - buffer : zone.High + buffer;
            }
            else if (SlMode == OrderFlowSlMode.Fixed)
            {
                sl = side == TradeType.Buy
                    ? entry - FixedSlPips * Symbol.PipSize
                    : entry + FixedSlPips * Symbol.PipSize;
            }
            else
            {
                if (!TryLowVolumeStop(zone, side, out sl))
                    sl = side == TradeType.Buy ? zone.Low - buffer : zone.High + buffer;
            }

            double slDistance = side == TradeType.Buy ? entry - sl : sl - entry;
            if (slDistance <= 0)
            {
                reason = "bad_sl";
                return false;
            }

            slPips = slDistance / Symbol.PipSize;
            if (slPips < MinSlPips)
            {
                reason = $"sl {F(slPips)} < min {F(MinSlPips)}";
                return false;
            }

            if (TpNextHvn && TryNextHvn(zone, side, entry, out tp))
            {
                tp = side == TradeType.Buy
                    ? tp - TpBufferPips * Symbol.PipSize
                    : tp + TpBufferPips * Symbol.PipSize;
            }
            else
            {
                tp = side == TradeType.Buy
                    ? entry + MinRR * slDistance
                    : entry - MinRR * slDistance;
            }

            double tpDistance = side == TradeType.Buy ? tp - entry : entry - tp;
            if (tpDistance <= 0)
            {
                reason = "bad_tp";
                return false;
            }

            double rr = tpDistance / slDistance;
            if (rr + 1e-9 < MinRR)
            {
                reason = $"rr {F(rr)} < min {F(MinRR)}";
                return false;
            }

            sl = Math.Round(sl, Symbol.Digits);
            tp = Math.Round(tp, Symbol.Digits);
            slPips = Math.Abs(entry - sl) / Symbol.PipSize;
            tpPips = Math.Abs(tp - entry) / Symbol.PipSize;

            if (slPips <= 0 || tpPips <= 0)
            {
                reason = "rounded_to_zero";
                return false;
            }

            return true;
        }

        /// <summary>
        /// The book's third stop: put it behind the next low-volume pocket beyond the zone, so
        /// the zone's volume wall is between price and the stop.
        /// </summary>
        private bool TryLowVolumeStop(Zone zone, TradeType side, out double sl)
        {
            sl = 0;
            Dictionary<long, Cell> profile = AggregateProfile(ProfileBars);
            if (profile.Count < 3)
                return false;

            double median = MedianOf(profile);
            if (median <= 0)
                return false;

            long edge = LevelOf(side == TradeType.Buy ? zone.Low : zone.High);
            int direction = side == TradeType.Buy ? -1 : 1;
            double threshold = LowVolumeFraction * median;

            for (int i = 1; i <= MaxZoneTicks * 2; i++)
            {
                long level = edge + direction * i;
                Cell cell;
                profile.TryGetValue(level, out cell);
                int volume = cell != null ? cell.Total : 0;
                if (volume < threshold)
                {
                    sl = side == TradeType.Buy
                        ? PriceOf(level) - Symbol.TickSize
                        : PriceOf(level) + Symbol.TickSize;
                    return true;
                }
            }

            return false;
        }

        private bool TryNextHvn(Zone zone, TradeType side, double entry, out double hvnPrice)
        {
            hvnPrice = 0;
            Dictionary<long, Cell> profile = AggregateProfile(ProfileBars);
            if (profile.Count < 3)
                return false;

            double median = MedianOf(profile);
            if (median <= 0)
                return false;

            double threshold = TpHvnFactor * median;
            long entryLevel = LevelOf(entry);
            long zoneLow = LevelOf(zone.Low);
            long zoneHigh = LevelOf(zone.High);

            var levels = new List<long>(profile.Keys);
            levels.Sort();

            if (side == TradeType.Buy)
            {
                long floor = Math.Max(entryLevel, zoneHigh);
                foreach (long level in levels)
                {
                    if (level <= floor)
                        continue;
                    if (profile[level].Total >= threshold)
                    {
                        hvnPrice = PriceOf(level);
                        return true;
                    }
                }
            }
            else
            {
                long ceiling = Math.Min(entryLevel, zoneLow);
                for (int i = levels.Count - 1; i >= 0; i--)
                {
                    long level = levels[i];
                    if (level >= ceiling)
                        continue;
                    if (profile[level].Total >= threshold)
                    {
                        hvnPrice = PriceOf(level);
                        return true;
                    }
                }
            }

            return false;
        }

        private double VolumeForRisk(double slPips, out double dollarRisk, out string refusal)
        {
            refusal = null;
            dollarRisk = 0;

            double perUnitRisk = slPips * Symbol.PipValue;
            if (perUnitRisk <= 0)
            {
                refusal = "pip value is not positive";
                return 0;
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
                double riskAmount = Math.Min(Account.Balance * (RiskPerTradePercent / 100.0),
                    MaxDollarRiskPerTrade);
                units = Symbol.NormalizeVolumeInUnits(riskAmount / perUnitRisk, RoundingMode.Down);
                units = Math.Min(units, maxVolume);
                if (units < minVolume)
                {
                    double minLotRisk = minVolume * perUnitRisk;
                    if (minLotRisk > MaxDollarRiskPerTrade)
                    {
                        refusal = $"min lot risks ${F(minLotRisk)} > ${F(MaxDollarRiskPerTrade)}";
                        return 0;
                    }
                    units = minVolume;
                }
            }

            dollarRisk = units * perUnitRisk;
            return units;
        }

        #endregion

        #region Management

        private void ManageOpenPosition(Tick t)
        {
            Position position = Positions.FindAll(BotId, SymbolName).FirstOrDefault();
            if (position == null)
            {
                if (_activePositionId != 0)
                    _activePositionId = 0;
                return;
            }

            double slPips;
            if (!_initialSlPips.TryGetValue(position.Id, out slPips) || slPips <= 0)
                return;

            double risk = slPips * Symbol.PipSize;
            if (risk <= 0)
                return;

            double profit = position.TradeType == TradeType.Buy
                ? t.Bid - position.EntryPrice
                : position.EntryPrice - t.Ask;

            if (BreakEvenR > 0 && !_beMoved.Contains(position.Id) && profit >= BreakEvenR * risk)
            {
                double beBuffer = Math.Max(Symbol.Spread, Symbol.TickSize * 2);
                double be = position.TradeType == TradeType.Buy
                    ? position.EntryPrice + beBuffer
                    : position.EntryPrice - beBuffer;
                if (TryModify(position, be, "BE"))
                {
                    _beMoved.Add(position.Id);
                    Print("BE|{0}|{1}|sl={2}", position.Id, position.TradeType, F(be));
                }
            }

            if (UseAtrTrail && _hasAtr5 && _atr5 > 0 && profit >= TrailStartR * risk)
            {
                double trail = position.TradeType == TradeType.Buy
                    ? t.Bid - TrailAtrMult * _atr5
                    : t.Ask + TrailAtrMult * _atr5;
                if (TryModify(position, trail, "TRAIL"))
                    Print("TRAIL|{0}|{1}|sl={2}", position.Id, position.TradeType, F(trail));
            }
        }

        private bool TryModify(Position position, double newSl, string tag)
        {
            if ((Server.Time - _lastModifyTime).TotalSeconds < 5)
                return false;

            newSl = Math.Round(newSl, Symbol.Digits);

            double buffer = Math.Max(Symbol.Spread, Symbol.TickSize * 2);
            if (position.TradeType == TradeType.Buy && newSl >= Symbol.Bid - buffer)
                return false;
            if (position.TradeType == TradeType.Sell && newSl <= Symbol.Ask + buffer)
                return false;

            if (position.StopLoss.HasValue)
            {
                if (position.TradeType == TradeType.Buy && newSl <= position.StopLoss.Value + Symbol.TickSize * 0.5)
                    return false;
                if (position.TradeType == TradeType.Sell && newSl >= position.StopLoss.Value - Symbol.TickSize * 0.5)
                    return false;
            }

#pragma warning disable CS0618
            TradeResult result = ModifyPosition(position, newSl, position.TakeProfit, position.HasTrailingStop);
#pragma warning restore CS0618

            if (result != null && result.IsSuccessful)
            {
                _lastModifyTime = Server.Time;
                return true;
            }

            if (ShowLogs && result != null)
                Print("MODIFY_FAIL|{0}|{1}|{2}", position.Id, tag, result.Error);
            return false;
        }

        private void TryOrderFlowExit()
        {
            Position position = Positions.FindAll(BotId, SymbolName).FirstOrDefault();
            if (position == null)
                return;

            int first = _m5Bars.FindIndex(b => b.Start >= position.EntryTime);
            if (first < 0)
                return;

            int available = _m5Bars.Count - first;
            if (available < 2)
                return;

            int start = _m5Bars.Count - Math.Min(OrderFlowExitBars, available);

            bool deltaAgainst = true;
            bool stackAgainst = false;

            for (int i = start; i < _m5Bars.Count; i++)
            {
                FootprintBar bar = _m5Bars[i];
                if (position.TradeType == TradeType.Buy)
                {
                    if (bar.Delta >= 0)
                        deltaAgainst = false;
                    if (FindStackRuns(bar.Cells, StackRatio, StackMinLevels).Any(r => !r.Buy))
                        stackAgainst = true;
                }
                else
                {
                    if (bar.Delta <= 0)
                        deltaAgainst = false;
                    if (FindStackRuns(bar.Cells, StackRatio, StackMinLevels).Any(r => r.Buy))
                        stackAgainst = true;
                }
            }

            if (deltaAgainst && stackAgainst)
                CloseWithReason(position, "Order flow reversal (delta + opposite stack)");
        }

        private void CloseWithReason(Position position, string reason)
        {
            _closeReasons[position.Id] = reason;
            TradeResult result = ClosePosition(position);
            if (result == null || !result.IsSuccessful)
                Print("EXIT_FAIL|{0}|{1}", position.Id, result != null ? result.Error.ToString() : "no result");
        }

        #endregion

        #region Position events

        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            BeginInvokeOnMainThread(() =>
            {
                try
                {
                    Position position = args.Position;
                    if (position == null || position.Label != BotId || position.SymbolName != SymbolName)
                        return;

                    double exitPrice = position.TradeType == TradeType.Buy ? Symbol.Bid : Symbol.Ask;
                    if (args.Reason == PositionCloseReason.TakeProfit && position.TakeProfit.HasValue)
                        exitPrice = position.TakeProfit.Value;
                    else if (args.Reason == PositionCloseReason.StopLoss && position.StopLoss.HasValue)
                        exitPrice = position.StopLoss.Value;

                    string reason;
                    if (!_closeReasons.TryGetValue(position.Id, out reason))
                    {
                        if (args.Reason == PositionCloseReason.StopLoss)
                            reason = "Stop loss";
                        else if (args.Reason == PositionCloseReason.TakeProfit)
                            reason = "Take profit";
                        else
                            reason = "Closed";
                    }

                    double initialSl = _initialSlPips.TryGetValue(position.Id, out double sl) ? sl : 0;

                    Print("EXIT|{0}|{1}|{2}|pnl={3}|pips={4}|{5}",
                        position.Id, position.TradeType, position.SymbolName,
                        F(position.NetProfit), F(position.Pips), reason);

                    ReportPositionClosed(position, position.NetProfit, reason, exitPrice, position.Pips, initialSl);

                    _closeReasons.Remove(position.Id);
                    _initialSlPips.Remove(position.Id);
                    _beMoved.Remove(position.Id);
                    if (_activePositionId == position.Id)
                        _activePositionId = 0;
                }
                catch (Exception ex)
                {
                    Print("EXIT_ERROR|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
                }
            });
        }

        #endregion

        #region Reporting

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
                foreach (var p in Positions.FindAll(BotId, SymbolName)) openIds.Add(p.Id);

                var since = Server.Time.ToUniversalTime().AddHours(-ReconcileClosedHours);
                var seen = new HashSet<int>();
                int sent = 0;

                foreach (var deal in History)
                {
                    if (!string.Equals(deal.SymbolName, SymbolName, StringComparison.OrdinalIgnoreCase)) continue;
                    if (deal.ClosingTime.ToUniversalTime() < since) continue;
                    if (deal.Label != BotId) continue;
                    if (openIds.Contains(deal.PositionId)) continue;   // partial close: position still alive
                    if (!seen.Add(deal.PositionId)) continue;          // one report per position
                    SendCloseReportForDeal(deal);
                    sent++;
                }

                if (sent > 0)
                    Print("OF|reconcile|sent={0}|window_h={1}", sent, ReconcileClosedHours);
            }
            catch (Exception ex)
            {
                Print("OF|reconcile|error|{0}", OneLine(ex.Message));
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
                        Print("OF|reconcile|failed|pos={0}|{1}", deal.PositionId, error);
                });
            }
            catch (Exception ex)
            {
                Print("OF|reconcile|payload_error|pos={0}|{1}", deal.PositionId, OneLine(ex.Message));
            }
        }

        private string ReportUrl()
        {
            return !string.IsNullOrWhiteSpace(AiReportUrl)
                ? AiReportUrl.Trim()
                : ApiUrl.Replace("/trade", "/portfolio/report");
        }

        private void SendAccountSync()
        {
            if (RunningMode != RunningMode.RealTime)
                return;
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
                Print("REPORT_ERROR|sync|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
            }
        }

        private void ResumeOpenPositions()
        {
            foreach (Position position in Positions.FindAll(BotId, SymbolName))
            {
                double slPips = InitialSlFromComment(position);
                if (slPips <= 0 && position.StopLoss.HasValue)
                    slPips = Math.Abs(position.EntryPrice - position.StopLoss.Value) / Symbol.PipSize;
                double tpPips = position.TakeProfit.HasValue
                    ? Math.Abs(position.TakeProfit.Value - position.EntryPrice) / Symbol.PipSize
                    : 0;

                _initialSlPips[position.Id] = slPips;
                _activePositionId = position.Id;

                Print("RESUME|{0}|{1}|{2}|entry={3}|sl={4}|tp={5}|slPips={6}",
                    position.Id, position.TradeType, position.SymbolName,
                    F(position.EntryPrice), F(position.StopLoss ?? 0), F(position.TakeProfit ?? 0), F(slPips));

                ReportPositionOpen(position, slPips, tpPips, "Resumed after restart", null, 0);
            }
        }

        /// <summary>
        /// The open comment is OF|kind|zoneId|initialSlPips. A resumed position keeps the risk it
        /// was sized with even if its stop has already been moved to break-even.
        /// </summary>
        private static double InitialSlFromComment(Position position)
        {
            string comment = position.Comment;
            if (string.IsNullOrWhiteSpace(comment))
                return 0;

            string[] parts = comment.Split('|');
            if (parts.Length < 4)
                return 0;

            double pips;
            return double.TryParse(parts[3], NumberStyles.Float, CultureInfo.InvariantCulture, out pips)
                ? pips
                : 0;
        }

        private void ReportPositionOpen(Position position, double slPips, double tpPips, string reason,
            Zone zone, int confirmations)
        {
            if (RunningMode != RunningMode.RealTime)
                return;

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
                        setup = zone != null ? zone.Kind.ToString() : "Resume",
                        zone_low = zone != null ? Math.Round(zone.Low, Symbol.Digits) : 0.0,
                        zone_high = zone != null ? Math.Round(zone.High, Symbol.Digits) : 0.0,
                        zone_anchor = zone != null ? Math.Round(zone.Anchor, Symbol.Digits) : 0.0,
                        confirmations = confirmations,
                        breakout_bars = BreakoutBars,
                        atr = _hasAtr ? Math.Round(_atr, Symbol.Digits) : 0.0,
                        trend = TrendText(),
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
                    if (error != null)
                        Print("REPORT_ERROR|open|{0}|{1}", position.Id, error);
                });
            }
            catch (Exception ex)
            {
                Print("REPORT_ERROR|open|{0}|{1}|{2}", position.Id, ex.GetType().Name, OneLine(ex.Message));
            }
        }

        private void ReportPositionClosed(Position position, double pnl, string reason,
            double exitPrice, double pips, double initialSlPips)
        {
            if (RunningMode != RunningMode.RealTime)
                return;

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
                        Print("REPORT_ERROR|close|{0}|{1}", position.Id, error);
                });
            }
            catch (Exception ex)
            {
                Print("REPORT_ERROR|close|{0}|{1}|{2}", position.Id, ex.GetType().Name, OneLine(ex.Message));
            }
        }

        private static readonly int[] ReportRetryDelaysSeconds = { 2, 4, 8, 16 };

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
                    using (HttpResponseMessage response = await _httpClient.PostAsync(url, content))
                    {
                        if (response.IsSuccessStatusCode)
                            return null;
                        lastError = $"HTTP {(int)response.StatusCode}";
                    }
                }
                catch (Exception ex)
                {
                    lastError = ex.InnerException != null
                        ? $"{ex.Message} ({ex.InnerException.Message})"
                        : ex.Message;
                }
            }
            return $"{lastError} (after {ReportRetryDelaysSeconds.Length + 1} attempts)";
        }

        #endregion

        #region Helpers

        private string TrendText()
        {
            if (!_hasEmaFast || !_hasEmaSlow)
                return "unknown";
            if (_emaFast > _emaSlow)
                return "up";
            if (_emaFast < _emaSlow)
                return "down";
            return "flat";
        }

        private Dictionary<long, Cell> AggregateProfile(int bars)
        {
            var profile = new Dictionary<long, Cell>();
            int start = Math.Max(0, _m30Bars.Count - Math.Max(1, bars));
            for (int i = start; i < _m30Bars.Count; i++)
            {
                foreach (KeyValuePair<long, Cell> kv in _m30Bars[i].Cells)
                    AddCell(profile, kv.Key, kv.Value.Bid, kv.Value.Ask);
            }
            return profile;
        }

        private static double MedianOf(Dictionary<long, Cell> cells)
        {
            if (cells.Count == 0)
                return 0;
            var volumes = new List<int>(cells.Count);
            foreach (Cell cell in cells.Values)
                volumes.Add(cell.Total);
            volumes.Sort();
            return volumes[volumes.Count / 2];
        }

        private static void AddCell(Dictionary<long, Cell> cells, long level, int bid, int ask)
        {
            Cell cell;
            if (!cells.TryGetValue(level, out cell))
            {
                cell = new Cell();
                cells[level] = cell;
            }
            cell.Bid += bid;
            cell.Ask += ask;
        }

        private double PriceOf(long level)
        {
            return level * Symbol.TickSize;
        }

        private long LevelOf(double price)
        {
            return (long)Math.Round(price / Symbol.TickSize, MidpointRounding.AwayFromZero);
        }

        private static DateTime Floor(DateTime time, TimeSpan period)
        {
            long ticks = period.Ticks;
            return new DateTime(time.Ticks - time.Ticks % ticks, time.Kind);
        }

        private static string F(double value)
        {
            return value.ToString("0.#####", CultureInfo.InvariantCulture);
        }

        private static string OneLine(string text)
        {
            return (text ?? string.Empty).Replace("\r", " ").Replace("\n", " ").Replace("|", "/");
        }

        #endregion
    }
}
