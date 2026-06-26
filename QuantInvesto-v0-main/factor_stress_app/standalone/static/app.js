const factors = ["equity", "rates", "inflation", "gold", "commodities", "credit"];
const labels = { equity: "Equity Shock", rates: "Rates Shock", inflation: "Inflation Shock", gold: "Gold Shock", commodities: "Commodity Shock", credit: "Credit Shock" };
const shock = { equity: -0.2, rates: 0.05, inflation: -0.02, gold: 0.04, commodities: -0.1, credit: -0.08 };
const assets = document.querySelector("#assets");
const statusEl = document.querySelector("#status");

function percent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${(value * 100).toFixed(2)}%`;
}

function setStatus(message) {
  statusEl.textContent = message;
}

function addAsset(symbol = "", weight = "", assetClass = "etf") {
  const row = document.createElement("div");
  row.className = "asset-row";
  row.innerHTML = `
    <input class="symbol" placeholder="SPY" value="${symbol}">
    <input class="weight" type="number" min="0" step="0.1" value="${weight}">
    <select class="asset-class">
      <option value="etf">ETF</option>
      <option value="stock">Azione</option>
      <option value="bond">Obbligazione</option>
      <option value="gold">Oro</option>
      <option value="commodity">Commodity</option>
    </select>
  `;
  row.querySelector(".asset-class").value = assetClass;
  assets.appendChild(row);
}

function collectPayload() {
  return {
    start: document.querySelector("#start").value,
    end: document.querySelector("#end").value,
    use_demo_data: document.querySelector("#demo").checked,
    portfolio: [...document.querySelectorAll(".asset-row")].map((row) => ({
      symbol: row.querySelector(".symbol").value,
      weight: Number(row.querySelector(".weight").value || 0),
      asset_class: row.querySelector(".asset-class").value,
    })).filter((item) => item.symbol.trim()),
  };
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Errore richiesta");
  return payload;
}

function drawBars(canvasId, items, labelKey, valueKey, color = "#2f66c5") {
  const canvas = document.querySelector(canvasId);
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const pad = { left: 70, right: 20, top: 20, bottom: 46 };
  ctx.clearRect(0, 0, width, height);
  if (!items.length) return;
  const values = items.map((item) => item[valueKey]);
  const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.01);
  const zeroY = pad.top + (height - pad.top - pad.bottom) / 2;
  const barWidth = (width - pad.left - pad.right) / items.length * 0.64;
  ctx.strokeStyle = "#d9e3de";
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(width - pad.right, zeroY);
  ctx.stroke();
  items.forEach((item, index) => {
    const value = item[valueKey];
    const x = pad.left + index * ((width - pad.left - pad.right) / items.length) + barWidth * 0.28;
    const barHeight = Math.abs(value) / maxAbs * ((height - pad.top - pad.bottom) / 2);
    const y = value >= 0 ? zeroY - barHeight : zeroY;
    ctx.fillStyle = value >= 0 ? color : "#c13e3e";
    ctx.fillRect(x, y, barWidth, barHeight);
    ctx.fillStyle = "#52645e";
    ctx.font = "12px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(String(item[labelKey]), x + barWidth / 2, height - 18);
    ctx.fillText(percent(value), x + barWidth / 2, value >= 0 ? y - 6 : y + barHeight + 16);
  });
}

function renderExposure(analysis) {
  const table = document.querySelector("#exposure-table");
  table.innerHTML = `
    <thead><tr><th>Asset</th><th>Alpha</th><th>R2</th><th>p-value</th></tr></thead>
    <tbody>${analysis.exposures.map((item) => `
      <tr><td>${item.symbol}</td><td>${item.alpha.toFixed(5)}</td><td>${item.r_squared.toFixed(3)}</td><td>${item.model_p_value === null ? "--" : item.model_p_value.toExponential(2)}</td></tr>
    `).join("")}</tbody>
  `;
  const heatmap = document.querySelector("#heatmap");
  heatmap.innerHTML = `
    <thead><tr><th>Asset</th>${factors.map((factor) => `<th>${factor}</th>`).join("")}</tr></thead>
    <tbody>${Object.entries(analysis.beta_matrix).map(([symbol, betas]) => `
      <tr><td>${symbol}</td>${factors.map((factor) => {
        const beta = betas[factor];
        const alpha = Math.min(0.85, 0.15 + Math.abs(beta) * 0.35);
        const bg = beta >= 0 ? `rgba(47,102,197,${alpha})` : `rgba(193,62,62,${alpha})`;
        return `<td style="background:${bg}">${beta.toFixed(2)}</td>`;
      }).join("")}</tr>
    `).join("")}</tbody>
  `;
}

function renderScenario(result) {
  document.querySelector("#expected").textContent = percent(result.expected_portfolio_return);
  document.querySelector("#best").textContent = result.best_hedge || "--";
  document.querySelector("#worst").textContent = result.worst_contributor || "--";
  renderExposure(result.factor_analysis);
  drawBars("#asset-chart", result.contribution_by_asset, "symbol", "weighted_contribution", "#18794e");
  drawBars("#factor-chart", result.contribution_by_factor, "factor", "contribution", "#2f66c5");
}

function renderMonteCarlo(result) {
  document.querySelector("#loss").textContent = percent(result.probability_of_loss);
  drawBars("#mc-chart", [
    { name: "P5", value: result.percentile_5 },
    { name: "P50", value: result.percentile_50 },
    { name: "P95", value: result.percentile_95 },
  ], "name", "value", "#7d5fff");
}

function setupSliders() {
  const sliders = document.querySelector("#sliders");
  sliders.innerHTML = factors.map((factor) => `
    <label class="slider-row">
      <span>${labels[factor]}</span>
      <input data-factor="${factor}" type="range" min="-50" max="50" step="1" value="${Math.round(shock[factor] * 100)}">
      <strong id="value-${factor}">${Math.round(shock[factor] * 100)}%</strong>
    </label>
  `).join("");
  sliders.addEventListener("input", (event) => {
    const input = event.target.closest("input[data-factor]");
    if (!input) return;
    const factor = input.dataset.factor;
    shock[factor] = Number(input.value) / 100;
    document.querySelector(`#value-${factor}`).textContent = `${input.value}%`;
  });
}

