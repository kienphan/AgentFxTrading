// Order Flow Probe: measures whether the cTrader tick feed can carry the footprint setups in
// "Order Flow" (Volume Clusters, Multiple Nodes, Stacked Imbalances, delta, absorption).
//
// The bot never trades and never calls the backend. It is an instrument: it loads as much tick
// history as the server will give it, builds an M30 (configurable) footprint from quote ticks,
// and prints stable, pipe-separated lines that answer the questions the design depends on:
//
//   PROBE_ENV            symbol, tick size, pip size, digits, period, bot id
//   PROBE_SERVER_FIRST   first tick time the server still has (GetServerFirstTime)
//   HIST_LOAD            one line per LoadMoreHistory() call: call, loaded, total, first, last
//   PROBE_HISTORY_DONE   calls, ticks, first/last loaded tick, load time, server first time
//   HIST_SUMMARY         bars scanned, ticks, scan time, classified/neutral/both shares, bid share
//   STAT|name|p50|p90|p99|max|n
//                        ticks per bar, price levels per bar, cell volume, |delta|, POC volume,
//                        ticks per UTC day
//   STACKS|ratio|bars|pct|max_run|total    stacked imbalances at 200/300/400 %, as the book
//                        uses 300 %: a bar counts if either side has a run of >= 3 levels
//   CLUSTERS|factor|bars|avg_runs|avg_levels|max_run    contiguous cells >= factor x the bar's
//                        median cell volume (Volume Cluster / Multiple Nodes calibration)
//   MULTINODE|bars|pct|max_run             bars whose POC repeats the previous bar's POC
//                        (within one tick), i.e. consecutive HVNs on the same level
//   GAP|rank|start|end|seconds             the five largest tick gaps (weekends, feed holes)
//   DOM|...              live depth: levels, best bid/ask, spread, totals, largest level
//   LIVE|...             ticks per second, spread average/p95, managed memory
//   FP_LIVE|...           each completed footprint bar: ticks, levels, delta, POC, stacks, build ms
//
// Why a tick-based footprint and not "volume": in cAlgo a Tick has only Bid, Ask and Time — no
// size and no traded volume (https://help.ctrader.com/ctrader-algo/references/MarketData/Ticks/Tick/).
// The platform's own footprint for FX is built from these quote ticks as well, so one tick is one
// unit of volume. Every tick is classified with the quote rule used by footprint charts:
//
//   ask rises  -> a buyer lifted the offer, +1 ask volume at the ask price
//   bid falls  -> a seller hit the bid,      +1 bid volume at the bid price
//   both       -> spread widened: one print on each side (counted in both)
//   neither    -> quote correction inside the spread, not counted as volume
//
// The probe reports what share of ticks the rule classifies, so the footprint's density is known
// before any signal is built on it. It also prints the DOM once per interval, which is the only
// way to know whether this broker exposes Level II for XAUUSD (the book's "Big Limit Orders"
// confirmation cannot be backtested and is live-only).
//
// Usage: run on any XAUUSD chart with --period=m30, --PeriodMinutes=30. Leave it for a session
// (DOM and LIVE lines need a live feed). The CSV is written only when WriteCsv is true.
//
// This is a measurement bot: it deliberately calls no trading API.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]
    public class OrderFlowProbeBot : Robot
    {
        #region Parameters

        [Parameter("Bot ID", Group = "General", DefaultValue = "ORDERFLOW_PROBE")]
        public string BotId { get; set; }

        [Parameter("Footprint Period (minutes)", Group = "Probe", DefaultValue = 30, MinValue = 1, MaxValue = 1440)]
        public int PeriodMinutes { get; set; }

        [Parameter("Load Step (seconds)", Group = "Probe", DefaultValue = 2, MinValue = 1, MaxValue = 60)]
        public int LoadStepSeconds { get; set; }

        [Parameter("Max Load Time (minutes)", Group = "Probe", DefaultValue = 10, MinValue = 1, MaxValue = 120)]
        public int MaxLoadMinutes { get; set; }

        [Parameter("Max Load Calls", Group = "Probe", DefaultValue = 300, MinValue = 10, MaxValue = 10000)]
        public int MaxLoadCalls { get; set; }

        [Parameter("Max Ticks To Scan (0 = all)", Group = "Probe", DefaultValue = 2000000, MinValue = 0, MaxValue = 100000000)]
        public int MaxTicksToScan { get; set; }

        [Parameter("DOM Interval (seconds)", Group = "Live", DefaultValue = 30, MinValue = 5, MaxValue = 3600)]
        public int DomIntervalSeconds { get; set; }

        [Parameter("Rate Interval (seconds)", Group = "Live", DefaultValue = 60, MinValue = 10, MaxValue = 3600)]
        public int RateIntervalSeconds { get; set; }

        [Parameter("Write CSV (Documents)", Group = "Reporting", DefaultValue = false)]
        public bool WriteCsv { get; set; }

        [Parameter("CSV Max Bars", Group = "Reporting", DefaultValue = 5000, MinValue = 100, MaxValue = 200000)]
        public int CsvMaxBars { get; set; }

        [Parameter("Verbose Bars", Group = "Reporting", DefaultValue = false)]
        public bool VerboseBars { get; set; }

        [Parameter("Verbose DOM", Group = "Reporting", DefaultValue = false)]
        public bool VerboseDom { get; set; }

        #endregion

        #region Constants and state types

        // The book enters on the first test of a 3-level stack at 300 %. The probe reports 200 %,
        // 300 % and 400 % so the threshold can be chosen from the data instead of guessed.
        private static readonly double[] StackRatios = { 2.0, 3.0, 4.0 };

        // A cluster is a run of adjacent price levels above the bar's median cell volume. The
        // book's "dark grey cells" have no numeric threshold, so the probe measures three.
        private static readonly double[] ClusterFactors = { 1.5, 2.0, 3.0 };

        private const int CellVolumeCap = 1_000_000;
        private const int MaxGaps = 5;
        private const int MaxSpreadWindow = 20000;

        private sealed class Cell
        {
            public int Bid;
            public int Ask;
            public int Total => Bid + Ask;
        }

        private sealed class BarAgg
        {
            public DateTime Start;
            public readonly Dictionary<long, Cell> Cells = new Dictionary<long, Cell>();
            public int Ticks;
            public int Buy;
            public int Sell;
            public int Both;
            public int Neutral;
        }

        private struct BarMetrics
        {
            public int Levels;
            public long TotalBid;
            public long TotalAsk;
            public int MaxCell;
            public long PocLevel;
            public int PocVol;
            public double MedianCell;
            public long Delta => TotalAsk - TotalBid;
        }

        private sealed class BarRow
        {
            public DateTime Start;
            public int Ticks;
            public int Levels;
            public long Bid;
            public long Ask;
            public int MaxCell;
            public long PocLevel;
            public int PocVol;
            public int MaxBuyRun;
            public int MaxSellRun;
            public int Stacks3;
            public int Cluster2Runs;
            public int Cluster2Levels;
            public double BuildMs;
        }

        #endregion

        #region Fields

        private Ticks _ticks;
        private MarketDepth _depth;
        private bool _depthAvailable;

        private bool _historyDone;
        private int _loadCalls;
        private int _zeroLoads;
        private readonly Stopwatch _loadWatch = new Stopwatch();
        private DateTime _serverFirst;
        private DateTime _startedUtc;

        private long _historyTicks;
        private bool _scanCapped;
        private bool _cellVolCapped;
        private readonly List<double> _cellVolumes = new List<double>(65536);
        private readonly List<double> _ticksPerBar = new List<double>();
        private readonly List<double> _levelsPerBar = new List<double>();
        private readonly List<double> _absDelta = new List<double>();
        private readonly List<double> _pocVolumes = new List<double>();
        private readonly List<double> _ticksPerDay = new List<double>();
        private readonly List<BarRow> _rows = new List<BarRow>();

        private readonly int[] _stackBars = new int[StackRatios.Length];
        private readonly int[] _stackMaxRun = new int[StackRatios.Length];
        private readonly int[] _stackTotal = new int[StackRatios.Length];
        private readonly int[] _clusterBars = new int[ClusterFactors.Length];
        private readonly double[] _clusterRuns = new double[ClusterFactors.Length];
        private readonly double[] _clusterLevels = new double[ClusterFactors.Length];
        private readonly int[] _clusterMaxRun = new int[ClusterFactors.Length];

        private long _bars;
        private long _totalBid;
        private long _totalAsk;
        private long _totalClassified;
        private long _totalNeutral;
        private long _totalBoth;
        private int _multiNodeBars;
        private int _multiNodeRun;
        private int _multiNodeMaxRun;
        private long _prevPocLevel = long.MinValue;

        private DateTime _firstHistoryTime;
        private DateTime _lastHistoryTime;

        private double _prevBid;
        private double _prevAsk;
        private bool _hasPrev;

        private Dictionary<long, Cell> _liveCells = new Dictionary<long, Cell>();
        private DateTime _liveBarStart;
        private bool _hasLiveBar;
        private int _liveBarTicks;
        private long _liveBars;
        private double _liveBuildMsSum;
        private int _liveBuildMsN;

        private long _liveTicks;
        private long _rateWindowTicks;
        private DateTime _rateWindowStart;
        private DateTime _lastDomUtc;
        private DateTime _lastRateUtc;
        private readonly List<double> _spreadWindow = new List<double>(4096);
        private double _maxMemoryMb;

        private TimeSpan _period;

        #endregion

        #region Lifecycle

        protected override void OnStart()
        {
            _startedUtc = DateTime.UtcNow;
            _period = TimeSpan.FromMinutes(PeriodMinutes);
            _loadWatch.Start();

            _ticks = MarketData.GetTicks(SymbolName);
            _ticks.Tick += OnTick;
            Print("PROBE_SERVER_FIRST|{0:o}", SafeServerFirstTime());

            try
            {
                _depth = MarketData.GetMarketDepth(SymbolName);
                _depthAvailable = true;
            }
            catch (Exception ex)
            {
                _depthAvailable = false;
                Print("DOM_ERROR|start|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
            }

            Print("PROBE_ENV|{0}|tick={1}|pip={2}|digits={3}|period={4}|bot={5}",
                SymbolName, F(Symbol.TickSize), F(Symbol.PipSize), Symbol.Digits, PeriodMinutes, BotId);
            Print("PROBE_START|{0:o}|utc", _startedUtc);

            Timer.Start(TimeSpan.FromSeconds(LoadStepSeconds));
        }

        protected override void OnStop()
        {
            double secs = Math.Max(1.0, (DateTime.UtcNow - _startedUtc).TotalSeconds);
            Print("PROBE_STOP|elapsed_s={0}|ticks={1}|bars={2}|avg_build_ms={3}|mem_mb={4}|dom={5}",
                F(secs), _liveTicks, _liveBars,
                _liveBuildMsN == 0 ? "0" : F(_liveBuildMsSum / _liveBuildMsN),
                F(CurrentMemoryMb()), _depthAvailable ? "yes" : "no");
        }

        #endregion

        #region History loading

        protected override void OnTimer()
        {
            if (_historyDone)
            {
                LiveDuties();
                return;
            }

            try
            {
                int loaded = _ticks.LoadMoreHistory();
                _loadCalls++;

                Print("HIST_LOAD|call={0}|loaded={1}|total={2}|first={3:o}|last={4:o}|elapsed_ms={5}",
                    _loadCalls, loaded, _ticks.Count, FirstTickTime(), LastTickTime(),
                    _loadWatch.ElapsedMilliseconds);

                if (loaded <= 0)
                {
                    _zeroLoads++;
                    if (_zeroLoads >= 2)
                        FinishHistory();
                }
                else
                {
                    _zeroLoads = 0;
                }
            }
            catch (Exception ex)
            {
                Print("HIST_ERROR|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
                FinishHistory();
                return;
            }

            if (!_historyDone &&
                (MaxTicksToScan > 0 && _ticks.Count >= MaxTicksToScan ||
                 _loadCalls >= MaxLoadCalls || _loadWatch.Elapsed.TotalMinutes >= MaxLoadMinutes))
                FinishHistory();
        }

        private void FinishHistory()
        {
            if (_historyDone)
                return;

            _historyDone = true;
            _loadWatch.Stop();
            _serverFirst = SafeServerFirstTime();

            Print("PROBE_HISTORY_DONE|calls={0}|ticks={1}|first={2:o}|last={3:o}|load_ms={4}|server_first={5:o}",
                _loadCalls, _ticks.Count, FirstTickTime(), LastTickTime(),
                _loadWatch.ElapsedMilliseconds, _serverFirst);

            AnalyzeHistory();

            DateTime now = DateTime.UtcNow;
            _rateWindowStart = now;
            _lastRateUtc = now;
            _lastDomUtc = now;
            _rateWindowTicks = _liveTicks;
        }

        private void AnalyzeHistory()
        {
            int total = _ticks.Count;
            int from = 0;
            if (MaxTicksToScan > 0 && total > MaxTicksToScan)
            {
                from = total - MaxTicksToScan;
                _scanCapped = true;
            }
            _historyTicks = total - from;

            if (total - from < 2)
            {
                Print("HIST_SUMMARY|ticks={0}|error=no_tick_history", total);
                return;
            }

            var scan = Stopwatch.StartNew();
            Print("SCAN_START|from={0}|to={1}|ticks={2}", from, total, total - from);

            var dayCounts = new Dictionary<DateTime, long>();
            var gaps = new List<double[]>();
            var bar = (BarAgg)null;
            DateTime prevTime = default;

            for (int i = from; i < total; i++)
            {
                Tick t = _ticks[i];
                if (i == from)
                    _firstHistoryTime = t.Time;
                _lastHistoryTime = t.Time;

                DateTime day = t.Time.Date;
                long dayTicks;
                dayCounts.TryGetValue(day, out dayTicks);
                dayCounts[day] = dayTicks + 1;

                if (prevTime != default && t.Time > prevTime)
                {
                    double gap = (t.Time - prevTime).TotalSeconds;
                    if (gap >= 120 && (gaps.Count < MaxGaps || gap > gaps[gaps.Count - 1][0]))
                    {
                        gaps.Add(new double[] { gap, prevTime.Ticks, t.Time.Ticks });
                        gaps.Sort((a, b) => b[0].CompareTo(a[0]));
                        if (gaps.Count > MaxGaps)
                            gaps.RemoveAt(gaps.Count - 1);
                    }
                }
                prevTime = t.Time;

                DateTime target = Floor(t.Time, _period);
                if (bar == null)
                {
                    bar = new BarAgg { Start = target };
                }
                else if (target > bar.Start)
                {
                    // A session gap: close the last bar and jump to the bar that owns this tick.
                    // Empty bars in the gap are not recorded, so a weekend does not become 96
                    // zero-volume bars in the percentiles.
                    if (bar.Ticks > 0)
                        FinishHistoryBar(bar);
                    bar = new BarAgg { Start = target };
                }

                AddHistoryTick(bar, t);
            }

            if (bar != null && bar.Ticks > 0)
                FinishHistoryBar(bar);

            foreach (long c in dayCounts.Values)
                _ticksPerDay.Add(c);

            scan.Stop();

            double memMb = CurrentMemoryMb();
            if (memMb > _maxMemoryMb)
                _maxMemoryMb = memMb;

            Print("HIST_SUMMARY|bars={0}|ticks={1}|first={2:o}|last={3:o}|scan_ms={4}|server_first={5:o}|mem_mb={6}|scan_capped={7}",
                _bars, _historyTicks, _firstHistoryTime, _lastHistoryTime, scan.ElapsedMilliseconds,
                _serverFirst, F(memMb), _scanCapped ? "yes" : "no");
            Print("MIX|classified={0}|neutral={1}|both={2}|bid_share={3}|cellvol_capped={4}",
                Share(_totalClassified, _historyTicks), Share(_totalNeutral, _historyTicks),
                Share(_totalBoth, _historyTicks), Share(_totalBid, _totalBid + _totalAsk),
                _cellVolCapped ? "yes" : "no");

            PrintStat("ticks_per_bar", _ticksPerBar);
            PrintStat("levels_per_bar", _levelsPerBar);
            PrintStat("cell_vol", _cellVolumes);
            PrintStat("abs_delta", _absDelta);
            PrintStat("poc_vol", _pocVolumes);
            PrintStat("ticks_per_day", _ticksPerDay);

            for (int r = 0; r < StackRatios.Length; r++)
            {
                Print("STACKS|r={0}|bars={1}|pct={2}|max_run={3}|total={4}",
                    F(StackRatios[r]), _stackBars[r], Share(_stackBars[r], _bars),
                    _stackMaxRun[r], _stackTotal[r]);
            }

            for (int f = 0; f < ClusterFactors.Length; f++)
            {
                Print("CLUSTERS|f={0}|bars={1}|avg_runs={2}|avg_levels={3}|max_run={4}",
                    F(ClusterFactors[f]), _clusterBars[f],
                    _clusterBars[f] == 0 ? "0" : F(_clusterRuns[f] / _clusterBars[f]),
                    _clusterBars[f] == 0 ? "0" : F(_clusterLevels[f] / _clusterBars[f]),
                    _clusterMaxRun[f]);
            }

            Print("MULTINODE|bars={0}|pct={1}|max_run={2}",
                _multiNodeBars, Share(_multiNodeBars, _bars), _multiNodeMaxRun);

            for (int g = 0; g < gaps.Count; g++)
            {
                Print("GAP|{0}|{1:o}|{2:o}|{3}",
                    g + 1, new DateTime((long)gaps[g][1], DateTimeKind.Utc),
                    new DateTime((long)gaps[g][2], DateTimeKind.Utc), F(gaps[g][0]));
            }

            if (_rows.Count > 0)
            {
                BarRow last = _rows[_rows.Count - 1];
                Print("LAST_BAR|{0:o}|ticks={1}|levels={2}|delta={3}|poc={4:o}",
                    last.Start, last.Ticks, last.Levels, last.Ask - last.Bid, last.PocLevel);
            }

            if (WriteCsv)
                WriteHistoryCsv(_rows);

            Print("SCAN_END|scan_ms={0}|bars={1}", scan.ElapsedMilliseconds, _bars);
        }

        private void AddHistoryTick(BarAgg bar, Tick t)
        {
            bar.Ticks++;
            int side = AddTickToCells(bar.Cells, t);

            if (side == 3)
            {
                bar.Both++;
                _totalBoth++;
                _totalClassified++;
            }
            else if (side == 1)
            {
                bar.Buy++;
                _totalClassified++;
            }
            else if (side == 2)
            {
                bar.Sell++;
                _totalClassified++;
            }
            else
            {
                bar.Neutral++;
                _totalNeutral++;
            }
        }

        private void FinishHistoryBar(BarAgg bar)
        {
            var watch = Stopwatch.StartNew();
            BarMetrics m = ComputeMetrics(bar.Cells);

            _ticksPerBar.Add(bar.Ticks);
            _levelsPerBar.Add(m.Levels);
            _absDelta.Add(Math.Abs(m.Delta));
            _pocVolumes.Add(m.PocVol);
            _totalBid += m.TotalBid;
            _totalAsk += m.TotalAsk;

            var row = new BarRow
            {
                Start = bar.Start,
                Ticks = bar.Ticks,
                Levels = m.Levels,
                Bid = m.TotalBid,
                Ask = m.TotalAsk,
                MaxCell = m.MaxCell,
                PocLevel = m.PocLevel,
                PocVol = m.PocVol
            };

            for (int r = 0; r < StackRatios.Length; r++)
            {
                int buyRun, sellRun, buyStacks, sellStacks;
                StackStats(bar.Cells, StackRatios[r], out buyRun, out sellRun, out buyStacks, out sellStacks);

                int run = Math.Max(buyRun, sellRun);
                if (run > _stackMaxRun[r])
                    _stackMaxRun[r] = run;
                if (run >= 3)
                    _stackBars[r]++;

                _stackTotal[r] += buyStacks + sellStacks;

                if (r == 1)
                {
                    row.MaxBuyRun = buyRun;
                    row.MaxSellRun = sellRun;
                    row.Stacks3 = buyStacks + sellStacks;
                }
            }

            for (int f = 0; f < ClusterFactors.Length; f++)
            {
                int runs, levels, maxRun;
                ClusterStats(bar.Cells, ClusterFactors[f], m.MedianCell, out runs, out levels, out maxRun);

                if (runs > 0)
                    _clusterBars[f]++;
                _clusterRuns[f] += runs;
                _clusterLevels[f] += levels;
                if (maxRun > _clusterMaxRun[f])
                    _clusterMaxRun[f] = maxRun;

                if (f == 1)
                {
                    row.Cluster2Runs = runs;
                    row.Cluster2Levels = levels;
                }
            }

            if (!_cellVolCapped)
            {
                foreach (Cell cell in bar.Cells.Values)
                {
                    if (_cellVolumes.Count >= CellVolumeCap)
                    {
                        _cellVolCapped = true;
                        break;
                    }
                    _cellVolumes.Add(cell.Total);
                }
            }

            if (_prevPocLevel != long.MinValue && Math.Abs(m.PocLevel - _prevPocLevel) <= 1)
            {
                _multiNodeBars++;
                _multiNodeRun++;
                if (_multiNodeRun > _multiNodeMaxRun)
                    _multiNodeMaxRun = _multiNodeRun;
            }
            else
            {
                _multiNodeRun = 1;
            }
            _prevPocLevel = m.PocLevel;

            _bars++;
            watch.Stop();
            row.BuildMs = watch.Elapsed.TotalMilliseconds;
            if (_rows.Count < CsvMaxBars)
                _rows.Add(row);

            PrintVerboseBar(row);
        }

        #endregion

        #region Live

        private void OnTick(TicksTickEventArgs args)
        {
            Tick t = args.Ticks.LastTick;
            _liveTicks++;

            if (!_historyDone)
                return;

            double spread = t.Ask - t.Bid;
            double pips = Symbol.PipSize > 0 ? spread / Symbol.PipSize : spread;
            if (_spreadWindow.Count < MaxSpreadWindow)
                _spreadWindow.Add(pips);

            DateTime target = Floor(t.Time, _period);
            if (!_hasLiveBar)
            {
                _liveBarStart = target;
                _hasLiveBar = true;
            }
            else if (target > _liveBarStart)
            {
                FinalizeLiveBar();
                _liveBarStart = target;
            }

            AddTickToCells(_liveCells, t);
            _liveBarTicks++;
        }

        private void FinalizeLiveBar()
        {
            if (_liveBarTicks == 0)
            {
                _liveCells = new Dictionary<long, Cell>();
                return;
            }

            var watch = Stopwatch.StartNew();
            BarMetrics m = ComputeMetrics(_liveCells);

            int buyRun, sellRun, buyStacks, sellStacks;
            StackStats(_liveCells, 3.0, out buyRun, out sellRun, out buyStacks, out sellStacks);

            watch.Stop();
            _liveBars++;
            _liveBuildMsSum += watch.Elapsed.TotalMilliseconds;
            _liveBuildMsN++;

            Print("FP_LIVE|{0:o}|ticks={1}|levels={2}|bid={3}|ask={4}|delta={5}|poc={6:o}|poc_vol={7}|max_cell={8}|buy_run={9}|sell_run={10}|stacks3={11}|build_ms={12}",
                _liveBarStart, _liveBarTicks, m.Levels, m.TotalBid, m.TotalAsk, m.Delta,
                m.PocLevel, m.PocVol, m.MaxCell, buyRun, sellRun, buyStacks + sellStacks,
                F(watch.Elapsed.TotalMilliseconds));

            _liveCells = new Dictionary<long, Cell>();
            _liveBarTicks = 0;
        }

        private void LiveDuties()
        {
            DateTime now = DateTime.UtcNow;

            if ((now - _lastDomUtc).TotalSeconds >= DomIntervalSeconds)
            {
                _lastDomUtc = now;
                LogDepth();
            }

            if ((now - _lastRateUtc).TotalSeconds >= RateIntervalSeconds)
            {
                double secs = Math.Max(1.0, (now - _rateWindowStart).TotalSeconds);
                long ticks = _liveTicks - _rateWindowTicks;
                LogRate(ticks / secs);
                _lastRateUtc = now;
                _rateWindowStart = now;
                _rateWindowTicks = _liveTicks;
            }

            double mem = CurrentMemoryMb();
            if (mem > _maxMemoryMb)
                _maxMemoryMb = mem;
        }

        private void LogRate(double ticksPerSecond)
        {
            double avg = 0;
            double p95 = 0;
            if (_spreadWindow.Count > 0)
            {
                _spreadWindow.Sort();
                double sum = 0;
                foreach (double s in _spreadWindow)
                    sum += s;
                avg = sum / _spreadWindow.Count;
                p95 = _spreadWindow[(int)Math.Min(_spreadWindow.Count - 1,
                    Math.Round((_spreadWindow.Count - 1) * 0.95))];
            }

            Print("LIVE|ticks_per_s={0}|spread_avg={1}|spread_p95={2}|spread_samples={3}|mem_mb={4}|bars={5}",
                F(ticksPerSecond), F(avg), F(p95), _spreadWindow.Count, F(CurrentMemoryMb()), _liveBars);
            _spreadWindow.Clear();
        }

        private void LogDepth()
        {
            if (!_depthAvailable || _depth == null)
            {
                Print("DOM|available=no");
                return;
            }

            try
            {
                int bids = _depth.BidEntries.Count;
                int asks = _depth.AskEntries.Count;
                double bidTotal = 0, askTotal = 0;
                double bidMax = 0, askMax = 0;
                double bestBid = 0, bestAsk = 0;

                for (int i = 0; i < bids; i++)
                {
                    var e = _depth.BidEntries[i];
                    bidTotal += e.Volume;
                    if (e.Volume > bidMax) bidMax = e.Volume;
                    if (i == 0) bestBid = e.Price;
                }
                for (int i = 0; i < asks; i++)
                {
                    var e = _depth.AskEntries[i];
                    askTotal += e.Volume;
                    if (e.Volume > askMax) askMax = e.Volume;
                    if (i == 0) bestAsk = e.Price;
                }

                Print("DOM|available={0}|bid_levels={1}|ask_levels={2}|best_bid={3}|best_ask={4}|spread_pips={5}|bid_total={6}|ask_total={7}|bid_max={8}|ask_max={9}",
                    bids + asks > 0 ? "yes" : "empty", bids, asks, F(bestBid), F(bestAsk),
                    F(Symbol.PipSize > 0 ? (bestAsk - bestBid) / Symbol.PipSize : bestAsk - bestBid),
                    F(bidTotal), F(askTotal), F(bidMax), F(askMax));

                if (VerboseDom)
                {
                    for (int i = 0; i < bids; i++)
                        Print("DOM_BID|{0}|{1}|{2}", i, F(_depth.BidEntries[i].Price), F(_depth.BidEntries[i].Volume));
                    for (int i = 0; i < asks; i++)
                        Print("DOM_ASK|{0}|{1}|{2}", i, F(_depth.AskEntries[i].Price), F(_depth.AskEntries[i].Volume));
                }
            }
            catch (Exception ex)
            {
                _depthAvailable = false;
                Print("DOM_ERROR|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
            }
        }

        #endregion

        #region Footprint math

        /// <summary>
        /// Classifies one quote tick and adds its unit of volume to the footprint. Returns
        /// 0 = neutral, 1 = buy at ask, 2 = sell at bid, 3 = both (spread widened).
        /// </summary>
        private int AddTickToCells(Dictionary<long, Cell> cells, Tick t)
        {
            if (!_hasPrev)
            {
                _hasPrev = true;
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
            {
                AddCell(cells, LevelOf(t.Ask), 0, 1);
                AddCell(cells, LevelOf(t.Bid), 1, 0);
                side = 3;
            }
            else if (buy)
            {
                AddCell(cells, LevelOf(t.Ask), 0, 1);
                side = 1;
            }
            else if (sell)
            {
                AddCell(cells, LevelOf(t.Bid), 1, 0);
                side = 2;
            }
            else
            {
                side = 0;
            }

            _prevBid = t.Bid;
            _prevAsk = t.Ask;
            return side;
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

        private BarMetrics ComputeMetrics(Dictionary<long, Cell> cells)
        {
            var m = new BarMetrics();
            m.Levels = cells.Count;
            if (cells.Count == 0)
                return m;

            var volumes = new List<double>(cells.Count);
            foreach (KeyValuePair<long, Cell> kv in cells)
            {
                m.TotalBid += kv.Value.Bid;
                m.TotalAsk += kv.Value.Ask;
                int total = kv.Value.Total;
                volumes.Add(total);
                if (total > m.MaxCell)
                {
                    m.MaxCell = total;
                    m.PocLevel = kv.Key;
                    m.PocVol = total;
                }
            }

            volumes.Sort();
            m.MedianCell = volumes[volumes.Count / 2];
            return m;
        }

        /// <summary>
        /// Runs of adjacent price levels with a diagonal bid/ask imbalance of at least
        /// <paramref name="ratio"/>: a buy stack needs ask(p) >= ratio*bid(p+1), a sell stack
        /// needs bid(p) >= ratio*ask(p-1). Counts every run of 3 or more levels.
        /// </summary>
        private static void StackStats(Dictionary<long, Cell> cells, double ratio,
            out int buyRun, out int sellRun, out int buyStacks, out int sellStacks)
        {
            buyRun = 0; sellRun = 0; buyStacks = 0; sellStacks = 0;
            if (cells.Count == 0)
                return;

            var levels = new List<long>(cells.Keys);
            levels.Sort();

            int curBuy = 0, curSell = 0, maxBuy = 0, maxSell = 0;
            long prev = long.MinValue;

            foreach (long level in levels)
            {
                Cell cell = cells[level];
                Cell up, down;
                cells.TryGetValue(level + 1, out up);
                cells.TryGetValue(level - 1, out down);

                if (prev != long.MinValue && level != prev + 1)
                {
                    if (curBuy >= 3) buyStacks++;
                    if (curSell >= 3) sellStacks++;
                    curBuy = 0;
                    curSell = 0;
                }

                bool buy = cell.Ask > 0 && up != null && up.Bid > 0 && cell.Ask >= ratio * up.Bid;
                bool sell = cell.Bid > 0 && down != null && down.Ask > 0 && cell.Bid >= ratio * down.Ask;

                if (buy)
                {
                    curBuy++;
                    if (curBuy > maxBuy) maxBuy = curBuy;
                }
                else
                {
                    if (curBuy >= 3) buyStacks++;
                    curBuy = 0;
                }

                if (sell)
                {
                    curSell++;
                    if (curSell > maxSell) maxSell = curSell;
                }
                else
                {
                    if (curSell >= 3) sellStacks++;
                    curSell = 0;
                }

                prev = level;
            }

            if (curBuy >= 3) buyStacks++;
            if (curSell >= 3) sellStacks++;

            buyRun = maxBuy;
            sellRun = maxSell;
        }

        /// <summary>
        /// Contiguous hot cells (volume >= factor x the bar's median cell volume). A run needs at
        /// least two levels, matching "a cluster of dark cells", not a single spike.
        /// </summary>
        private static void ClusterStats(Dictionary<long, Cell> cells, double factor, double medianCell,
            out int runs, out int levels, out int maxRun)
        {
            runs = 0; levels = 0; maxRun = 0;
            if (cells.Count == 0)
                return;

            double threshold = factor * medianCell;
            if (threshold <= 0)
                return;

            var keys = new List<long>(cells.Keys);
            keys.Sort();

            int run = 0;
            long prev = long.MinValue;
            foreach (long level in keys)
            {
                if (prev != long.MinValue && level != prev + 1)
                {
                    if (run >= 2) runs++;
                    run = 0;
                }

                if (cells[level].Total >= threshold)
                {
                    run++;
                    levels++;
                    if (run > maxRun) maxRun = run;
                }
                else
                {
                    if (run >= 2) runs++;
                    run = 0;
                }
                prev = level;
            }

            if (run >= 2) runs++;
        }

        #endregion

        #region Helpers

        private static DateTime Floor(DateTime time, TimeSpan period)
        {
            long ticks = period.Ticks;
            return new DateTime(time.Ticks - time.Ticks % ticks, time.Kind);
        }

        private long LevelOf(double price)
        {
            return (long)Math.Round(price / Symbol.TickSize, MidpointRounding.AwayFromZero);
        }

        private DateTime FirstTickTime()
        {
            return _ticks != null && _ticks.Count > 0 ? _ticks[0].Time : default(DateTime);
        }

        private DateTime LastTickTime()
        {
            return _ticks != null && _ticks.Count > 0 ? _ticks.LastTick.Time : default(DateTime);
        }

        private DateTime SafeServerFirstTime()
        {
            try
            {
                return _ticks != null ? _ticks.GetServerFirstTime() : default(DateTime);
            }
            catch
            {
                return default(DateTime);
            }
        }

        private static double CurrentMemoryMb()
        {
            return GC.GetTotalMemory(false) / 1048576.0;
        }

        private void PrintStat(string name, List<double> values)
        {
            if (values.Count == 0)
            {
                Print("STAT|{0}|0|0|0|0|0", name);
                return;
            }

            values.Sort();
            Print("STAT|{0}|{1}|{2}|{3}|{4}|{5}", name,
                F(Pct(values, 0.50)), F(Pct(values, 0.90)), F(Pct(values, 0.99)),
                F(values[values.Count - 1]), values.Count);
        }

        private static double Pct(List<double> sorted, double p)
        {
            if (sorted.Count == 0)
                return 0;
            int index = (int)Math.Round((sorted.Count - 1) * p);
            if (index < 0) index = 0;
            if (index >= sorted.Count) index = sorted.Count - 1;
            return sorted[index];
        }

        private static string Share(long part, long total)
        {
            return total <= 0 ? "0" : F((double)part / total);
        }

        private static string F(double value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        private static string OneLine(string text)
        {
            return (text ?? string.Empty).Replace("\r", " ").Replace("\n", " ").Replace("|", "/");
        }

        private void PrintVerboseBar(BarRow row)
        {
            if (!VerboseBars)
                return;

            Print("HIST_BAR|{0:o}|ticks={1}|levels={2}|bid={3}|ask={4}|delta={5}|poc={6:o}|poc_vol={7}|max_cell={8}|buy_run={9}|sell_run={10}|stacks3={11}|build_ms={12}",
                row.Start, row.Ticks, row.Levels, row.Bid, row.Ask, row.Ask - row.Bid,
                row.PocLevel, row.PocVol, row.MaxCell, row.MaxBuyRun, row.MaxSellRun, row.Stacks3,
                F(row.BuildMs));
        }

        private void WriteHistoryCsv(List<BarRow> rows)
        {
            try
            {
                string dir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "OrderFlowProbe");
                Directory.CreateDirectory(dir);
                string path = Path.Combine(dir, string.Format(
                    CultureInfo.InvariantCulture, "{0}_{1:yyyyMMdd_HHmmss}.csv", SymbolName, _startedUtc));

                var sb = new StringBuilder();
                sb.AppendLine("bar_start_utc,ticks,levels,bid,ask,delta,max_cell,poc_level,poc_vol,max_buy_run,max_sell_run,stacks3,cluster2_runs,cluster2_levels,build_ms");
                foreach (BarRow r in rows)
                {
                    sb.AppendLine(string.Join(",", new[]
                    {
                        r.Start.ToString("o", CultureInfo.InvariantCulture),
                        r.Ticks.ToString(CultureInfo.InvariantCulture),
                        r.Levels.ToString(CultureInfo.InvariantCulture),
                        r.Bid.ToString(CultureInfo.InvariantCulture),
                        r.Ask.ToString(CultureInfo.InvariantCulture),
                        (r.Ask - r.Bid).ToString(CultureInfo.InvariantCulture),
                        r.MaxCell.ToString(CultureInfo.InvariantCulture),
                        r.PocLevel.ToString(CultureInfo.InvariantCulture),
                        r.PocVol.ToString(CultureInfo.InvariantCulture),
                        r.MaxBuyRun.ToString(CultureInfo.InvariantCulture),
                        r.MaxSellRun.ToString(CultureInfo.InvariantCulture),
                        r.Stacks3.ToString(CultureInfo.InvariantCulture),
                        r.Cluster2Runs.ToString(CultureInfo.InvariantCulture),
                        r.Cluster2Levels.ToString(CultureInfo.InvariantCulture),
                        F(r.BuildMs)
                    }));
                }

                System.IO.File.WriteAllText(path, sb.ToString(), Encoding.UTF8);
                Print("CSV|path={0}|rows={1}", path, rows.Count);
            }
            catch (Exception ex)
            {
                Print("CSV_ERROR|{0}|{1}", ex.GetType().Name, OneLine(ex.Message));
            }
        }

        #endregion
    }
}
