function el(id) {
  return document.getElementById(id);
}

async function apiJson(path, opts = {}) {
  const headers = opts.body instanceof FormData ? {} : { "content-type": "application/json" };
  const r = await fetch(path, { headers: { ...headers, ...(opts.headers || {}) }, ...opts });
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

async function loadSummary() {
  const s = await apiJson("/api/journal/summary");
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
  await loadSignals();
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

async function loadSignals() {
  const out = el("signalCards");
  const empty = el("signalsEmpty");
  out.innerHTML = "Loading…";
  empty.style.display = "none";

  const data = await apiJson("/api/recommendations?status=active&limit=50");
  const recs = data.recommendations || [];
  if (!recs.length) {
    out.innerHTML = "";
    let reason = "";
    try {
      const p = await apiJson("/portfolio");
      const cash = Number(p.cash_usd || 0);
      if (cash <= 0) {
        reason = "Cash is $0. Set cash in the Sync tab so position sizing can produce recommendations.";
      }
    } catch {
      // ignore
    }
    if (!reason) {
      reason = "No active recommendations. Tap Run scan (background scans may be paused outside market hours).";
    }
    empty.style.display = "";
    empty.textContent = reason;
    return;
  }

  out.innerHTML = recs
    .map((r) => {
      const tag = (r.strategy || "manual").toLowerCase();
      const expiresIn = fmtCountdown(msUntil(r.expires_at));
      const reasons = (r.reasons || []).slice(0, 4);
      return `
        <div class="action-card" data-rec="${r.rec_id}" data-expires="${r.expires_at}">
          <div class="action-top">
            <div>
              <div class="ticker">${r.ticker}</div>
              <div class="subtle">Expires in <span data-exp="${r.rec_id}">${expiresIn}</span></div>
            </div>
            <div class="tag ${tag}">${tag}</div>
          </div>
          <div class="meta">
            <div class="kv"><div class="k">Score</div><div class="v">${Number(r.score || 0).toFixed(1)}</div></div>
            <div class="kv"><div class="k">Cash after</div><div class="v">${fmtMoney(r.cash_after)}</div></div>
            <div class="kv"><div class="k">Entry</div><div class="v">${fmtMoney(r.entry_price)}</div></div>
            <div class="kv"><div class="k">Stop</div><div class="v">${fmtMoney(r.stop_loss)}</div></div>
            <div class="kv"><div class="k">Shares</div><div class="v">${r.shares ?? "—"}</div></div>
            <div class="kv"><div class="k">Max loss</div><div class="v">${fmtMoney(r.max_loss_usd)}</div></div>
            <div class="kv"><div class="k">Take profit</div><div class="v">${fmtMoney(r.take_profit)}</div></div>
            <div class="kv"><div class="k">Notional</div><div class="v">${fmtMoney(r.notional_usd)}</div></div>
            <div class="kv"><div class="k">R/R</div><div class="v">${Number(r.risk_reward_ratio || 0).toFixed(2)}</div></div>
            <div class="kv"><div class="k">Stops at</div><div class="v">${fmtMoney(r.stop_loss)}</div></div>
          </div>
          <div class="reasons">
            <div class="k">Why</div>
            <ul>${reasons.map((x) => `<li>${x}</li>`).join("")}</ul>
          </div>
          <div class="actions">
            <button class="btn" data-exec="${r.rec_id}">Execute</button>
            <button class="btn btn-secondary" data-skip="${r.rec_id}">Skip</button>
          </div>
        </div>
      `;
    })
    .join("");

  document.querySelectorAll("[data-skip]").forEach((b) => {
    b.addEventListener("click", async () => {
      const id = b.getAttribute("data-skip");
      b.disabled = true;
      try {
        await apiJson(`/api/recommendations/${encodeURIComponent(id)}/skip`, { method: "POST", body: "{}" });
        await loadSignals();
      } catch (e) {
        alert(e.message);
      } finally {
        b.disabled = false;
      }
    });
  });

  document.querySelectorAll("[data-exec]").forEach((b) => {
    b.addEventListener("click", async () => {
      const id = b.getAttribute("data-exec");
      const card = document.querySelector(`[data-rec="${CSS.escape(id)}"]`);
      const rec = recs.find((x) => x.rec_id === id);
      if (!rec) return;
      const bodyHtml = `
        <div class="subtle">Adjust entry and shares if needed, then confirm.</div>
        <div style="height:10px"></div>
        <label class="label">Entry price</label>
        <input id="mEntry" class="input" type="number" step="0.01" inputmode="decimal" value="${rec.entry_price ?? ""}" />
        <div style="height:10px"></div>
        <label class="label">Shares</label>
        <input id="mShares" class="input" type="number" step="1" inputmode="numeric" value="${rec.shares ?? ""}" />
      `;
      const res = await confirmDialog({ title: `Execute ${rec.ticker}`, bodyHtml, okText: "Execute" });
      if (res !== "ok") return;
      const entry = parseFloat(el("mEntry").value || "0");
      const shares = parseFloat(el("mShares").value || "0");
      b.disabled = true;
      if (card) card.style.opacity = "0.7";
      try {
        await apiJson(`/api/recommendations/${encodeURIComponent(id)}/execute`, {
          method: "POST",
          body: JSON.stringify({ entry_price: entry, shares }),
        });
        await Promise.all([loadSummary(), loadSignals(), loadPositions(), loadPortfolio(), loadJournal()]);
      } catch (e) {
        alert(e.message);
      } finally {
        b.disabled = false;
        if (card) card.style.opacity = "";
      }
    });
  });
}

async function loadScans() {
  const out = el("scansTable");
  out.innerHTML = "Loading…";
  const data = await apiJson("/scans?limit=25");
  const rows = data.scans || [];
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
  const data = await apiJson(`/scan/${encodeURIComponent(scanId)}`);
  const scan = data.scan;
  const result = scan.result;
  if (!result) {
    box.innerHTML = `<pre>Status: ${scan.status}\n\nNo result yet.</pre>`;
    return;
  }
  const lines = [];
  lines.push(`Scan: ${scan.scan_id}`);
  lines.push(`Status: ${scan.status}`);
  lines.push(`Created: ${scan.created_at}`);
  if (scan.error) lines.push(`Error: ${scan.error}`);
  lines.push("");
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
  await apiJson("/scan", { method: "POST", body: JSON.stringify({ tickers, as_of: "auto" }) });
  await loadScans();
}

async function loadPositions() {
  const out = el("openPositions");
  out.innerHTML = "Loading…";
  const data = await apiJson("/api/trades?status=open&limit=200");
  const trades = data.trades || [];
  if (!trades.length) {
    out.innerHTML = `<div class="box">No open trades.</div>`;
    return;
  }

  let priceByTicker = {};
  try {
    const latest = await apiJson("/marketscan/latest");
    const candidates = (latest.result && latest.result.candidates) ? latest.result.candidates : [];
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
          <td><button class="btn btn-secondary" data-close="${t.trade_id}">Close</button></td>
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

async function loadJournal() {
  const out = el("closedTrades");
  out.innerHTML = "Loading…";
  const data = await apiJson("/api/trades?status=closed&limit=200");
  const trades = data.trades || [];
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
}

async function loadPortfolio() {
  const box = el("portfolioBox");
  box.textContent = "Loading…";
  const p = await apiJson("/portfolio");
  const positions = p.positions || [];
  const posRows = positions
    .map((x) => `<tr><td>${x.ticker}</td><td>${x.quantity}</td><td>${x.avg_price ?? ""}</td><td>${fmtTs(x.updated_at)}</td></tr>`)
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
  await Promise.all([loadSummary(), loadPortfolio()]);
}

async function uploadCsv() {
  const fileInput = el("csvFile");
  const mode = el("csvMode").value;
  if (!fileInput.files || !fileInput.files[0]) return;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  await apiJson(`/portfolio/revolut/upload?mode=${encodeURIComponent(mode)}`, { method: "POST", body: fd });
  await Promise.all([loadSummary(), loadPortfolio()]);
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

async function refreshVisible() {
  await loadSummary();
  const tab = localStorage.getItem("activeTab") || "signals";
  if (tab === "signals") await loadSignals();
  if (tab === "scans") await loadScans();
  if (tab === "positions") await loadPositions();
  if (tab === "journal") await loadJournal();
  if (tab === "sync") await loadPortfolio();
  el("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function main() {
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => {
      setTab(b.dataset.tab);
      refreshVisible().catch((e) => alert(e.message));
    });
  });

  el("runMarketScanBtn").addEventListener("click", () => runMarketScan().catch((e) => alert(e.message)));
  el("refreshSignalsBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("refreshScansBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("runScanBtn").addEventListener("click", () => runScan().catch((e) => alert(e.message)));
  el("refreshPositionsBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("refreshJournalBtn").addEventListener("click", () => refreshVisible().catch((e) => alert(e.message)));
  el("setCashBtn").addEventListener("click", () => setCash().catch((e) => alert(e.message)));
  el("uploadCsvBtn").addEventListener("click", () => uploadCsv().catch((e) => alert(e.message)));

  await loadMarketScanStatus();
  setTab(localStorage.getItem("activeTab") || "signals");
  await refreshVisible();

  setInterval(() => {
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

main().catch((e) => alert(e.message));

