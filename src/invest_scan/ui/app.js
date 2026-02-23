async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "content-type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const isJson = (r.headers.get("content-type") || "").includes("application/json");
  const body = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = typeof body === "string" ? body : JSON.stringify(body);
    throw new Error(`${r.status} ${r.statusText}: ${msg}`);
  }
  return body;
}

function el(id) {
  return document.getElementById(id);
}

function fmtTs(s) {
  if (!s) return "";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

async function loadAutoscan() {
  try {
    const st = await api("/autoscan/status");
    const enabled = st.enabled ? "enabled" : "disabled";
    el("autoscanStatus").textContent = `Autoscan: ${enabled} | interval=${st.interval_seconds}s | tickers=${st.tickers_csv}`;
  } catch (e) {
    el("autoscanStatus").textContent = `Autoscan: error (${e.message})`;
  }
}

async function loadMarketScanStatus() {
  try {
    const st = await api("/marketscan/status");
    const enabled = st.enabled ? "enabled" : "disabled";
    el("marketscanStatus").textContent = `Market scan: ${enabled} | interval=${st.interval_seconds}s | top_n=${st.top_n} | universe=${st.universe_source}`;
  } catch (e) {
    el("marketscanStatus").textContent = `Market scan: error (${e.message})`;
  }
}

function scorePill(score) {
  const s = Number(score || 0);
  if (s >= 20) return `<span class="pill ok">${s.toFixed(1)}</span>`;
  if (s >= 5) return `<span class="pill warn">${s.toFixed(1)}</span>`;
  return `<span class="pill bad">${s.toFixed(1)}</span>`;
}

async function loadMarketCandidates() {
  const out = el("marketCandidatesTable");
  out.innerHTML = "Loading latest market scan...";
  let latest;
  try {
    latest = await api("/marketscan/latest", { method: "GET" });
  } catch (e) {
    out.innerHTML = `<div class="box"><pre>No market scan yet.\n\nClick “Run market scan”.\n\n${e.message}</pre></div>`;
    return;
  }
  const result = latest.result;
  if (!result || !result.candidates) {
    out.innerHTML = `<div class="box"><pre>Status: ${latest.status}\nNo results yet.</pre></div>`;
    return;
  }

  const rows = result.candidates || [];
  const html = `
    <table>
      <thead>
        <tr>
          <th>Score</th>
          <th>Ticker</th>
          <th>Why</th>
          <th>Entry</th>
          <th>Stop</th>
          <th>Shares</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map((c) => {
            const tp = c.trade_plan || {};
            const m = c.market || {};
            const reasons = (c.reasons || []).slice(0, 3).join("; ");
            return `
              <tr>
                <td>${scorePill(c.score)}</td>
                <td><b>${c.ticker}</b></td>
                <td class="subtle">${reasons}</td>
                <td>${m.last_close ?? ""}</td>
                <td>${tp.stop_loss ? Number(tp.stop_loss).toFixed(2) : ""}</td>
                <td>${tp.shares ?? ""}</td>
                <td><button class="btn btn-secondary" data-cand="${c.ticker}">Details</button></td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
  out.innerHTML = `<div class="table">${html}</div>`;

  document.querySelectorAll("[data-cand]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.getAttribute("data-cand");
      const cand = rows.find((x) => x.ticker === t);
      showCandidateDetail(cand, result);
    });
  });

  if (rows[0]) showCandidateDetail(rows[0], result);
}

function showCandidateDetail(c, result) {
  const box = el("marketCandidateDetail");
  if (!c) {
    box.innerHTML = "<pre>No candidate selected.</pre>";
    return;
  }
  const m = c.market || {};
  const s = c.signals || {};
  const tp = c.trade_plan || {};
  const lines = [];
  lines.push(`${c.ticker} — score ${Number(c.score || 0).toFixed(1)}`);
  lines.push("");
  lines.push("WHY:");
  for (const r of (c.reasons || [])) lines.push(`- ${r}`);
  lines.push("");
  lines.push("SIGNALS:");
  lines.push(`- trend: ${s.trend}`);
  lines.push(`- momentum_score: ${s.momentum_score}`);
  lines.push(`- mean_reversion: ${s.mean_reversion}`);
  lines.push(`- volume_spike: ${s.volume_spike}`);
  lines.push("");
  lines.push("MARKET:");
  lines.push(`- last_close: ${m.last_close}`);
  lines.push(`- return_1w: ${m.return_1w}`);
  lines.push(`- return_1m: ${m.return_1m}`);
  lines.push(`- volatility_60d_ann: ${m.volatility_60d_ann}`);
  lines.push(`- atr14: ${m.atr14}`);
  lines.push("");
  lines.push("TRADE PLAN (MVP):");
  if (tp.enabled) {
    lines.push(`- shares: ${tp.shares}`);
    lines.push(`- entry: ${tp.entry_price}`);
    lines.push(`- stop_loss: ${tp.stop_loss}`);
    lines.push(`- notional_usd: ${tp.notional_usd}`);
    lines.push(`- cash_valid: ${tp.cash_valid}`);
  } else {
    lines.push(`- disabled: ${tp.reason || "n/a"}`);
  }
  lines.push("");
  lines.push(`Scan generated: ${result.generated_at}`);
  box.innerHTML = `<pre>${lines.join("\n")}</pre>`;
}

