/* Backtest page (docs/superpowers/specs/2026-09-24-backtest-page-design.md).
 *
 * Loaded with `defer`, after the dashboard's inline script whose escapeHtml it uses. The bt*Html
 * functions are pure (data in, HTML out) so tests/test_backtest_ui.py can render them in node with
 * hostile strings; everything that touches the DOM, fetch or Chart.js lives below them. */

const BT_POLL_MS = 3000;
const BT_WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
// [summary key, label, which value is best: 'max' | 'min' | 'zero' (closest to 0) | null, format]
// cTrader's own statistics count closing deals, so a partial close makes one position two "trades";
// the position rows regroup them (app/backtest_report.py).
const BT_COMPARE_ROWS = [
    ['net_profit', 'Net P&amp;L', 'max', 'money'],
    ['roi_pct', 'ROI %', 'max', 'num'],
    ['profit_factor', 'Profit factor', 'max', 'num'],
    ['position_win_rate', 'Win rate % (positions)', 'max', 'num'],
    ['positions', 'Positions', null, 'int'],
    ['avg_position_win', 'Avg win (position)', 'max', 'money'],
    ['avg_position_loss', 'Avg loss (position)', 'zero', 'money'],
    ['full_loss_pct', 'Full-size losses %', 'min', 'num'],
    ['win_rate', 'Win rate % (deals)', 'max', 'num'],
    ['total_trades', 'Deals', null, 'int'],
    ['avg_trade', 'Avg deal', 'max', 'money'],
    ['avg_win', 'Avg win (deal)', 'max', 'money'],
    ['avg_loss', 'Avg loss (deal)', 'zero', 'money'],
    ['largest_loss', 'Largest loss (deal)', 'zero', 'money'],
    ['max_equity_dd_pct', 'Max equity DD %', 'min', 'pct'],
    ['commissions', 'Commissions', 'zero', 'money'],
    ['swaps', 'Swaps', 'zero', 'money'],
];
const BT_IGNORED_PARAMS = new Set(['BotId', 'AccountLabel']);
const BT_PAGE_SIZES = [10, 20, 50];
const bt = {sources: [], view: null, overrides: {}, pendingOverrides: null, jobs: [], selected: new Set(),
            pollTimer: null, charts: {}, loaded: false, helpBtn: null,
            page: 1, perPage: 20, total: 0, modalJobId: null};

// ---------- formatting ----------

function btNum(v, digits = 2) {
    const n = Number(v);
    return v !== null && v !== '' && Number.isFinite(n) ? n.toFixed(digits) : '—';
}

function btMoney(v) {
    const n = Number(v);
    if (v === null || v === '' || !Number.isFinite(n)) return '—';
    return `${n < 0 ? '−' : n > 0 ? '+' : ''}$${Math.abs(n).toFixed(2)}`;
}

function btPct(v, digits = 1) {
    const text = btNum(v, digits);
    return text === '—' ? text : `${text}%`;
}

function btFormat(v, kind) {
    if (kind === 'money') return btMoney(v);
    if (kind === 'int') return btNum(v, 0);
    return btNum(v);
}

function btSignClass(v) {
    const n = Number(v);
    return n > 0 ? 'kpi-value--profit' : n < 0 ? 'kpi-value--loss' : '';
}

function btDateText(iso) {
    const [y, m, d] = String(iso || '').split('-');
    return d ? `${d}/${m}/${y}` : String(iso || '');
}

// ---------- pure renderers ----------

function btSourceOptionsHtml(sources) {
    if (!sources.length) return '<option value="">No installed bots</option>';
    return sources.map(s => {
        const label = s.supported ? s.name : `${s.name} — ${s.reason}`;
        return `<option value="${escapeHtml(s.name)}"${s.supported ? '' : ' disabled'} title="${escapeHtml(s.reason || '')}">${escapeHtml(label)}</option>`;
    }).join('');
}

function btParamInputHtml(p, value, timeframes) {
    const key = escapeHtml(p.key);
    const current = value === null || value === undefined ? '' : String(value);
    const options = list => list.map(o =>
        `<option value="${escapeHtml(o)}"${String(o) === current ? ' selected' : ''}>${escapeHtml(o)}</option>`).join('');
    if (p.type === 'Boolean') return `<select class="log-select bt-param" data-key="${key}">${options(['true', 'false'])}</select>`;
    if (p.type === 'Enum') return `<select class="log-select bt-param" data-key="${key}">${options(p.enum_values || [])}</select>`;
    if (p.type === 'TimeFrame') return `<select class="log-select bt-param" data-key="${key}">${options(timeframes || [])}</select>`;
    if (p.type === 'Integer' || p.type === 'Double') {
        const bounds = (p.min !== null && p.min !== undefined ? ` min="${escapeHtml(p.min)}"` : '')
            + (p.max !== null && p.max !== undefined ? ` max="${escapeHtml(p.max)}"` : '');
        return `<input type="number" class="log-input bt-param" data-key="${key}" step="${p.type === 'Integer' ? '1' : 'any'}"${bounds} value="${escapeHtml(current)}">`;
    }
    return `<input type="text" class="log-input bt-param" data-key="${key}" maxlength="200" value="${escapeHtml(current)}">`;
}