async function runFactorAnalysis() {
  setStatus("Calcolo esposizioni...");
  try {
    const result = await post("/portfolio/factor-analysis", collectPayload());
    renderExposure(result);
    setStatus("Factor analysis completata");
  } catch (error) {
    setStatus(error.message);
  }
}

async function runScenario(custom) {
  setStatus("Calcolo scenario...");
  try {
    const payload = { ...collectPayload(), scenario: custom ? "Custom Shock" : document.querySelector("#scenario").value };
    if (custom) payload.shocks = shock;
    const result = await post("/portfolio/scenario", payload);
    renderScenario(result);
    setStatus("Scenario completato");
  } catch (error) {
    setStatus(error.message);
  }
}

async function runMonteCarlo() {
  setStatus("Eseguo Monte Carlo...");
  try {
    const result = await post("/portfolio/monte-carlo", { ...collectPayload(), simulations: 10000, horizon_days: 252, seed: 42 });
    renderMonteCarlo(result);
    setStatus("Monte Carlo completata");
  } catch (error) {
    setStatus(error.message);
  }
}

addAsset("SPY", 50, "etf");
addAsset("TLT", 30, "bond");
addAsset("GLD", 20, "gold");
setupSliders();
document.querySelector("#add-asset").addEventListener("click", () => addAsset("", 0, "etf"));
document.querySelector("#run-factor").addEventListener("click", runFactorAnalysis);
document.querySelector("#run-scenario").addEventListener("click", () => runScenario(false));
document.querySelector("#run-custom").addEventListener("click", () => runScenario(true));
document.querySelector("#run-mc").addEventListener("click", runMonteCarlo);

