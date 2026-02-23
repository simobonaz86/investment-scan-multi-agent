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

async function loadScans() {
  const out = el("scansTable");
  out.innerHTML = "Loading scans...";
  const data = await api("/scans?limit=25", { method: "GET" });

  const rows = data.scans || [];
  const html = `
    <table>
      <thead>
        <tr>
          <th>Created</th>
          <th>Status</th>
          <th>Tickers</th>
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
                <td><span class="pill">${s.status}</span></td>
                <td>${tickers}</td>
                <td style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">${s.scan_id}</td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
  out.innerHTML = `<div class="table">${html}</div>`;
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
  box.textContent = pretty(p);
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

  await loadAutoscan();
  await loadPortfolio();
  await loadScans();
}

main().catch((e) => alert(e.message));