function btParamGroupsHtml(view, overrides, openGroups) {
    const has = key => Object.prototype.hasOwnProperty.call(overrides, key);
    return (view.groups || []).map(g => {
        const changed = g.params.filter(p => has(p.key)).length;
        const rows = g.params.map(p => {
            const isChanged = has(p.key);
            const hint = isChanged
                ? `<span class="bt-param-hint">bot: ${escapeHtml(p.bot_value ?? '—')}</span><button type="button" class="bt-reset" data-key="${escapeHtml(p.key)}" title="Back to the bot's value">↺</button>`
                : '';
            const help = p.help
                ? `<button type="button" class="bt-help" data-key="${escapeHtml(p.key)}" aria-label="Giải thích: ${escapeHtml(p.label)}">?</button>`
                : '';
            return `<div class="bt-param-row${isChanged ? ' bt-param-row--changed' : ''}">`
                + `<span class="bt-param-label"><span class="bt-param-name" title="${escapeHtml(p.key)}">${escapeHtml(p.label)}</span>${help}</span>`
                + `${btParamInputHtml(p, isChanged ? overrides[p.key] : p.bot_value, view.timeframes)}${hint}</div>`;
        }).join('');
        const open = changed || (openGroups && openGroups.has(g.name)) ? ' open' : '';
        const count = changed ? ` <span class="bt-changed-count">(${changed} changed)</span>` : '';
        return `<details class="bt-group" data-group="${escapeHtml(g.name)}"${open}><summary>${escapeHtml(g.name)}${count}</summary><div class="bt-param-grid">${rows}</div></details>`;
    }).join('');
}

// The tooltip of a parameter's "?" icon: its help text (app/backtest_param_help.py, one paragraph per
// line) over the cBot's own default and range from the metadata.
function btParamHelpHtml(p) {
    const has = v => v !== null && v !== undefined && v !== '';
    const range = has(p.min) && has(p.max) ? `${escapeHtml(p.min)} – ${escapeHtml(p.max)}`
        : has(p.min) ? `≥ ${escapeHtml(p.min)}` : has(p.max) ? `≤ ${escapeHtml(p.max)}` : '';
    const meta = [has(p.default) ? `Mặc định cBot: <b>${escapeHtml(p.default)}</b>` : '',
                  range ? `Phạm vi: ${range}` : '', `<code>${escapeHtml(p.key)}</code>`].filter(Boolean).join(' · ');
    return `<div class="bt-help-tip-title">${escapeHtml(p.label)}</div>`
        + `<div class="bt-help-tip-body">${escapeHtml(p.help)}</div>`
        + `<div class="bt-help-tip-meta">${meta}</div>`;
}