async function runMarketScan() {
  await api("/marketscan/run", { method: "POST", body: "{}" });
  // poll briefly
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 250));
    try {
      const latest = await api("/marketscan/latest", { method: "GET" });
      if (latest.status === "completed") break;
    } catch {
      // ignore
    }
  }
  await loadMarketCandidates();
}

async function loadScans() {
  const out = el("scansTable");
  out.innerHTML = "Loading scans...";
  const data = await api("/scans?limit=25", { method: "GET" });

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
          <th>Scan ID</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map((s) => {
            const tickers = (s.request && s.request.tickers) ? s.request.tickers.join(",") : "";
            return `
              <tr>
                <td>${fmtTs(s.created_at)}</td>
                <td><span class="${pillClass(s.status)}">${s.status}</span></td>
                <td>${tickers}</td>
                <td><button class="btn btn-secondary" data-scan-view="${s.scan_id}">View</button></td>
                <td style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">${s.scan_id}</td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
  out.innerHTML = `<div class="table">${html}</div>`;

  // Wire view buttons
  document.querySelectorAll("[data-scan-view]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-scan-view");
      await loadScanDetail(id);
    });
  });
}

async function loadScanDetail(scanId) {
  const box = el("scanDetailBox");
  box.innerHTML = "Loading scan details...";
  const data = await api(`/scan/${encodeURIComponent(scanId)}`, { method: "GET" });
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

  for (const rep of (result.reports || [])) {
    lines.push(rep.summary || `${rep.ticker}`);
    if (rep.trade_plan && rep.trade_plan.enabled) {
      const tp = rep.trade_plan;
      lines.push(
        `  trade_plan: shares=${tp.shares}, stop_loss=${tp.stop_loss?.toFixed?.(2) ?? tp.stop_loss}, notional=$${(tp.notional_usd || 0).toFixed?.(2) ?? tp.notional_usd}, cash_valid=${tp.cash_valid}`
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
  await api("/scan", { method: "POST", body: JSON.stringify({ tickers, as_of: "auto" }) });
  await loadScans();
}

async function loadPortfolio() {
  const box = el("portfolioBox");
  box.textContent = "Loading portfolio...";
  const p = await api("/portfolio");
  const positions = p.positions || [];
  const posTable = `
    <table>
      <thead><tr><th>Ticker</th><th>Quantity</th><th>Avg Price</th><th>Updated</th></tr></thead>
      <tbody>
        ${positions
          .map((x) => `<tr><td>${x.ticker}</td><td>${x.quantity}</td><td>${x.avg_price ?? ""}</td><td>${fmtTs(x.updated_at)}</td></tr>`)
          .join("")}
      </tbody>
    </table>
  `;
  box.innerHTML = `<div><div class="pill ok">Cash: $${(p.cash_usd || 0).toFixed?.(2) ?? p.cash_usd}</div></div><div style="height:10px"></div><div class="table">${posTable}</div>`;
}

async function setCash() {
  const v = parseFloat(el("cashInput").value || "0");
  await api("/portfolio/cash", { method: "POST", body: JSON.stringify({ cash_usd: v }) });
  await loadPortfolio();
}

async function uploadCsv() {
  const fileInput = el("csvFile");
  const mode = el("csvMode").value;
  if (!fileInput.files || !fileInput.files[0]) return;

  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  const r = await fetch(`/portfolio/revolut/upload?mode=${encodeURIComponent(mode)}`, { method: "POST", body: fd });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${JSON.stringify(body)}`);
  await loadPortfolio();
}

async function loadRanking() {
  const box = el("rankingBox");
  box.textContent = "Loading ranking...";
  try {
    const data = await api("/rankings/sp500/weekly", { method: "GET" });
    const items = data.items || [];
    box.textContent = `Top 20 of ${data.scored_size}/${data.universe_size}\n\n` + pretty(items.slice(0, 20));
  } catch (e) {
    box.textContent = `Ranking not available: ${e.message}`;
  }
}

async function main() {
  el("refreshScansBtn").addEventListener("click", () => loadScans().catch(alert));
  el("runScanBtn").addEventListener("click", () => runScan().catch(alert));
  el("setCashBtn").addEventListener("click", () => setCash().catch(alert));
  el("uploadCsvBtn").addEventListener("click", () => uploadCsv().catch(alert));
  el("loadRankingBtn").addEventListener("click", () => loadRanking().catch(alert));
  el("refreshMarketScanBtn").addEventListener("click", () => loadMarketCandidates().catch(alert));
  el("runMarketScanBtn").addEventListener("click", () => runMarketScan().catch(alert));

  await loadAutoscan();
  await loadMarketScanStatus();
  await loadPortfolio();
  await loadMarketCandidates();
  await loadScans();
  // Preload detail for most recent scan (if any)
  try {
    const scans = await api("/scans?limit=1", { method: "GET" });
    if (scans.scans && scans.scans[0]) await loadScanDetail(scans.scans[0].scan_id);
  } catch {
    // ignore
  }
}

main().catch((e) => alert(e.message));

