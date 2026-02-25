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

function renderSignalCards(recs, { cashUsd = null, mode = "active" } = {}) {
  const skipped = mode === "active" ? getSkippedSignalIds() : new Set();
  return recs
    .filter((r) => !skipped.has(String(r.rec_id)))
    .map((r) => {
      const tag = (r.strategy || "manual").toLowerCase();
      const expiresIn = fmtCountdown(msUntil(r.expires_at));
      const reasons = (r.reasons || []).slice(0, 4);
      const cashAfter = Number(r.cash_after);
      const overBudget = r.cash_valid === false || (Number.isFinite(cashAfter) && cashAfter < 0);
      const budgetPill = overBudget
        ? `<span class="pill bad">Over budget</span>`
        : `<span class="pill ok">Cash OK</span>`;
      const plain = plainEnglishReason(r, Number.isFinite(cashUsd) ? cashUsd : null);
      const status = (r.status || (mode === "history" ? "history" : "active")).toLowerCase();
      const statusPill =
        status === "executed"
          ? `<span class="pill ok">Executed</span>`
          : status === "skipped"
            ? `<span class="pill warn">Skipped</span>`
            : status === "expired"
              ? `<span class="pill bad">Expired</span>`
              : `<span class="pill ok">Active</span>`;
      const timingLine =
        mode === "history"
          ? `Created ${fmtTs(r.created_at)}`
          : `Expires in <span data-exp="${r.rec_id}">${expiresIn}</span>`;
      const rating = ratingPill(r.rating);
      const chips = mechanismChips(r.mechanisms);
      return `
        <div class="action-card ${overBudget ? "over-budget" : ""}" data-rec="${r.rec_id}" data-expires="${r.expires_at}">
          <div class="action-top">
            <div>
              <div class="ticker">${escapeHtml(r.ticker)}</div>
              <div class="subtle">
                ${timingLine}
                &nbsp;·&nbsp; <span class="pill ok">BUY</span>
                ${rating ? "&nbsp;·&nbsp;" + rating : ""}
                &nbsp;·&nbsp; ${budgetPill}
                &nbsp;·&nbsp; ${statusPill}
              </div>
              ${chips ? `<div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:6px;">${chips}</div>` : ""}
            </div>
            <div class="tag ${tag}">${escapeHtml(tag)}</div>
          </div>
          <div class="meta">
            <div class="kv"><div class="k">Score</div><div class="v">${Number(r.score || 0).toFixed(1)}</div></div>
            <div class="kv"><div class="k">Cash after</div><div class="v">${fmtMoney(r.cash_after)}</div></div>
            <div class="kv"><div class="k">Entry</div><div class="v">${fmtMoney(r.entry_price)}</div></div>
            <div class="kv"><div class="k">Stop</div><div class="v">${fmtMoney(r.stop_loss)}</div></div>
            <div class="kv"><div class="k">Shares</div><div class="v">${r.shares == null ? "—" : r.shares}</div></div>
            <div class="kv"><div class="k">Max loss</div><div class="v">${fmtMoney(r.max_loss_usd)}</div></div>
            <div class="kv"><div class="k">Take profit</div><div class="v">${fmtMoney(r.take_profit)}</div></div>
            <div class="kv"><div class="k">Notional</div><div class="v">${fmtMoney(r.notional_usd)}</div></div>
            <div class="kv"><div class="k">R/R</div><div class="v">${Number(r.risk_reward_ratio || 0).toFixed(2)}</div></div>
            <div class="kv"><div class="k">Stop distance</div><div class="v">${fmtMoney(Number(r.entry_price || 0) - Number(r.stop_loss || 0))}</div></div>
          </div>
          <div class="reasons">
            <div class="k">Key reasons</div>
            <ul>${reasons.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>
          </div>
          <details class="details">
            <summary>Deep dive (plain English)</summary>
            <div class="plain">${escapeHtml(plain)}</div>
          </details>
          ${
            mode === "active"
              ? `<div class="actions">
                   <button class="btn" data-exec="${r.rec_id}">Execute</button>
                   <button class="btn btn-secondary" data-skip="${r.rec_id}">Skip</button>
                 </div>`
              : `<div class="actions">
                   <button class="btn btn-secondary" data-copy="${r.rec_id}">Copy ticker</button>
                   <button class="btn btn-secondary" data-hide="${r.rec_id}">Hide</button>
                 </div>`
          }
        </div>
      `;
    })
    .join("");
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
  if (dashboard) {
    recs = mode === "history" ? (dashboard.recommendations_history || []) : (dashboard.recommendations || []);
    p = dashboard.portfolio || null;
    msl = dashboard.marketscan_latest || null;
  } else {
    const endpoint =
      mode === "history"
        ? "/api/recommendations/history?limit=200"
        : "/api/recommendations?status=active&limit=50";
    const [data, port] = await Promise.all([
      apiJson(endpoint),
      getPortfolioCached(),
    ]);
    recs = data.recommendations || [];
    p = port;
  }

  if (mode === "history") {
    out.innerHTML = renderSignalCards(recs, { cashUsd: Number((p && p.cash_usd) || 0), mode: "history" });
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
          const entry = Number(tp.entry_price || m.last_close || 0);
          const stop = Number(tp.stop_loss || 0);
          const shares = Number(tp.shares || 0);
          const notional = entry * shares;
          const cashAfter = Number(p.cash_usd || 0) - notional;
          const stopDist = entry > 0 && stop > 0 ? entry - stop : 0;
          const take = entry + (2 * stopDist);
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

  out.innerHTML = renderSignalCards(recs, { cashUsd: Number((p && p.cash_usd) || 0), mode: "active" });
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
    if (!execBtn && !skipBtn && !copyBtn && !hideBtn) return;

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