function btLockedHtml(locked) {
    const items = Object.entries(locked || {}).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(v)}`).join(' · ');
    return `🔒 Locked for backtests: ${items}`;
}

function btChipsHtml(overrides) {
    const entries = Object.entries(overrides || {});
    if (!entries.length) return '<span class="td-dim">(bot)</span>';
    return entries.map(([k, c]) =>
        `<span class="bt-chip">${escapeHtml(k)} ${escapeHtml((c && c.from) ?? '—')}→${escapeHtml(c && c.to)}</span>`).join(' ');
}

function btStatusHtml(job) {
    if (job.status === 'running') {
        const pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
        return `<span class="bt-status bt-status--running">${escapeHtml(job.phase || 'running')} ${pct.toFixed(0)}%</span>`
            + `<div class="bt-progress"><div style="width:${pct}%"></div></div>`;
    }
    const title = job.status === 'failed' && job.error ? ` title="${escapeHtml(job.error)}"` : '';
    return `<span class="bt-status bt-status--${escapeHtml(job.status)}"${title}>${escapeHtml(job.status)}</span>`;
}

// counts: the list response's running / queued totals, over every page.
function btRunsInfoText(counts, maxParallel) {
    const count = status => Number(counts && counts[status]) || 0;
    const max = Number(maxParallel);
    const slots = Number.isFinite(max) && max > 0 ? `${count('running')}/${max} running` : `${count('running')} running`;
    return `${slots} · ${count('queued')} queued`;
}

function btParallelOptionsHtml(current, cap) {
    const max = Math.max(1, Math.floor(Number(cap)) || 1);
    const value = Number(current);
    return Array.from({length: max}, (_, i) => i + 1)
        .map(n => `<option value="${n}"${n === value ? ' selected' : ''}>${n}</option>`).join('');
}

async function btSaveParallel(e) {
    const select = e.target;
    select.disabled = true;
    try {
        const res = await btFetchJson('/api/backtests/settings',
            {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({max_parallel: Number(select.value)})});
        btMessage(`Up to ${res.max_parallel} backtest(s) now run at once.`);
    } catch (err) {
        btMessage(`Could not change the parallel limit: ${err.message}`, true);
    } finally {
        select.disabled = false;
        select.blur();
        await btRefreshJobs();
    }
}

function btJobRowsHtml(jobs, selected) {
    if (!jobs.length) return '<tr><td colspan="13" class="td-dim" style="text-align:center;">No backtests yet</td></tr>';
    return jobs.map(j => {
        const id = Number(j.id);
        const s = j.summary || {};
        const done = j.status === 'done';
        const active = j.status === 'queued' || j.status === 'running';
        const cell = (v, kind) => (done ? btFormat(v, kind) : '—');
        // Summaries saved before the position stats existed show only the deal figure.
        const withDeals = (pos, deals, kind) =>
            (done ? `${btFormat(pos, kind)}<div class="td-dim bt-small">${btFormat(deals, kind)} deals</div>` : '—');
        const lastButton = active
            ? `<button class="log-btn bt-act" data-act="cancel" data-id="${id}">Cancel</button>`
            : `<button class="log-btn bt-act" data-act="delete" data-id="${id}">Delete</button>`;
        return `<tr>
            <td><input type="checkbox" class="bt-select" data-id="${id}"${done ? '' : ' disabled'}${selected.has(id) ? ' checked' : ''}></td>
            <td>#${id}</td>
            <td>${escapeHtml(j.symbol)} <span class="td-dim">${escapeHtml(j.period)}</span><div class="td-dim bt-small">${escapeHtml(j.bot_name)}</div></td>
            <td>${escapeHtml(btDateText(j.start_date))} – ${escapeHtml(btDateText(j.end_date))}<div class="td-dim bt-small">${escapeHtml(j.data_mode)}</div></td>
            <td>${btChipsHtml(j.overrides)}</td>
            <td>${btStatusHtml(j)}</td>
            <td class="${done ? btSignClass(s.net_profit) : ''}">${cell(s.net_profit, 'money')}</td>
            <td>${cell(s.profit_factor, 'num')}</td>
            <td>${withDeals(s.position_win_rate, s.win_rate, 'num')}</td>
            <td>${cell(s.max_equity_dd_pct, 'num')}</td>
            <td>${withDeals(s.positions, s.total_trades, 'int')}</td>
            <td class="bt-note">${escapeHtml(j.note || '')}</td>
            <td><div class="bt-actions">
                <button class="log-btn bt-act" data-act="view" data-id="${id}"${done || j.status === 'failed' ? '' : ' disabled'}>View</button>
                <button class="log-btn bt-act" data-act="clone" data-id="${id}">Clone</button>
                ${lastButton}
            </div></td>
        </tr>`;
    }).join('');
}

function btKpiCardsHtml(s) {
    const card = (label, value, cls = '', sub = '') =>
        `<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value kpi-value--mono ${cls}">${value}</div>`
        + `${sub ? `<div class="td-dim bt-small">${sub}</div>` : ''}</div>`;
    return `<div class="kpi-grid">${[
        card('Net P&amp;L', btMoney(s.net_profit), btSignClass(s.net_profit)),
        card('ROI', `${btNum(s.roi_pct)}%`, btSignClass(s.roi_pct)),
        card('Profit factor', btNum(s.profit_factor)),
        card('Win rate · positions', btPct(s.position_win_rate), '', `${btPct(s.win_rate)} of deals`),
        card('Positions', btFormat(s.positions, 'int'), '', `${btFormat(s.total_trades, 'int')} deals`),
        card('Max equity DD', `${btNum(s.max_equity_dd_pct)}%`),
        card('Avg win / loss · position', `${btMoney(s.avg_position_win)} / ${btMoney(s.avg_position_loss)}`, 'bt-kpi-wrap',
             `per deal ${btMoney(s.avg_win)} / ${btMoney(s.avg_loss)}`),
        card('Full-size losses', btPct(s.full_loss_pct), '', 'of positions, no partial close'),
        card('Commission + swap', btMoney(Number(s.commissions) + Number(s.swaps))),
    ].join('')}</div>`;
}

function btTradeRowsHtml(trades) {
    if (!trades.length) return '<tr><td colspan="9" class="td-dim" style="text-align:center;">No trades</td></tr>';
    return trades.map(t => `<tr>
        <td>${escapeHtml(t.entry_time)}</td><td>${escapeHtml(t.close_time)}</td><td>${escapeHtml(t.direction)}</td>
        <td>${btNum(t.lots)}</td><td>${escapeHtml(t.entry_price)}</td><td>${escapeHtml(t.close_price)}</td>
        <td>${btNum(t.pips, 1)}</td><td class="${btSignClass(t.net)}">${btMoney(t.net)}</td>
        <td>${btMoney(Number(t.commissions) + Number(t.swaps))}</td></tr>`).join('');
}

function btDetailTitleHtml(job) {
    return `<strong>#${Number(job.id)} ${escapeHtml(job.symbol)} ${escapeHtml(job.period)}</strong>`
        + `<span class="td-dim">${escapeHtml(btDateText(job.start_date))} – ${escapeHtml(btDateText(job.end_date))} · ${escapeHtml(job.data_mode)} · ${escapeHtml(job.bot_name)}</span>`
        + (job.note ? `<span class="bt-modal-note">${escapeHtml(job.note)}</span>` : '');
}

