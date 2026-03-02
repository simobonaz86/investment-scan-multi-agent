function el(id) {
  return document.getElementById(id);
}

// Polyfills for older mobile browsers
if (typeof Element !== "undefined" && !Element.prototype.closest) {
  Element.prototype.closest = function (s) {
    let el = this;
    while (el && el.nodeType === 1) {
      if (el.matches && el.matches(s)) return el;
      el = el.parentElement || el.parentNode;
    }
    return null;
  };
}

const state = {
  dashboard: null,
  dashboardAt: 0,
  portfolio: null,
  portfolioAt: 0,
  signalsById: new Map(),
  signalsAt: 0,
  scansAt: 0,
  positionsAt: 0,
  journalAt: 0,
  summaryAt: 0,
};

function showFatal(msg) {
  const box = el("fatalError");
  if (!box) return;
  box.style.display = "";
  box.innerHTML = `<pre>${escapeHtml(msg)}</pre>`;
}

async function apiJson(path, opts = {}) {
  const headers = opts.body instanceof FormData ? {} : { "content-type": "application/json" };
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeoutMs = 30000;
  const fetchPromise = fetch(path, {
    headers: { ...headers, ...(opts.headers || {}) },
    ...opts,
    signal: controller ? controller.signal : undefined,
  });

  const timeoutPromise = new Promise((_, reject) => {
    const id = setTimeout(() => {
      try {
        if (controller) controller.abort();
      } catch {
        // ignore
      }
      reject(new Error(`request_timeout_after_${timeoutMs}ms`));
    }, timeoutMs);
    // Avoid leaking the timer if fetch wins (no Promise.finally for older Safari).
    fetchPromise.then(
      () => clearTimeout(id),
      () => clearTimeout(id),
    );
  });

  const r = await Promise.race([fetchPromise, timeoutPromise]);
  const isJson = (r.headers.get("content-type") || "").includes("application/json");
  const body = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = typeof body === "string" ? body : (body && body.detail) ? body.detail : JSON.stringify(body);
    throw new Error(msg || `${r.status} ${r.statusText}`);
  }
  return body;
}

function fmtMoney(x) {
  const v = Number(x || 0);
  if (!Number.isFinite(v)) return "—";
  return `$${v.toFixed(2)}`;
}

function fmtPct(x) {
  const v = Number(x || 0);
  if (!Number.isFinite(v)) return "—";
  return `${v.toFixed(2)}%`;
}

function fmtTs(s) {
  if (!s) return "";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return String(s);
  }
}

function msUntil(iso) {
  try {
    const t = new Date(iso).getTime();
    return t - Date.now();
  } catch {
    return 0;
  }
}