function btDetailHtml(job) {
    const id = Number(job.id);
    if (job.status === 'failed') return `<pre class="bt-error">${escapeHtml(job.error || 'failed')}</pre>`;
    if (!job.report) return '<div class="td-dim">No report for this run.</div>';
    return `${btKpiCardsHtml(job.summary || {})}
        <div class="bt-charts">
            <div class="bt-chart bt-chart--wide"><canvas id="bt-equity-chart"></canvas></div>
            <div class="bt-chart"><canvas id="bt-hour-chart"></canvas></div>
            <div class="bt-chart"><canvas id="bt-weekday-chart"></canvas></div>
        </div>
        <div class="bt-detail-meta">Changed: ${btChipsHtml(job.overrides)} · <a href="/api/backtests/${id}/report.json" target="_blank" rel="noopener">report.json</a></div>
        <div class="table-wrap bt-trades"><table class="data-table">
            <thead><tr><th>Entry (UTC)</th><th>Exit (UTC)</th><th>Side</th><th>Lots</th><th>Entry</th><th>Exit</th><th>Pips</th><th>Net</th><th>Comm + swap</th></tr></thead>
            <tbody>${btTradeRowsHtml(job.report.trades || [])}</tbody>
        </table></div>`;
}

function btReportParams(job) {
    return (job.report && job.report.parameters) || {};
}

function btParamDiff(jobs) {
    const keys = new Set();
    jobs.forEach(j => Object.keys(btReportParams(j)).forEach(k => keys.add(k)));
    return [...keys]
        .filter(k => !BT_IGNORED_PARAMS.has(k))
        .filter(k => new Set(jobs.map(j => String(btReportParams(j)[k]))).size > 1)
        .sort();
}

function btCompareWarnings(jobs) {
    const differs = f => new Set(jobs.map(f)).size > 1;
    const warnings = [];
    if (differs(j => `${j.start_date}|${j.end_date}`)) warnings.push('date ranges differ');
    if (differs(j => j.data_mode)) warnings.push('data modes differ');
    if (differs(j => j.algo_build_time)) warnings.push('the .algo builds differ');
    if (differs(j => j.symbol)) warnings.push('symbols differ');
    return warnings;
}

function btBestIndex(values, best) {
    const nums = values.map(Number);
    if (!best || nums.some(n => !Number.isFinite(n))) return -1;
    const score = best === 'max' ? nums : best === 'min' ? nums.map(n => -n) : nums.map(n => -Math.abs(n));
    const top = Math.max(...score);
    return score.filter(s => s === top).length === 1 ? score.indexOf(top) : -1;
}

function btCompareHtml(jobs) {
    const head = jobs.map(j => `<th>#${Number(j.id)} ${escapeHtml(j.symbol)}<div class="td-dim bt-small">${escapeHtml(j.note || '')}</div></th>`).join('');
    const rows = BT_COMPARE_ROWS.map(([key, label, best, kind]) => {
        const values = jobs.map(j => (j.summary || {})[key]);
        const winner = btBestIndex(values, best);
        const cells = values.map((v, i) => {
            const base = Number(values[0]);
            const delta = i > 0 && Number.isFinite(Number(v)) && Number.isFinite(base)
                ? ` <span class="td-dim bt-small">Δ ${btFormat(Number(v) - base, kind)}</span>` : '';
            return `<td${i === winner ? ' class="bt-best"' : ''}>${btFormat(v, kind)}${delta}</td>`;
        }).join('');
        return `<tr><td>${label}</td>${cells}</tr>`;
    }).join('');
    const diff = btParamDiff(jobs);
    const diffRows = diff.length
        ? diff.map(k => `<tr><td>${escapeHtml(k)}</td>${jobs.map(j => `<td>${escapeHtml(btReportParams(j)[k] ?? '—')}</td>`).join('')}</tr>`).join('')
        : `<tr><td colspan="${jobs.length + 1}" class="td-dim">No parameter differs</td></tr>`;
    const warnings = btCompareWarnings(jobs);
    return `${warnings.length ? `<div class="bt-warning">⚠ Not directly comparable: ${warnings.join(', ')}</div>` : ''}
        <div class="bt-chart bt-chart--wide"><canvas id="bt-compare-chart"></canvas></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Metric</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>
        <h4 class="bt-subtitle">Parameters that differ</h4>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Parameter</th>${head}</tr></thead><tbody>${diffRows}</tbody></table></div>`;
}

// Page numbers around the current one, with the first and the last always reachable.
function btPageList(page, pages) {
    const keep = new Set([1, pages, page - 1, page, page + 1].filter(n => n >= 1 && n <= pages));
    const list = [];
    [...keep].sort((a, b) => a - b).forEach((n, i, arr) => {
        if (i && n - arr[i - 1] > 1) list.push(null);                 // a gap: '…'
        list.push(n);
    });
    return list;
}

function btPagerHtml(page, perPage, total) {
    const pages = Math.max(1, Math.ceil(total / perPage));
    const from = total ? (page - 1) * perPage + 1 : 0;
    const to = Math.min(total, page * perPage);
    const btn = (n, label, extra = '') =>
        `<button type="button" class="log-btn bt-page" data-page="${n}"${extra}>${label}</button>`;
    const numbers = btPageList(page, pages).map(n => (n === null
        ? '<span class="bt-page-gap">…</span>'
        : btn(n, n, n === page ? ' aria-current="page" disabled' : ''))).join('');
    const nav = pages > 1
        ? `<div class="bt-pager-btns">${btn(page - 1, '‹', page <= 1 ? ' disabled aria-label="Previous page"' : ' aria-label="Previous page"')}`
          + `${numbers}${btn(page + 1, '›', page >= pages ? ' disabled aria-label="Next page"' : ' aria-label="Next page"')}</div>`
        : '';
    const sizes = BT_PAGE_SIZES.map(n => `<option value="${n}"${n === perPage ? ' selected' : ''}>${n}</option>`).join('');
    return `<span class="td-dim">${from}–${to} of ${total}</span>${nav}`
        + `<label class="bt-per-page td-dim">Rows <select class="log-select" data-act="per-page">${sizes}</select></label>`;
}

// ---------- DOM, fetch and charts ----------

function btStored(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch (e) { return fallback; }
}

function btStore(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private window: not remembered */ }
}

async function btFetchJson(url, options) {
    const resp = await fetch(url, options);
    let body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    if (!resp.ok) {
        const detail = body && body.detail;
        const message = Array.isArray(detail)
            ? detail.map(d => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
            : (detail || `HTTP ${resp.status}`);
        throw new Error(message);
    }
    return body;
}

function btMessage(text, isError) {
    const el = document.getElementById('bt-form-msg');
    el.textContent = text || '';
    el.className = `bt-form-msg${isError ? ' bt-form-msg--error' : ''}`;
}

async function btOnShow() {
    if (!bt.loaded) {
        bt.loaded = true;
        btInitForm();
        await Promise.all([btLoadSources(), btRefreshJobs()]);
        return;
    }
    await btRefreshJobs();
}

function btInitForm() {
    const perPage = Number(btStored('bt-per-page', ''));
    if (BT_PAGE_SIZES.includes(perPage)) bt.perPage = perPage;
    btSetFormOpen(btStored('bt-form-collapsed', 'false') !== 'true');
    document.getElementById('bt-form-toggle').addEventListener('click', () => {
        const open = document.getElementById('bt-form-panel').classList.contains('bt-collapsed');
        btSetFormOpen(open);
        btStore('bt-form-collapsed', String(!open));
    });
    const pager = document.getElementById('bt-pager');
    pager.addEventListener('click', e => {
        const b = e.target.closest('.bt-page');
        if (b && !b.disabled) btGoToPage(Number(b.dataset.page));
    });
    pager.addEventListener('change', e => {
        if (e.target.dataset.act !== 'per-page') return;
        bt.perPage = Number(e.target.value);
        btStore('bt-per-page', String(bt.perPage));
        btGoToPage(1);
    });
    btInitModal();
    const day = 86400000;
    const end = new Date(Date.now() - day);
    document.getElementById('bt-end').value = end.toISOString().slice(0, 10);
    document.getElementById('bt-start').value = new Date(end.getTime() - 59 * day).toISOString().slice(0, 10);
    document.getElementById('bt-bot').addEventListener('change', () => { bt.overrides = {}; bt.pendingOverrides = null; btLoadParams(); });
    document.getElementById('bt-data-mode').addEventListener('change', btToggleSpread);
    ['bt-start', 'bt-end'].forEach(id => document.getElementById(id).addEventListener('change', btUpdateFormSummary));
    document.getElementById('bt-run-btn').addEventListener('click', btSubmit);
    document.getElementById('bt-compare-btn').addEventListener('click', btCompare);
    document.getElementById('bt-parallel').addEventListener('change', btSaveParallel);
    const params = document.getElementById('bt-params');
    params.addEventListener('change', btOnParamChange);
    params.addEventListener('click', e => {
        const help = e.target.closest('.bt-help');
        if (help) { e.preventDefault(); btShowHelp(help); return; }     // a tap on a touch screen
        const reset = e.target.closest('.bt-reset');
        if (!reset) return;
        e.preventDefault();
        delete bt.overrides[reset.dataset.key];
        btRenderParams();
    });
    const helpIcon = e => e.target.closest('.bt-help');
    params.addEventListener('mouseover', e => { const b = helpIcon(e); if (b) btShowHelp(b); });
    params.addEventListener('mouseout', e => { const b = helpIcon(e); if (b && !b.contains(e.relatedTarget)) btHideHelp(); });
    params.addEventListener('focusin', e => { const b = helpIcon(e); if (b) btShowHelp(b); });
    params.addEventListener('focusout', e => { if (helpIcon(e)) btHideHelp(); });
    document.addEventListener('click', e => { if (!e.target.closest('.bt-help')) btHideHelp(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') btHideHelp(); });
    document.addEventListener('scroll', btHideHelp, true);        // the tooltip is fixed, the icon scrolls
    window.addEventListener('resize', btHideHelp);
    const tbody = document.getElementById('bt-jobs-tbody');
    tbody.addEventListener('click', btOnJobAction);
    tbody.addEventListener('change', e => {
        if (!e.target.classList.contains('bt-select')) return;
        const id = Number(e.target.dataset.id);
        if (e.target.checked) bt.selected.add(id); else bt.selected.delete(id);
        btUpdateCompareButton();
    });
    btToggleSpread();
}

function btSetFormOpen(open) {
    document.getElementById('bt-form-panel').classList.toggle('bt-collapsed', !open);
    document.getElementById('bt-form-toggle').setAttribute('aria-expanded', String(open));
    if (!open) btHideHelp();
    btUpdateFormSummary();
}

// What the folded form would run, so it can stay closed while runs are browsed.
function btUpdateFormSummary() {
    const el = document.getElementById('bt-form-summary');
    const bot = document.getElementById('bt-bot').value;
    const changed = Object.keys(bt.overrides).length;
    el.textContent = bot
        ? `${bot} · ${btDateText(document.getElementById('bt-start').value)} – ${btDateText(document.getElementById('bt-end').value)}`
          + (changed ? ` · ${changed} changed` : '')
        : '';
}

function btToggleSpread() {
    const m1 = document.getElementById('bt-data-mode').value === 'm1';
    document.getElementById('bt-spread-wrap').style.display = m1 ? '' : 'none';
}

async function btLoadSources() {
    const select = document.getElementById('bt-bot');
    try {
        const data = await btFetchJson('/api/backtests/sources');
        bt.sources = data.sources || [];
        select.innerHTML = btSourceOptionsHtml(bt.sources);
        const first = bt.sources.find(s => s.supported);
        if (first) select.value = first.name;
        if (!data.docker_available) btMessage('Docker is not available on this server: backtests cannot run.', true);
        await btLoadParams();
    } catch (e) {
        btMessage(`Could not load bots: ${e.message}`, true);
    }
}

async function btLoadParams() {
    const bot = document.getElementById('bt-bot').value;
    const container = document.getElementById('bt-params');
    bt.view = null;
    document.getElementById('bt-locked').textContent = '';
    if (!bot) { container.innerHTML = ''; return; }
    container.innerHTML = '<div class="td-dim">Loading parameters… (the first time for a build takes ~15 s)</div>';
    try {
        const view = await btFetchJson(`/api/backtests/sources/${encodeURIComponent(bot)}/params`);
        if (document.getElementById('bt-bot').value !== bot) return;       // switched meanwhile
        bt.view = view;
        if (bt.pendingOverrides) { bt.overrides = bt.pendingOverrides; bt.pendingOverrides = null; }
        document.getElementById('bt-locked').innerHTML = btLockedHtml(view.locked);
        btRenderParams();
    } catch (e) {
        container.innerHTML = `<div class="bt-form-msg--error">${escapeHtml(e.message)}</div>`;
    }
}

function btRenderParams() {
    if (!bt.view) return;
    btHideHelp();                                             // its icon is about to be replaced
    const container = document.getElementById('bt-params');
    const open = new Set([...container.querySelectorAll('details.bt-group[open]')].map(d => d.dataset.group));
    container.innerHTML = btParamGroupsHtml(bt.view, bt.overrides, open);
    btUpdateFormSummary();
}

// One tooltip for every "?" icon, fixed to the viewport: .panel clips whatever overflows it. It opens
// under the icon, or above it when there is no room below, and stays inside the window.
function btShowHelp(btn) {
    const p = btFindParam(btn.dataset.key);
    if (!p || !p.help) return;
    let tip = document.getElementById('bt-help-tip');
    if (!tip) {
        tip = document.createElement('div');
        tip.id = 'bt-help-tip';
        tip.className = 'bt-help-tip';
        tip.setAttribute('role', 'tooltip');
        document.body.appendChild(tip);
    }
    if (bt.helpBtn && bt.helpBtn !== btn) bt.helpBtn.removeAttribute('aria-describedby');
    bt.helpBtn = btn;
    btn.setAttribute('aria-describedby', tip.id);
    tip.innerHTML = btParamHelpHtml(p);
    tip.hidden = false;
    const gap = 6, margin = 8, r = btn.getBoundingClientRect();
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const left = Math.max(margin, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - margin));
    const roomBelow = r.bottom + gap + h <= window.innerHeight - margin;
    const top = roomBelow || r.top - gap - h < margin ? r.bottom + gap : r.top - gap - h;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
}

function btHideHelp() {
    const tip = document.getElementById('bt-help-tip');
    if (!tip || tip.hidden) return;
    tip.hidden = true;
    if (bt.helpBtn) bt.helpBtn.removeAttribute('aria-describedby');
    bt.helpBtn = null;
}

function btFindParam(key) {
    for (const g of (bt.view && bt.view.groups) || []) {
        const p = g.params.find(x => x.key === key);
        if (p) return p;
    }
    return null;
}

function btOnParamChange(e) {
    const input = e.target.closest('.bt-param');
    const p = input && btFindParam(input.dataset.key);
    if (!p) return;
    const numeric = p.type === 'Integer' || p.type === 'Double';
    const same = numeric ? Number(input.value) === Number(p.bot_value) : input.value === String(p.bot_value);
    if (same) delete bt.overrides[p.key]; else bt.overrides[p.key] = input.value;
    btRenderParams();
}

async function btSubmit() {
    const btn = document.getElementById('bt-run-btn');
    const mode = document.getElementById('bt-data-mode').value;
    const body = {
        bot_name: document.getElementById('bt-bot').value,
        start: document.getElementById('bt-start').value,
        end: document.getElementById('bt-end').value,
        data_mode: mode,
        balance: Number(document.getElementById('bt-balance').value),
        note: document.getElementById('bt-note').value.trim() || null,
        overrides: bt.overrides,
    };
    if (mode === 'm1') body.spread_pips = Number(document.getElementById('bt-spread').value);
    btn.disabled = true;
    btMessage('Queuing…');
    try {
        const res = await btFetchJson('/api/backtests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        btMessage(`Backtest #${res.id} queued (position ${res.queue_position}; ${res.running}/${res.max_parallel} running).`);
        await btRefreshJobs();
    } catch (e) {
        btMessage(e.message, true);
    } finally {
        btn.disabled = false;
    }
}

async function btRefreshJobs() {
    clearTimeout(bt.pollTimer);
    let active = false;
    try {
        const data = await btFetchJson(`/api/backtests?page=${bt.page}&per_page=${bt.perPage}`);
        bt.jobs = data.jobs || [];
        bt.page = data.page || 1;                     // the server falls back to the last page
        bt.total = data.total || 0;
        active = data.active > 0;
        // A selection may sit on another page: drop only the runs this page shows as not done.
        const notDone = new Set(bt.jobs.filter(j => j.status !== 'done').map(j => Number(j.id)));
        bt.selected = new Set([...bt.selected].filter(id => !notDone.has(id)));
        document.getElementById('bt-jobs-tbody').innerHTML = btJobRowsHtml(bt.jobs, bt.selected);
        document.getElementById('bt-runs-info').textContent = btRunsInfoText(data, data.max_parallel);
        const parallel = document.getElementById('bt-parallel');
        if (document.activeElement !== parallel) parallel.innerHTML = btParallelOptionsHtml(data.max_parallel, data.max_parallel_cap);
        document.getElementById('bt-pager').innerHTML = btPagerHtml(bt.page, bt.perPage, bt.total);
        btUpdateCompareButton();
        btUpdateModalNav();
    } catch (e) {
        btMessage(`Could not load backtests: ${e.message}`, true);
    }
    // Poll only while something is queued or running (on any page) and the page is on screen.
    const visible = document.getElementById('view-backtest').classList.contains('active');
    if (active && visible) bt.pollTimer = setTimeout(btRefreshJobs, BT_POLL_MS);
}

async function btGoToPage(page) {
    bt.page = Math.max(1, page);
    await btRefreshJobs();
    document.getElementById('bt-jobs-tbody').closest('.panel').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function btUpdateCompareButton() {
    const btn = document.getElementById('bt-compare-btn');
    btn.textContent = `Compare (${bt.selected.size})`;
    btn.disabled = bt.selected.size < 2 || bt.selected.size > 4;
}

// ---------- modal: run detail and compare ----------

function btInitModal() {
    const modal = document.getElementById('bt-modal');
    modal.addEventListener('click', e => { if (e.target === modal) btCloseModal(); });     // the backdrop
    document.getElementById('bt-modal-close').addEventListener('click', btCloseModal);
    document.getElementById('bt-modal-prev').addEventListener('click', () => btStepDetail(-1));
    document.getElementById('bt-modal-next').addEventListener('click', () => btStepDetail(1));
    document.addEventListener('keydown', e => {
        if (modal.hidden) return;
        if (e.key === 'Escape') btCloseModal();
        else if (e.target instanceof Element && e.target.closest('input, select, textarea')) return;
        else if (e.key === 'ArrowLeft') btStepDetail(-1);
        else if (e.key === 'ArrowRight') btStepDetail(1);
    });
}

function btOpenModal(titleHtml, bodyHtml, jobId = null) {
    const modal = document.getElementById('bt-modal');
    document.getElementById('bt-modal-title').innerHTML = titleHtml;
    document.getElementById('bt-modal-body').innerHTML = bodyHtml;
    bt.modalJobId = jobId;
    btUpdateModalNav();
    if (modal.hidden) {
        modal.hidden = false;
        document.body.classList.add('bt-modal-open');
        document.getElementById('bt-modal-close').focus();
    }
    document.getElementById('bt-modal-body').scrollTop = 0;
}

function btCloseModal() {
    const modal = document.getElementById('bt-modal');
    if (modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove('bt-modal-open');
    ['equity', 'hour', 'weekday', 'compare'].forEach(btDestroyChart);
    bt.modalJobId = null;
}

// The runs of this page the detail can step to: the ones a View button opens.
function btViewableIds() {
    return bt.jobs.filter(j => j.status === 'done' || j.status === 'failed').map(j => Number(j.id));
}

function btUpdateModalNav() {
    const ids = btViewableIds();
    const at = ids.indexOf(bt.modalJobId);
    const prev = document.getElementById('bt-modal-prev');
    const next = document.getElementById('bt-modal-next');
    prev.hidden = next.hidden = bt.modalJobId === null;
    prev.disabled = at <= 0;
    next.disabled = at < 0 || at >= ids.length - 1;
}

function btStepDetail(step) {
    if (bt.modalJobId === null) return;
    const ids = btViewableIds();
    const at = ids.indexOf(bt.modalJobId);
    if (at >= 0 && ids[at + step] !== undefined) btShowDetail(ids[at + step]).catch(e => btMessage(e.message, true));
}

async function btOnJobAction(e) {
    const btn = e.target.closest('.bt-act');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    try {
        if (btn.dataset.act === 'view') {
            await btShowDetail(id);
        } else if (btn.dataset.act === 'clone') {
            await btClone(id);
        } else if (btn.dataset.act === 'cancel') {
            btn.disabled = true;
            await btFetchJson(`/api/backtests/${id}/cancel`, {method: 'POST'});
            await btRefreshJobs();
        } else if (btn.dataset.act === 'delete') {
            if (!confirm(`Delete backtest #${id} and its report?`)) return;
            btn.disabled = true;
            await btFetchJson(`/api/backtests/${id}`, {method: 'DELETE'});
            bt.selected.delete(id);
            await btRefreshJobs();
        }
    } catch (err) {
        btMessage(err.message, true);
        btn.disabled = false;
    }
}

async function btShowDetail(id) {
    const job = await btFetchJson(`/api/backtests/${id}`);
    btOpenModal(btDetailTitleHtml(job), btDetailHtml(job), Number(job.id));
    if (job.report) btDrawDetailCharts(job.report);           // the canvases must be on screen first
}

async function btClone(id) {
    const job = bt.jobs.find(j => Number(j.id) === id) || await btFetchJson(`/api/backtests/${id}`);
    const select = document.getElementById('bt-bot');
    if (![...select.options].some(o => o.value === job.bot_name && !o.disabled)) {
        btMessage(`${job.bot_name} is no longer installed or supported; cannot clone #${id}.`, true);
        return;
    }
    select.value = job.bot_name;
    document.getElementById('bt-start').value = job.start_date;
    document.getElementById('bt-end').value = job.end_date;
    document.getElementById('bt-data-mode').value = job.data_mode;
    document.getElementById('bt-spread').value = job.spread_pips ?? '';
    document.getElementById('bt-balance').value = job.balance;
    document.getElementById('bt-note').value = `${job.note ? job.note + ' ' : ''}(clone of #${id})`.slice(0, 200);
    btToggleSpread();
    bt.pendingOverrides = Object.fromEntries(Object.entries(job.overrides || {}).map(([k, c]) => [k, c.to]));
    await btLoadParams();
    btMessage(`Form filled from #${id}: change a parameter and run.`);
    btSetFormOpen(true);
    document.getElementById('bt-form-panel').scrollIntoView({behavior: 'smooth', block: 'start'});
}

async function btCompare() {
    const ids = [...bt.selected].sort((a, b) => a - b);        // the oldest run is the baseline column
    if (ids.length < 2) return;
    try {
        const jobs = await Promise.all(ids.map(id => btFetchJson(`/api/backtests/${id}`)));
        const title = `<strong>Compare</strong><span class="td-dim">${jobs.map(j => `#${Number(j.id)} ${escapeHtml(j.symbol)}`).join(' · ')}</span>`;
        btOpenModal(title, btCompareHtml(jobs));
        btDrawCompareChart(jobs);
    } catch (e) {
        btMessage(`Compare failed: ${e.message}`, true);
    }
}

function btDestroyChart(name) {
    if (bt.charts[name]) { bt.charts[name].destroy(); delete bt.charts[name]; }
}

function btDrawDetailCharts(report) {
    if (typeof Chart === 'undefined') return;
    ['equity', 'hour', 'weekday'].forEach(btDestroyChart);
    const eq = report.equity || [];
    bt.charts.equity = new Chart(document.getElementById('bt-equity-chart'), {
        type: 'line',
        data: {
            labels: eq.map(p => new Date(p.t).toISOString().slice(0, 16).replace('T', ' ')),
            datasets: [
                {label: 'Balance', data: eq.map(p => p.balance), borderColor: '#7dbd1e', pointRadius: 0, borderWidth: 1.5},
                {label: 'Equity (low)', data: eq.map(p => p.equity), borderColor: '#4fc3f7', pointRadius: 0, borderWidth: 1},
            ],
        },
        options: {responsive: true, maintainAspectRatio: false, animation: false,
                  interaction: {mode: 'index', intersect: false}, scales: {x: {ticks: {maxTicksLimit: 8}}}},
    });
    const bars = (id, labels, buckets) => new Chart(document.getElementById(id), {
        type: 'bar',
        data: {labels, datasets: [{label: 'Net P&L', data: buckets.map(b => b.net),
                                   backgroundColor: buckets.map(b => (b.net < 0 ? '#e53935' : '#7dbd1e'))}]},
        options: {responsive: true, maintainAspectRatio: false, animation: false,
                  plugins: {tooltip: {callbacks: {afterLabel: c => `${buckets[c.dataIndex].count} deals`}}}},
    });
    bt.charts.hour = bars('bt-hour-chart', [...Array(24).keys()].map(h => `${h}h`), report.pnl_by_hour || []);
    bt.charts.weekday = bars('bt-weekday-chart', BT_WEEKDAYS, report.pnl_by_weekday || []);
}

function btDrawCompareChart(jobs) {
    if (typeof Chart === 'undefined') return;
    btDestroyChart('compare');
    const colors = ['#7dbd1e', '#4fc3f7', '#ffa726', '#ba68c8'];
    bt.charts.compare = new Chart(document.getElementById('bt-compare-chart'), {
        type: 'line',
        data: {datasets: jobs.map((j, i) => {
            const eq = (j.report && j.report.equity) || [];
            const base = Number(j.summary && j.summary.starting_capital) || (eq[0] && eq[0].balance) || 1;
            return {label: `#${j.id}`, data: eq.map(p => ({x: p.t, y: (p.balance / base - 1) * 100})),
                    borderColor: colors[i % colors.length], pointRadius: 0, borderWidth: 1.5};
        })},
        options: {responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
                  scales: {x: {type: 'linear', ticks: {callback: v => new Date(v).toISOString().slice(0, 10)}},
                           y: {ticks: {callback: v => `${v}%`}}}},
    });
}