function fmtCountdown(ms) {
  const x = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(x / 3600);
  const m = Math.floor((x % 3600) / 60);
  const s = x % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

async function getPortfolioCached({ force = false } = {}) {
  const now = Date.now();
  if (!force && state.portfolio && now - state.portfolioAt < 15000) return state.portfolio;
  const p = await apiJson("/portfolio");
  state.portfolio = p;
  state.portfolioAt = now;
  return p;
}

async function getDashboardCached({ force = false } = {}) {
  const now = Date.now();
  if (!force && state.dashboard && now - state.dashboardAt < 15000) return state.dashboard;
  const d = await apiJson("/api/dashboard");
  state.dashboard = d;
  state.dashboardAt = now;
  if (d && d.portfolio) {
    state.portfolio = d.portfolio;
    state.portfolioAt = now;
  }
  return d;
}

async function loadMarketScanStatus() {
  try {
    const st = await apiJson("/marketscan/status");
    const enabled = st.enabled ? "enabled" : "disabled";
    const mh = st.only_market_hours ? "market-hours" : "24/7";
    el("marketscanStatus").textContent =
      `Market scan: ${enabled} | ${mh} | interval=${st.interval_seconds}s | top_n=${st.top_n} | min_score=${st.min_score}`;
  } catch (e) {
    el("marketscanStatus").textContent = `Market scan: error (${e.message})`;
  }
}

function renderMarketScanStatus(st) {
  if (!st) return;
  const enabled = st.enabled ? "enabled" : "disabled";
  const mh = st.only_market_hours ? "market-hours" : "24/7";
  el("marketscanStatus").textContent =
    `Market scan: ${enabled} | ${mh} | interval=${st.interval_seconds}s | top_n=${st.top_n} | min_score=${st.min_score}`;
}

function escapeHtml(s) {
  // Avoid String.prototype.replaceAll (older mobile browsers).
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function plainEnglishReason(rec, cashUsd) {
  const t = rec.ticker;
  const entry = Number(rec.entry_price || 0);
  const stop = Number(rec.stop_loss || 0);
  const take = Number(rec.take_profit || 0);
  const shares = Number(rec.shares || 0);
  const notional = Number(rec.notional_usd || (entry * shares));
  const riskPerShare = entry > 0 && stop > 0 ? entry - stop : 0;
  const maxLoss = Number(rec.max_loss_usd || (shares * riskPerShare));
  const rr = Number(rec.risk_reward_ratio || 0);
  const reasons = (rec.reasons || []).slice(0, 6);
  const overBudget = Number.isFinite(cashUsd) && entry > 0 && shares > 0 ? (notional > cashUsd) : false;
  const fitShares = entry > 0 && Number.isFinite(cashUsd) ? Math.max(0, Math.floor(cashUsd / entry)) : null;

  const lines = [];
  const rating = rec.rating ? String(rec.rating) : null;
  const mechs = (rec.mechanisms || []).slice(0, 6);
  lines.push(`BUY ${t}.`);
  if (rating) lines.push(`Rating: ${rating}.`);
  if (mechs.length) lines.push(`Mechanisms: ${mechs.join(", ")}.`);
  if (rec.strategy) {
    if (rec.strategy === "momentum") lines.push("Style: momentum (trend-following).");
    if (rec.strategy === "reversion") lines.push("Style: mean reversion (bounce from oversold).");
  }
  if (reasons.length) lines.push(`Why: ${reasons.join("; ")}.`);
  if (entry > 0 && stop > 0 && take > 0) {
    lines.push(
      `Plan: enter around ${fmtMoney(entry)}, stop at ${fmtMoney(stop)} (risk/share ${fmtMoney(riskPerShare)}), take-profit ${fmtMoney(take)} (R/R ~ ${rr.toFixed(2)}).`,
    );
  }
  if (shares > 0) lines.push(`Sizing: ${shares} shares (~${fmtMoney(notional)} notional), max loss ~${fmtMoney(maxLoss)}.`);
  if (entry > 0 && stop > 0) {
    lines.push("Invalidation: if price hits the stop, the setup is wrong—exit or reassess.");
  }
  if (overBudget && fitShares != null) {
    lines.push(
      `Over budget: this uses ${fmtMoney(notional)} but you have ${fmtMoney(cashUsd)} cash. To fit, reduce to ~${fitShares} shares.`,
    );
  }
  return lines.join(" ");
}

function getSkippedSignalIds() {
  try {
    const raw = localStorage.getItem("skippedSignals") || "[]";
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.map(String) : []);
  } catch {
    return new Set();
  }
}

function addSkippedSignalId(id) {
  const s = getSkippedSignalIds();
  s.add(String(id));
  localStorage.setItem("skippedSignals", JSON.stringify(Array.from(s).slice(-500)));
}

function computeTakeProfit({ entry, stop, atr, sma20, rating }) {
  const e = Number(entry || 0);
  const s = Number(stop || 0);
  const a = Number(atr || 0);
  const sd = e > 0 && s > 0 ? (e - s) : 0;
  if (e <= 0 || sd <= 0) return 0;

  const r = String(rating || "").toLowerCase();
  let rr = 1.2;
  if (r.includes("very")) rr = 1.7;
  else if (r.includes("strong")) rr = 1.5;
  else if (r.includes("light") || r.includes("medium")) rr = 1.3;

  const capPct = 0.15;
  let dist = rr * sd;
  dist = Math.min(dist, e * capPct);
  if (Number.isFinite(a) && a > 0) dist = Math.min(dist, 3.0 * a);
  dist = Math.max(0, dist);
  let tp = e + dist;
  if (Number.isFinite(sma20) && sma20 > e) tp = Math.min(tp, sma20);
  return tp;
}

function ratingPill(rating) {
  const r = String(rating || "").toLowerCase();
  if (!r) return "";
  if (r.includes("very")) return `<span class="pill ok">Very Strong</span>`;
  if (r.includes("strong")) return `<span class="pill warn">Strong</span>`;
  if (r.includes("light")) return `<span class="pill warn">Light</span>`;
  return `<span class="pill bad">Not strong</span>`;
}

function mechanismChips(mechanisms) {
  const ms = Array.isArray(mechanisms) ? mechanisms.slice(0, 4) : [];
  if (!ms.length) return "";
  return ms.map((m) => `<span class="pill">${escapeHtml(m)}</span>`).join(" ");
}

function ratingBadge(rating) {
  const r = String(rating || "").toLowerCase();
  if (!r) return "";
  if (r.includes("very")) return `<span class="pill ok">Very strong</span>`;
  if (r.includes("strong")) return `<span class="pill warn">Strong</span>`;
  if (r.includes("light") || r.includes("medium")) return `<span class="pill warn">Medium</span>`;
  return `<span class="pill bad">Low</span>`;
}

function triggerPill(status) {
  const s = String(status || "WAITING").toUpperCase();
  if (s === "TRIGGERED") return `<span class="pill ok">Triggered</span>`;
  if (s === "TOO_LATE") return `<span class="pill warn">Too late</span>`;
  if (s === "INVALIDATED") return `<span class="pill bad">Invalid</span>`;
  if (s === "DATA_ERROR") return `<span class="pill bad">No data</span>`;
  return `<span class="pill">Waiting</span>`;
}

function renderRecommendationsTable(
  recs,
  { cashUsd = null, mode = "active", triggersByTicker = null } = {},
) {
  const skipped = mode === "active" ? getSkippedSignalIds() : new Set();
  const xs = (Array.isArray(recs) ? recs.slice() : [])
    .filter((r) => !skipped.has(String(r.rec_id)))
    .sort((a, b) => Number(b.score || 0) - Number(a.score || 0));

  const cols = mode === "active" ? 10 : 8;
  const rows = xs
    .map((r) => {
      const id = String(r.rec_id);
      const score = Number(r.score || 0);
      const entry = Number(r.entry_price || 0);
      const stop = Number(r.stop_loss || 0);
      const take = Number(r.take_profit || 0);
      const shares = r.shares == null ? "—" : String(r.shares);
      const cashAfter = Number(r.cash_after);
      const overBudget = r.cash_valid === false || (Number.isFinite(cashAfter) && cashAfter < 0);
      const budget = overBudget ? `<span class="pill bad">Over</span>` : `<span class="pill ok">OK</span>`;
      const reasons = (r.reasons || []).slice(0, 6);
      const plain = plainEnglishReason(r, Number.isFinite(cashUsd) ? cashUsd : null);
      const chips = mechanismChips(r.mechanisms);
      const status = String(r.status || (mode === "history" ? "history" : "active")).toLowerCase();
      const trig = triggersByTicker && r.ticker ? triggersByTicker[String(r.ticker).toUpperCase()] : null;
      const trigStatus = trig ? trig.status : null;
      const trigReason = trig ? trig.reason : null;
      const trigLast = trig ? trig.last_price : null;
      const trigPx = trig ? trig.trigger_price : null;
      const trigExt = trig ? trig.extension_pct : null;
      const trigSetup = trig ? trig.setup_type : null;
      const trigInterval = trig ? trig.interval : null;
      const trigDetails = trig ? trig.details : null;

      const actions =
        mode === "active"
          ? `<button class="btn btn-mini" data-exec="${escapeHtml(id)}" type="button">Execute</button>
             <button class="btn btn-secondary btn-mini" data-skip="${escapeHtml(id)}" type="button">Ignore</button>`
          : `<button class="btn btn-secondary btn-mini" data-copy="${escapeHtml(id)}" type="button">Copy</button>
             <button class="btn btn-secondary btn-mini" data-hide="${escapeHtml(id)}" type="button">Hide</button>`;

      const statusPill =
        status === "executed"
          ? `<span class="pill ok">Executed</span>`
          : status === "skipped"
            ? `<span class="pill warn">Ignored</span>`
            : status === "expired"
              ? `<span class="pill bad">Expired</span>`
              : `<span class="pill ok">Active</span>`;

      return `
        <tr class="rec-row ${overBudget ? "over-budget" : ""}" data-rec-row="${escapeHtml(id)}">
          <td class="ticker-cell col-ticker">${escapeHtml(r.ticker)}</td>
          <td class="num col-score">${score.toFixed(1)}</td>
          <td class="col-trigger">${triggerPill(trigStatus || "WAITING")}</td>
          <td class="col-rating">${ratingBadge(r.rating)}</td>
          <td class="num col-entry">${fmtMoney(entry)}</td>
          <td class="num col-take">${fmtMoney(take)}</td>
          <td class="num col-stop">${fmtMoney(stop)}</td>
          <td class="num col-shares">${escapeHtml(shares)}</td>
          ${
            mode === "active"
              ? `<td class="num col-cash">${budget}&nbsp;<span class="subtle">${fmtMoney(r.cash_after)}</span></td>`
              : `<td class="col-status">${statusPill}</td>`
          }
          <td class="actions-cell col-actions">${actions}</td>
        </tr>
        <tr class="rec-details-row" data-rec-detail="${escapeHtml(id)}">
          <td colspan="${cols}">
            <div class="recs-details">
              <div>
                <div class="k">Key reasons</div>
                ${chips ? `<div style="margin-bottom:8px; display:flex; flex-wrap:wrap; gap:6px;">${chips}</div>` : ""}
                <ul>${reasons.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>
                <div style="height:10px"></div>
                <div class="subtle">Created: ${fmtTs(r.created_at)} · Expires: ${fmtTs(r.expires_at)}</div>
              </div>
              <div>
                <div class="k">Intraday trigger</div>
                <div class="plain">
                  Status: <b>${escapeHtml(String(trigStatus || "WAITING"))}</b>${trigSetup ? ` · Setup: <b>${escapeHtml(String(trigSetup))}</b>` : ""}${trigInterval ? ` · Interval: <b>${escapeHtml(String(trigInterval))}</b>` : ""}<br/>
                  ${trigPx != null ? `Trigger: <b>${fmtMoney(trigPx)}</b>` : ""}${trigLast != null ? ` · Last: <b>${fmtMoney(trigLast)}</b>` : ""}${trigExt != null ? ` · Ext: <b>${(Number(trigExt) * 100).toFixed(2)}%</b>` : ""}<br/>
                  ${trigReason ? `Reason: ${escapeHtml(String(trigReason))}` : "Reason: —"}
                </div>
                ${
                  trigDetails
                    ? `<div class="subtle" style="margin-top:8px;">
                         EMA20: ${trigDetails.ema20 == null ? "—" : fmtMoney(trigDetails.ema20)}
                         · VWAP: ${trigDetails.vwap == null ? "—" : fmtMoney(trigDetails.vwap)}
                         · RangeHi(20): ${trigDetails.range_high_20 == null ? "—" : fmtMoney(trigDetails.range_high_20)}
                         · VolRatio: ${trigDetails.vol_ratio == null ? "—" : Number(trigDetails.vol_ratio).toFixed(2)}
                       </div>`
                    : ""
                }
                <div style="height:10px"></div>
                <div class="k">Explanation (plain English)</div>
                <div class="plain">${escapeHtml(plain)}</div>
              </div>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  const headActive = `
    <tr>
      <th class="col-ticker">Ticker</th>
      <th class="num col-score">Score</th>
      <th class="col-trigger">Trigger</th>
      <th class="col-rating">Rating</th>
      <th class="num col-entry">Entry</th>
      <th class="num col-take">Take</th>
      <th class="num col-stop">Stop</th>
      <th class="num col-shares">Shares</th>
      <th class="num col-cash">Cash</th>
      <th></th>
    </tr>
  `;
  const headHist = `
    <tr>
      <th class="col-ticker">Ticker</th>
      <th class="num col-score">Score</th>
      <th class="col-trigger">Trigger</th>
      <th class="col-rating">Rating</th>
      <th class="num col-entry">Entry</th>
      <th class="num col-take">Take</th>
      <th class="num col-stop">Stop</th>
      <th class="col-status">Status</th>
      <th></th>
    </tr>
  `;

  return `
    <div class="recs-table">
      <div class="table-scroll">
        <table>
          <thead>${mode === "active" ? headActive : headHist}</thead>
          <tbody>${rows || `<tr><td colspan="${cols}">No recommendations.</td></tr>`}</tbody>
        </table>
      </div>
    </div>
  `;
}

function renderSummary(s) {
  if (!s) return;
  el("sumBudget").textContent = fmtMoney(s.initial_budget);
  el("sumCash").textContent = fmtMoney(s.current_cash);
  el("sumPositions").textContent = fmtMoney(s.open_positions_value);
  const pnlEl = el("sumPnl");
  pnlEl.textContent = `${fmtMoney(s.total_pnl)} (${fmtPct(s.total_pnl_pct)})`;
  pnlEl.classList.remove("positive", "negative");
  if (Number(s.total_pnl || 0) > 0) pnlEl.classList.add("positive");
  if (Number(s.total_pnl || 0) < 0) pnlEl.classList.add("negative");

  const banner = el("lossBanner");
  const msg = s.loss_critical || s.loss_warning;
  if (msg) {
    banner.style.display = "";
    banner.textContent = msg;
    banner.classList.toggle("critical", Boolean(s.loss_critical));
  } else {
    banner.style.display = "none";
    banner.textContent = "";
    banner.classList.remove("critical");
  }
}

async function loadSummary() {
  const s = await apiJson("/api/journal/summary");
  renderSummary(s);
}

async function runMarketScan() {
  await apiJson("/marketscan/run", { method: "POST", body: "{}" });
  // poll briefly
  for (let i = 0; i < 50; i++) {
    await new Promise((r) => setTimeout(r, 250));
    try {
      const latest = await apiJson("/marketscan/latest");
      if (latest.status === "completed" || latest.status === "failed") break;
    } catch {
      // ignore
    }
  }
  state.dashboardAt = 0;
  await refreshVisible({ force: true });
}

function modal() {
  return el("modal");
}

async function confirmDialog({ title, bodyHtml, okText = "Confirm" }) {
  const d = modal();
  el("modalTitle").textContent = title;
  el("modalBody").innerHTML = bodyHtml;
  el("modalOk").textContent = okText;
  if (!d.open) d.showModal();
  const res = await new Promise((resolve) => {
    const onClose = () => {
      d.removeEventListener("close", onClose);
      resolve(d.returnValue || "cancel");
    };
    d.addEventListener("close", onClose);
  });
  return res;
}

async function loadSignals({ dashboard = null, force = false } = {}) {
  const out = el("signalCards");
  const empty = el("signalsEmpty");
  const now = Date.now();
  if (!force && now - state.signalsAt < 8000 && out.innerHTML && out.innerHTML !== "Loading…") return;
  out.innerHTML = "Loading…";
  empty.style.display = "none";

  const mode = localStorage.getItem("signalsMode") || "active";
  let recs = [];
  let p = null;
  let msl = null;
  let triggersByTicker = {};
  if (dashboard) {
    recs = mode === "history" ? (dashboard.recommendations_history || []) : (dashboard.recommendations || []);
    p = dashboard.portfolio || null;
    msl = dashboard.marketscan_latest || null;
    const iw = dashboard.intraday_watchlist || [];
    if (Array.isArray(iw)) {
      for (const it of iw) {
        if (!it || !it.ticker) continue;
        triggersByTicker[String(it.ticker).toUpperCase()] = it;
      }
    }
  } else {
    const endpoint =
      mode === "history"
        ? "/api/recommendations/history?limit=200"
        : "/api/recommendations?status=active&limit=50";
    const [data, port, iw] = await Promise.all([
      apiJson(endpoint),
      getPortfolioCached(),
      apiJson("/api/intraday/watchlist?limit=30").catch(() => ({ items: [] })),
    ]);
    recs = data.recommendations || [];
    p = port;
    const items = iw && iw.items ? iw.items : [];
    if (Array.isArray(items)) {
      for (const it of items) {
        if (!it || !it.ticker) continue;
        triggersByTicker[String(it.ticker).toUpperCase()] = it;
      }
    }
  }

  if (mode === "history") {
    out.innerHTML = renderRecommendationsTable(recs, {
      cashUsd: Number((p && p.cash_usd) || 0),
      mode: "history",
      triggersByTicker,
    });
    state.signalsById = new Map(recs.map((r) => [String(r.rec_id), r]));
    state.signalsAt = now;
    return;
  }

  if (!recs.length) {
    // Fallback: show the latest market scan ranked list even if recommendations table is empty.
    try {
      const latest = msl || (await apiJson("/marketscan/latest"));
      const ranked = (latest.result && (latest.result.candidates || latest.result.ranked))
        ? (latest.result.candidates || latest.result.ranked)
        : [];
      if (ranked.length) {
        recs = ranked.map((c) => {
          const tp = c.trade_plan || {};
          const m = c.market || {};
          const sig = c.signals || {};
          const entry = Number(tp.entry_price || m.last_close || 0);
          const stop = Number(tp.stop_loss || 0);
          const shares = Number(tp.shares || 0);
          const notional = entry * shares;
          const cashAfter = Number(p.cash_usd || 0) - notional;
          const stopDist = entry > 0 && stop > 0 ? entry - stop : 0;
          const take = computeTakeProfit({
            entry,
            stop,
            atr: Number(m.atr14 || 0),
            sma20: Number(sig.sma20 || 0),
            rating: String(c.rating || ""),
          });
          const rr = stopDist > 0 ? (take - entry) / stopDist : 0;
          return {
            rec_id: `cand:${c.ticker}`,
            ticker: c.ticker,
            strategy: "manual",
            score: c.score,
            reasons: c.reasons || [],
            entry_price: entry,
            stop_loss: stop,
            take_profit: take,
            shares: shares,
            notional_usd: notional,
            max_loss_usd: stopDist * shares,
            risk_reward_ratio: rr,
            cash_after: cashAfter,
            cash_valid: cashAfter >= 0,
            expires_at: new Date(Date.now() + 2 * 3600 * 1000).toISOString(),
            created_at: new Date().toISOString(),
          };
        });
      }
    } catch {
      // ignore
    }
    if (!recs.length) {
      out.innerHTML = "";
      const cash = Number((p && p.cash_usd) || 0);
      const reason =
        cash <= 0
          ? "Cash is $0. Set cash in the Sync tab, then tap Run scan."
          : "No active recommendations yet. Tap Run scan (background scans may be paused outside market hours).";
      empty.style.display = "";
      empty.textContent = reason;
      return;
    }
  }

  out.innerHTML = renderRecommendationsTable(recs, {
    cashUsd: Number((p && p.cash_usd) || 0),
    mode: "active",
    triggersByTicker,
  });
  state.signalsById = new Map(recs.map((r) => [String(r.rec_id), r]));
  state.signalsAt = now;
}

async function loadScans({ dashboard = null, force = false } = {}) {
  const out = el("scansTable");
  out.innerHTML = "Loading…";
  const now = Date.now();
  if (!force && now - state.scansAt < 8000 && out.innerHTML && out.innerHTML !== "Loading…") return;
  const rows = dashboard ? (dashboard.scans || []) : ((await apiJson("/scans?limit=25")).scans || []);
  const pillClass = (status) => {
    if (status === "completed") return "pill ok";
    if (status === "failed") return "pill bad";
    return "pill warn";
  };
  const html = `
    <table>
      <thead>
        <tr>
          <th>Created</th>
          <th>Status</th>
          <th>Tickers</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map((s) => {
            const tickers = s.request && s.request.tickers ? s.request.tickers.join(",") : "";
            return `
              <tr>
                <td>${fmtTs(s.created_at)}</td>
                <td><span class="${pillClass(s.status)}">${s.status}</span></td>
                <td>${tickers}</td>
                <td><button class="btn btn-secondary" data-scan-view="${s.scan_id}">View</button></td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
  out.innerHTML = `<div class="table">${html}</div>`;
  state.scansAt = now;
  document.querySelectorAll("[data-scan-view]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-scan-view");
      await loadScanDetail(id);
    });
  });
}

async function loadScanDetail(scanId) {
  const box = el("scanDetailBox");
  box.innerHTML = "Loading…";
  let scan = null;
  for (let i = 0; i < 40; i++) {
    const data = await apiJson(`/scan/${encodeURIComponent(scanId)}`);
    scan = data.scan;
    if (scan && (scan.status === "completed" || scan.status === "failed")) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!scan) {
    box.innerHTML = `<pre>Scan not found.</pre>`;
    return;
  }
  const result = scan.result;
  const lines = [];
  lines.push(`Scan: ${scan.scan_id}`);
  lines.push(`Status: ${scan.status}`);
  lines.push(`Created: ${scan.created_at}`);
  if (scan.error) lines.push(`Error: ${scan.error}`);
  lines.push("");
  if (!result) {
    lines.push("No result yet.");
    box.innerHTML = `<pre>${lines.join("\n")}</pre>`;
    return;
  }
  for (const rep of result.reports || []) {
    lines.push(rep.summary || rep.ticker);
    if (rep.trade_plan && rep.trade_plan.enabled) {
      const tp = rep.trade_plan;
      lines.push(
        `  trade_plan: shares=${tp.shares}, stop_loss=${tp.stop_loss}, notional_usd=${tp.notional_usd}, cash_valid=${tp.cash_valid}`,
      );
    } else if (rep.trade_plan) {
      lines.push(`  trade_plan: disabled (${rep.trade_plan.reason || "n/a"})`);
    }
    if (rep.error) lines.push(`  error: ${rep.error}`);
    lines.push("");
  }
  box.innerHTML = `<pre>${lines.join("\n")}</pre>`;
}

async function runScan() {
  const tickers = (el("tickersInput").value || "")
    .split(",")
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);
  if (!tickers.length) return;
  const created = await apiJson("/scan", { method: "POST", body: JSON.stringify({ tickers, as_of: "auto" }) });
  await loadScans({ force: true });
  if (created && created.scan_id) {
    await loadScanDetail(created.scan_id);
  }
}

async function loadPositions({ dashboard = null, force = false } = {}) {
  const out = el("openPositions");
  out.innerHTML = "Loading…";
  const now = Date.now();
  if (!force && now - state.positionsAt < 8000 && out.innerHTML && out.innerHTML !== "Loading…") return;
  const trades = dashboard ? (dashboard.trades_open || []) : ((await apiJson("/api/trades?status=open&limit=200")).trades || []);
  if (!trades.length) {
    out.innerHTML = `<div class="box">No open trades.</div>`;
    return;
  }

  let priceByTicker = {};
  try {
    const latest = dashboard ? (dashboard.marketscan_latest || null) : (await apiJson("/marketscan/latest"));
    const candidates = (latest && latest.result && latest.result.ranked)
      ? latest.result.ranked
      : ((latest && latest.result && latest.result.candidates) ? latest.result.candidates : []);
    for (const c of candidates) {
      const t = c.ticker;
      const px = c.market && c.market.last_close;
      if (t && px != null) priceByTicker[String(t).toUpperCase()] = Number(px);
    }
  } catch {
    priceByTicker = {};
  }

  const rows = trades
    .map((t) => {
      const entryDate = t.entry_date;
      const daysHeld = Math.max(0, Math.floor((Date.now() - new Date(entryDate).getTime()) / 86400000));
      const stop = t.stop_loss != null ? fmtMoney(t.stop_loss) : "—";
      const curPx = priceByTicker[String(t.ticker || "").toUpperCase()];
      const curPxTxt = Number.isFinite(curPx) ? fmtMoney(curPx) : "—";
      const pnl = Number.isFinite(curPx) ? (curPx - Number(t.entry_price || 0)) * Number(t.shares || 0) : null;
      const pnlTxt = pnl == null ? "—" : fmtMoney(pnl);
      const pnlCls = pnl == null ? "pill" : pnl > 0 ? "pill ok" : pnl < 0 ? "pill bad" : "pill warn";
      return `
        <tr>
          <td><b>${t.ticker}</b></td>
          <td>${t.shares}</td>
          <td>${fmtMoney(t.entry_price)}</td>
          <td>${curPxTxt}</td>
          <td><span class="${pnlCls}">${pnlTxt}</span></td>
          <td>${stop}</td>
          <td>${daysHeld}</td>
          <td><button class="btn btn-secondary" data-close="${t.trade_id}">Sell</button></td>
        </tr>
      `;
    })
    .join("");

  out.innerHTML = `
    <div class="table">
      <table>
        <thead><tr><th>Ticker</th><th>Shares</th><th>Entry</th><th>Current</th><th>P&amp;L</th><th>Stop</th><th>Days</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  state.positionsAt = now;

  document.querySelectorAll("[data-close]").forEach((b) => {
    b.addEventListener("click", async () => {
      const id = b.getAttribute("data-close");
      const res = await confirmDialog({
        title: "Close trade",
        bodyHtml: `
          <label class="label">Exit price</label>
          <input id="mExit" class="input" type="number" step="0.01" inputmode="decimal" value="" />
          <div style="height:10px"></div>
          <label class="label">Exit reason</label>
          <input id="mExitReason" class="input" placeholder="manual / stop_loss / take_profit" value="manual" />
        `,
        okText: "Close",
      });
      if (res !== "ok") return;
      const exitPrice = parseFloat(el("mExit").value || "0");
      const exitReason = el("mExitReason").value || "manual";
      b.disabled = true;
      try {
        await apiJson(`/api/trade/close/${encodeURIComponent(id)}`, {
          method: "POST",
          body: JSON.stringify({ exit_price: exitPrice, exit_reason: exitReason }),
        });
        await Promise.all([loadSummary(), loadPositions(), loadPortfolio(), loadJournal()]);
      } catch (e) {
        alert(e.message);
      } finally {
        b.disabled = false;
      }
    });
  });
}

async function loadJournal({ dashboard = null, force = false } = {}) {
  const out = el("closedTrades");
  out.innerHTML = "Loading…";
  const now = Date.now();
  if (!force && now - state.journalAt < 8000 && out.innerHTML && out.innerHTML !== "Loading…") return;
  const trades = dashboard ? (dashboard.trades_closed || []) : ((await apiJson("/api/trades?status=closed&limit=200")).trades || []);
  if (!trades.length) {
    out.innerHTML = `<div class="box">No closed trades yet.</div>`;
    return;
  }
  const rows = trades
    .map((t) => {
      const pnl = Number(t.realised_pnl || 0);
      const cls = pnl > 0 ? "pill ok" : pnl < 0 ? "pill bad" : "pill warn";
      return `
        <tr>
          <td>${fmtTs(t.exit_date || t.updated_at)}</td>
          <td><b>${t.ticker}</b></td>
          <td>${t.shares}</td>
          <td>${fmtMoney(t.entry_price)}</td>
          <td>${fmtMoney(t.exit_price)}</td>
          <td><span class="${cls}">${fmtMoney(pnl)}</span></td>
          <td class="subtle">${t.exit_reason || ""}</td>
        </tr>
      `;
    })
    .join("");

  out.innerHTML = `
    <div class="table">
      <table>
        <thead><tr><th>Exit</th><th>Ticker</th><th>Shares</th><th>Entry</th><th>Exit Px</th><th>P&amp;L</th><th>Reason</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  state.journalAt = now;
}

async function loadPortfolio({ dashboard = null } = {}) {
  const box = el("portfolioBox");
  box.textContent = "Loading…";
  const p = dashboard ? dashboard.portfolio : await getPortfolioCached({ force: true });
  if (p && p.positions) {
    state.portfolio = p;
    state.portfolioAt = Date.now();
  }
  const positions = p.positions || [];
  const posRows = positions
    .map((x) => `<tr><td>${x.ticker}</td><td>${x.quantity}</td><td>${x.avg_price == null ? "" : x.avg_price}</td><td>${fmtTs(x.updated_at)}</td></tr>`)
    .join("");
  box.innerHTML = `
    <div class="pill ok">Cash: ${fmtMoney(p.cash_usd)}</div>
    <div style="height:10px"></div>
    <div class="table">
      <table>
        <thead><tr><th>Ticker</th><th>Quantity</th><th>Avg Price</th><th>Updated</th></tr></thead>
        <tbody>${posRows || ""}</tbody>
      </table>
    </div>
  `;
}

async function setCash() {
  const v = parseFloat(el("cashInput").value || "0");
  await apiJson("/portfolio/cash", { method: "POST", body: JSON.stringify({ cash_usd: v }) });
  state.portfolioAt = 0;
  state.dashboardAt = 0;
  await refreshVisible({ force: true });
}

async function uploadCsv() {
  const fileInput = el("csvFile");
  const mode = el("csvMode").value;
  if (!fileInput.files || !fileInput.files[0]) return;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  await apiJson(`/portfolio/revolut/upload?mode=${encodeURIComponent(mode)}`, { method: "POST", body: fd });
  state.portfolioAt = 0;
  state.dashboardAt = 0;
  await refreshVisible({ force: true });
}

function setTab(tab) {
  document.querySelectorAll(".tab").forEach((b) => {
    if (!b.dataset.tab) return;
    b.classList.toggle("is-active", b.dataset.tab === tab);
    b.setAttribute("aria-selected", b.dataset.tab === tab ? "true" : "false");
  });
  document.querySelectorAll("[data-tabpane]").forEach((p) => {
    p.classList.toggle("is-active", p.dataset.tabpane === tab);
  });
  localStorage.setItem("activeTab", tab);
}

async function loadIntradayConfig() {
  const box = el("configStatus");
  box.style.display = "none";
  try {
    const cfg = await apiJson("/api/config/intraday");
    const eff = cfg.effective || {};
    el("cfgIntradayEnabled").value = String(eff.enabled);
    el("cfgIntradayOnlyMH").value = String(eff.only_market_hours);
    el("cfgIntradayInterval").value = String(eff.interval || "15m");
    el("cfgIntradayPeriod").value = String(eff.period || "5d");
    el("cfgIntradayWatchlist").value = String(eff.watchlist_size == null ? 20 : eff.watchlist_size);
    el("cfgIntradayPoll").value = String(eff.poll_seconds == null ? 180 : eff.poll_seconds);
    const stored = cfg.stored;
    box.style.display = "";
    box.innerHTML = `<pre>Effective intraday config loaded.${stored && stored.updated_at ? `\nSaved: ${stored.updated_at}` : "\nSaved: (env defaults)"}</pre>`;
  } catch (e) {
    box.style.display = "";
    box.innerHTML = `<pre>Failed to load config: ${escapeHtml(e.message || String(e))}</pre>`;
  }
}

async function saveIntradayConfig() {
  const box = el("configStatus");
  box.style.display = "none";
  const body = {
    enabled: el("cfgIntradayEnabled").value === "true",
    only_market_hours: el("cfgIntradayOnlyMH").value === "true",
    interval: el("cfgIntradayInterval").value,
    period: el("cfgIntradayPeriod").value,
    watchlist_size: parseInt(el("cfgIntradayWatchlist").value || "20", 10),
    poll_seconds: parseInt(el("cfgIntradayPoll").value || "180", 10),
  };
  try {
    const res = await apiJson("/api/config/intraday", { method: "POST", body: JSON.stringify(body) });
    box.style.display = "";
    box.innerHTML = `<pre>Saved.\nUpdated: ${escapeHtml((res.saved && res.saved.updated_at) || "")}</pre>`;
    state.dashboardAt = 0;
  } catch (e) {
    box.style.display = "";
    box.innerHTML = `<pre>Save failed: ${escapeHtml(e.message || String(e))}</pre>`;
  }
}

async function refreshIntradayNow() {
  const box = el("configStatus");
  box.style.display = "none";
  try {
    await apiJson("/api/intraday/watchlist?refresh=true");
    box.style.display = "";
    box.innerHTML = `<pre>Intraday refresh requested.</pre>`;
    state.dashboardAt = 0;
    await refreshVisible({ force: true });
  } catch (e) {
    box.style.display = "";
    box.innerHTML = `<pre>Intraday refresh failed: ${escapeHtml(e.message || String(e))}</pre>`;
  }
}

async function loadPortfolioConfig() {
  try {
    const cfg = await apiJson("/api/config/portfolio");
    const eff = cfg.effective || {};
    el("cfgTotalPortfolio").value = String(eff.total_portfolio_usd == null ? 0 : eff.total_portfolio_usd);
    el("cfgSleevePct").value = String(((Number(eff.sleeve_pct || 0.01) * 100).toFixed(2)));
    el("cfgMaxPositions").value = String(eff.max_positions == null ? 4 : eff.max_positions);
    el("cfgRiskPct").value = String(((Number(eff.risk_per_trade_pct || 0.01) * 100).toFixed(2)));
    el("cfgMaxPosPct").value = String(((Number(eff.max_position_pct || 0.35) * 100).toFixed(2)));
  } catch {
    // ignore
  }
}

async function savePortfolioConfig() {
  const total = parseFloat(el("cfgTotalPortfolio").value || "0");
  const sleevePct = parseFloat(el("cfgSleevePct").value || "1");
  const maxPos = parseInt(el("cfgMaxPositions").value || "4", 10);
  const riskPct = parseFloat(el("cfgRiskPct").value || "1");
  const maxPosPct = parseFloat(el("cfgMaxPosPct").value || "35");
  const body = {
    total_portfolio_usd: total,
    sleeve_pct: sleevePct / 100.0,
    max_positions: maxPos,
    risk_per_trade_pct: riskPct / 100.0,
    max_position_pct: maxPosPct / 100.0,
  };
  try {
    await apiJson("/api/config/portfolio", { method: "POST", body: JSON.stringify(body) });
    await refreshPlan();
  } catch (e) {
    const box = el("planBox");
    box.innerHTML = `<pre>Save failed: ${escapeHtml(e.message || String(e))}</pre>`;
  }
}

async function refreshPlan() {
  const box = el("planBox");
  box.textContent = "Loading plan…";
  try {
    const plan = await apiJson("/api/portfolio/plan");
    if (!plan.ok) {
      box.innerHTML = `<pre>Plan not available: ${escapeHtml(plan.error || "unknown")}\n\nSet Total portfolio USD in Config.</pre>`;
      return;
    }
    const lines = plan.lines || [];
    const rows = lines
      .map((x) => {
        const st = x.trigger_status || "WAITING";
        return `${x.ticker} | ${st} | shares=${x.shares} | entry=${fmtMoney(x.entry)} | stop=${fmtMoney(x.stop)} | take=${fmtMoney(x.take)} | notional=${fmtMoney(x.notional_usd)} | risk=${fmtMoney(x.risk_usd)}`;
      })
      .join("\n");
    box.innerHTML = `<pre>Sleeve: ${fmtMoney(plan.sleeve_value_usd)} (${(Number(plan.sleeve_pct) * 100).toFixed(2)}%)\nAllocated: ${fmtMoney(plan.allocated_usd)}\nRisk/trade: ${fmtMoney(plan.risk_per_trade_usd)}\n\n${rows || "No eligible TRIGGERED/WAITING recommendations yet."}</pre>`;
  } catch (e) {
    box.innerHTML = `<pre>Failed to load plan: ${escapeHtml(e.message || String(e))}</pre>`;
  }
}

async function refreshVisible({ force = false } = {}) {
  let dash;
  try {
    dash = await getDashboardCached({ force });
  } catch (e) {
    const msg = `Dashboard load failed: ${e && e.message ? e.message : String(e)}\n\nTry:\n- refresh the page\n- open /health\n- open /docs`;
    showFatal(msg);
    const out = el("signalCards");
    if (out) out.innerHTML = "";
    const empty = el("signalsEmpty");
    if (empty) {
      empty.style.display = "";
      empty.textContent = "Failed to load data. See error banner above.";
    }
    return;
  }
  renderSummary(dash.journal_summary);
  renderMarketScanStatus(dash.marketscan_status);
  const msi = el("marketScanInfo");
  const latest = dash.marketscan_latest;
  if (msi) {
    if (!latest) {
      msi.style.display = "";
      msi.innerHTML = `<pre>No market scan has been run yet.\n\nTap “Run scan”.</pre>`;
    } else if (latest.status !== "completed") {
      msi.style.display = "";
      msi.innerHTML = `<pre>Last market scan status: ${latest.status}\nCreated: ${latest.created_at}\nError: ${latest.error || "—"}</pre>`;
    } else {
      const res = latest.result || {};
      const scored = res.scored_size == null ? "—" : res.scored_size;
      const uni = res.universe_size == null ? "—" : res.universe_size;
      const ranked = (res.ranked || []).length;
      const cand = (res.candidates || []).length;
      const failed = res.failed_size == null ? "—" : res.failed_size;
      const errors = (res.errors_sample || []).slice(0, 5).map((x) => `- ${x.ticker}: ${x.error}`).join("\n");
      const err = latest.error || "—";
      msi.style.display = "";
      msi.innerHTML = `<pre>Last market scan: completed\nUniverse: ${uni}\nScored: ${scored}\nFailed: ${failed}\nRanked: ${ranked}\nCandidates: ${cand}\nError: ${err}${errors ? "\n\nSample fetch errors:\n" + errors : ""}</pre>`;
    }
  }
  const tab = localStorage.getItem("activeTab") || "signals";
  if (tab === "signals") await loadSignals({ dashboard: dash, force });
  if (tab === "scans") await loadScans({ dashboard: dash, force });
  if (tab === "positions") await loadPositions({ dashboard: dash, force });
  if (tab === "journal") await loadJournal({ dashboard: dash, force });
  if (tab === "sync") await loadPortfolio({ dashboard: dash });
  if (tab === "config") await loadIntradayConfig();
  if (tab === "config") {
    await loadPortfolioConfig();
    await refreshPlan();
  }
  const serverTs = dash && dash.server_time_utc ? new Date(dash.server_time_utc) : new Date();
  el("lastUpdated").textContent = `Updated ${serverTs.toLocaleTimeString()}`;
}

async function main() {
  window.addEventListener("error", (evt) => {
    showFatal(`JavaScript error: ${evt.message || "unknown"}\n${evt.filename || ""}:${evt.lineno || ""}`);
  });
  window.addEventListener("unhandledrejection", (evt) => {
    const r = evt.reason;
    showFatal(`Unhandled promise rejection: ${r && r.message ? r.message : String(r)}`);
  });

  try {
    el("lastUpdated").textContent = "Loading…";
  } catch {
    // ignore
  }

  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => {
      setTab(b.dataset.tab);
      refreshVisible().catch((e) => alert(e.message));
    });
  });

  el("signalCards").addEventListener("click", async (evt) => {
    const execBtn = evt.target.closest("[data-exec]");
    const skipBtn = evt.target.closest("[data-skip]");
    const copyBtn = evt.target.closest("[data-copy]");
    const hideBtn = evt.target.closest("[data-hide]");
    const row = evt.target.closest("tr[data-rec-row]");
    if (!execBtn && !skipBtn && !copyBtn && !hideBtn) {
      if (row) {
        const id = row.getAttribute("data-rec-row");
        const detail = document.querySelector(`tr[data-rec-detail="${CSS.escape(id)}"]`);
        row.classList.toggle("is-open");
        if (detail) detail.classList.toggle("is-open");
      }
      return;
    }

    const srcEl = execBtn || skipBtn || copyBtn || hideBtn;
    const id =
      (execBtn && execBtn.getAttribute("data-exec")) ||
      (skipBtn && skipBtn.getAttribute("data-skip")) ||
      (copyBtn && copyBtn.getAttribute("data-copy")) ||
      (hideBtn && hideBtn.getAttribute("data-hide"));
    if (!id) return;

    if (copyBtn) {
      const rec = state.signalsById.get(String(id));
      if (!rec) return;
      const text = String(rec.ticker || "");
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        // fallback
        prompt("Copy ticker", text);
      }
      return;
    }

    if (hideBtn) {
      addSkippedSignalId(id);
      state.dashboardAt = 0;
      await refreshVisible({ force: true });
      return;
    }

    if (skipBtn) {
      skipBtn.disabled = true;
      try {
        addSkippedSignalId(id);
        if (!String(id).startsWith("cand:")) {
          await apiJson(`/api/recommendations/${encodeURIComponent(id)}/skip`, {
            method: "POST",
            body: "{}",
          });
        }
        state.dashboardAt = 0;
        await refreshVisible({ force: true });
      } catch (e) {
        alert(e.message);
      } finally {
        skipBtn.disabled = false;
      }
      return;
    }

    const rec = state.signalsById.get(String(id));
    if (!rec) return;
    const card = document.querySelector(`[data-rec="${CSS.escape(id)}"]`);
    const overBudget = rec.cash_valid === false || Number(rec.cash_after || 0) < 0;
    const hint = overBudget
      ? `<div class="pill bad" style="display:inline-block;margin-top:8px;">Over budget — reduce shares to fit cash.</div>`
      : "";
    const bodyHtml = `
      <div class="subtle">Adjust entry and shares if needed, then confirm.</div>
      ${hint}
      <div style="height:10px"></div>
      <label class="label">Entry price</label>
      <input id="mEntry" class="input" type="number" step="0.01" inputmode="decimal" value="${rec.entry_price == null ? "" : rec.entry_price}" />
      <div style="height:10px"></div>
      <label class="label">Shares</label>
      <input id="mShares" class="input" type="number" step="1" inputmode="numeric" value="${rec.shares == null ? "" : rec.shares}" />
    `;
    const res = await confirmDialog({ title: `Execute ${rec.ticker}`, bodyHtml, okText: "Execute" });
    if (res !== "ok") return;
    const entry = parseFloat(el("mEntry").value || "0");
    const shares = parseFloat(el("mShares").value || "0");

    execBtn.disabled = true;
    if (card) card.style.opacity = "0.7";
    try {
      if (String(id).startsWith("cand:")) {
        await apiJson("/api/trade/execute", {
          method: "POST",
          body: JSON.stringify({
            ticker: rec.ticker,
            entry_price: entry,
            shares,
            stop_loss: rec.stop_loss,
            take_profit: rec.take_profit,
            strategy: rec.strategy,
            reason: (rec.reasons || []).join("; "),
          }),
        });
      } else {
        await apiJson(`/api/recommendations/${encodeURIComponent(id)}/execute`, {
          method: "POST",
          body: JSON.stringify({ entry_price: entry, shares }),
        });
      }
      state.dashboardAt = 0;
      await refreshVisible({ force: true });
    } catch (e) {
      alert(e.message);
    } finally {
      execBtn.disabled = false;
      if (card) card.style.opacity = "";
    }
  });

  const setSignalsMode = (mode) => {
    localStorage.setItem("signalsMode", mode);
    el("signalsModeActive").classList.toggle("is-active", mode === "active");
    el("signalsModeHistory").classList.toggle("is-active", mode === "history");
  };
  el("signalsModeActive").addEventListener("click", () => {
    setSignalsMode("active");
    refreshVisible({ force: true }).catch((e) => alert(e.message));
  });
  el("signalsModeHistory").addEventListener("click", () => {
    setSignalsMode("history");
    refreshVisible({ force: true }).catch((e) => alert(e.message));
  });
  el("clearSkippedBtn").addEventListener("click", () => {
    localStorage.removeItem("skippedSignals");
    refreshVisible({ force: true }).catch((e) => alert(e.message));
  });

  el("runMarketScanBtn").addEventListener("click", () => runMarketScan().catch((e) => alert(e.message)));
  el("refreshSignalsBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("refreshScansBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("runScanBtn").addEventListener("click", () => runScan().catch((e) => alert(e.message)));
  el("refreshPositionsBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("refreshJournalBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("setCashBtn").addEventListener("click", () => setCash().catch((e) => alert(e.message)));
  el("uploadCsvBtn").addEventListener("click", () => uploadCsv().catch((e) => alert(e.message)));
  el("refreshConfigBtn").addEventListener("click", () => loadIntradayConfig().catch((e) => alert(e.message)));
  el("saveConfigBtn").addEventListener("click", () => saveIntradayConfig().catch((e) => alert(e.message)));
  el("refreshIntradayNowBtn").addEventListener("click", () => refreshIntradayNow().catch((e) => alert(e.message)));
  el("savePortfolioConfigBtn").addEventListener("click", () => savePortfolioConfig().catch((e) => alert(e.message)));
  el("refreshPlanBtn").addEventListener("click", () => refreshPlan().catch((e) => alert(e.message)));

  setTab(localStorage.getItem("activeTab") || "signals");
  setSignalsMode(localStorage.getItem("signalsMode") || "active");
  await refreshVisible({ force: true });

  setInterval(() => {
    if ((localStorage.getItem("activeTab") || "signals") !== "signals") return;
    document.querySelectorAll("[data-exp]").forEach((span) => {
      const id = span.getAttribute("data-exp");
      const card = document.querySelector(`[data-rec="${CSS.escape(id)}"]`);
      if (!card) return;
      const expiresAt = card.getAttribute("data-expires");
      if (expiresAt) span.textContent = fmtCountdown(msUntil(expiresAt));
    });
  }, 1000);

  setInterval(() => {
    refreshVisible().catch(() => {});
  }, 60000);
}

main().catch((e) => showFatal(e && e.message ? e.message : String(e)));

