const positionsEl = document.querySelector("#positions");
const form = document.querySelector("#backtest-form");
const statusEl = document.querySelector("#status");
let currentStep = "profile";
let advisorSections = {};
let savedPortfolios = [];
let planConfig = {};
let featureMessages = {};
let instrumentMetadata = {};
let standardPortfolios = [];
let selectedStandardBenchmark = null;
let selectedStandardPortfolioMode = "none";
let latestAdvancedAnalytics = null;
let latestComparisonContext = null;
let latestFinalAnalysisContext = null;
let activeImprovementContext = {
  activeComparisonMode: "optimization",
  activeSourcePortfolio: null,
  activeTargetPortfolio: null,
  activeSourceMetrics: null,
  activeTargetMetrics: null,
};
const renderedAdvancedSteps = new Set();
let currentRollingWindow = "1y";
let hiddenRiskScenarioCalculated = false;
let stressScenarioCalculated = false;
const explanationDetailPayloads = new Map();
const explanationDetailCache = new Map();
const explanationDetailInFlight = new Map();
const localPanelTechnicalDetails = new Map();
const pendingAiPanelSelectors = new Set();
let analysisInFlight = false;
const instrumentSearchTimers = new WeakMap();
const instrumentSearchCache = new Map();
const instrumentSearchInFlight = new Map();
const INSTRUMENT_SEARCH_DELAY_MS = 1200;
const INSTRUMENT_SEARCH_MIN_CHARS = 3;
const DEFAULT_REBALANCE_FREQUENCY = "monthly";

const planRank = { FREE: 0, PLUS: 1, ADVANCED: 2 };
const stepRequirements = {
  tracking: "PLUS",
  frontier: "PLUS",
  montecarlo: "PLUS",
  scenarios: "PLUS",
  factors: "ADVANCED",
  geography: "ADVANCED",
  rolling: "ADVANCED",
  correlation: "ADVANCED",
  pac: "ADVANCED",
  drawdown: "ADVANCED",
  factorrisk: "ADVANCED",
  scenarioadvanced: "ADVANCED",
};
const comparisonModeHiddenSteps = {
  standard_benchmark: new Set(["frontier"]),
  standard_only: new Set(["frontier"]),
  final_user_vs_recommended_standard: new Set(["frontier"]),
  final_optimized_vs_recommended_standard: new Set(["frontier"]),
};

const euro = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2 });
window.euro = euro;
const assetClassOptions = [
  ["equity", "Azioni"],
  ["bonds", "Obbligazioni"],
  ["gold", "Oro"],
  ["commodities", "Materie prime"],
];

function looksLikeIsin(value) {
  return /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/.test(String(value || "").trim().toUpperCase());
}

function looksLikeFigiId(value) {
  return /^FIGI:BBG[A-Z0-9]+$/.test(String(value || "").trim().toUpperCase());
}

function displayInstrumentName(symbol) {
  if (typeof symbol === "object" && symbol !== null) {
    return symbol.displayName || symbol.name || displayInstrumentName(symbol.symbol || symbol.instrumentId || symbol.isin);
  }
  const key = String(symbol || "").trim().toUpperCase();
  const meta = instrumentMetadata[key] || {};
  return meta.displayName || meta.name || key || "--";
}

function displayAnalysisInstrumentName(symbol) {
  if (typeof symbol === "object" && symbol !== null) {
    const direct = symbol.displayName || symbol.name;
    if (direct && !looksLikeIsin(direct) && !looksLikeFigiId(direct)) return direct;
    return displayAnalysisInstrumentName(symbol.symbol || symbol.instrumentId || symbol.isin);
  }
  const key = String(symbol || "").trim().toUpperCase();
  const meta = instrumentMetadata[key] || {};
  const candidate = meta.displayName || meta.name || "";
  if (candidate && !looksLikeIsin(candidate) && !looksLikeFigiId(candidate)) return candidate;
  if (key && !looksLikeIsin(key) && !looksLikeFigiId(key)) return key;
  return "Strumento finanziario";
}

function displayInstrumentPair(pair) {
  return String(pair || "--")
    .split("/")
    .map((symbol) => displayAnalysisInstrumentName(symbol))
    .join(" / ");
}

function displayCorrelationSymbol(data, symbol) {
  const display = data?.displaySymbols?.[symbol];
  return display && !looksLikeIsin(display) && !looksLikeFigiId(display) ? display : displayAnalysisInstrumentName(symbol);
}

function displayCorrelationPair(data, pairData) {
  if (pairData?.displayPair) return pairData.displayPair;
  return displayInstrumentPair(pairData?.pair);
}

function setActiveImprovementContext(next = {}) {
  activeImprovementContext = {
    ...activeImprovementContext,
    ...next,
  };
}

function syncActiveImprovementContextFromResult(result) {
  const finalContext = result?.aiAdvisor?.finalAnalysisContext || null;
  if (finalContext?.improvementContext) {
    const improvement = finalContext.improvementContext;
    setActiveImprovementContext({
      activeComparisonMode: finalContext.mode || "optimization",
      activeSourcePortfolio: improvement.sourcePortfolioName || null,
      activeTargetPortfolio: improvement.targetPortfolioName || null,
      activeSourceMetrics: finalContext.primaryMetrics || null,
      activeTargetMetrics: finalContext.comparisonMetrics || null,
    });
    return;
  }
  const planContext = result?.improvementPlan?.activeContext || {};
  setActiveImprovementContext({
    activeComparisonMode:
      planContext.activeComparisonMode ||
      result?.improvementPlan?.comparisonMode ||
      result?.analysisComparisonContext?.comparisonMode ||
      "optimization",
    activeSourcePortfolio: planContext.activeSourcePortfolio || result?.improvementPlan?.sourceName || null,
    activeTargetPortfolio: planContext.activeTargetPortfolio || result?.improvementPlan?.target?.name || null,
    activeSourceMetrics: planContext.activeSourceMetrics || null,
    activeTargetMetrics: planContext.activeTargetMetrics || null,
  });
}
const factorStressScenarios = {
  "Global Recession": { equity: -0.2, rates: 0.05, inflation: -0.02, credit: -0.08, gold: 0.04, commodities: -0.1 },
  "High Inflation": { equity: -0.08, rates: -0.06, inflation: 0.12, credit: -0.03, gold: 0.1, commodities: 0.16 },
  Stagflation: { equity: -0.16, rates: -0.08, inflation: 0.1, credit: -0.07, gold: 0.12, commodities: 0.14 },
  "Tech Crash": { equity: -0.28, rates: 0.04, inflation: -0.01, credit: -0.06, gold: 0.05, commodities: -0.06 },
  "Credit Crisis": { equity: -0.18, rates: 0.07, inflation: -0.02, credit: -0.16, gold: 0.06, commodities: -0.08 },
  "Interest Rate Shock": { equity: -0.07, rates: -0.1, inflation: 0.03, credit: -0.05, gold: -0.02, commodities: 0.02 },
  "Commodity Shock": { equity: -0.06, rates: -0.02, inflation: 0.08, credit: -0.02, gold: 0.06, commodities: 0.22 },
  Deflation: { equity: -0.14, rates: 0.09, inflation: -0.08, credit: -0.05, gold: 0.03, commodities: -0.16 },
  "Dot-com Crash": { equity: -0.3, rates: 0.06, inflation: -0.01, credit: -0.05, gold: 0.02, commodities: -0.05 },
  "2008 Financial Crisis": { equity: -0.38, rates: 0.12, inflation: -0.04, credit: -0.22, gold: 0.05, commodities: -0.28 },
};
const factorLabels = {
  equity: "Equity",
  rates: "Rates",
  inflation: "Inflation",
  credit: "Credit",
  gold: "Gold",
  commodities: "Commodity",
};
const factorOrder = ["equity", "rates", "inflation", "credit", "gold", "commodities"];

function currentPlan() {
  return document.querySelector("#user-plan")?.value || "PLUS";
}

function updatePlanBadge() {
  const badge = document.querySelector("#plan-badge");
  if (!badge) return;
  badge.textContent = currentPlan();
  badge.dataset.plan = currentPlan().toLowerCase();
}

function canAccessStep(step) {
  const required = stepRequirements[step] || "FREE";
  const hasPlanAccess = (planRank[currentPlan()] ?? 1) >= (planRank[required] ?? 0);
  if (!hasPlanAccess) return false;
  const mode = latestFinalAnalysisContext?.mode || latestComparisonContext?.comparisonMode || "optimization";
  return !comparisonModeHiddenSteps[mode]?.has(step);
}

function firstAccessibleStep(preferred = "profile") {
  if (canAccessStep(preferred)) return preferred;
  const firstButton = [...document.querySelectorAll(".step-button")].find((button) => canAccessStep(button.dataset.stepTarget));
  return firstButton?.dataset.stepTarget || "profile";
}

function updatePlanVisibility() {
  document.querySelectorAll(".step-button").forEach((button) => {
    const allowed = canAccessStep(button.dataset.stepTarget);
    button.hidden = !allowed;
    button.disabled = !allowed;
    button.setAttribute("aria-hidden", String(!allowed));
    button.classList.toggle("mode-hidden", !allowed && !stepRequirements[button.dataset.stepTarget]);
  });
  document.querySelectorAll(".step-panel, .result-step").forEach((item) => {
    const allowed = canAccessStep(item.dataset.step);
    item.classList.toggle("plan-hidden", !allowed);
    item.hidden = !allowed && item.classList.contains("result-step");
    if (!allowed) item.classList.remove("active");
  });
  if (!canAccessStep(currentStep)) {
    setStep(firstAccessibleStep("portfolio"));
  }
  updateGuidedFlow();
}

function mountConfigurationPanels() {
  const profileHost = document.querySelector("#profile-form-host");
  const portfolioHost = document.querySelector("#portfolio-form-host");
  const profilePanel = document.querySelector('#backtest-form > .step-panel[data-step="profile"]');
  if (profileHost && profilePanel && profilePanel.parentElement !== profileHost) {
    profileHost.append(profilePanel);
  }
  if (portfolioHost) {
    document.querySelectorAll('#backtest-form > .step-panel[data-step="portfolio"]').forEach((panel) => {
      portfolioHost.append(panel);
    });
  }
  document.querySelectorAll("[data-flow-step]").forEach((item) => {
    item.setAttribute("role", "button");
    item.setAttribute("tabindex", "0");
  });
}

function hasFeature(feature) {
  const value = planConfig[currentPlan()]?.[feature];
  return value === true || value === "basic" || value === "full" || value === "advanced" || value === "unlimited";
}

function lockedMeta(feature, fallback = {}) {
  return {
    name: fallback.name || featureMessages[feature]?.name || fallback.title || feature,
    requiredPlan: fallback.requiredPlan || featureMessages[feature]?.requiredPlan || "PLUS",
    message: fallback.message || featureMessages[feature]?.message || "Funzione disponibile con un piano superiore.",
    previewMessage: fallback.previewMessage || featureMessages[feature]?.previewMessage || "Questa funzione aggiunge un livello di analisi più approfondito.",
  };
}

function lockedFeatureCard(feature, fallback = {}) {
  const meta = lockedMeta(feature, fallback);
  return `
    <article class="locked-feature-card">
      <span>${escapeHtml(meta.name)}</span>
      <strong>Funzione bloccata</strong>
      <p>${escapeHtml(meta.previewMessage)}</p>
      <small>${escapeHtml(meta.message)}</small>
      <button class="upgrade-button" type="button">Sblocca con ${escapeHtml(meta.requiredPlan)}</button>
    </article>
  `;
}

function isLocked(payload) {
  return payload?.locked || payload?.error === "FEATURE_LOCKED";
}

function toggleExplainCloud(button) {
  const host = button.closest("article, .table-title");
  if (!host) return;
  const cloud = host.querySelector(".explain-cloud");
  if (!cloud) return;
  const panel = button.closest(".metric-grid, .monte-carlo-grid, .efficient-frontier-results, .results") || document;
  const isOpen = !cloud.hidden;
  panel.querySelectorAll(".explain-cloud").forEach((item) => {
    item.hidden = true;
  });
  panel.querySelectorAll(".explain-button").forEach((item) => {
    item.setAttribute("aria-expanded", "false");
  });
  cloud.hidden = isOpen;
  button.setAttribute("aria-expanded", String(!isOpen));
  if (!isOpen) {
    openTechnicalDrawer(host.querySelector("span")?.textContent || host.querySelector("h3")?.textContent || "Dettaglio tecnico", cloud.textContent);
  }
}

function openTechnicalDrawer(title, body) {
  const drawer = document.querySelector("#technical-drawer");
  if (!drawer) return;
  document.querySelector("#technical-drawer-title").textContent = plainFinanceLanguage(title || "Dettaglio tecnico");
  document.querySelector("#technical-drawer-body").textContent = plainFinanceLanguage(body || "Dettaglio non disponibile.");
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  drawer.querySelector(".technical-drawer-close")?.focus();
}

function closeTechnicalDrawer() {
  const drawer = document.querySelector("#technical-drawer");
  if (!drawer) return;
  drawer.classList.remove("is-open");
  drawer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return `${(value * 100).toFixed(2)}%`;
}
window.formatPercent = formatPercent;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
window.escapeHtml = escapeHtml;

function plainFinanceLanguage(value) {
  return String(value ?? "")
    .replace(/[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]/g, "")
    .replace(/\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b/g, "strumento finanziario")
    .replace(/\bFIGI:BBG[A-Z0-9]+\b/g, "strumento finanziario")
    .replace(/\bSharpe\s+Ratio\b/gi, "rapporto rischio-rendimento")
    .replace(/\bSharpe\b/gi, "rapporto rischio-rendimento")
    .replace(/\bSortino\s+Ratio\b/gi, "rapporto rendimento-rischio negativo")
    .replace(/\bSortino\b/gi, "rapporto rendimento-rischio negativo")
    .replace(/\bCAGR\b/g, "crescita media annua")
    .replace(/\bMax\s+Drawdown\b/gi, "peggiore perdita temporanea")
    .replace(/\bDrawdown\b/g, "Perdita temporanea")
    .replace(/\bdrawdown\b/g, "perdita temporanea")
    .replace(/\bvolatilita\b/gi, "oscillazione del portafoglio")
    .replace(/\bvolatilità\b/gi, "oscillazione del portafoglio")
    .replace(/\bcorrelazione\b/gi, "movimento comune tra strumenti");
}

function completeFinanceComment(value) {
  const text = plainFinanceLanguage(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (/[.!?)]$/.test(text) || text.length < 90) return text;
  const lastStop = Math.max(text.lastIndexOf("."), text.lastIndexOf("!"), text.lastIndexOf("?"));
  if (lastStop >= 50) return text.slice(0, lastStop + 1).trim();
  return text;
}

function completeWhatToWatch(value) {
  const text = completeFinanceComment(value);
  if (!text) return "";
  const imperativeVerbs = {
    guarda: "guardare",
    osserva: "osservare",
    controlla: "controllare",
    valuta: "valutare",
    confronta: "confrontare",
    verifica: "verificare",
    leggi: "leggere",
  };
  const imperativeMatch = text.match(/^(guarda|osserva|controlla|valuta|confronta|verifica|leggi)\b\s*(.*)$/i);
  if (imperativeMatch) {
    const infinitive = imperativeVerbs[imperativeMatch[1].toLowerCase()] || imperativeMatch[1].toLowerCase();
    const object = imperativeMatch[2].trim();
    const sentence = `L'utente deve ${infinitive}${object ? ` ${object}` : ""}`.trim();
    return /[.!?)]$/.test(sentence) ? sentence : `${sentence}.`;
  }
  if (/\b(l'utente|utente|investitore|il portafoglio|la tabella|il grafico|il sistema)\b.+\b(deve|può|mostra|confronta|indica|evidenzia|osserva|valuta)\b/i.test(text)) {
    return text;
  }
  const parts = text
    .split(/\s+-\s+|[;\n•]+/)
    .map((part) => part.trim().replace(/^[.,;:\-\s]+|[.,;:\-\s]+$/g, ""))
    .filter(Boolean)
    .slice(0, 3);
  const fragments = parts.length ? parts : [text.replace(/^[.,;:\-\s]+|[.,;:\-\s]+$/g, "")];
  const sentences = fragments.map((part) => {
    const lower = part.toLowerCase();
    let sentence = "";
    if (lower.includes("distanza") && lower.includes("curva efficiente")) {
      sentence = "L'utente deve osservare quanto il punto del portafoglio è distante dalla curva efficiente.";
    } else if ((lower.includes("variazione") || lower.includes("differenza")) && (lower.includes("portafoglio inserito") || lower.includes("ottimizzato"))) {
      sentence = "L'utente deve confrontare come cambiano rischio e rendimento tra portafoglio inserito e portafoglio di confronto.";
    } else if (lower.includes("riduzione del rischio") && lower.includes("rendimento")) {
      sentence = "L'utente deve valutare se la riduzione del rischio compensa il cambiamento di rendimento.";
    } else if (lower.includes("benchmark")) {
      sentence = "L'utente deve confrontare il portafoglio analizzato con il benchmark standard.";
    } else if (lower.includes("perdita") || lower.includes("drawdown")) {
      sentence = "L'utente deve osservare quanto il portafoglio può scendere nei periodi peggiori.";
    } else if (lower.includes("probabilità") || lower.includes("probabilita")) {
      sentence = "L'utente deve osservare quante simulazioni raggiungono l'obiettivo o restano sopra il capitale iniziale.";
    } else if (lower.includes("scenario") || lower.includes("shock")) {
      sentence = "L'utente deve osservare quale scenario genera l'impatto più negativo sul portafoglio.";
    } else if (lower.includes("movimento comune") || lower.includes("muovono insieme")) {
      sentence = "L'utente deve osservare se gli strumenti si muovono insieme nei periodi difficili.";
    } else if (/^(guarda|osserva|controlla|valuta|confronta|verifica|leggi)\b/i.test(part)) {
      const match = part.match(/^(guarda|osserva|controlla|valuta|confronta|verifica|leggi)\b\s*(.*)$/i);
      const infinitive = imperativeVerbs[match[1].toLowerCase()] || match[1].toLowerCase();
      sentence = `L'utente deve ${infinitive}${match[2] ? ` ${match[2].trim()}` : ""}`;
    } else if (/\b(deve|può|mostra|confronta|indica|evidenzia|osserva|valuta|guarda|controlla|verifica|leggi)\b/i.test(part)) {
      sentence = part;
    } else {
      sentence = `L'utente deve osservare ${part}`;
    }
    sentence = sentence.trim();
    return /[.!?)]$/.test(sentence) ? sentence : `${sentence}.`;
  });
  return sentences.join(" ");
}

function activeAnalysisMode(options = {}) {
  return options.comparisonMode ||
    latestFinalAnalysisContext?.mode ||
    latestComparisonContext?.comparisonMode ||
    "optimization";
}

function defaultWhatToWatchText(options = {}) {
  const mode = activeAnalysisMode(options);
  if (mode === "standard_only") {
    return "L'utente deve osservare composizione, pesi e ruolo degli strumenti del portfolio standard analizzato.";
  }
  if (mode === "standard_benchmark") {
    return "L'utente deve confrontare il portafoglio inserito con il portfolio benchmark standard QuantInvest selezionato.";
  }
  if (mode === "final_user_vs_recommended_standard") {
    return "L'utente deve confrontare il portafoglio inserito con il portfolio standard suggerito.";
  }
  if (mode === "final_optimized_vs_recommended_standard") {
    return "L'utente deve confrontare il portafoglio ottimizzato con il portfolio standard suggerito.";
  }
  return "L'utente deve confrontare il portafoglio inserito con il portafoglio efficiente e osservare differenze di rischio, rendimento e perdita temporanea.";
}

function possibleImprovementDirection(text) {
  const lower = String(text || "").toLowerCase();
  if (lower.includes("monte carlo") || lower.includes("simulaz") || lower.includes("instabilità") || lower.includes("dispersione")) {
    return "Una direzione possibile è valutare più componenti difensive, strumenti a breve durata o cash-like e minore dipendenza dagli asset più volatili.";
  }
  if (lower.includes("perdita") || lower.includes("rischio") || lower.includes("oscillazione") || lower.includes("volatile")) {
    return "Una direzione possibile è ridurre la dipendenza dagli asset più volatili e aumentare le componenti che storicamente tendono a contenere le oscillazioni.";
  }
  if (lower.includes("diversificazione") || lower.includes("concentrazione") || lower.includes("pochi strumenti") || lower.includes("asset class")) {
    return "Una direzione possibile è valutare una distribuzione più ampia tra asset class, aree geografiche o settori.";
  }
  if (lower.includes("movimento comune") || lower.includes("muovono insieme")) {
    return "Una direzione possibile è valutare strumenti o asset class che si comportano in modo diverso nelle fasi di mercato difficili.";
  }
  if (lower.includes("geograf")) {
    return "Una direzione possibile è confrontare il portafoglio con un riferimento più globale o valutare maggiore diversificazione geografica.";
  }
  if (lower.includes("benchmark")) {
    return "Una direzione possibile è usare il benchmark come confronto per capire se rischio e struttura del portafoglio sono coerenti.";
  }
  if (lower.includes("pac")) {
    return "Una direzione possibile è valutare se la composizione del portafoglio resta coerente anche con versamenti progressivi nel tempo.";
  }
  if (lower.includes("ottimizz") || lower.includes("efficiente") || lower.includes("frontiera")) {
    return "Una direzione possibile è confrontare l'alternativa ottimizzata con profilo, obiettivo e perdita massima sopportabile, senza leggerla come scelta automatica.";
  }
  if (lower.includes("fattor") || lower.includes("driver") || lower.includes("mercato")) {
    return "Una direzione possibile è capire se il risultato dipende da vera diversificazione o da una singola esposizione dominante.";
  }
  return "Una direzione possibile è valutare più equilibrio tra rischio, diversificazione e coerenza con il profilo indicato.";
}

function normalizePossibleImprovementText(value) {
  return completeFinanceComment(value)
    .replace(/\bcompra\b/gi, "valuta l'esposizione a")
    .replace(/\bvendi\b/gi, "valuta una minore esposizione a")
    .replace(/\bdevi\b/gi, "può")
    .replace(/\bgarantito\b/gi, "non certo")
    .replace(/\b[Aa]ggiungi\s+\d+([,.]\d+)?%?\s+[^.]+/g, "Valuta modifiche simulate nel Piano di miglioramento")
    .replace(/\b[Rr]iduci\s+[^.]{0,50}\s+al\s+\d+([,.]\d+)?%/g, "Valuta modifiche simulate nel Piano di miglioramento")
    .replace(/\b[Pp]orta\s+[^.]{0,50}\s+al\s+\d+([,.]\d+)?%/g, "Valuta modifiche simulate nel Piano di miglioramento")
    .trim();
}

function buildPossibleImprovementParts(value) {
  const text = normalizePossibleImprovementText(value) || "Il portafoglio presenta un'area da approfondire rispetto a rischio, diversificazione o coerenza con il profilo.";
  const area = text.replace(/\s+/g, " ").trim();
  const direction = /direzione possibile/i.test(area) ? "" : possibleImprovementDirection(area);
  const actions = /piano di miglioramento/i.test(area)
    ? "Il Piano di miglioramento resta il punto in cui verificare modifiche simulate su pesi e strumenti."
    : "Apri il Piano di miglioramento per vedere modifiche simulate su pesi e strumenti, mantenendo coerenza con profilo, obiettivo e benchmark.";
  return { area, direction, actions };
}

function renderPossibleImprovementGuidance(value) {
  const parts = buildPossibleImprovementParts(value);
  return `
    <div class="improvement-guidance" data-ai-field="possible_improvement">
      <div>
        <small>Area di miglioramento</small>
        <p>${escapeHtml(parts.area)}</p>
      </div>
      ${parts.direction ? `
        <div>
          <small>Direzione possibile</small>
          <p>${escapeHtml(parts.direction)}</p>
        </div>
      ` : ""}
      <div>
        <small>Azioni precise</small>
        <p>${escapeHtml(parts.actions)}</p>
      </div>
      <button type="button" class="secondary improvement-plan-link" data-step-link="improvement">Apri Piano di miglioramento</button>
    </div>
  `;
}

function escapePlainFinance(value) {
  return escapeHtml(plainFinanceLanguage(value));
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function addYears(date, years) {
  const copy = new Date(date);
  copy.setFullYear(copy.getFullYear() + years);
  return copy;
}

function inferAssetClassFromText(...parts) {
  const text = parts.filter(Boolean).join(" ").toLowerCase();
  if (!text.trim()) return "";
  if (["physical gold", " gold", " oro", "xetra-gold", "sgold", "gld", "iau", "phys"].some((token) => text.includes(token))) return "gold";
  if (["commodity", "commodit", "materie prime", "bloomberg commodity", "dbc", "pdbc", "gsg", "comt", " oil", " crude", " energy", "uso", "ung", "dba"].some((token) => text.includes(token))) return "commodities";
  if (["bond", "obblig", "treasury", "government", "corporate", "aggregate", "ultrashort", "money market", "cash", "duration", "fixed income", "floating rate", "high yield", "lqd", "hyg", "agg", "bnd", "tlt", "ief", "shy", "tip"].some((token) => text.includes(token))) return "bonds";
  if (["common stock", "equity", "stock", "shares", "azioni", "msci", "s&p", "spdr", "nasdaq", "world", "acwi", "all-world", "stoxx", "emerging markets"].some((token) => text.includes(token))) return "equity";
  return "";
}

function inferAssetClass(symbol, metadata = {}) {
  const value = String(symbol || "").toUpperCase();
  const direct = {
    GLD: "gold",
    IAU: "gold",
    SGOL: "gold",
    GLDM: "gold",
    PHYS: "gold",
    AGG: "bonds",
    BND: "bonds",
    TLT: "bonds",
    IEF: "bonds",
    SHY: "bonds",
    LQD: "bonds",
    HYG: "bonds",
    TIP: "bonds",
    MUB: "bonds",
    BIL: "bonds",
    SHV: "bonds",
    DBC: "commodities",
    PDBC: "commodities",
    GSG: "commodities",
    COMT: "commodities",
    USO: "commodities",
    UNG: "commodities",
    DBA: "commodities",
  }[value || String(metadata.ticker || "").toUpperCase()];
  if (direct) return direct;
  return metadata.assetClass || inferAssetClassFromText(value, metadata.ticker, metadata.name, metadata.displayName, metadata.securityType, metadata.marketSector) || "equity";
}

function inferAssetClassFromStandardHolding(holding = {}) {
  const text = `${holding.name || ""} ${holding.role || ""}`.toLowerCase();
  if (text.includes("oro") || text.includes("gold")) return "gold";
  if (text.includes("commodity")) return "commodities";
  if (text.includes("bond") || text.includes("obblig") || text.includes("treasury") || text.includes("government")) return "bonds";
  return "equity";
}

function addPosition(symbol = "", weight = "", assetClass = "", minWeight = 0, maxWeight = 100, displayName = "") {
  const row = document.createElement("div");
  row.className = "position-row";
  const selectedClass = assetClass || inferAssetClass(symbol, instrumentMetadata[symbol] || { displayName });
  const displayValue = displayName || (symbol ? displayInstrumentName(symbol) : "");
  if (symbol && displayName) {
    instrumentMetadata[symbol] = {
      ...(instrumentMetadata[symbol] || {}),
      instrumentId: symbol,
      isin: looksLikeIsin(symbol) ? symbol : instrumentMetadata[symbol]?.isin,
      name: displayName,
      displayName,
    };
  }
  const options = [
    `<option value="" ${selectedClass ? "" : "selected"}>Rileva automaticamente</option>`,
    ...assetClassOptions
    .map(([value, label]) => `<option value="${value}" ${value === selectedClass ? "selected" : ""}>${label}</option>`)
  ].join("");
  row.innerHTML = `
    <label>
      Cerca strumento
      <span class="instrument-search-wrap">
        <input class="instrument-search" autocomplete="off" inputmode="search" placeholder="Es. Microsoft, MSCI World, oro..." value="${escapeHtml(displayValue)}" />
        <input class="symbol" type="hidden" value="${escapeHtml(symbol)}" />
        <span class="instrument-search-preference">
          <label>
            <input class="include-global-listings" type="checkbox" />
            Mostra anche USA/globali
          </label>
        </span>
        <span class="instrument-options" hidden></span>
      </span>
    </label>
    <label>
      Peso %
      <input class="weight" type="number" min="0" step="0.1" placeholder="auto" value="${weight}" />
    </label>
    <label>
      Classe
      <select class="asset-class">${options}</select>
      <small class="asset-class-hint">Usata per Crisi simulate. Puoi correggerla se il rilevamento non è preciso.</small>
    </label>
    <label>
      Min %
      <input class="min-weight" type="number" min="0" max="100" step="0.1" value="${minWeight}" />
    </label>
    <label>
      Max %
      <input class="max-weight" type="number" min="0" max="100" step="0.1" value="${maxWeight}" />
    </label>
    <button class="icon-button" type="button" title="Rimuovi strumento" aria-label="Rimuovi strumento">x</button>
  `;
  row.querySelector("button").addEventListener("click", () => {
    row.remove();
    if (!positionsEl.children.length) addPosition();
    updatePortfolioLiveCheck();
  });
  row.querySelectorAll(".weight, .asset-class, .symbol, .instrument-search").forEach((input) => {
    input.addEventListener("input", updatePortfolioLiveCheck);
    input.addEventListener("change", updatePortfolioLiveCheck);
  });
  setupInstrumentSearch(row);
  positionsEl.appendChild(row);
  updatePortfolioLiveCheck();
}

function standardPortfolioOptionLabel(portfolio) {
  const plan = String(portfolio.plan || "").toUpperCase();
  const locked = portfolio.locked ? " · bloccato" : "";
  return `${portfolio.name} · ${portfolio.category} · rischio ${portfolio.riskLevel} · ${plan}${locked}`;
}

async function loadStandardPortfolios() {
  const select = document.querySelector("#standard-portfolio-select");
  if (!select) return;
  try {
    const response = await fetch(`/api/standard-portfolios?plan=${encodeURIComponent(currentPlan())}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Portfolio standard non disponibili.");
    standardPortfolios = Array.isArray(result.items) ? result.items : [];
    select.innerHTML = `<option value="">Seleziona un portfolio standard</option>` + standardPortfolios.map((portfolio) => `
      <option value="${escapeHtml(portfolio.id)}">${escapeHtml(standardPortfolioOptionLabel(portfolio))}</option>
    `).join("");
    renderStandardSelectorPreview();
    syncStandardModeSelection({ announce: false });
  } catch (error) {
    select.innerHTML = `<option value="">${escapeHtml(error.message)}</option>`;
  }
}

function selectedStandardPortfolio() {
  const id = document.querySelector("#standard-portfolio-select")?.value || "";
  return standardPortfolios.find((portfolio) => portfolio.id === id) || null;
}

function renderStandardSelectorPreview() {
  const preview = document.querySelector("#standard-selector-preview");
  if (!preview) return;
  const portfolio = selectedStandardPortfolio();
  if (!portfolio) {
    preview.innerHTML = "";
    return;
  }
  preview.innerHTML = `
    <article class="standard-mini-card ${portfolio.locked ? "locked" : ""}">
      <div>
        <span>${escapeHtml(String(portfolio.plan || "").toUpperCase())}${portfolio.locked ? " · bloccato" : ""}</span>
        <strong>${escapeHtml(portfolio.name)}</strong>
        <p>${escapePlainFinance(portfolio.description || "")}</p>
        <small>Rischio ${escapeHtml(standardRiskLabel(portfolio.riskLevel))} · Orizzonte ${escapeHtml(portfolio.suggestedHorizon || "--")}</small>
      </div>
      <ul>${renderStandardHoldings(portfolio.holdings || [])}</ul>
      ${portfolio.locked ? `<p class="standard-warning">Disponibile con ${escapeHtml(String(portfolio.plan || "").toUpperCase())}. Il piano attuale può vedere solo la preview. Passa ad Advanced per usarlo o impostarlo come benchmark.</p>` : ""}
    </article>
  `;
}

function applyStandardPortfolioToPositions(portfolio) {
  if (!portfolio || portfolio.locked) return;
  positionsEl.innerHTML = "";
  (portfolio.holdings || []).forEach((holding) => {
    addPosition(
      holding.isin || "",
      holding.weight ?? "",
      inferAssetClassFromStandardHolding(holding),
      0,
      100,
      holding.name || holding.isin || "",
    );
  });
  document.querySelector("#portfolio-name").value = portfolio.name;
  updatePortfolioLiveCheck();
}

function syncStandardModeSelection({ requireSelection = false, announce = false } = {}) {
  const mode = document.querySelector("#standard-mode")?.value || "manual";
  const portfolio = selectedStandardPortfolio();
  const note = document.querySelector("#standard-selector-note");
  const select = document.querySelector("#standard-portfolio-select");

  if (mode === "manual") {
    selectedStandardBenchmark = null;
    selectedStandardPortfolioMode = "none";
    renderStandardModeNote("");
    if (note) {
      note.textContent = "I portfolio standard QuantInvest sono benchmark educativi, non raccomandazioni personalizzate.";
    }
    return true;
  }

  if (!portfolio) {
    selectedStandardBenchmark = null;
    selectedStandardPortfolioMode = "none";
    const message = mode === "load"
      ? "Scegli un portfolio standard dal menu sotto per usare questa modalità."
      : "Scegli un portfolio standard dal menu sotto per usarlo come benchmark.";
    renderStandardModeNote(message);
    if (note) note.textContent = message;
    if (requireSelection) {
      setStatus(message, "error");
      select?.focus?.();
      return false;
    }
    return true;
  }

  if (portfolio.locked) {
    selectedStandardBenchmark = null;
    selectedStandardPortfolioMode = "none";
    const message = `Questo portfolio standard è disponibile con il piano ${String(portfolio.plan || "").toUpperCase()}.`;
    renderStandardModeNote(message);
    if (note) note.textContent = message;
    if (requireSelection || announce) {
      setStatus(message, "error");
    }
    return false;
  }

  selectedStandardBenchmark = portfolio;
  if (mode === "load") {
    selectedStandardPortfolioMode = "use_as_portfolio";
    applyStandardPortfolioToPositions(portfolio);
    renderStandardModeNote("Stai usando un portfolio standard QuantInvest come punto di partenza. L’analisi userà i pesi indicati nella tabella e non creerà un portfolio standard ottimale.");
    if (note) {
      note.textContent = "Portfolio standard applicato automaticamente in base alla modalità selezionata. Puoi modificarne strumenti e pesi prima dell’analisi.";
    }
    if (announce) setStatus(`Portfolio standard selezionato: ${portfolio.name}.`, "ok");
    return true;
  }

  if (mode === "benchmark") {
    selectedStandardPortfolioMode = "use_as_benchmark";
    renderStandardModeNote("Hai scelto di confrontare il tuo portfolio con un portfolio standard QuantInvest. L’analisi principale userà questo benchmark, mentre l’ottimizzazione resterà separata.");
    if (note) {
      note.textContent = `Portfolio benchmark standard QuantInvest selezionato: ${portfolio.name}. Resta separato dal portafoglio ottimale.`;
    }
    if (announce) setStatus(`Benchmark standard selezionato: ${portfolio.name}.`, "ok");
    return true;
  }

  return true;
}

function clearStandardPortfolioContext(message = "") {
  selectedStandardBenchmark = null;
  selectedStandardPortfolioMode = "none";
  const modeSelect = document.querySelector("#standard-mode");
  if (modeSelect) modeSelect.value = "manual";
  const standardSelect = document.querySelector("#standard-portfolio-select");
  if (standardSelect) standardSelect.value = "";
  renderStandardSelectorPreview();
  renderStandardModeNote("");
  const note = document.querySelector("#standard-selector-note");
  if (note) {
    note.textContent = message || "I portfolio standard QuantInvest sono benchmark educativi, non raccomandazioni personalizzate.";
  }
}

function setupInstrumentSearch(row) {
  const input = row.querySelector(".instrument-search");
  const hidden = row.querySelector(".symbol");
  const options = row.querySelector(".instrument-options");
  const includeGlobal = row.querySelector(".include-global-listings");
  if (!input || !hidden || !options) return;

  input.addEventListener("input", () => {
    const query = input.value.trim();
    if (looksLikeIsin(query)) {
      hidden.value = query.toUpperCase();
    } else {
      hidden.value = "";
    }
    options.hidden = true;
    options.innerHTML = "";
    const previous = instrumentSearchTimers.get(row);
    if (previous) clearTimeout(previous);
    if (query.length < INSTRUMENT_SEARCH_MIN_CHARS) return;
    const timer = setTimeout(() => searchInstrumentOptions(row, query), INSTRUMENT_SEARCH_DELAY_MS);
    instrumentSearchTimers.set(row, timer);
  });

  includeGlobal?.addEventListener("change", () => {
    const query = input.value.trim();
    options.hidden = true;
    options.innerHTML = "";
    if (query.length >= INSTRUMENT_SEARCH_MIN_CHARS) searchInstrumentOptions(row, query);
  });

  input.addEventListener("blur", () => {
    setTimeout(() => {
      closeInstrumentOptions(options);
    }, 160);
  });
}

function closeInstrumentOptions(options) {
  options.hidden = true;
  options.innerHTML = "";
}

async function searchInstrumentOptions(row, query) {
  const input = row.querySelector(".instrument-search");
  const hidden = row.querySelector(".symbol");
  const options = row.querySelector(".instrument-options");
  const includeGlobal = row.querySelector(".include-global-listings")?.checked || false;
  if (!input || !hidden || !options) return;
  try {
    const normalizedQuery = `${query.trim().toLowerCase()}::global=${includeGlobal ? 1 : 0}`;
    let result = instrumentSearchCache.get(normalizedQuery);
    if (!result) {
      if (!instrumentSearchInFlight.has(normalizedQuery)) {
        instrumentSearchInFlight.set(
          normalizedQuery,
          fetch(`/api/instruments/search?q=${encodeURIComponent(query)}&includeGlobal=${includeGlobal ? "1" : "0"}`).then(async (response) => {
            const payload = await response.json();
            if (response.status === 429) throw new Error("Troppe ricerche ravvicinate. Aspetta qualche minuto e riprova.");
            if (!response.ok) throw new Error(payload.error || "Ricerca non disponibile.");
            return payload;
          }),
        );
      }
      try {
        result = await instrumentSearchInFlight.get(normalizedQuery);
      } finally {
        instrumentSearchInFlight.delete(normalizedQuery);
      }
      instrumentSearchCache.set(normalizedQuery, result);
    }
    const items = Array.isArray(result.items) ? result.items : [];
    if (!items.length) {
      const message =
        result.hasGlobalResults && !includeGlobal
          ? "Nessuna quotazione europea trovata. Attiva “Mostra anche USA/globali” per vedere altri mercati."
          : "Nessun ISIN trovato";
      options.innerHTML = `<button type="button" class="instrument-option empty" disabled>${escapeHtml(message)}</button>`;
      options.hidden = false;
      return;
    }
    options.innerHTML = items.map((item) => `
      <button type="button" class="instrument-option" data-instrument-id="${escapeHtml(item.instrumentId || item.isin)}" data-isin="${escapeHtml(item.isin || "")}" data-name="${escapeHtml(item.name || item.isin || item.instrumentId)}" data-asset-class="${escapeHtml(item.assetClass || "")}" data-security-type="${escapeHtml(item.securityType || "")}" data-ticker="${escapeHtml(item.ticker || "")}">
        <strong>${escapeHtml(item.name || item.isin || item.instrumentId)}</strong>
        <small>${escapeHtml([item.ticker, item.exchange, item.currency, item.isin].filter(Boolean).join(" - "))}${item.assetClassLabel ? ` · ${escapeHtml(item.assetClassLabel)}` : ""}${item.isEuropeanListing === true || item.isEuropeanListing === "true" ? " · Europa" : " · Globale"}</small>
      </button>
    `).join("");
    options.querySelectorAll(".instrument-option:not(.empty)").forEach((button) => {
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        const instrumentId = button.dataset.instrumentId || button.dataset.isin || "";
        const isin = button.dataset.isin || instrumentId;
        const name = button.dataset.name || isin;
        const detectedAssetClass = button.dataset.assetClass || inferAssetClass(instrumentId, {
          ticker: button.dataset.ticker,
          name,
          securityType: button.dataset.securityType,
        });
        hidden.value = instrumentId;
        input.value = name;
        instrumentMetadata[instrumentId] = {
          isin,
          instrumentId,
          name,
          displayName: name,
          ticker: button.dataset.ticker || "",
          securityType: button.dataset.securityType || "",
          assetClass: detectedAssetClass,
        };
        const select = row.querySelector(".asset-class");
        if (select && detectedAssetClass) select.value = detectedAssetClass;
        closeInstrumentOptions(options);
        input.blur();
        updatePortfolioLiveCheck();
      });
    });
    options.hidden = false;
  } catch (error) {
    options.innerHTML = `<button type="button" class="instrument-option empty" disabled>${escapeHtml(error.message)}</button>`;
    options.hidden = false;
  }
}

function setDefaultDates() {
  const end = new Date();
  const start = addYears(end, -3);
  document.querySelector("#end").value = end.toISOString().slice(0, 10);
  document.querySelector("#start").value = start.toISOString().slice(0, 10);
}

function collectPortfolio() {
  return [...positionsEl.querySelectorAll(".position-row")]
    .map((row) => {
      const visibleValue = row.querySelector(".instrument-search")?.value.trim().toUpperCase() || "";
      const hiddenValue = row.querySelector(".symbol").value.trim().toUpperCase();
      const symbol = hiddenValue || (looksLikeIsin(visibleValue) ? visibleValue : "");
      const displayName = row.querySelector(".instrument-search")?.value.trim() || "";
      const selectedAssetClass = row.querySelector(".asset-class").value;
      const inferredAssetClass = selectedAssetClass || inferAssetClass(symbol || displayName, instrumentMetadata[symbol] || { displayName });
      return {
        symbol,
        query: row.querySelector(".instrument-search")?.value.trim() || "",
        displayName,
        weight: Number(row.querySelector(".weight").value || 0),
        assetClass: inferredAssetClass,
        minWeight: Number(row.querySelector(".min-weight").value || 0),
        maxWeight: Number(row.querySelector(".max-weight").value || 100),
      };
    })
    .filter((item) => item.symbol || item.query);
}

function portfolioWeightTotal(portfolio = collectPortfolio()) {
  return portfolio.reduce((sum, item) => sum + Number(item.weight || 0), 0);
}

function updatePortfolioLiveCheck() {
  const portfolio = collectPortfolio();
  const total = portfolioWeightTotal(portfolio);
  const totalEl = document.querySelector("#weight-total");
  const statusEl = document.querySelector("#weight-status");
  const dominantEl = document.querySelector("#dominant-asset-class");
  const assetWarningEl = document.querySelector("#asset-warning");
  const recognizedEl = document.querySelector("#recognized-count");
  if (!totalEl || !statusEl || !dominantEl || !assetWarningEl || !recognizedEl) return;
  const diff = Math.abs(total - 100);
  totalEl.textContent = `${number.format(total)}%`;
  totalEl.dataset.state = diff <= 0.1 ? "ok" : "warning";
  statusEl.textContent =
    diff <= 0.1
      ? "Totale corretto: puoi eseguire l’analisi."
      : total < 100
        ? `Mancano ${number.format(100 - total)} punti per arrivare al 100%.`
        : `Il totale supera il 100% di ${number.format(total - 100)} punti.`;
  const totalsByClass = portfolio.reduce((acc, item) => {
    acc[item.assetClass] = (acc[item.assetClass] || 0) + Number(item.weight || 0);
    return acc;
  }, {});
  const [dominantClass, dominantWeight] = Object.entries(totalsByClass).sort((a, b) => b[1] - a[1])[0] || [];
  const classLabel = assetClassOptions.find(([value]) => value === dominantClass)?.[1] || "--";
  dominantEl.textContent = dominantClass ? `${classLabel} ${number.format(dominantWeight)}%` : "--";
  assetWarningEl.textContent =
    dominantWeight >= 75
      ? `Attenzione: ${number.format(dominantWeight)}% del portafoglio è nella stessa classe. Verifica il rischio massimo stimato.`
      : "Composizione in lettura: controlla se una classe domina troppo il portafoglio.";
  const recognized = portfolio.filter((item) => looksLikeIsin(item.symbol) || looksLikeFigiId(item.symbol)).length;
  recognizedEl.textContent = `${recognized}/${portfolio.length || 0}`;
  updatePortfolioReadiness({ total, diff, recognized, count: portfolio.length });
}

function updatePortfolioReadiness({ total, diff, recognized, count }) {
  const readiness = document.querySelector("#portfolio-readiness");
  if (!readiness) return;
  const title = readiness.querySelector("strong");
  const detail = readiness.querySelector("small");
  const isWeightOk = diff <= 0.1;
  const allRecognized = count > 0 && recognized === count;
  readiness.dataset.state = isWeightOk && allRecognized ? "ok" : "warning";
  if (isWeightOk && allRecognized) {
    title.textContent = "Pronto per l'analisi completa";
    detail.textContent = "Pesi al 100% e strumenti riconosciuti. Puoi avviare il check-up.";
  } else if (!isWeightOk) {
    title.textContent = total < 100 ? "Mancano pesi" : "Pesi oltre il 100%";
    detail.textContent = `Totale attuale ${number.format(total)}%. Correggi i pesi prima dell'analisi.`;
  } else {
    title.textContent = "Seleziona strumenti riconosciuti";
    detail.textContent = `Riconosciuti ${recognized}/${count || 0}. Scegli gli strumenti dalla ricerca per evitare ambiguità.`;
  }
}

function validatePortfolioIsins(portfolio) {
  const invalid = portfolio.filter((item) => !looksLikeIsin(item.symbol) && !looksLikeFigiId(item.symbol)).map((item) => item.query || item.symbol || "vuoto");
  if (invalid.length) {
    throw new Error(`Seleziona uno strumento dalla ricerca oppure inserisci un ISIN valido a 12 caratteri: ${invalid.join(", ")}.`);
  }
  const total = portfolioWeightTotal(portfolio);
  if (Math.abs(total - 100) > 0.1) {
    throw new Error(`La somma dei pesi deve essere 100%. Ora è ${number.format(total)}%.`);
  }
}

function collectMonteCarlo() {
  return {
    horizonYears: Number(document.querySelector("#mc-horizon").value),
    simulations: Number(document.querySelector("#mc-simulations").value),
  };
}

function collectInvestorProfile() {
  return {
    capital: Number(document.querySelector("#initial-capital").value),
    age: Number(document.querySelector("#investor-age").value),
    horizonYears: Number(document.querySelector("#investor-horizon").value),
    riskPreference: document.querySelector("#investor-risk").value,
    maxTemporaryLoss: Number(document.querySelector("#investor-max-loss")?.value || 0.2),
    monthlyPac: Number(document.querySelector("#investor-pac")?.value || 0),
    goalPriority: document.querySelector("#investor-goal-priority")?.value || "capital_growth",
    experienceLevel: document.querySelector("#investor-experience")?.value || "beginner",
    liquidityNeed: document.querySelector("#investor-liquidity-need")?.value || "low",
    objective: document.querySelector("#investor-objective").value.trim(),
  };
}

function updateProfileSummary() {
  const completion = document.querySelector("#profile-completion");
  const summary = document.querySelector("#profile-summary");
  const formCompletion = document.querySelector("#profile-form-completion");
  const formSummary = document.querySelector("#profile-form-summary");
  if (!completion || !summary) return;
  const riskLabels = { conservative: "Prudente", balanced: "Bilanciato", aggressive: "Aggressivo" };
  const goalLabels = {
    capital_growth: "crescita",
    capital_preservation: "protezione",
    income: "entrate periodiche",
    retirement: "pensione",
    home: "casa o spesa futura",
  };
  const liquidityLabels = { low: "liquidità bassa", medium: "liquidità media", high: "liquidità alta" };
  const profile = collectInvestorProfile();
  const isComplete = profile.capital > 0 && profile.age >= 18 && profile.horizonYears > 0 && profile.objective.length >= 6;
  const completionText = isComplete ? "Profilo completato" : "Profilo in compilazione";
  const summaryText = `Rischio dichiarato: ${riskLabels[profile.riskPreference] || "Bilanciato"} · Orizzonte: ${profile.horizonYears} anni · Obiettivo: ${goalLabels[profile.goalPriority] || "crescita"} · Perdita sopportabile: ${formatPercent(profile.maxTemporaryLoss)} · ${liquidityLabels[profile.liquidityNeed] || "liquidità bassa"}${profile.monthlyPac > 0 ? ` · PAC: ${euro.format(profile.monthlyPac)}/mese` : ""}`;
  completion.textContent = completionText;
  summary.textContent = summaryText;
  if (formCompletion) formCompletion.textContent = completionText;
  if (formSummary) formSummary.textContent = summaryText;

  // Live "Il tuo profilo in sintesi" snapshot card (presentation only).
  const riskLevelLabels = { conservative: "Prudente", balanced: "Bilanciato", aggressive: "Aggressivo" };
  const riskMeterWidth = { conservative: 28, balanced: 60, aggressive: 92 };
  const goalSnapshotLabels = {
    capital_growth: "Crescita del capitale",
    capital_preservation: "Protezione del capitale",
    income: "Entrate periodiche",
    retirement: "Pensione / lungo periodo",
    home: "Casa o spesa futura",
  };
  const setText = (id, value) => {
    const node = document.querySelector(id);
    if (node) node.textContent = value;
  };
  const lossEur = profile.capital > 0 ? euro.format(Math.round(profile.capital * profile.maxTemporaryLoss)) : "--";
  setText("#profile-snapshot-risk", riskLevelLabels[profile.riskPreference] || "Bilanciato");
  setText("#profile-snapshot-horizon", `${profile.horizonYears} anni`);
  setText("#profile-snapshot-loss-eur", lossEur);
  setText("#profile-snapshot-goal", goalSnapshotLabels[profile.goalPriority] || "Crescita del capitale");
  setText("#profile-snapshot-pac", profile.monthlyPac > 0 ? `${euro.format(profile.monthlyPac)}/mese` : "Nessuno");

  const meter = document.querySelector("#profile-snapshot-risk-meter");
  if (meter) {
    meter.style.width = `${riskMeterWidth[profile.riskPreference] || 60}%`;
    meter.dataset.risk = profile.riskPreference || "balanced";
  }

  const stateBadge = document.querySelector("#profile-snapshot-state");
  if (stateBadge) {
    stateBadge.textContent = isComplete ? "Pronto" : "Da completare";
    stateBadge.dataset.state = isComplete ? "ready" : "pending";
  }

  const lossHint = document.querySelector("#max-loss-eur-hint");
  if (lossHint) {
    lossHint.textContent = profile.capital > 0
      ? `Sul capitale indicato significa circa ${lossEur} di calo temporaneo.`
      : "Inserisci il capitale per vedere la perdita in euro.";
  }
}

function buildPortfolioPayload() {
  const portfolio = collectPortfolio();
  validatePortfolioIsins(portfolio);
  return {
    name: document.querySelector("#portfolio-name").value.trim(),
    userPlan: currentPlan(),
    portfolio,
    investorProfile: collectInvestorProfile(),
    initialCapital: Number(document.querySelector("#initial-capital").value),
    rebalanceFrequency: DEFAULT_REBALANCE_FREQUENCY,
  };
}

function renderSavedPortfolios(items) {
  savedPortfolios = items || [];
  const options = savedPortfolios.length
    ? savedPortfolios.map((item) => `<option value="${item.id}">${item.name}</option>`).join("")
    : `<option value="">Nessun portfolio salvato</option>`;
  document.querySelectorAll("#saved-portfolios, #saved-portfolios-inline, #saved-portfolios-tracking").forEach((select) => {
    const previous = select.value;
    select.innerHTML = options;
    if (previous && savedPortfolios.some((item) => item.id === previous)) select.value = previous;
  });
}

function setSavedPortfolioSelection(id) {
  document.querySelectorAll("#saved-portfolios, #saved-portfolios-inline, #saved-portfolios-tracking").forEach((select) => {
    select.value = id || "";
  });
}

function selectedSavedPortfolioId() {
  return document.querySelector("#saved-portfolios-tracking")?.value || document.querySelector("#saved-portfolios-inline")?.value || document.querySelector("#saved-portfolios")?.value || "";
}

function loadSelectedPortfolio() {
  const saved = savedPortfolios.find((item) => item.id === selectedSavedPortfolioId());
  if (saved) {
    clearStandardPortfolioContext("Portfolio salvato caricato: l’analisi userà il confronto normale con il portafoglio ottimale.");
  }
  fillPortfolio(saved);
}

function syncSavedPortfolioSelects(source) {
  setSavedPortfolioSelection(source?.value || "");
}

async function loadSavedPortfolios() {
  try {
    const response = await fetch("/api/portfolios");
    const result = await response.json();
    renderSavedPortfolios(result.items || []);
  } catch (_error) {
    renderSavedPortfolios([]);
  }
}

async function loadPlanConfig() {
  try {
    const response = await fetch("/api/plans");
    const result = await response.json();
    planConfig = result.plans || {};
    featureMessages = result.features || {};
    if (result.serverPlanEnforced && result.serverPlan) {
      const planSelect = document.querySelector("#user-plan");
      planSelect.value = result.serverPlan;
      planSelect.disabled = true;
      planSelect.title = "Piano imposto dal server";
    }
  } catch (_error) {
    planConfig = {};
    featureMessages = {};
  }
  updatePlanBadge();
  updatePlanVisibility();
}

function fillPortfolio(saved) {
  if (!saved) return;
  document.querySelector("#portfolio-name").value = saved.name || "";
  document.querySelector("#initial-capital").value = Math.round(saved.initialCapital || 10000);
  if (saved.investorProfile) {
    document.querySelector("#investor-age").value = saved.investorProfile.age || 40;
    document.querySelector("#investor-horizon").value = saved.investorProfile.horizonYears || 10;
    document.querySelector("#investor-risk").value = saved.investorProfile.riskPreference || "balanced";
    document.querySelector("#investor-max-loss").value = saved.investorProfile.maxTemporaryLoss || 0.2;
    document.querySelector("#investor-pac").value = saved.investorProfile.monthlyPac || 0;
    const goalPriority = document.querySelector("#investor-goal-priority");
    const experience = document.querySelector("#investor-experience");
    const liquidity = document.querySelector("#investor-liquidity-need");
    if (goalPriority) goalPriority.value = saved.investorProfile.goalPriority || "capital_growth";
    if (experience) experience.value = saved.investorProfile.experienceLevel || "beginner";
    if (liquidity) liquidity.value = saved.investorProfile.liquidityNeed || "low";
    document.querySelector("#investor-objective").value = saved.investorProfile.objective || "";
  }
  positionsEl.innerHTML = "";
  (saved.portfolio || []).forEach((item) => {
    const minWeight = item.minWeight === undefined ? 0 : item.minWeight;
    const maxWeight = item.maxWeight === undefined ? 100 : item.maxWeight;
    const savedDisplayName = item.displayName && !looksLikeIsin(item.displayName) && !looksLikeFigiId(item.displayName) ? item.displayName : "";
    addPosition(item.symbol, Math.round(item.weight * 1000) / 10, item.assetClass, minWeight, maxWeight, savedDisplayName);
  });
  updatePortfolioLiveCheck();
}

async function saveCurrentPortfolio() {
  setStatus("Salvataggio portfolio...");
  try {
    const response = await fetch("/api/portfolios", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPortfolioPayload()),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || "Salvataggio non riuscito.");
    renderSavedPortfolios(result.items || []);
    setSavedPortfolioSelection(result.portfolio.id);
    setStatus("Portfolio salvato.", "ok");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function deleteCurrentPortfolio() {
  const id = selectedSavedPortfolioId();
  if (!id) {
    setStatus("Seleziona prima un portfolio salvato.", "error");
    return;
  }
  const saved = savedPortfolios.find((item) => item.id === id);
  const label = saved?.name || "questo portfolio";
  if (!confirm(`Eliminare definitivamente ${label}?`)) return;
  setStatus("Eliminazione portfolio...");
  try {
    const response = await fetch(`/api/portfolios?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || "Eliminazione non riuscita.");
    renderSavedPortfolios(result.items || []);
    setStatus("Portfolio eliminato.", "ok");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function renderMonitor(result) {
  instrumentMetadata = result.instrumentMetadata || instrumentMetadata;
  if (isLocked(result)) {
    document.querySelector("#monitor-note").innerHTML = lockedFeatureCard(result.feature || "portfolioTracking", result);
    return;
  }
  document.querySelector("#monitor-value").textContent = euro.format(result.currentValue);
  document.querySelector("#monitor-due").textContent = result.rebalance.due ? "Da fare" : "Non ancora";
  document.querySelector("#monitor-date").textContent = result.latestPriceDate;
  const notifications = Array.isArray(result.notifications) ? result.notifications : [];
  const previousMonitor = result.monitor?.previousDate
    ? `<article class="notification info"><strong>Ultimo monitoraggio precedente</strong><span>${escapeHtml(result.monitor.previousDate)}</span></article>`
    : "";
  document.querySelector("#monitor-note").innerHTML = `
    ${previousMonitor}
    ${notifications.map((item) => `
      <article class="notification ${escapeHtml(item.level || "info")}">
        <strong>${escapeHtml(item.title || "Notifica")}</strong>
        <span>${escapeHtml(item.message || "")}</span>
      </article>
    `).join("")}
    <p>${escapeHtml(result.note || "")}</p>
  `;
  const body = document.querySelector("#monitor-body");
  body.innerHTML = result.trades.map((item) => `
    <tr>
      <td>${escapeHtml(displayAnalysisInstrumentName(item))}</td>
      <td>${formatPercent(item.currentWeight)}</td>
      <td>${formatPercent(item.targetWeight)}</td>
      <td>${item.action}</td>
      <td>${number.format(Math.abs(item.quantity))}</td>
      <td>${euro.format(Math.abs(item.tradeValue))}</td>
    </tr>
  `).join("");
}

async function monitorSavedPortfolio() {
  const id = selectedSavedPortfolioId();
  if (!id) {
    setStatus("Seleziona prima un portfolio salvato.", "error");
    return;
  }
  setStatus("Monitoraggio portfolio...");
  try {
    const response = await fetch("/api/monitor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id,
        userPlan: currentPlan(),
        demo: document.querySelector("#demo").checked,
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      renderMonitor(result);
      throw new Error(result.message || result.error || "Monitoraggio non riuscito.");
    }
    renderMonitor(result);
    setStep("tracking");
    setStatus("Monitoraggio aggiornato.", "ok");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function setStep(step) {
  const nextStep = firstAccessibleStep(step);
  window.ChartLite?.hideTooltip?.();
  currentStep = nextStep;
  document.querySelectorAll(".step-panel, .result-step").forEach((item) => {
    item.classList.toggle("active", item.dataset.step === nextStep && canAccessStep(item.dataset.step));
  });
  document.querySelectorAll(".step-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.stepTarget === nextStep);
  });
  renderActiveAdvancedSection();
  loadVisibleAiPanels();
  updateGuidedFlow();
  updateMobileStepNav();
  updateEmptyStateGuidance();
  enhanceAdvancedAccordions();
  rebalancePortfolioComparisons();
  updateComparisonContextStrips();
}

function updateGuidedFlow() {
  const order = ["profile", "portfolio", "diagnosis", "risk", "backtest", "frontier", "montecarlo", "scenarios", "correlation", "tracking", "analysis"];
  const currentIndex = order.indexOf(currentStep);
  document.querySelectorAll("[data-flow-step]").forEach((item) => {
    const step = item.dataset.flowStep;
    const index = order.indexOf(step);
    const allowed = canAccessStep(step);
    item.hidden = !allowed;
    item.classList.toggle("is-current", step === currentStep);
    item.classList.toggle("is-complete", currentIndex > -1 && index > -1 && index < currentIndex);
    item.setAttribute("aria-disabled", String(!allowed));
    item.setAttribute("aria-current", step === currentStep ? "step" : "false");
  });
}

function updateEmptyStateGuidance() {
  const hasAnalysis = document.querySelector("#diagnostic-score")?.textContent?.includes("/100") &&
    !document.querySelector("#diagnostic-score")?.textContent?.startsWith("--");
  document.querySelectorAll(".result-step").forEach((section) => {
    if (section.dataset.step === "profile" || section.dataset.step === "portfolio") return;
    section.classList.toggle("needs-analysis", !hasAnalysis);
  });
}

function initializeMobileStepNav() {
  const nav = document.querySelector("#mobile-step-nav");
  if (!nav || nav.childElementCount) return;
  const items = [
    ["profile", "Profilo"],
    ["portfolio", "Portafoglio"],
    ["diagnosis", "Diagnosi"],
    ["frontier", "Ottimizza"],
    ["tracking", "Tracking"],
  ];
  nav.innerHTML = items
    .map(([step, label]) => `<button type="button" data-mobile-step="${step}"><span></span>${label}</button>`)
    .join("");
  nav.querySelectorAll("[data-mobile-step]").forEach((button) => {
    button.addEventListener("click", () => setStep(button.dataset.mobileStep));
  });
}

function updateMobileStepNav() {
  const nav = document.querySelector("#mobile-step-nav");
  if (!nav) return;
  nav.querySelectorAll("[data-mobile-step]").forEach((button) => {
    const allowed = canAccessStep(button.dataset.mobileStep);
    button.hidden = !allowed;
    button.classList.toggle("active", button.dataset.mobileStep === currentStep);
  });
}

function enhanceAdvancedAccordions() {
  document.querySelectorAll(".advanced-results").forEach((section) => {
    if (section.id === "benchmark-results") return;
    if (section.dataset.accordionReady === "true") return;
    const title = section.querySelector(":scope > .table-title");
    if (!title) return;
    const button = document.createElement("button");
    button.className = "advanced-collapse-button";
    button.type = "button";
    const shouldCollapseByDefault = currentPlan() !== "advanced";
    if (shouldCollapseByDefault) section.classList.add("is-collapsed");
    button.textContent = shouldCollapseByDefault ? "Mostra dettagli" : "Comprimi dettagli";
    button.addEventListener("click", () => {
      const collapsed = section.classList.toggle("is-collapsed");
      button.textContent = collapsed ? "Mostra dettagli" : "Comprimi dettagli";
    });
    title.append(button);
    section.dataset.accordionReady = "true";
  });
}

function collectStressTesting(forceEnabled = false) {
  const scenario =
    document.querySelector("#stress-scenario")?.value ||
    document.querySelector("#hidden-risk-scenario")?.value ||
    "Global Recession";
  const shocks = factorStressScenarios[scenario] || factorStressScenarios["Global Recession"];
  return {
    enabled: Boolean(forceEnabled),
    scenario,
    useCustomShock: false,
    shocks,
    runMonteCarlo: false,
  };
}

function syncStressSlidersFromScenario() {
  const scenario =
    document.querySelector("#stress-scenario")?.value ||
    document.querySelector("#hidden-risk-scenario")?.value ||
    "Global Recession";
  const shocks = factorStressScenarios[scenario] || factorStressScenarios["Global Recession"];
  factorOrder.forEach((factor) => {
    const input = document.querySelector(`[data-factor-shock="${factor}"]`);
    const output = document.querySelector(`[data-factor-value="${factor}"]`);
    const value = Math.round((shocks[factor] || 0) * 100);
    if (input) input.value = value;
    if (output) output.textContent = `${value}%`;
  });
}

function setStatus(message, type = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`.trim();
}

function validateBacktestForm() {
  if (!form) return true;
  if (typeof form.checkValidity === "function") {
    if (form.checkValidity()) return true;
    if (typeof form.reportValidity === "function") form.reportValidity();
    return false;
  }
  const invalid = [...form.querySelectorAll("[required]")].find((field) => !String(field.value || "").trim());
  if (invalid) {
    invalid.focus?.();
    return false;
  }
  return true;
}

function setAnalysisBusy(isBusy, message = "Analisi in corso...") {
  analysisInFlight = isBusy;
  document.body.classList.toggle("analysis-loading", isBusy);
  const submitButton = document.querySelector("#run-analysis-button");
  const scenarioButton = document.querySelector("#run-scenarios");
  const stressScenarioButton = document.querySelector("#calculate-stress-scenario");
  const monitorButton = document.querySelector("#monitor-portfolio-tracking");
  [submitButton, scenarioButton, stressScenarioButton, monitorButton].forEach((button) => {
    if (!button) return;
    button.disabled = isBusy;
    button.dataset.busy = String(isBusy);
  });
  if (submitButton) {
    submitButton.textContent = isBusy ? "Analisi in corso..." : "Esegui analisi completa";
  }
  if (scenarioButton) {
    scenarioButton.textContent = isBusy ? "Simulazione in corso..." : "Esegui simulazione scenari";
  }
  if (stressScenarioButton) {
    stressScenarioButton.textContent = isBusy ? "Calcolo in corso..." : "Calcola crisi simulate";
  }
  if (isBusy) setStatus(message);
}

function drawEmptyChart(canvasOrSelector, message = "Il grafico apparira qui") {
  const canvas = typeof canvasOrSelector === "string" ? document.querySelector(canvasOrSelector) : canvasOrSelector;
  if (!canvas) return;
  ChartLite.clearTooltip(canvas);
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#657080";
  context.font = "18px system-ui";
  context.textAlign = "center";
  context.fillText(message, canvas.width / 2, canvas.height / 2);
}

const ChartLite = window.ChartLite || {
  tooltip: null,
  ensureTooltip() {
    if (!this.tooltip) {
      this.tooltip = document.createElement("div");
      this.tooltip.className = "chart-tooltip";
      this.tooltip.hidden = true;
      this.tooltip.style.display = "none";
      document.body.appendChild(this.tooltip);
    }
    return this.tooltip;
  },
  hideTooltip() {
    if (this.tooltip) {
      this.tooltip.hidden = true;
      this.tooltip.innerHTML = "";
      this.tooltip.style.display = "none";
    }
  },
  clearTooltip(canvas) {
    if (canvas?._chartLiteCleanup) canvas._chartLiteCleanup();
    this.hideTooltip();
  },
  attachTooltip(canvas, points = []) {
    if (!canvas) return;
    this.clearTooltip(canvas);
    const tooltip = this.ensureTooltip();
    const validPoints = points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
    const step = Math.max(1, Math.ceil(validPoints.length / 600));
    const normalized = validPoints.filter((_, index) => index % step === 0 || index === validPoints.length - 1);
    if (!normalized.length) return;
    const isOutsideCanvas = (event) => {
      if (!event) return true;
      const rect = canvas.getBoundingClientRect();
      return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    };
    const nearest = (event) => {
      if (!event) return;
      if (isOutsideCanvas(event)) {
        hide();
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const x = (event.clientX - rect.left) * scaleX;
      const y = (event.clientY - rect.top) * scaleY;
      let best = null;
      let bestDistance = Infinity;
      normalized.forEach((point) => {
        const distance = Math.hypot(point.x - x, point.y - y);
        if (distance < bestDistance) {
          best = point;
          bestDistance = distance;
        }
      });
      if (!best || bestDistance > 42) {
        hide();
        return;
      }
      tooltip.innerHTML = best.html || escapeHtml(best.label || "");
      tooltip.hidden = false;
      tooltip.style.display = "grid";
      tooltip.style.left = `${Math.min(window.innerWidth - 280, Math.max(12, event.clientX + 14))}px`;
      tooltip.style.top = `${Math.max(12, event.clientY + 14)}px`;
    };
    const hide = () => {
      tooltip.hidden = true;
      tooltip.innerHTML = "";
      tooltip.style.display = "none";
    };
    const hideIfOutside = (event) => {
      if (isOutsideCanvas(event)) hide();
    };
    const hideIfPointerTargetsOutside = (event) => {
      if (event?.target === canvas || canvas.contains(event?.target)) return;
      hide();
    };
    const hideIfLeavingWindow = (event) => {
      if (!event.relatedTarget) hide();
    };
    const hideOnVisibilityChange = () => {
      if (document.hidden) hide();
    };
    const touchStart = (event) => nearest(event.touches[0]);
    const touchMove = (event) => nearest(event.touches[0]);
    canvas.addEventListener("mousemove", nearest);
    canvas.addEventListener("pointermove", nearest);
    canvas.addEventListener("mouseleave", hide);
    canvas.addEventListener("pointerleave", hide);
    canvas.addEventListener("mouseout", hide);
    canvas.addEventListener("pointercancel", hide);
    canvas.addEventListener("blur", hide);
    canvas.addEventListener("touchstart", touchStart, { passive: true });
    canvas.addEventListener("touchmove", touchMove, { passive: true });
    canvas.addEventListener("touchend", hide);
    document.addEventListener("pointermove", hideIfOutside, true);
    document.addEventListener("pointerover", hideIfPointerTargetsOutside, true);
    document.addEventListener("pointerdown", hideIfOutside, true);
    document.addEventListener("mouseout", hideIfLeavingWindow, true);
    document.addEventListener("visibilitychange", hideOnVisibilityChange);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    window.addEventListener("resize", hide);
    window.addEventListener("scroll", hide, true);
    canvas._chartLiteCleanup = () => {
      canvas.removeEventListener("mousemove", nearest);
      canvas.removeEventListener("pointermove", nearest);
      canvas.removeEventListener("mouseleave", hide);
      canvas.removeEventListener("pointerleave", hide);
      canvas.removeEventListener("mouseout", hide);
      canvas.removeEventListener("pointercancel", hide);
      canvas.removeEventListener("blur", hide);
      canvas.removeEventListener("touchstart", touchStart);
      canvas.removeEventListener("touchmove", touchMove);
      canvas.removeEventListener("touchend", hide);
      document.removeEventListener("pointermove", hideIfOutside, true);
      document.removeEventListener("pointerover", hideIfPointerTargetsOutside, true);
      document.removeEventListener("pointerdown", hideIfOutside, true);
      document.removeEventListener("mouseout", hideIfLeavingWindow, true);
      document.removeEventListener("visibilitychange", hideOnVisibilityChange);
      document.removeEventListener("scroll", hide, true);
      window.removeEventListener("blur", hide);
      window.removeEventListener("resize", hide);
      window.removeEventListener("scroll", hide, true);
      tooltip.hidden = true;
      tooltip.innerHTML = "";
      tooltip.style.display = "none";
      canvas._chartLiteCleanup = null;
    };
  },
  shortLabel(value, max = 18) {
    const label = String(value || "--");
    return label.length > max ? `${label.slice(0, max - 2)}...` : label;
  },
  impactLabel(value) {
    const percent = formatPercent(value);
    const capital = Number(document.querySelector("#initial-capital")?.value || 0);
    if (!capital || !Number.isFinite(value)) return percent;
    return `${percent} · ${euro.format(capital * value)}`;
  },
};

function drawChart(points, selector = "#equity-chart", color = "#18794e") {
  const chart = document.querySelector(selector);
  if (!chart) return;
  ChartLite.clearTooltip(chart);
  const ctx = chart.getContext("2d");
  ctx.clearRect(0, 0, chart.width, chart.height);
  const width = chart.width;
  const height = chart.height;
  const pad = { left: 72, right: 24, top: 28, bottom: 46 };

  if (!points.length) {
    drawEmptyChart(chart, "La curva equity apparira qui");
    return;
  }

  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const x = (index) => pad.left + (index / Math.max(points.length - 1, 1)) * (width - pad.left - pad.right);
  const y = (value) => pad.top + (1 - (value - min) / range) * (height - pad.top - pad.bottom);
  let peakIndex = 0;
  let troughIndex = 0;
  let runningPeakIndex = 0;
  let worstDrawdown = 0;
  values.forEach((value, index) => {
    if (value > values[runningPeakIndex]) runningPeakIndex = index;
    const drawdown = value / values[runningPeakIndex] - 1;
    if (drawdown < worstDrawdown) {
      worstDrawdown = drawdown;
      peakIndex = runningPeakIndex;
      troughIndex = index;
    }
  });

  ctx.strokeStyle = "#d9e1e8";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < 5; i += 1) {
    const yy = pad.top + (i / 4) * (height - pad.top - pad.bottom);
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(width - pad.right, yy);
  }
  ctx.stroke();

  ctx.fillStyle = "#657080";
  ctx.font = "13px system-ui";
  ctx.textAlign = "right";
  for (let i = 0; i < 5; i += 1) {
    const value = max - (i / 4) * range;
    ctx.fillText(euro.format(value), pad.left - 10, pad.top + (i / 4) * (height - pad.top - pad.bottom) + 4);
  }

  const gradient = ctx.createLinearGradient(0, pad.top, 0, height - pad.bottom);
  gradient.addColorStop(0, color === "#2f66c5" ? "rgba(47, 102, 197, 0.22)" : "rgba(24, 121, 78, 0.26)");
  gradient.addColorStop(1, color === "#2f66c5" ? "rgba(47, 102, 197, 0.02)" : "rgba(24, 121, 78, 0.02)");

  ctx.beginPath();
  points.forEach((point, index) => {
    const xx = x(index);
    const yy = y(point.value);
    if (index === 0) ctx.moveTo(xx, yy);
    else ctx.lineTo(xx, yy);
  });
  ctx.lineTo(x(points.length - 1), height - pad.bottom);
  ctx.lineTo(x(0), height - pad.bottom);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  ctx.beginPath();
  points.forEach((point, index) => {
    const xx = x(index);
    const yy = y(point.value);
    if (index === 0) ctx.moveTo(xx, yy);
    else ctx.lineTo(xx, yy);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.stroke();

  if (worstDrawdown < 0 && troughIndex > peakIndex) {
    ctx.save();
    ctx.strokeStyle = "rgba(193, 62, 62, 0.72)";
    ctx.lineWidth = 4;
    ctx.beginPath();
    for (let index = peakIndex; index <= troughIndex; index += 1) {
      const xx = x(index);
      const yy = y(points[index].value);
      if (index === peakIndex) ctx.moveTo(xx, yy);
      else ctx.lineTo(xx, yy);
    }
    ctx.stroke();
    ctx.restore();
  }

  ctx.fillStyle = "#a46a11";
  points.forEach((point, index) => {
    if (!point.rebalanced || index === 0) return;
    ctx.beginPath();
    ctx.arc(x(index), y(point.value), 4, 0, Math.PI * 2);
    ctx.fill();
  });

  const markerItems = [
    { index: peakIndex, label: "Massimo prima della peggiore perdita", color: "#a46a11" },
    { index: troughIndex, label: "Minimo della peggiore perdita", color: "#c13e3e" },
    { index: points.length - 1, label: "Valore finale", color },
  ];
  markerItems.forEach((marker) => {
    const point = points[marker.index];
    if (!point) return;
    ctx.beginPath();
    ctx.fillStyle = marker.color;
    ctx.arc(x(marker.index), y(point.value), 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
  });

  ctx.fillStyle = "#657080";
  ctx.textAlign = "left";
  ctx.fillText(points[0].date, pad.left, height - 14);
  ctx.textAlign = "right";
  ctx.fillText(points[points.length - 1].date, width - pad.right, height - 14);

  ChartLite.attachTooltip(chart, points.map((point, index) => ({
    x: x(index),
    y: y(point.value),
    html: `
      <b>${escapeHtml(point.date || "")}</b>
      <span>Valore: ${euro.format(point.value || 0)}</span>
      <span>Rendimento giorno: ${formatPercent(point.return || 0)}</span>
      ${index === troughIndex && worstDrawdown < 0 ? `<span>Perdita peggiore: ${formatPercent(worstDrawdown)}</span>` : ""}
    `,
  })));
}

function sampledPointForIndex(points, index, maxLength) {
  if (!Array.isArray(points) || !points.length) return null;
  if (maxLength <= 1) return points[0];
  const mappedIndex = Math.round((index / Math.max(maxLength - 1, 1)) * (points.length - 1));
  return points[Math.min(Math.max(mappedIndex, 0), points.length - 1)] || null;
}

function drawMultiLineChart(seriesItems, selector = "#benchmark-portfolio-chart") {
  const chart = document.querySelector(selector);
  if (!chart) return;
  ChartLite.clearTooltip(chart);
  const ctx = chart.getContext("2d");
  ctx.clearRect(0, 0, chart.width, chart.height);
  const series = (seriesItems || [])
    .map((item) => ({
      ...item,
      points: Array.isArray(item.points) ? item.points.filter((point) => Number.isFinite(Number(point?.value))) : [],
    }))
    .filter((item) => item.points.length);
  if (!series.length) {
    drawEmptyChart(chart, "Confronto non disponibile");
    return;
  }

  const width = chart.width;
  const height = chart.height;
  const pad = { left: 72, right: 24, top: 48, bottom: 46 };
  const allValues = series.flatMap((item) => item.points.map((point) => Number(point.value)));
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const range = max - min || 1;
  const maxLength = Math.max(...series.map((item) => item.points.length));
  const x = (index) => pad.left + (index / Math.max(maxLength - 1, 1)) * (width - pad.left - pad.right);
  const y = (value) => pad.top + (1 - (value - min) / range) * (height - pad.top - pad.bottom);

  ctx.strokeStyle = "#d9e1e8";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < 5; i += 1) {
    const yy = pad.top + (i / 4) * (height - pad.top - pad.bottom);
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(width - pad.right, yy);
  }
  ctx.stroke();

  ctx.fillStyle = "#657080";
  ctx.font = "13px system-ui";
  ctx.textAlign = "right";
  for (let i = 0; i < 5; i += 1) {
    const value = max - (i / 4) * range;
    ctx.fillText(euro.format(value), pad.left - 10, pad.top + (i / 4) * (height - pad.top - pad.bottom) + 4);
  }

  series.forEach((item) => {
    ctx.beginPath();
    for (let index = 0; index < maxLength; index += 1) {
      const point = sampledPointForIndex(item.points, index, maxLength);
      if (!point) continue;
      const xx = x(index);
      const yy = y(Number(point.value));
      if (index === 0) ctx.moveTo(xx, yy);
      else ctx.lineTo(xx, yy);
    }
    ctx.strokeStyle = item.color;
    ctx.lineWidth = item.width || 3;
    ctx.setLineDash(item.dashed ? [8, 6] : []);
    ctx.stroke();
    ctx.setLineDash([]);
    const lastPoint = sampledPointForIndex(item.points, maxLength - 1, maxLength);
    if (lastPoint) {
      ctx.beginPath();
      ctx.fillStyle = item.color;
      ctx.arc(x(maxLength - 1), y(Number(lastPoint.value)), 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  });

  let legendX = pad.left;
  const legendY = 24;
  ctx.textAlign = "left";
  ctx.font = "12px system-ui";
  series.forEach((item) => {
    ctx.fillStyle = item.color;
    ctx.fillRect(legendX, legendY - 9, 11, 11);
    ctx.fillStyle = "#263241";
    const label = ChartLite.shortLabel(item.label, 24);
    ctx.fillText(label, legendX + 16, legendY);
    legendX += ctx.measureText(label).width + 36;
  });

  const firstSeries = series[0]?.points || [];
  if (firstSeries.length) {
    ctx.fillStyle = "#657080";
    ctx.textAlign = "left";
    ctx.fillText(firstSeries[0].date || "", pad.left, height - 14);
    ctx.textAlign = "right";
    ctx.fillText(firstSeries[firstSeries.length - 1].date || "", width - pad.right, height - 14);
  }

  ChartLite.attachTooltip(chart, Array.from({ length: maxLength }, (_, index) => {
    const rows = series.map((item) => {
      const point = sampledPointForIndex(item.points, index, maxLength);
      return point
        ? `<span><b style="color:${item.color}">${escapeHtml(item.label)}</b>: ${euro.format(Number(point.value || 0))}</span>`
        : "";
    }).join("");
    const datePoint = sampledPointForIndex(firstSeries, index, maxLength);
    return {
      x: x(index),
      y: y(Number(sampledPointForIndex(firstSeries, index, maxLength)?.value || allValues[0])),
      html: `<b>${escapeHtml(datePoint?.date || "")}</b>${rows}`,
    };
  }));
}

function renderResult(result) {
  hiddenRiskScenarioCalculated = false;
  stressScenarioCalculated = Boolean(result?.stressTesting?.enabled && result?.stressTesting?.result);
  arrangeComparisonMetricCards();
  latestComparisonContext = result.analysisComparisonContext || null;
  applyAnalysisLayoutContext(result);
  syncActiveImprovementContextFromResult(result);
  const comparisonData = comparisonDataForResult(result);
  updateComparisonLabels(latestComparisonContext);
  instrumentMetadata = result.instrumentMetadata || {};
  (result.portfolio || []).forEach((item) => {
    if (item.symbol && item.displayName) {
      instrumentMetadata[item.symbol] = {
        ...(instrumentMetadata[item.symbol] || {}),
        instrumentId: item.symbol,
        name: item.displayName,
        displayName: item.displayName,
      };
    }
  });
  document.querySelector("#period-title").textContent = `${result.start} - ${result.end}`;
  const sourceText = {
    eodhd: "EODHD",
    demo: "Demo",
  }[result.dataSource] || result.dataSource;
  document.querySelector("#source-pill").textContent = sourceText;
  document.querySelector("#source-pill").classList.toggle("warning", result.dataSource !== "eodhd");

  document.querySelector("#final-value").textContent = euro.format(result.metrics.finalValue);
  document.querySelector("#total-return").textContent = formatPercent(result.metrics.totalReturn);
  document.querySelector("#cagr").textContent = formatPercent(result.metrics.cagr);
  document.querySelector("#drawdown").textContent = formatPercent(result.metrics.maxDrawdown);
  document.querySelector("#sharpe").textContent = result.metrics.sharpe === null ? "--" : number.format(result.metrics.sharpe);
  document.querySelector("#trading-days").textContent = number.format(result.tradingDays);
  document.querySelector("#rebalance-count").textContent = `${result.rebalance.count}x`;
  renderAdvisor(result.aiAdvisor);
  renderDiagnosticDashboard(result);
  renderRiskReality(result);
  renderActionDashboard(result);

  drawChart(result.equityCurve, "#equity-chart", "#18794e");
  if (comparisonData?.available && Array.isArray(comparisonData.equityCurve) && comparisonData.equityCurve.length) {
    drawChart(comparisonData.equityCurve, "#optimized-equity-chart", latestComparisonContext?.comparisonMode === "standard_benchmark" ? "#6f4dbf" : "#2f66c5");
  } else {
    drawEmptyChart("#optimized-equity-chart", comparisonData?.reason || latestComparisonContext?.benchmarkReason || "Confronto non disponibile.");
  }
  renderMonteCarlo(result.monteCarlo, comparisonData?.monteCarlo, latestComparisonContext?.labels?.monteCarloComparison);
  renderEfficientFrontier(result.efficientFrontier);
  renderOptimizationAsSecondary(result);
  if (stressScenarioCalculated) {
    renderStressTesting(result.stressTesting, comparisonData?.stressTesting, latestComparisonContext?.labels?.scenarioComparison);
  } else {
    renderStressScenarioAwaiting(result.stressTesting, comparisonData?.stressTesting);
  }
  renderComparisonBacktest(comparisonData, latestComparisonContext?.labels?.backtestComparison);
  renderContributionTable("#contribution-body", result.contribution);
  renderContributionTable(
    "#optimized-contribution-body",
    comparisonData?.available ? comparisonData.contribution : [],
    comparisonData?.reason || latestComparisonContext?.benchmarkReason || "Confronto non disponibile.",
  );
  renderBenchmarkComparison(result.benchmarkComparison, result.equityCurve, result.standardBenchmarkAnalysis, result);
  renderAdvancedAnalytics(result.advancedAnalytics);
  renderSelectedStandardBenchmark(result.standardBenchmarkAnalysis, result);
  renderStandardPortfolioRecommendation(result.standardPortfolioRecommendation, result);
  renderImprovementPlan(result.improvementPlan, result);
  rebalancePortfolioComparisons();
}

function applyAnalysisLayoutContext(result) {
  const mode = result?.analysisComparisonContext?.comparisonMode || "optimization";
  document.body.dataset.comparisonMode = mode;
  document.querySelectorAll(".result-step").forEach((section) => {
    section.dataset.comparisonMode = mode;
  });
  const frontierSection = document.querySelector("#efficient-frontier-results");
  if (frontierSection) {
    frontierSection.classList.toggle("optimization-secondary-mode", mode !== "optimization");
    frontierSection.classList.toggle("optimization-primary-mode", mode === "optimization");
  }
  updatePlanVisibility();
}

function standardRiskLabel(level) {
  return {
    1: "Molto prudente",
    2: "Prudente",
    3: "Moderato",
    4: "Dinamico",
    5: "Aggressivo",
  }[Number(level)] || "Non disponibile";
}

function renderStandardHoldings(holdings = []) {
  return holdings.slice(0, 5).map((holding) => `
    <li>
      <span>${escapeHtml(holding.name || holding.isin || "--")}</span>
      <strong>${number.format(Number(holding.weight || 0))}%</strong>
    </li>
  `).join("");
}

function defaultComparisonContext() {
  return {
    comparisonMode: "optimization",
    comparisonDataKey: "optimizedPortfolio",
    labels: {
      comparison: "Portfolio efficiente",
      backtestComparison: "Backtest ottimizzato",
      monteCarloComparison: "Monte Carlo ottimizzato",
      scenarioComparison: "Scenario ottimizzato",
    },
    shouldShowOptimizedAsPrimary: true,
  };
}

function comparisonDataForResult(result) {
  const context = result?.analysisComparisonContext || defaultComparisonContext();
  if (context.comparisonMode === "standard_benchmark") return result.standardBenchmarkAnalysis;
  if (context.comparisonMode === "standard_only") return null;
  return result.optimizedPortfolio;
}

function setLaneTitle(selector, primary, secondary) {
  const panel = document.querySelector(selector);
  const lane = panel?.closest(".portfolio-lane");
  const title = lane?.querySelector(".lane-title");
  if (!title) return;
  const span = title.querySelector("span");
  const strong = title.querySelector("strong");
  if (span && primary !== undefined) span.textContent = primary;
  if (strong && secondary !== undefined) strong.textContent = secondary;
}

function setChartTitle(selector, primary, secondary) {
  const chart = document.querySelector(selector);
  const panel = chart?.closest(".chart-panel, section");
  const title = panel?.querySelector(".chart-title");
  if (!title) return;
  const span = title.querySelector("span");
  const strong = title.querySelector("strong");
  if (span && primary !== undefined) span.textContent = primary;
  if (strong && secondary !== undefined) strong.textContent = secondary;
}

function setChartGuidance(selector, text) {
  const chart = document.querySelector(selector);
  const panel = chart?.closest(".chart-panel, section");
  const guidance = panel?.querySelector(".chart-guidance");
  if (guidance && text) guidance.textContent = text;
}

function renderStandardModeNote(message = "") {
  const note = document.querySelector("#standard-mode-note");
  if (!note) return;
  if (!message) {
    note.hidden = true;
    note.innerHTML = "";
    return;
  }
  note.hidden = false;
  note.innerHTML = `<strong>Modalità portfolio standard</strong><p>${escapePlainFinance(message)}</p>`;
}

function updateComparisonLabels(context = defaultComparisonContext()) {
  const labels = context?.labels || defaultComparisonContext().labels;
  const primaryLabel = latestFinalAnalysisContext?.primaryPortfolioName ||
    (latestFinalAnalysisContext?.mode === "final_optimized_vs_recommended_standard"
    ? "Portafoglio ottimizzato"
    : labels.primary || "Portfolio inserito");
  const comparisonLabel = latestFinalAnalysisContext?.comparisonPortfolioName || labels.comparison || "Portfolio efficiente";
  const isStandardBenchmark = context?.comparisonMode === "standard_benchmark";
  setChartTitle("#equity-chart", primaryLabel, "Backtest storico");
  setLaneTitle("#optimized-backtest-metrics", comparisonLabel, labels.backtestComparison || "Backtest ottimizzato");
  setChartTitle("#optimized-equity-chart", comparisonLabel, labels.backtestComparison || "Backtest ottimizzato");
  setLaneTitle("#optimized-monte-carlo-grid", comparisonLabel, labels.monteCarloComparison || "Monte Carlo ottimizzato");
  setChartTitle("#optimized-monte-carlo-chart", comparisonLabel, labels.monteCarloComparison || "Monte Carlo ottimizzato");
  setLaneTitle("#optimized-stress-grid", comparisonLabel, labels.scenarioComparison || "Scenario ottimizzato");
  setChartTitle("#optimized-stress-asset-chart", comparisonLabel, "Contributo per asset");
  setChartTitle("#optimized-stress-factor-chart", comparisonLabel, "Contributo per fattore");
  setLaneTitle("#analysis-intelligence-optimized", comparisonLabel, labels.diagnosisComparison || "Confronto");
  setLaneTitle("#backtest-intelligence-optimized", comparisonLabel, labels.backtestComparison || "Backtest confronto");
  setLaneTitle("#montecarlo-intelligence-optimized", comparisonLabel, labels.monteCarloComparison || "Monte Carlo confronto");
  setLaneTitle("#scenarios-intelligence-optimized", comparisonLabel, labels.scenarioComparison || "Scenario confronto");
  setLaneTitle("#optimized-contribution-body", comparisonLabel, "Contributo per asset");
  setLaneTitle("#fama-current-comment", primaryLabel, "Esposizioni fattoriali");
  setLaneTitle("#fama-optimized-comment", comparisonLabel, isStandardBenchmark ? "Esposizioni portfolio benchmark" : "Esposizioni ottimizzate");
  setLaneTitle("#geo-current-comment", primaryLabel, "Continenti e stati");
  setLaneTitle("#geo-optimized-comment", comparisonLabel, isStandardBenchmark ? "Geografia portfolio benchmark" : "Continenti e stati ottimizzati");
  setLaneTitle("#rolling-current-comment", primaryLabel, "Rolling metrics");
  setLaneTitle("#rolling-optimized-comment", comparisonLabel, isStandardBenchmark ? "Stabilità portfolio benchmark" : "Rolling metrics ottimizzate");
  setLaneTitle("#correlation-current-comment", primaryLabel, "Diversificazione reale");
  setLaneTitle("#correlation-optimized-comment", comparisonLabel, isStandardBenchmark ? "Diversificazione portfolio benchmark" : "Diversificazione ottimizzata");
  setLaneTitle("#pac-current-comment", primaryLabel, "Piano di accumulo");
  setLaneTitle("#pac-optimized-comment", comparisonLabel, isStandardBenchmark ? "PAC portfolio benchmark" : "PAC ottimizzato");
  setLaneTitle("#drawdown-current-comment", primaryLabel, "Perdita temporanea storica");
  setLaneTitle("#drawdown-optimized-comment", comparisonLabel, isStandardBenchmark ? "Perdita temporanea portfolio benchmark" : "Perdita temporanea ottimizzata");
  setLaneTitle("#factor-risk-current-comment", primaryLabel, "Rischio fattoriale");
  setLaneTitle("#factor-risk-optimized-comment", comparisonLabel, isStandardBenchmark ? "Rischio fattoriale portfolio benchmark" : "Rischio fattoriale ottimizzato");
  setLaneTitle("#scenario-comparison-current-comment", primaryLabel, "Tutti gli scenari");
  setLaneTitle("#scenario-comparison-optimized-comment", comparisonLabel, isStandardBenchmark ? "Scenari portfolio benchmark" : "Tutti gli scenari ottimizzati");
  setChartTitle("#fama-current-chart", primaryLabel, "Beta factor exposures");
  setChartTitle("#fama-optimized-chart", comparisonLabel, "Beta factor exposures");
  setChartGuidance(
    "#fama-optimized-chart",
    isStandardBenchmark
      ? "Confronta se il portfolio benchmark standard ha dipendenze fattoriali diverse: beta sotto zero significa sensibilità inversa rispetto al fattore."
      : "Confronta se l’ottimizzazione cambia le dipendenze principali: beta sotto zero significa sensibilità inversa rispetto al fattore."
  );
  setChartTitle("#geo-current-chart", primaryLabel, "Esposizione per continente");
  setChartTitle("#geo-optimized-chart", comparisonLabel, "Esposizione per continente");
  setChartTitle("#rolling-current-chart", primaryLabel, "Rolling return");
  setChartTitle("#rolling-optimized-chart", comparisonLabel, "Rolling return");
  setChartTitle("#correlation-results .table-panel:first-child", primaryLabel, "Matrice di correlazione");
  setChartTitle("#correlation-results .table-panel:nth-child(2)", comparisonLabel, isStandardBenchmark ? "Matrice portfolio benchmark" : "Matrice confronto");

  const optional = document.querySelector("#frontier-results");
  if (optional) optional.dataset.secondary = ["standard_benchmark", "standard_only"].includes(context?.comparisonMode) ? "true" : "false";
}

function renderStandardMetricGrid(analysis) {
  const metrics = analysis?.metrics || {};
  if (!analysis?.available || !metrics.finalValue) return "";
  return `
    <div class="standard-meta-grid">
      <article><span>Valore finale simulato</span><strong>${euro.format(metrics.finalValue)}</strong></article>
      <article><span>Crescita media annua</span><strong>${formatPercent(metrics.cagr || 0)}</strong></article>
      <article><span>Peggiore perdita temporanea</span><strong>${formatPercent(metrics.maxDrawdown || 0)}</strong></article>
      <article><span>Rapporto rischio-rendimento</span><strong>${metrics.sharpe === null || metrics.sharpe === undefined ? "--" : number.format(metrics.sharpe)}</strong></article>
    </div>
  `;
}

function comparisonDeltaLine(comparison) {
  if (!comparison?.available) return comparison?.reason || "Confronto quantitativo non disponibile.";
  const deltas = comparison.deltas || {};
  const parts = [];
  if (Number.isFinite(Number(deltas.cagr))) parts.push(`crescita media annua ${formatPercent(deltas.cagr)}`);
  if (Number.isFinite(Number(deltas.maxDrawdown))) parts.push(`differenza perdita temporanea ${formatPercent(deltas.maxDrawdown)}`);
  if (Number.isFinite(Number(deltas.sharpe))) parts.push(`differenza rapporto rischio-rendimento ${number.format(deltas.sharpe)}`);
  const exposure = comparison.exposure || {};
  if (Number.isFinite(Number(exposure.primaryMaxWeight)) && Number.isFinite(Number(exposure.benchmarkMaxWeight))) {
    parts.push(`peso massimo ${formatPercent(exposure.primaryMaxWeight)} vs ${formatPercent(exposure.benchmarkMaxWeight)}`);
  }
  const equityDelta = exposure.delta?.equity;
  if (Number.isFinite(Number(equityDelta))) parts.push(`differenza azionario ${formatPercent(equityDelta)}`);
  return parts.length ? parts.join(" · ") : comparison.summary;
}

function standardComparisonSummary(type, recommendation, result) {
  const portfolio = recommendation?.recommendedPortfolio;
  if (!portfolio) return "";
  const comparisons = recommendation?.comparisons || {};
  const comparison = type === "optimized" ? comparisons.optimizedVsStandard : comparisons.userVsStandard;
  const targetLabel = type === "optimized" ? "portafoglio ottimale" : "portafoglio inserito";
  return `
    <article class="standard-comparison-note">
      <span>Confronto ${escapeHtml(targetLabel)} vs benchmark standard</span>
      <p>Il benchmark educativo selezionato è <b>${escapeHtml(portfolio.name)}</b>. Differenze principali: ${escapePlainFinance(comparisonDeltaLine(comparison))}</p>
      <small>Questo confronto non sostituisce l’analisi quantitativa: serve a capire differenze di rischio, composizione e diversificazione.</small>
    </article>
  `;
}

function normalizePortfolioOverrideWeights(items) {
  const raw = (items || [])
    .filter((item) => item?.symbol && Number(item.weight) > 0)
    .map((item) => {
      const rawWeight = Number(item.weight || 0);
      return {
        ...item,
        weight: rawWeight <= 1 ? rawWeight * 100 : rawWeight,
      };
    });
  const total = raw.reduce((sum, item) => sum + Number(item.weight || 0), 0);
  if (!raw.length || total <= 0) return [];
  const normalized = raw.map((item) => ({
    ...item,
    weight: Math.round((Number(item.weight || 0) / total) * 1000) / 10,
  }));
  const normalizedTotal = normalized.reduce((sum, item) => sum + Number(item.weight || 0), 0);
  const diff = Math.round((100 - normalizedTotal) * 10) / 10;
  if (normalized.length && Math.abs(diff) > 0) {
    normalized[0].weight = Math.round((Number(normalized[0].weight || 0) + diff) * 10) / 10;
  }
  return normalized;
}

function optimizedPortfolioOverrideFromResult(result) {
  const finalMode = result?.aiAdvisor?.finalAnalysisContext?.mode || result?.improvementPlan?.comparisonMode || "";
  if (finalMode === "final_optimized_vs_recommended_standard" && Array.isArray(result?.portfolio) && result.portfolio.length) {
    const currentOptimized = normalizePortfolioOverrideWeights(result.portfolio.map((item) => ({
      symbol: item.symbol,
      query: item.displayName || item.symbol,
      displayName: item.displayName || displayAnalysisInstrumentName(item),
      weight: item.weight,
      assetClass: item.assetClass,
      minWeight: 0,
      maxWeight: 100,
    })));
    if (currentOptimized.length) return currentOptimized;
  }
  const optimized = result?.optimizedPortfolio;
  const currentPortfolio = Array.isArray(result?.portfolio) ? result.portfolio : [];
  const weights = Array.isArray(optimized?.weights) ? optimized.weights : [];
  if (!optimized?.available || !weights.length || !currentPortfolio.length) return null;
  const currentBySymbol = new Map(currentPortfolio.map((item) => [String(item.symbol || "").toUpperCase(), item]));
  const override = normalizePortfolioOverrideWeights(weights.map((row) => {
    const symbol = String(row.symbol || "").toUpperCase();
    const item = currentBySymbol.get(symbol) || row;
    return {
      symbol,
      query: row.displayName || item.displayName || symbol,
      displayName: row.displayName || item.displayName || displayAnalysisInstrumentName(item),
      weight: Number(row.weight || 0),
      assetClass: row.assetClass || item.assetClass || inferAssetClass(symbol, item.displayName || row.displayName || ""),
      minWeight: 0,
      maxWeight: 100,
    };
  }));
  return override.length ? override : null;
}

function runSuggestedStandardBenchmark(recommendation, result, mode) {
  const portfolio = recommendation?.recommendedPortfolio;
  if (!portfolio) {
    setStatus("Portfolio standard suggerito non disponibile.", "error");
    return;
  }
  let portfolioOverride = null;
  let loadingMessage = `Analisi completa: portfolio inserito vs ${portfolio.name}...`;
  let successMessage = `Analisi aggiornata: benchmark standard ${portfolio.name}.`;
  let improvementComparisonMode = "final_user_vs_recommended_standard";
  let activeSourcePortfolio = result?.portfolio || null;
  let activeSourceMetrics = result?.metrics || null;
  if (mode === "optimized") {
    portfolioOverride = optimizedPortfolioOverrideFromResult(result);
    if (!portfolioOverride) {
      setStatus("Portfolio ottimizzato non disponibile per questa analisi.", "error");
      return;
    }
    improvementComparisonMode = "final_optimized_vs_recommended_standard";
    activeSourcePortfolio = portfolioOverride;
    activeSourceMetrics = result?.optimizedPortfolio?.metrics || null;
    loadingMessage = `Analisi completa: portfolio ottimizzato vs ${portfolio.name}...`;
    successMessage = `Analisi aggiornata: portfolio ottimizzato confrontato con ${portfolio.name}.`;
  }
  setActiveImprovementContext({
    activeComparisonMode: improvementComparisonMode,
    activeSourcePortfolio,
    activeTargetPortfolio: portfolio,
    activeSourceMetrics,
    activeTargetMetrics: recommendation?.analysis?.metrics || null,
  });
  selectedStandardBenchmark = portfolio;
  selectedStandardPortfolioMode = "use_as_benchmark";
  renderStandardModeNote(`Portfolio benchmark standard QuantInvest selezionato: ${portfolio.name}. La nuova analisi userà questo standard come confronto principale.`);
  runBacktest(null, {
    targetStep: "diagnosis",
    includeStress: true,
    loadingMessage,
    successMessage,
    portfolioOverride,
    standardBenchmarkOverride: portfolio,
    standardPortfolioModeOverride: "use_as_benchmark",
    improvementComparisonModeOverride: improvementComparisonMode,
  });
}

function renderStandardAnalysisCard(analysis, title, result, recommendation = null) {
  const portfolio = analysis?.portfolio || recommendation?.recommendedPortfolio;
  if (!analysis?.available || !portfolio) {
    return `<div class="recommendations-empty">${escapePlainFinance(analysis?.reason || "Benchmark standard non disponibile.")}</div>`;
  }
  const comparisons = analysis.comparisons || recommendation?.comparisons || {};
  return `
    <article class="standard-portfolio-card">
      <div class="standard-card-head">
        <div>
          <span>${escapeHtml(title)}</span>
          <strong>${escapeHtml(portfolio.name)}</strong>
          <p>${escapePlainFinance(portfolio.description || "")}</p>
        </div>
        <div class="standard-score">
          <small>Rischio</small>
          <b>${escapeHtml(String(portfolio.riskLevel || "--"))}/5</b>
        </div>
      </div>
      ${renderStandardMetricGrid(analysis)}
      <div class="standard-holdings">
        <span>Principali strumenti</span>
        <ul>${renderStandardHoldings(portfolio.holdings)}</ul>
      </div>
      <div class="standard-comparison-grid">
        <article>
          <span>Portfolio inserito vs standard</span>
          <p>${escapePlainFinance(comparisonDeltaLine(comparisons.userVsStandard))}</p>
        </article>
        <article>
          <span>Portafoglio ottimale vs standard</span>
          <p>${escapePlainFinance(comparisonDeltaLine(comparisons.optimizedVsStandard))}</p>
        </article>
      </div>
      ${portfolio.warning ? `<p class="standard-warning">${escapePlainFinance(portfolio.warning)}</p>` : ""}
      <p class="standard-disclaimer">${escapePlainFinance(analysis.disclaimer || recommendation?.disclaimer || "")}</p>
    </article>
  `;
}

function renderSelectedStandardBenchmark(analysis, result) {
  const panel = document.querySelector("#standard-benchmark-analysis");
  const content = document.querySelector("#standard-benchmark-analysis-content");
  if (!panel || !content) return;
  if (result?.analysisComparisonContext?.comparisonMode === "standard_benchmark") {
    panel.hidden = true;
    content.innerHTML = "";
    return;
  }
  if (!analysis?.available) {
    panel.hidden = true;
    content.innerHTML = "";
    return;
  }
  panel.hidden = false;
  content.innerHTML = renderStandardAnalysisCard(analysis, "Benchmark standard scelto", result);
}

function renderStandardPortfolioRecommendation(recommendation, result) {
  const panel = document.querySelector("#standard-portfolio-recommendation");
  const content = document.querySelector("#standard-portfolio-recommendation-content");
  if (!panel || !content) return;
  if (!recommendation?.shouldSuggest) {
    panel.hidden = true;
    content.innerHTML = "";
    return;
  }
  panel.hidden = false;
  if (recommendation.locked) {
    content.innerHTML = `
      <article class="standard-portfolio-card locked">
        <div>
          <span>Portfolio standard QuantInvest</span>
          <strong>Sbloccabile con Plus</strong>
          <p>${escapePlainFinance(recommendation.message || "Sblocca i portfolio standard con il piano Plus.")}</p>
        </div>
        <button class="secondary" type="button" disabled>Sblocca con Plus</button>
      </article>
      <p class="standard-disclaimer">${escapePlainFinance(recommendation.disclaimer || "")}</p>
    `;
    return;
  }
  const portfolio = recommendation.recommendedPortfolio;
  if (!portfolio) {
    content.innerHTML = `<div class="recommendations-empty">${escapePlainFinance(recommendation.message || "Nessun portfolio standard coerente disponibile.")}</div>`;
    return;
  }
  const alternatives = Array.isArray(recommendation.alternatives) ? recommendation.alternatives : [];
  const lockedAdvanced = recommendation.lockedAdvancedCandidate;
  content.innerHTML = `
    <article class="standard-portfolio-card">
      <div class="standard-card-head">
        <div>
          <span>Benchmark educativo suggerito</span>
          <strong>${escapeHtml(portfolio.name)}</strong>
          <p>${escapePlainFinance(portfolio.description || "")}</p>
        </div>
        <div class="standard-score">
          <small>Match locale</small>
          <b>${Math.round(recommendation.matchScore || 0)}/100</b>
        </div>
      </div>
      <div class="standard-meta-grid">
        <article><span>Categoria</span><strong>${escapeHtml(portfolio.category || "--")}</strong></article>
        <article><span>Rischio</span><strong>${escapeHtml(standardRiskLabel(portfolio.riskLevel))}</strong></article>
        <article><span>Orizzonte</span><strong>${escapeHtml(portfolio.suggestedHorizon || "--")}</strong></article>
        <article><span>Piano</span><strong>${escapeHtml(String(portfolio.plan || "").toUpperCase())}</strong></article>
      </div>
      ${renderStandardMetricGrid(recommendation.analysis)}
      <div class="standard-explanation">
        <p>${escapePlainFinance(recommendation.explanation?.summary || "")}</p>
        <p>${escapePlainFinance(recommendation.explanation?.reason || "")}</p>
        <ul>${(recommendation.explanation?.bullets || []).slice(0, 6).map((item) => `<li>${escapePlainFinance(item)}</li>`).join("")}</ul>
      </div>
      <div class="standard-holdings">
        <span>Principali strumenti</span>
        <ul>${renderStandardHoldings(portfolio.holdings)}</ul>
      </div>
      ${portfolio.warning ? `<p class="standard-warning">${escapePlainFinance(portfolio.warning)}</p>` : ""}
      ${lockedAdvanced ? `
        <div class="standard-advanced-lock">
          <span>Alternativa Advanced bloccata</span>
          <p>${escapePlainFinance(lockedAdvanced.message || "")}</p>
          <strong>${escapeHtml(lockedAdvanced.portfolio?.name || "--")} · ${Math.round(lockedAdvanced.score || 0)}/100</strong>
        </div>
      ` : ""}
      ${alternatives.length ? `
        <div class="standard-alternatives">
          <span>Alternative disponibili</span>
          <p>${alternatives.map((item) => `${escapeHtml(item.portfolio?.name || "--")} (${Math.round(item.score || 0)}/100)`).join(" · ")}</p>
        </div>
      ` : ""}
      <div class="standard-actions">
        <button class="secondary" type="button" data-standard-compare="current">Avvia analisi: mio portfolio vs standard</button>
        <button class="secondary" type="button" data-standard-compare="optimized">Avvia analisi: portfolio ottimizzato vs standard</button>
      </div>
      <p class="standard-disclaimer">${escapePlainFinance(recommendation.disclaimer || "")}</p>
    </article>
  `;
  content.querySelectorAll("[data-standard-compare]").forEach((button) => {
    button.addEventListener("click", () => {
      runSuggestedStandardBenchmark(recommendation, result, button.dataset.standardCompare);
    });
  });
}

function improvementPriorityLabel(priority) {
  return {
    high: "Alta priorità",
    medium: "Priorità media",
    low: "Priorità bassa",
  }[priority] || "Priorità media";
}

function improvementActionLabel(actionType) {
  return {
    reduce: "Riduci esposizione",
    increase: "Aumenta esposizione",
    add: "Aggiungi al confronto",
    remove: "Riduci fino al target",
    keep: "Mantieni",
  }[actionType] || "Azione simulata";
}

function improvementImpactLabel(area) {
  return {
    risk_reduction: "Riduzione rischio",
    diversification: "Diversificazione",
    goal_alignment: "Coerenza obiettivo",
    drawdown_reduction: "Perdita storica",
    return_efficiency: "Efficienza",
    liquidity: "Liquidità",
  }[area] || area;
}

function formatImprovementValue(value, format) {
  if (value === undefined || value === null || value === "") return "--";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return escapeHtml(String(value));
  if (format === "percent") return formatPercent(numeric);
  if (format === "score") return `${Math.round(numeric)}/100`;
  if (format === "number") return number.format(numeric);
  return number.format(numeric);
}

function renderImprovementAction(action) {
  const diff = Number(action.difference || 0);
  const diffClass = diff >= 0 ? "positive" : "negative";
  return `
    <article class="improvement-action-card ${escapeHtml(action.priority || "medium")}">
      <div class="improvement-action-head">
        <span>${escapeHtml(improvementActionLabel(action.actionType))}</span>
        <strong>${escapeHtml(displayAnalysisInstrumentName(action.instrumentName || action.symbol))}</strong>
      </div>
      <div class="improvement-weight-grid">
        <div><small>Peso attuale</small><b>${number.format(Number(action.currentWeight || 0))}%</b></div>
        <div><small>Peso target</small><b>${number.format(Number(action.targetWeight || 0))}%</b></div>
        <div><small>Differenza</small><b class="${diffClass}">${diff > 0 ? "+" : ""}${number.format(diff)} pp</b></div>
      </div>
      <p>${escapePlainFinance(action.reason || "")}</p>
      <div class="impact-tags">
        ${(action.impactAreas || []).slice(0, 4).map((area) => `<span>${escapeHtml(improvementImpactLabel(area))}</span>`).join("")}
      </div>
      <small class="priority-pill">${escapeHtml(improvementPriorityLabel(action.priority))}</small>
    </article>
  `;
}

function renderSimulatedEtfAddition(addition) {
  if (!addition) return "";
  const additionTitle = addition.comparisonMode === "final_optimized_vs_recommended_standard"
    ? "Aggiunta simulata suggerita sull'alternativa ottimizzata"
    : "Aggiunta simulata suggerita sul tuo portafoglio inserito";
  if (addition.locked) {
    return `
      <section class="simulated-etf-card locked">
        <div class="table-title compact">
          <h3>${additionTitle}</h3>
          <p class="section-intro">${escapePlainFinance(addition.message || "Preview bloccata dal piano corrente.")}</p>
        </div>
      </section>
    `;
  }
  if (!addition.available) {
    const reason = addition.reason || "";
    if (!reason) return "";
    return `
      <section class="simulated-etf-card muted">
        <div class="table-title compact">
          <h3>${additionTitle}</h3>
          <p class="section-intro">${escapePlainFinance(reason)}</p>
        </div>
      </section>
    `;
  }
  const suggestions = Array.isArray(addition.suggestions) ? addition.suggestions.slice(0, 1) : [];
  if (!suggestions.length) return "";
  const sourceText = addition.comparisonMode === "final_optimized_vs_recommended_standard"
    ? "La modifica simulata riguarda l'alternativa ottimizzata, non il portafoglio originale dell'utente."
    : "La modifica simulata riguarda il tuo portafoglio inserito.";
  return `
    <section class="simulated-etf-card">
      <div class="table-title compact">
        <h3>${additionTitle}</h3>
        <p class="section-intro">${escapePlainFinance(addition.subtitle || "Scelta locale basata sul pilastro più debole del portafoglio.")}</p>
        <p class="section-intro">${escapePlainFinance(sourceText)} Riferimento educativo: ${escapePlainFinance(addition.referencePortfolioName || "target del Piano di miglioramento")}.</p>
      </div>
      <div class="simulated-etf-summary">
        <article>
          <span>Pilastro target</span>
          <strong>${escapePlainFinance(addition.targetPillarLabel || "--")}</strong>
          <small>${formatImprovementValue(addition.currentPillarScore, "score")} → ${formatImprovementValue(addition.estimatedPillarAfter, "score")}</small>
        </article>
        <article>
          <span>Metodo</span>
          <strong>Fallback locale</strong>
          <small>Regole deterministiche, senza OpenAI.</small>
        </article>
      </div>
      <div class="simulated-etf-grid">
        ${suggestions.map((item) => `
          <article>
            <div class="simulated-etf-head">
              <span>Strumento candidato</span>
              <strong>${escapeHtml(item.name || "ETF standard QuantInvest")}</strong>
              ${item.isin ? `<small>ISIN ${escapeHtml(item.isin)}</small>` : ""}
            </div>
            <div class="improvement-weight-grid">
              <div><small>Categoria</small><b>${escapePlainFinance(item.category || "--")}</b></div>
              <div><small>Peso simulato</small><b>${number.format(Number(item.simulatedWeight || 0))}%</b></div>
              <div><small>Effetto stimato</small><b>${formatImprovementValue(item.scoreCurrent, "score")} → ${formatImprovementValue(item.scoreAfter, "score")}</b></div>
            </div>
            <p><b>Perché è stato selezionato:</b> ${escapePlainFinance(item.reason || "")}</p>
            <p><b>Cosa potrebbe migliorare:</b> ${escapePlainFinance(item.expectedImprovement || "")}</p>
            <p><b>Cosa monitorare:</b> ${escapePlainFinance(item.watchOut || "")}</p>
            <p><b>Effetti collaterali:</b> ${escapePlainFinance(item.sideEffects || "")}</p>
          </article>
        `).join("")}
      </div>
      <p class="standard-disclaimer">${escapePlainFinance(addition.disclaimer || "È una simulazione educativa, non una raccomandazione finanziaria personalizzata.")}</p>
    </section>
  `;
}

function renderStandardPersonalization(personalization) {
  if (!personalization) return "";
  const alternative = personalization.alternativeStandard;
  return `
    <section class="simulated-etf-card muted">
      <div class="table-title compact">
        <h3>${escapePlainFinance(personalization.title || "Possibile personalizzazione educativa")}</h3>
        <p class="section-intro">${escapePlainFinance(personalization.summary || "")}</p>
      </div>
      <div class="simulated-etf-grid">
        <article>
          <div class="simulated-etf-head">
            <span>Direzione possibile</span>
            <strong>Personalizzazione del modello</strong>
          </div>
          <p>${escapePlainFinance(personalization.direction || "")}</p>
          <p>${escapePlainFinance(personalization.nextStep || "")}</p>
        </article>
        ${alternative ? `
          <article>
            <div class="simulated-etf-head">
              <span>Altro standard da confrontare</span>
              <strong>${escapeHtml(alternative.name || "--")}</strong>
              <small>${escapeHtml(standardRiskLabel(alternative.riskLevel))} · ${escapeHtml(alternative.suggestedHorizon || "--")}</small>
            </div>
            <p>${escapePlainFinance(alternative.description || "")}</p>
          </article>
        ` : ""}
      </div>
    </section>
  `;
}

function renderImprovementPlan(plan, result) {
  const content = document.querySelector("#improvement-plan-content");
  if (!content) return;
  if (!plan?.available) {
    content.innerHTML = `<div class="recommendations-empty">Esegui l'analisi completa per generare il piano di miglioramento.</div>`;
    return;
  }
  const target = plan.target || {};
  const activeContext = plan.activeContext || activeImprovementContext || {};
  const standardOnly = plan.standardOnly;
  const standardPersonalization = plan.standardPersonalization;
  const problems = Array.isArray(plan.problems) ? plan.problems : [];
  const recommendations = Array.isArray(plan.recommendations) ? plan.recommendations : [];
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  const priorityActions = actions.slice(0, 3);
  const hiddenActions = actions.slice(3);
  const impactRows = Array.isArray(plan.impact?.rows) ? plan.impact.rows : [];
  const simulatedEtfAddition = plan.simulatedEtfAddition || null;
  const targetTypeLabel = {
    optimized: "Portafoglio ottimizzato",
    selected_standard: "Benchmark standard selezionato",
    recommended_standard: "Portafoglio standard suggerito",
    standard_self_analysis: "Portafoglio standard analizzato",
    light_adjustment: "Correzione leggera",
  }[target.type] || "Target educativo";
  const targetDisplayName = activeContext.activeTargetPortfolio || target.name || target.title || targetTypeLabel;
  const sourceDisplayName = activeContext.activeSourcePortfolio || plan.sourceName || "Portafoglio inserito";
  const actionTitle = (String(sourceDisplayName).toLowerCase().includes("ottimizzato"))
    ? `Azioni simulate: dal portafoglio ottimizzato al portfolio target`
    : `Azioni simulate: dal portafoglio inserito al portfolio target`;

  if (standardOnly) {
    content.innerHTML = `
      <section class="improvement-target-card wide">
        <span>${escapeHtml(targetTypeLabel)}</span>
        <h3>${escapePlainFinance(target.title || "Come è costruito questo portafoglio standard")}</h3>
        <p>${escapePlainFinance(standardOnly.modelLogic || target.description || "")}</p>
        <small>Contesto attivo: ${escapeHtml(activeContext.activeComparisonMode || plan.comparisonMode || "standard_only")}</small>
      </section>
      <div class="improvement-grid">
        <article>
          <span>Logica del modello</span>
          <p>${escapePlainFinance(standardOnly.profileFit || "")}</p>
        </article>
        <article>
          <span>Rischi principali</span>
          <p>${escapePlainFinance(standardOnly.mainRisks || "")}</p>
        </article>
        <article>
          <span>Come modificarlo</span>
          <p>${escapePlainFinance(standardOnly.howToModify || "")}</p>
        </article>
      </div>
      <section class="improvement-actions-panel">
        <h4>Ruolo degli strumenti</h4>
        <div class="standard-role-grid">
          ${(standardOnly.holdings || []).map((holding) => `
            <article>
              <strong>${escapeHtml(displayAnalysisInstrumentName(holding.instrumentName))}</strong>
              <span>${number.format(Number(holding.weight || 0))}%</span>
              <p>${escapePlainFinance(holding.explanation || holding.role || "")}</p>
            </article>
          `).join("")}
        </div>
      </section>
      ${renderStandardPersonalization(standardPersonalization)}
      <p class="standard-disclaimer">${escapePlainFinance(plan.disclaimer || "")}</p>
    `;
    return;
  }

  content.innerHTML = `
    <section class="improvement-target-card">
      <span>Target usato</span>
      <h3>${escapeHtml(target.name || target.title || targetTypeLabel)}</h3>
      <p>${escapePlainFinance(target.reason || target.description || "")}</p>
      <small>${escapeHtml(targetTypeLabel)} · Intensità ${escapeHtml(target.correctionIntensity || "light")}</small>
      <div class="improvement-context-strip">
        <span>Sorgente: ${escapeHtml(activeContext.activeSourcePortfolio || plan.sourceName || "Portafoglio analizzato")}</span>
        <span>Target: ${escapeHtml(activeContext.activeTargetPortfolio || target.name || "Target educativo")}</span>
        <span>Modalità: ${escapeHtml(activeContext.activeComparisonMode || plan.comparisonMode || "optimization")}</span>
      </div>
      ${target.lockedTarget ? `
        <div class="standard-advanced-lock">
          <span>Target avanzato bloccato</span>
          <p>${escapePlainFinance(target.lockedTarget.message || "")}</p>
          <strong>${escapeHtml(target.lockedTarget.name || "--")} · ${escapeHtml(target.lockedTarget.requiredPlan || "ADVANCED")}</strong>
        </div>
      ` : ""}
    </section>
    ${renderSimulatedEtfAddition(simulatedEtfAddition)}
    <div class="improvement-grid">
      <section>
        <h4>Cosa non funziona</h4>
        ${problems.length ? problems.map((problem) => `
          <article class="improvement-problem ${escapeHtml(problem.priority || "medium")}">
            <span>${escapeHtml(improvementPriorityLabel(problem.priority))}</span>
            <strong>${escapePlainFinance(problem.title || "")}</strong>
            <p>${escapePlainFinance(problem.message || "")}</p>
          </article>
        `).join("") : `<div class="recommendations-empty">Non emergono criticità principali con i dati disponibili.</div>`}
      </section>
      <section>
        <h4>Perché conta</h4>
        ${problems.length ? problems.slice(0, 3).map((problem) => `
          <article class="improvement-problem">
            <p>${escapePlainFinance(problem.whyItMatters || problem.message || "")}</p>
          </article>
        `).join("") : `<div class="recommendations-empty">Il piano resta una simulazione educativa: controlla comunque rischio, diversificazione e coerenza nel tempo.</div>`}
      </section>
      <section>
        <h4>Possibili interventi</h4>
        ${recommendations.length ? recommendations.map((item) => `
          <article class="improvement-problem ${escapeHtml(item.priority || "medium")}">
            <span>${escapeHtml(improvementPriorityLabel(item.priority))}</span>
            <strong>${escapePlainFinance(item.title || "")}</strong>
            <p>${escapePlainFinance(item.message || "")}</p>
          </article>
        `).join("") : `<div class="recommendations-empty">Nessuna raccomandazione locale specifica disponibile.</div>`}
      </section>
    </div>
    <section class="improvement-actions-panel">
      <div class="table-title compact">
        <h3>${escapeHtml(actionTitle)}</h3>
        <p class="section-intro">Le prime 3 card mostrano le modifiche prioritarie. Il target di riferimento è ${escapeHtml(targetDisplayName)}; il pulsante sotto mostra anche le altre modifiche simulate.</p>
      </div>
      <div class="improvement-actions-grid">
        ${priorityActions.length ? priorityActions.map(renderImprovementAction).join("") : `<div class="recommendations-empty">Non ci sono differenze superiori al 2% rispetto al target scelto.</div>`}
      </div>
      ${hiddenActions.length ? `
        <button type="button" class="secondary improvement-more-button" id="toggle-all-improvement-actions">Mostra tutte le modifiche simulate</button>
        <div class="improvement-actions-grid hidden-actions" id="all-improvement-actions" hidden>
          ${hiddenActions.map(renderImprovementAction).join("")}
        </div>
      ` : ""}
    </section>
    <section class="improvement-impact-panel">
      <div class="table-title compact">
        <h3>Effetto stimato</h3>
        <p class="section-intro">Confronto educativo tra situazione attuale e target usato.</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Metrica</th><th>Attuale</th><th>Dopo correzione</th><th>Effetto</th></tr></thead>
          <tbody>
            ${impactRows.length ? impactRows.map((row) => `
              <tr>
                <td>${escapeHtml(row.metric || "--")}</td>
                <td>${formatImprovementValue(row.before, row.format)}</td>
                <td>${formatImprovementValue(row.after, row.format)}</td>
                <td>${escapePlainFinance(row.effect || "--")}</td>
              </tr>
            `).join("") : `<tr><td colspan="4">Effetto stimato non disponibile.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
    <p class="standard-disclaimer">${escapePlainFinance(plan.disclaimer || "")}</p>
  `;
  const toggle = content.querySelector("#toggle-all-improvement-actions");
  const allActions = content.querySelector("#all-improvement-actions");
  if (toggle && allActions) {
    toggle.addEventListener("click", () => {
      const nextHidden = !allActions.hidden ? true : false;
      allActions.hidden = nextHidden;
      toggle.textContent = nextHidden ? "Mostra tutte le modifiche simulate" : "Nascondi modifiche aggiuntive";
    });
  }
}

function renderActionDashboard(result) {
  const contextDashboard = result?.aiAdvisor?.finalAnalysisContext?.monitoringDashboard;
  if (contextDashboard) {
    const actionRisk = document.querySelector("#action-risk");
    if (!actionRisk) return;
    actionRisk.textContent = contextDashboard.riskToMonitor || "Rischio non ancora calcolato";
    document.querySelector("#action-overweight").textContent = contextDashboard.dominantComponent || "Composizione non disponibile";
    document.querySelector("#action-scenario").textContent = contextDashboard.criticalScenario || "Scenario non attivo";
    document.querySelector("#action-next-check").textContent = contextDashboard.nextCheck || "Controllo mensile";
    return;
  }
  const metrics = result?.metrics || {};
  const contribution = Array.isArray(result?.contribution) ? result.contribution : [];
  const biggest = [...contribution].sort((a, b) => Math.abs(b.weightedReturn || 0) - Math.abs(a.weightedReturn || 0))[0];
  const stress = result?.stressTesting?.result || {};
  const drawdown = Number(metrics.maxDrawdown || 0);
  const dominantClass = collectPortfolio().reduce((acc, item) => {
    acc[item.assetClass] = (acc[item.assetClass] || 0) + Number(item.weight || 0);
    return acc;
  }, {});
  const [topClass, topWeight] = Object.entries(dominantClass).sort((a, b) => b[1] - a[1])[0] || [];
  const classLabel = assetClassOptions.find(([value]) => value === topClass)?.[1] || "--";
  const actionRisk = document.querySelector("#action-risk");
  if (!actionRisk) return;
  actionRisk.textContent = drawdown < 0 ? `Perdita temporanea ${formatPercent(drawdown)}` : "Rischio non ancora calcolato";
  document.querySelector("#action-overweight").textContent =
    topClass ? `${classLabel} ${number.format(topWeight)}%` : "Composizione non disponibile";
  document.querySelector("#action-scenario").textContent =
    stress.scenario || stress.scenario_name || document.querySelector("#hidden-risk-scenario")?.value || "Scenario non attivo";
  document.querySelector("#action-next-check").textContent =
    biggest ? `Controlla ${displayAnalysisInstrumentName(biggest)}` : "Controllo mensile";
}

function annualizedVolatilityFromCurve(equityCurve = []) {
  const returns = equityCurve.map((point) => Number(point.return || 0)).filter((value) => Number.isFinite(value));
  if (returns.length < 2) return null;
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const variance = returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (returns.length - 1);
  return Math.sqrt(variance) * Math.sqrt(252);
}

function renderRiskReality(result) {
  const metrics = result?.metrics || {};
  const capital = Number(document.querySelector("#initial-capital")?.value || metrics.initialCapital || 0);
  const drawdown = Number(metrics.maxDrawdown || 0);
  const maxLoss = Number(document.querySelector("#investor-max-loss")?.value || 0.2);
  const volatility = annualizedVolatilityFromCurve(result?.equityCurve || []);
  const drawdownLoss = capital && drawdown < 0 ? Math.abs(capital * drawdown) : null;
  const thresholdLoss = capital * maxLoss;
  const stressReturn = Number(result?.stressTesting?.result?.expected_portfolio_return ?? result?.stressTesting?.result?.portfolio_return ?? NaN);
  const recoveryDays = result?.advancedAnalytics?.drawdown?.current?.recoveryDays;
  document.querySelector("#risk-volatility").textContent = volatility === null ? "--" : formatPercent(volatility);
  document.querySelector("#risk-drawdown").textContent = formatPercent(drawdown);
  document.querySelector("#risk-loss-eur").textContent =
    drawdownLoss === null ? "Perdita in euro non disponibile." : `Su ${euro.format(capital)}, perdita temporanea di circa ${euro.format(drawdownLoss)}.`;
  document.querySelector("#risk-threshold").textContent = formatPercent(maxLoss);
  document.querySelector("#risk-threshold-status").textContent =
    drawdownLoss === null
      ? "Confronto non disponibile."
      : drawdownLoss <= thresholdLoss
        ? `Dentro la soglia dichiarata di circa ${euro.format(thresholdLoss)}.`
        : `Oltre la soglia dichiarata di circa ${euro.format(thresholdLoss)}.`;
  document.querySelector("#risk-recovery").textContent =
    recoveryDays === undefined || recoveryDays === null ? "Advanced" : `${number.format(recoveryDays)} giorni`;
  document.querySelector("#risk-stress").textContent = Number.isFinite(stressReturn) ? formatPercent(stressReturn) : "Scenario non attivo";
  document.querySelector("#risk-watch").textContent =
    Math.abs(drawdown) > maxLoss
      ? "La perdita storica supera la tua soglia psicologica."
      : "Controlla che la perdita storica resti compatibile con il profilo.";
}

function plainTextSnippet(value, maxLength = 140) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text;
}

function renderDiagnosticDashboard(result) {
  const metrics = result?.metrics || {};
  const advisor = result?.aiAdvisor || {};
  const intelligence = advisor.intelligence || {};
  const comparisonContext = result?.analysisComparisonContext || defaultComparisonContext();
  const benchmarkCheckup = comparisonContext?.benchmarkCheckup || {};
  const isStandardBenchmark = comparisonContext.comparisonMode === "standard_benchmark";
  const capital = Number(document.querySelector("#initial-capital")?.value || result?.initialCapital || 0);
  const drawdown = Number(metrics.maxDrawdown || 0);
  const drawdownValue = capital && drawdown < 0 ? capital * (1 + drawdown) : null;
  const riskBudget = { conservative: 0.12, balanced: 0.25, aggressive: 0.38 }[document.querySelector("#investor-risk")?.value] || 0.25;
  const riskFit = Math.abs(drawdown) <= riskBudget ? "Coerente" : "Da monitorare";
  const health = intelligence.healthScore || {};
  const advisorSummary = advisor.summary || advisor.finalAnalysis?.text || intelligence.backtest?.executiveSummary || "";
  const risk = intelligence.backtest?.riskInsight || intelligence.analysis?.riskInsight || "";
  const strength = intelligence.backtest?.mainStrength || intelligence.analysis?.mainStrength || "";
  const benchmarkStrength = diagnosticBenchmarkStrength(result);
  const contextualRisk = diagnosticRiskFromActiveContext(result);
  const contextualStrength = diagnosticStrengthFromActiveContext(result);
  const improvement = intelligence.backtest?.suggestedAction || intelligence.frontier?.optimizedAlternative || "";
  const healthScore = Math.round(health.overall || advisor.score || 0);
  const displayScore = healthScore;
  const scoreType = health.scoreType || health.name || "Check-up del portafoglio";
  const confidence = health.confidence || "--";
  const healthSummary =
    displayScore
      ? `Check-up in 30 secondi: ${displayScore}/100. ${plainTextSnippet(health.diagnosis || "Questa sintesi misura la coerenza del portafoglio con il profilo scelto.", 150)}`
      : "";
  document.querySelector("#diagnostic-title").textContent =
    displayScore ? `Check-up in 30 secondi: ${displayScore}/100` : "Check-up completato";
  document.querySelector("#diagnostic-summary").textContent =
    isStandardBenchmark
      ? plainFinanceLanguage(plainTextSnippet(
          benchmarkCheckup.benchmarkInsight ||
            "Il tuo portafoglio viene confrontato con il portfolio benchmark standard QuantInvest selezionato. Il confronto mostra differenze di rischio, rendimento, perdita massima e coerenza con il profilo indicato.",
          260
        ))
      : plainFinanceLanguage(plainTextSnippet(healthSummary || advisorSummary, 230)) || "La diagnosi è stata calcolata. Usa le sezioni sotto per approfondire rischio, probabilità e scenari.";
  document.querySelector("#diagnostic-score").textContent =
    displayScore ? `${displayScore}/100` : "--/100";
  const diagnosticScoreLabel = document.querySelector("#diagnostic-score-label");
  if (diagnosticScoreLabel) diagnosticScoreLabel.textContent =
    isStandardBenchmark ? `Portfolio analizzato · benchmark ${benchmarkCheckup.label || "confronto educativo"}` : scoreType;
  document.querySelector("#diagnostic-plan").textContent =
    isStandardBenchmark ? `Portfolio inserito vs benchmark · Piano ${currentPlan()}` : `Portfolio inserito · Piano ${currentPlan()}`;
  const diagnosticConfidence = document.querySelector("#diagnostic-confidence");
  if (diagnosticConfidence) diagnosticConfidence.textContent = `Affidabilità: ${confidence}`;
  document.querySelector("#diagnostic-risk").textContent =
    isStandardBenchmark
      ? contextualRisk || plainFinanceLanguage(plainTextSnippet(benchmarkCheckup.mainRisk, 180)) || `Peggiore perdita storica ${formatPercent(drawdown)}`
      : contextualRisk || plainFinanceLanguage(plainTextSnippet(risk, 260)) || `Peggiore perdita storica ${formatPercent(drawdown)}`;
  document.querySelector("#diagnostic-strength").textContent =
    isStandardBenchmark
      ? contextualStrength || plainFinanceLanguage(plainTextSnippet(benchmarkCheckup.strength, 180)) || `Crescita media annua ${formatPercent(metrics.cagr || 0)}`
      : contextualStrength || benchmarkStrength || plainFinanceLanguage(plainTextSnippet(strength, 90)) || `Crescita media annua ${formatPercent(metrics.cagr || 0)}`;
  document.querySelector("#diagnostic-improvement").textContent =
    isStandardBenchmark
      ? plainFinanceLanguage(plainTextSnippet(benchmarkCheckup.improvementArea, 120)) || "Confronta rischio e perdita massima con il benchmark standard selezionato."
      : plainFinanceLanguage(plainTextSnippet(improvement, 90)) || "Confronta il portfolio efficiente e gli scenari negativi.";
  document.querySelector("#diagnostic-loss-eur").textContent =
    drawdownValue === null ? "--" : `${euro.format(capital)} → ${euro.format(drawdownValue)}`;
  document.querySelector("#diagnostic-risk-fit").textContent = riskFit;
  document.querySelector("#diagnostic-rebalance").textContent =
    result?.rebalance?.count ? `${result.rebalance.count} controlli storici` : "Mensile";
  const lastUpdated = document.querySelector("#last-updated");
  if (lastUpdated) lastUpdated.textContent = `Ultimo aggiornamento: ${new Date().toLocaleString("it-IT")}`;
}

function compactPortfolioName(name) {
  return String(name || "Portfolio")
    .replace(/^Portfolio\s+/i, "")
    .replace(/^Portafoglio\s+/i, "")
    .replace(/^Benchmark\s+standard\s+QuantInvest$/i, "Benchmark")
    .replace(/^Portfolio\s+standard\s+suggerito$/i, "Standard suggerito")
    .trim() || "Portfolio";
}

function diagnosticContext(result) {
  const context = result?.aiAdvisor?.finalAnalysisContext || latestFinalAnalysisContext || null;
  if (!context?.primaryMetrics) return null;
  return context;
}

function diagnosticMetric(metrics, key) {
  const value = Number(metrics?.[key]);
  return Number.isFinite(value) ? value : null;
}

function diagnosticSinglePortfolioText(context) {
  const metrics = context?.primaryMetrics || {};
  const name = compactPortfolioName(context?.primaryPortfolioName || "Portfolio standard");
  const finalValue = diagnosticMetric(metrics, "finalValue");
  const sharpe = diagnosticMetric(metrics, "sharpe");
  const drawdown = diagnosticMetric(metrics, "maxDrawdown");
  if (finalValue !== null) {
    return `${name}: valore finale ${euro.format(finalValue)}. Analisi senza confronto: valuta pesi, rischio e coerenza del modello standard.`;
  }
  if (sharpe !== null) return `${name}: rapporto rischio-rendimento ${number.format(sharpe)}. Analisi senza confronto con portafoglio ottimale.`;
  if (drawdown !== null) return `${name}: peggiore perdita temporanea ${formatPercent(drawdown)}.`;
  return "";
}

function diagnosticRiskFromActiveContext(result) {
  const context = diagnosticContext(result);
  if (!context) return "";
  const mode = context.mode || result?.analysisComparisonContext?.comparisonMode || "optimization";
  if (mode === "optimization") return "";
  if (mode === "standard_only" || !context.comparisonMetrics) return diagnosticSinglePortfolioText(context);
  const primary = context.primaryMetrics || {};
  const comparison = context.comparisonMetrics || {};
  const primaryName = compactPortfolioName(context.primaryPortfolioName);
  const comparisonName = compactPortfolioName(context.comparisonPortfolioName);
  const primarySharpe = diagnosticMetric(primary, "sharpe");
  const comparisonSharpe = diagnosticMetric(comparison, "sharpe");
  if (primarySharpe !== null && comparisonSharpe !== null && Math.abs(comparisonSharpe - primarySharpe) >= 0.15) {
    return `${primaryName}: ${number.format(primarySharpe)} euro per unità di rischio. ${comparisonName}: ${number.format(comparisonSharpe)}.`;
  }
  const primaryDrawdown = diagnosticMetric(primary, "maxDrawdown");
  const comparisonDrawdown = diagnosticMetric(comparison, "maxDrawdown");
  if (primaryDrawdown !== null && comparisonDrawdown !== null) {
    return `${primaryName}: perdita temporanea ${formatPercent(primaryDrawdown)}. ${comparisonName}: ${formatPercent(comparisonDrawdown)}.`;
  }
  const primaryCagr = diagnosticMetric(primary, "cagr");
  const comparisonCagr = diagnosticMetric(comparison, "cagr");
  if (primaryCagr !== null && comparisonCagr !== null) {
    return `${primaryName}: crescita media annua ${formatPercent(primaryCagr)}. ${comparisonName}: ${formatPercent(comparisonCagr)}.`;
  }
  return "";
}

function diagnosticStrengthFromActiveContext(result) {
  const context = diagnosticContext(result);
  if (!context) return "";
  const mode = context.mode || result?.analysisComparisonContext?.comparisonMode || "optimization";
  if (mode === "optimization") return "";
  if (mode === "standard_only" || !context.comparisonMetrics) return diagnosticSinglePortfolioText(context);
  const primaryFinalValue = diagnosticMetric(context.primaryMetrics, "finalValue");
  const comparisonFinalValue = diagnosticMetric(context.comparisonMetrics, "finalValue");
  if (primaryFinalValue === null || comparisonFinalValue === null) return "";
  const primaryName = compactPortfolioName(context.primaryPortfolioName);
  const comparisonName = compactPortfolioName(context.comparisonPortfolioName);
  const isBenchmark = /standard|benchmark/i.test(`${context.comparisonPortfolioRole || ""} ${comparisonName}`);
  const intro = isBenchmark ? "Benchmark = confronto." : "Confronto = simulazione.";
  const status = primaryFinalValue >= comparisonFinalValue ? "batte" : "non batte";
  return `${intro} ${comparisonName} ${euro.format(comparisonFinalValue)}. ${primaryName} ${euro.format(primaryFinalValue)}: ${status}.`;
}

function diagnosticBenchmarkStrength(result) {
  const benchmarkMetrics = result?.benchmarkComparison?.available ? result.benchmarkComparison.metrics : null;
  const userFinalValue = Number(result?.metrics?.finalValue);
  const benchmarkFinalValue = Number(benchmarkMetrics?.finalValue);
  if (!Number.isFinite(userFinalValue) || !Number.isFinite(benchmarkFinalValue) || benchmarkFinalValue <= 0) return "";
  const optimizedMetrics = result?.optimizedPortfolio?.available ? result.optimizedPortfolio.metrics : null;
  const optimizedFinalValue = Number(optimizedMetrics?.finalValue);
  const benchmarkName = result?.benchmarkComparison?.name || "Benchmark";
  const userStatus = userFinalValue >= benchmarkFinalValue ? "batte" : "non batte";
  const optimizedStatus = Number.isFinite(optimizedFinalValue)
    ? ` Ottimale ${euro.format(optimizedFinalValue)}: ${optimizedFinalValue >= benchmarkFinalValue ? "batte" : "non batte"}.`
    : "";
  return `Benchmark = confronto. ${benchmarkName} ${euro.format(benchmarkFinalValue)}. Utente ${euro.format(userFinalValue)}: ${userStatus}.${optimizedStatus}`;
}

function renderContributionTable(selector, contribution, emptyMessage = "Nessun backtest eseguito.") {
  const targetBody = document.querySelector(selector);
  if (!targetBody) return;
  targetBody.innerHTML = "";
  const rows = Array.isArray(contribution) ? contribution : [];
  if (!rows.length) {
    targetBody.innerHTML = `<tr><td colspan="5">${escapeHtml(emptyMessage)}</td></tr>`;
    return;
  }
  rows.forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(displayAnalysisInstrumentName(item))}</td>
      <td>${formatPercent(item.weight)}</td>
      <td>${formatPercent(item.finalWeight)}</td>
      <td>${formatPercent(item.assetReturn)}</td>
      <td>${formatPercent(item.weightedReturn)}</td>
    `;
    targetBody.appendChild(row);
  });
}

function renderAdvancedAnalytics(advanced) {
  latestAdvancedAnalytics = advanced || null;
  renderedAdvancedSteps.clear();
  renderActiveAdvancedSection();
}

function renderActiveAdvancedSection() {
  if (!latestAdvancedAnalytics || renderedAdvancedSteps.has(currentStep)) return;
  const advanced = latestAdvancedAnalytics;
  const renderers = {
    factors: () => renderFamaFrench(advanced?.famaFrench, advanced),
    geography: () => renderGeography(advanced?.geography, advanced),
    rolling: () => renderRollingSortino(advanced?.rollingSortino, advanced),
    correlation: () => renderCorrelation(advanced?.correlation, advanced),
    pac: () => renderPacAnalysis(advanced?.pac, advanced),
    drawdown: () => renderDrawdownAnalysis(advanced?.drawdown, advanced),
    factorrisk: () => renderFactorRisk(advanced?.factorRisk, advanced),
    scenarioadvanced: () => renderScenarioComparison(advanced?.scenarioComparison, advanced),
  };
  const render = renderers[currentStep];
  if (!render) return;
  render();
  renderedAdvancedSteps.add(currentStep);
  updateComparisonLabels(latestComparisonContext);
  rebalancePortfolioComparisons();
}

function setAdvancedLocked(sectionId, feature, payload) {
  const root = document.querySelector(sectionId);
  if (!root) return;
  root.querySelectorAll(".advanced-grid, .intelligence-panel").forEach((item) => {
    item.innerHTML = lockedFeatureCard(feature, payload);
  });
  root.querySelectorAll("canvas").forEach((canvas) => drawEmptyChart(canvas, "Disponibile nel piano Advanced"));
}

function renderAdvancedComment(selector, data) {
  const panel = document.querySelector(selector);
  if (!panel) return;
  if (!data?.structuredExplanation && !data?.explanationRequest) {
    panel.innerHTML = "";
    panel.hidden = true;
    return;
  }
  renderIntelligencePanel(selector, {
    structuredExplanation: data.structuredExplanation || {},
    explanationRequest: data.explanationRequest,
    executiveSummary: data.summary || data.method || "",
    riskInsight: data.riskInsight || "",
    suggestedAction: data.suggestedAction || "",
  });
}

function clearAdvancedComment(selector) {
  const panel = document.querySelector(selector);
  if (!panel) return;
  panel.innerHTML = "";
  panel.hidden = true;
}

function activePrimaryComparisonLabel() {
  const labels = latestComparisonContext?.labels || {};
  if (latestFinalAnalysisContext?.primaryPortfolioName) return latestFinalAnalysisContext.primaryPortfolioName;
  if (latestFinalAnalysisContext?.mode === "final_optimized_vs_recommended_standard") {
    return "Portafoglio ottimizzato";
  }
  return labels.primary || "Portafoglio inserito";
}

function activeSecondaryComparisonLabel() {
  const labels = latestComparisonContext?.labels || {};
  if (latestFinalAnalysisContext?.comparisonPortfolioName) return latestFinalAnalysisContext.comparisonPortfolioName;
  return labels.comparison || "Portafoglio di confronto";
}

function restoreAdvancedCommentPanel(selector) {
  const panel = document.querySelector(selector);
  if (!panel) return;
  const root = panel.closest(".advanced-results");
  const lane = root?.querySelector(".portfolio-lane.efficient");
  if (panel.classList.contains("wide-comment-panel") && lane && panel.parentElement !== lane) {
    panel.classList.remove("wide-comment-panel");
    lane.appendChild(panel);
  }
}

function ensureAdvancedUnifiedCommentPanel(currentSelector, optimizedSelector) {
  restoreAdvancedCommentPanel(optimizedSelector);
  const currentPanel = document.querySelector(currentSelector);
  const root = currentPanel?.closest(".advanced-results");
  const comparison = currentPanel?.closest(".portfolio-comparison");
  if (!root || !comparison) return optimizedSelector;

  let shell = root.querySelector(":scope > .advanced-unified-comment");
  if (!shell) {
    shell = document.createElement("section");
    shell.className = "advanced-unified-comment";
    shell.innerHTML = `
      <div class="lane-title">
        <span>Commento guidato</span>
        <strong>Confronto avanzato</strong>
      </div>
      <div class="intelligence-panel"></div>
    `;
    comparison.insertAdjacentElement("afterend", shell);
  }

  const primaryLabel = activePrimaryComparisonLabel();
  const comparisonLabel = activeSecondaryComparisonLabel();
  const span = shell.querySelector(".lane-title span");
  const strong = shell.querySelector(".lane-title strong");
  if (span) span.textContent = "Commento guidato";
  if (strong) strong.textContent = `${primaryLabel} vs ${comparisonLabel}`;

  const panel = shell.querySelector(".intelligence-panel");
  if (!panel.id) panel.id = `${root.id || "advanced"}-unified-comment`;
  shell.hidden = false;
  return `#${panel.id}`;
}

function hideAdvancedUnifiedComment(currentSelector) {
  const root = document.querySelector(currentSelector)?.closest(".advanced-results");
  const shell = root?.querySelector(":scope > .advanced-unified-comment");
  if (!shell) return;
  shell.hidden = true;
  const panel = shell.querySelector(".intelligence-panel");
  if (panel) panel.innerHTML = "";
}

function advancedComparisonSummaryText(section) {
  const currentSummary = section?.current?.structuredExplanation?.summary || section?.current?.summary || "";
  const comparisonSummary = section?.optimized?.structuredExplanation?.summary || section?.optimized?.summary || "";
  if (currentSummary && comparisonSummary) {
    return `${currentSummary} ${comparisonSummary}`;
  }
  return currentSummary || comparisonSummary || "Questa sezione confronta due portafogli usando le metriche avanzate calcolate.";
}

function buildAdvancedComparisonExplanation(section) {
  const primaryLabel = activePrimaryComparisonLabel();
  const comparisonLabel = activeSecondaryComparisonLabel();
  const current = section?.current || {};
  const comparison = section?.optimized || {};
  const currentExplanation = current.structuredExplanation || {};
  const comparisonExplanation = comparison.structuredExplanation || {};
  return {
    summary: `Confronto ${primaryLabel} vs ${comparisonLabel}: ${advancedComparisonSummaryText(section)}`,
    meaning:
      `Questa lettura confronta ${primaryLabel} e ${comparisonLabel} nella stessa sezione, usando dati già calcolati. ` +
      (comparisonExplanation.meaning || currentExplanation.meaning || "Serve a capire se il benchmark mostra una struttura più stabile, più diversificata o semplicemente diversa."),
    what_to_watch:
      `L'utente deve confrontare ${primaryLabel} e ${comparisonLabel} guardando differenze di rischio, stabilità, diversificazione e coerenza con il profilo.`,
    main_strength:
      comparisonExplanation.main_strength ||
      currentExplanation.main_strength ||
      "Il punto forte emerge se il portafoglio di confronto riduce una fragilità senza creare una nuova concentrazione.",
    main_weakness:
      comparisonExplanation.main_weakness ||
      currentExplanation.main_weakness ||
      "Il punto debole emerge se uno dei due portafogli dipende troppo da un solo fattore, area geografica o scenario.",
    possible_improvement:
      "Una possibile direzione è usare questo confronto come riferimento educativo e aprire il Piano di miglioramento per vedere eventuali modifiche simulate su pesi e strumenti.",
    technical_detail:
      comparisonExplanation.technical_detail ||
      currentExplanation.technical_detail ||
      "Il confronto usa solo metriche avanzate già calcolate dalla sezione. OpenAI interpreta i risultati, non calcola score o pesi.",
    disclaimer:
      comparisonExplanation.disclaimer ||
      currentExplanation.disclaimer ||
      "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
  };
}

function renderAdvancedComparisonComments(currentSelector, optimizedSelector, section, options = {}) {
  const hasComparison = Boolean(section?.optimized?.structuredExplanation || section?.optimized?.explanationRequest || section?.optimized?.available);
  if (hasComparison) {
    const unifiedSelector = ensureAdvancedUnifiedCommentPanel(currentSelector, optimizedSelector);
    clearAdvancedComment(currentSelector);
    clearAdvancedComment(optimizedSelector);
    renderIntelligencePanel(unifiedSelector, {
      structuredExplanation: buildAdvancedComparisonExplanation(section),
      explanationRequest: section?.optimized?.explanationRequest,
      executiveSummary: section?.optimized?.summary || section?.current?.summary || "",
      riskInsight: section?.optimized?.riskInsight || section?.current?.riskInsight || "",
      suggestedAction: section?.optimized?.suggestedAction || section?.current?.suggestedAction || "",
    });
    return;
  }
  hideAdvancedUnifiedComment(currentSelector);
  if (options.keepCurrent) renderAdvancedComment(currentSelector, section?.current);
  else clearAdvancedComment(currentSelector);
  clearAdvancedComment(optimizedSelector);
}

function renderFamaFrench(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#fama-french-results", "famaFrench", section?.current || {});
    return;
  }
  renderFamaSummary("#fama-current-summary", section?.current);
  renderFamaSummary("#fama-optimized-summary", section?.optimized);
  drawBarChart("#fama-current-chart", (section?.current?.betas || []).map((item) => ({ factor: item.factor, beta: item.beta })), "factor", "beta");
  drawBarChart("#fama-optimized-chart", (section?.optimized?.betas || []).map((item) => ({ factor: item.factor, beta: item.beta })), "factor", "beta");
  renderAdvancedComparisonComments("#fama-current-comment", "#fama-optimized-comment", section, { keepCurrent: true });
}

function renderFamaSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Analisi fattoriale non disponibile.");
    return;
  }
  const strongest = [...(data.betas || [])].sort((a, b) => Math.abs(b.beta) - Math.abs(a.beta))[0];
  container.innerHTML = `
    <article>
      <span>Alpha</span>
      <strong>${formatPercent(data.alpha || 0)}</strong>
      <small>intercetta OLS giornaliera</small>
    </article>
    <article>
      <span>R2</span>
      <strong>${formatPercent(data.rSquared || 0)}</strong>
      <small>quota spiegata dai fattori</small>
    </article>
    <article>
      <span>Fattore dominante</span>
      <strong>${escapeHtml(strongest?.factor || "--")}</strong>
      <small>${strongest ? number.format(strongest.beta) : "non disponibile"}</small>
    </article>
    <article>
      <span>Dataset</span>
      <strong>${escapeHtml(data.region || data.dataset || "Fama-French")}</strong>
      <small>${escapeHtml(data.frequency || "mensile")} · ${data.observations ? `${number.format(data.observations)} osservazioni` : escapeHtml(data.method || "")}${data.includesMomentum ? " · MOM incluso" : ""}</small>
    </article>
  `;
}

function renderGeography(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#geography-results", "geographyExposure", section?.current || {});
    return;
  }
  renderGeoSummary("#geo-current-summary", section?.current);
  renderGeoSummary("#geo-optimized-summary", section?.optimized);
  drawBarChart("#geo-current-chart", section?.current?.continents || [], "name", "weight");
  drawBarChart("#geo-optimized-chart", section?.optimized?.continents || [], "name", "weight");
  renderAdvancedComparisonComments("#geo-current-comment", "#geo-optimized-comment", section, { keepCurrent: true });
}

function renderGeoSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Mappa geografica non disponibile.");
    return;
  }
  const topContinent = data.continents?.[0];
  const topCountry = data.countries?.[0];
  container.innerHTML = `
    <article>
      <span>Continente principale</span>
      <strong>${escapeHtml(topContinent?.name || "--")}</strong>
      <small>${topContinent ? formatPercent(topContinent.weight) : "non disponibile"}</small>
    </article>
    <article>
      <span>Stato principale</span>
      <strong>${escapeHtml(topCountry?.name || "--")}</strong>
      <small>${topCountry ? formatPercent(topCountry.weight) : "non disponibile"}</small>
    </article>
    <article>
      <span>Metodo</span>
      <strong>Proxy</strong>
      <small>${escapeHtml(data.method || "")}</small>
    </article>
  `;
}

function renderRollingSortino(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#rolling-results", "rollingReturns", section?.current || {});
    return;
  }
  const selectedSection = selectedRollingSection(section);
  renderRollingSummary("#rolling-current-summary", selectedSection?.current, section?.sortino?.current);
  renderRollingSummary("#rolling-optimized-summary", selectedSection?.optimized, section?.sortino?.optimized);
  drawRollingChart("#rolling-current-chart", selectedSection?.current);
  drawRollingChart("#rolling-optimized-chart", selectedSection?.optimized);
  renderAdvancedComparisonComments("#rolling-current-comment", "#rolling-optimized-comment", selectedSection, { keepCurrent: true });
}

function selectedRollingSection(section) {
  const selected = section?.windows?.[currentRollingWindow];
  if (!selected) {
    return { current: section?.current, optimized: section?.optimized };
  }
  return {
    ...section,
    current: selected.current,
    optimized: selected.optimized,
    selectedWindow: currentRollingWindow,
    windowLabel: selected.label,
  };
}

function renderRollingSummary(selector, data, sortinoValue) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Rolling metrics non disponibili.");
    return;
  }
  const windowDetail = data.requestedWindow && data.window < data.requestedWindow
    ? `finestra adattata a ${data.window} giorni`
    : `finestra ${data.window} giorni`;
  container.innerHTML = `
    <article>
      <span>Rendimento recente su finestre mobili</span>
      <strong>${formatPercent(data.latest?.rollingReturn || 0)}</strong>
      <small>${windowDetail}</small>
    </article>
    <article>
      <span>Rapporto rendimento-rischio negativo</span>
      <strong>${sortinoValue === null || sortinoValue === undefined ? "--" : number.format(sortinoValue)}</strong>
      <small>rendimento rispetto alle oscillazioni negative</small>
    </article>
    <article>
      <span>Peggior rolling return</span>
      <strong>${formatPercent(data.worstRollingReturn || 0)}</strong>
      <small>periodo peggiore nella finestra</small>
    </article>
  `;
}

function renderBenchmarkComparison(benchmark, equityCurve, standardBenchmarkAnalysis = null, result = null) {
  const container = document.querySelector("#benchmark-summary");
  if (!container) return;
  container.className = "benchmark-summary-grid";
  const secondaryBenchmarkChartPanel = document.querySelector("#benchmark-chart")?.closest(".chart-panel");
  if (secondaryBenchmarkChartPanel) secondaryBenchmarkChartPanel.hidden = true;
  setChartTitle("#benchmark-portfolio-chart", "Confronto semplice", "Portafoglio, smart benchmark e confronto attivo");
  if (!benchmark || isLocked(benchmark) || !benchmark.available) {
    const smart = benchmark?.smartBenchmark;
    const fallbackMarkup = smart?.benchmark
      ? `
        <article class="benchmark-hero-card">
          <span>Benchmark intelligente</span>
          <strong>${escapeHtml(smart.benchmark.name || "Benchmark locale")}</strong>
          <p>${escapePlainFinance(smart.reason || "Selezione locale deterministica.")}</p>
        </article>
      `
      : "";
    container.innerHTML = `${lockedFeatureCard("benchmarkComparison", benchmark || {})}${fallbackMarkup}`;
    drawEmptyChart("#benchmark-portfolio-chart", "Benchmark non disponibile");
    renderBenchmarkComments(benchmark, equityCurve, standardBenchmarkAnalysis, result);
    return;
  }
  const metrics = benchmark.metrics || {};
  const smartBenchmark = benchmark.benchmark || {};
  const exposures = benchmark.exposures || {};
  const equalWeight = benchmark.equalWeightBenchmark || {};
  const portfolioFinal = Array.isArray(equityCurve) && equityCurve.length ? equityCurve[equityCurve.length - 1].value : 0;
  const delta = portfolioFinal && metrics.finalValue ? portfolioFinal - metrics.finalValue : 0;
  const confidenceLabel = { high: "Alta", medium: "Media", low: "Bassa" }[benchmark.confidence] || "Media";
  const typeLabel = benchmark.benchmarkType === "synthetic" ? "multi-asset ponderato" : "benchmark singolo";
  const holdings = Array.isArray(smartBenchmark.holdings) ? smartBenchmark.holdings : [];
  const holdingsList = holdings.length
    ? holdings
        .map((holding) => `
          <li>
            <span>${escapeHtml(holding.name || "Strumento")}</span>
            <strong>${Number(holding.weight || 0).toFixed(1)}%</strong>
          </li>
        `)
        .join("")
    : "<li><span>Composizione non disponibile</span><strong>--</strong></li>";
  const warningList = Array.isArray(benchmark.warnings) && benchmark.warnings.length
    ? `<ul class="benchmark-list benchmark-warning-list">${benchmark.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>`
    : "<small>Nessun warning rilevante sulla classificazione locale.</small>";
  const exposureChips = [
    ["Azioni", exposures.equityExposure],
    ["Obbligazioni", exposures.bondExposure],
    ["Cash-like", exposures.cashLikeExposure],
    ["Oro", exposures.goldExposure],
    ["Commodity", exposures.commodityExposure],
    ["Non classificato", exposures.unknownExposure],
  ]
    .map(([label, value]) => `
      <span class="benchmark-chip">
        <b>${escapeHtml(label)}</b>
        <strong>${Number(value || 0).toFixed(1)}%</strong>
      </span>
    `)
    .join("");
  const advancedBenchmarkDetails = equalWeight?.available
    ? `
      <article class="benchmark-advanced-card">
        <h4>Dettaglio avanzato</h4>
        <p>Il benchmark equal weight resta un confronto secondario: usa gli stessi strumenti del portafoglio inserito, ma con pesi uguali.</p>
        <div class="benchmark-advanced-metrics">
          <span>
            <small>Valore finale equal weight</small>
            <strong>${euro.format(equalWeight.metrics?.finalValue || 0)}</strong>
          </span>
          <span>
            <small>Crescita media annua</small>
            <strong>${formatPercent(equalWeight.metrics?.cagr || 0)}</strong>
          </span>
        </div>
      </article>
    `
    : `
      <article class="benchmark-advanced-card">
        <h4>Dettaglio avanzato</h4>
        <p>Il benchmark principale è costruito localmente sulle macro asset class riconosciute. Il confronto equal weight resta secondario e viene mostrato solo quando i dati sono sufficienti.</p>
      </article>
    `;
  const selectedStandardNote = result?.analysisComparisonContext?.comparisonMode === "standard_benchmark" && standardBenchmarkAnalysis?.available
    ? `
      <article class="benchmark-disclaimer-card">
        <span>Portfolio benchmark standard selezionato</span>
        <strong>${escapeHtml(standardBenchmarkAnalysis.name || "Portfolio benchmark standard QuantInvest")}</strong>
        <small>Questo è il portfolio standard usato come seconda colonna nelle sezioni Advanced. Il grafico “Benchmark intelligente” qui sopra è invece un riferimento automatico costruito sulle macro asset class del portafoglio analizzato.</small>
      </article>
    `
    : "";
  container.innerHTML = `
    <article class="benchmark-hero-card">
      <div>
        <span>Benchmark intelligente</span>
        <strong>${escapeHtml(benchmark.name || "Benchmark locale")}</strong>
        <small>${escapeHtml(typeLabel)} · affidabilità ${escapeHtml(confidenceLabel)}</small>
      </div>
      <p>${escapePlainFinance(benchmark.reason || "Benchmark costruito localmente.")}</p>
    </article>
    <article class="benchmark-reason-card">
      <span>Motivo della scelta</span>
      <p>${escapePlainFinance(benchmark.reason || "Benchmark costruito localmente.")}</p>
      <small>Logica deterministica, senza uso di OpenAI.</small>
    </article>
    <article class="benchmark-confidence-card">
      <span>Affidabilità benchmark</span>
      <strong>${escapeHtml(confidenceLabel)}</strong>
      ${warningList}
    </article>
    <article class="benchmark-composition-card">
      <span>Composizione benchmark</span>
      <p>${escapePlainFinance(smartBenchmark.description || "Riferimento costruito sulle macro asset class.")}</p>
      <ul class="benchmark-list">${holdingsList}</ul>
    </article>
    <article class="benchmark-exposure-card">
      <span>Esposizioni rilevate</span>
      <div class="benchmark-chip-list">${exposureChips}</div>
      <small>Azioni, obbligazioni, oro e commodity guidano la scelta del riferimento.</small>
    </article>
    <article class="benchmark-metric-card">
      <span>Valore finale benchmark</span>
      <strong>${euro.format(metrics.finalValue || 0)}</strong>
      <small>Crescita media annua ${formatPercent(metrics.cagr || 0)}</small>
    </article>
    <article class="benchmark-metric-card">
      <span>Differenza portfolio</span>
      <strong>${euro.format(delta)}</strong>
      <small>portfolio inserito meno benchmark</small>
    </article>
    ${advancedBenchmarkDetails}
    ${selectedStandardNote}
    <article class="benchmark-disclaimer-card">
      <span>Nota educativa</span>
      <strong>Benchmark di confronto, non consiglio operativo</strong>
      <small>${escapeHtml(benchmark.disclaimer || "Il benchmark intelligente QuantInvest è un modello educativo e non costituisce consulenza finanziaria personalizzata.")}</small>
    </article>
  `;
  const comparisonData = result ? comparisonDataForResult(result) : null;
  const comparisonLabel = latestComparisonContext?.labels?.comparison ||
    (result?.analysisComparisonContext?.comparisonMode === "standard_benchmark" ? "Portfolio benchmark standard" : "Portafoglio ottimizzato");
  const comparisonColor = result?.analysisComparisonContext?.comparisonMode === "standard_benchmark" ? "#6f4dbf" : "#d9822b";
  drawMultiLineChart(
    [
      { label: "Benchmark intelligente", points: benchmark.equityCurve || [], color: "#2f66c5", width: 3 },
      { label: "Portafoglio inserito", points: equityCurve || [], color: "#18794e", width: 3 },
      ...(comparisonData?.available && Array.isArray(comparisonData.equityCurve) && comparisonData.equityCurve.length
        ? [{ label: comparisonLabel, points: comparisonData.equityCurve, color: comparisonColor, width: 3, dashed: true }]
        : []),
    ],
    "#benchmark-portfolio-chart",
  );
  renderBenchmarkComments(benchmark, equityCurve, standardBenchmarkAnalysis, result);
}

function renderBenchmarkComments(benchmark, equityCurve, standardBenchmarkAnalysis = null, result = null) {
  const currentPanel = document.querySelector("#benchmark-current-comment");
  const smartPanel = document.querySelector("#benchmark-smart-comment");
  if (!currentPanel || !smartPanel) return;
  if (!benchmark || isLocked(benchmark) || !benchmark.available) {
    currentPanel.hidden = true;
    currentPanel.innerHTML = "";
    smartPanel.hidden = false;
    smartPanel.innerHTML = `<div class="loading-note">${escapeHtml(benchmark?.message || benchmark?.reason || "Benchmark intelligente non disponibile per questa analisi.")}</div>`;
    return;
  }
  const metrics = benchmark.metrics || {};
  const portfolioFinal = Array.isArray(equityCurve) && equityCurve.length ? Number(equityCurve[equityCurve.length - 1].value || 0) : 0;
  const benchmarkFinal = Number(metrics.finalValue || 0);
  const delta = portfolioFinal && benchmarkFinal ? portfolioFinal - benchmarkFinal : 0;
  const benchmarkName = benchmark.name || benchmark.benchmark?.name || "benchmark intelligente";
  const unifiedStructured = {
    summary: delta >= 0
      ? "Il portafoglio inserito chiude sopra il benchmark intelligente nel periodo analizzato."
      : "Il portafoglio inserito chiude sotto il benchmark intelligente nel periodo analizzato.",
    meaning:
      `Il confronto misura il portafoglio inserito rispetto a ${benchmarkName}, costruito localmente sulla composizione riconosciuta. ` +
      `La differenza finale è ${euro.format(delta)}. ` +
      (result?.analysisComparisonContext?.comparisonMode === "standard_benchmark" && standardBenchmarkAnalysis?.available
        ? `Questo benchmark intelligente automatico resta distinto dal portfolio benchmark standard selezionato: ${standardBenchmarkAnalysis.name || "Portfolio benchmark standard QuantInvest"}.`
        : benchmark.reason || "QuantInvest usa azioni, obbligazioni, oro e commodity per costruire il riferimento."),
    what_to_watch: "L'utente deve confrontare valore finale, crescita media annua, peggiore perdita temporanea e composizione tra portafoglio inserito e benchmark intelligente.",
    main_strength: delta >= 0
      ? "Il punto forte è che il portafoglio inserito ha generato più valore del benchmark nel periodo analizzato."
      : "Il punto forte del benchmark è che offre un riferimento più coerente della semplice versione a pesi uguali.",
    main_weakness: delta >= 0
      ? "Il risultato va letto insieme al rischio assunto, perché battere il benchmark non significa automaticamente essere più coerenti con il profilo."
      : "Il punto debole è che il portafoglio ha prodotto meno valore rispetto al benchmark intelligente nel periodo analizzato.",
    possible_improvement: "Una possibile area è usare il benchmark intelligente come confronto e aprire il Piano di miglioramento per vedere modifiche simulate su pesi e strumenti.",
    disclaimer: "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
  };
  currentPanel.hidden = true;
  currentPanel.innerHTML = "";
  renderIntelligencePanel("#benchmark-smart-comment", { structuredExplanation: unifiedStructured });
}

function renderCorrelation(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#correlation-results", "correlationMatrix", section?.current || {});
    return;
  }
  renderCorrelationSummary("#correlation-current-summary", section?.current);
  renderCorrelationSummary("#correlation-optimized-summary", section?.optimized);
  renderAdvancedComparisonComments("#correlation-current-comment", "#correlation-optimized-comment", section, { keepCurrent: true });
  renderCorrelationTable(section?.current, "#correlation-current-head", "#correlation-current-body");
  renderCorrelationTable(section?.optimized, "#correlation-optimized-head", "#correlation-optimized-body");
}

function renderCorrelationSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Correlazione non disponibile.");
    return;
  }
  container.innerHTML = `
    <article>
      <span>Correlazione media</span>
      <strong>${number.format(data.averageAbsoluteCorrelation || 0)}</strong>
      <small>media assoluta tra coppie</small>
    </article>
    <article>
      <span>Coppia più simile</span>
      <strong>${escapeHtml(displayCorrelationPair(data, data.highestPair))}</strong>
      <small>${number.format(data.highestPair?.correlation || 0)}</small>
    </article>
    <article>
      <span>Coppia più diversa</span>
      <strong>${escapeHtml(displayCorrelationPair(data, data.lowestPair))}</strong>
      <small>${number.format(data.lowestPair?.correlation || 0)}</small>
    </article>
  `;
}

function renderCorrelationTable(data, headSelector = "#correlation-current-head", bodySelector = "#correlation-current-body") {
  const head = document.querySelector(headSelector);
  const body = document.querySelector(bodySelector);
  if (!head || !body) return;
  if (!data?.available) {
    head.innerHTML = "";
    body.innerHTML = `<tr><td>${escapeHtml(data?.reason || "Matrice non disponibile.")}</td></tr>`;
    return;
  }
  body.closest(".table-panel")?.classList.add("correlation-heatmap-panel");
  head.innerHTML = `<tr><th>Asset</th>${data.symbols.map((symbol) => `<th>${escapeHtml(displayCorrelationSymbol(data, symbol))}</th>`).join("")}</tr>`;
  body.innerHTML = data.symbols
    .map((symbol, rowIndex) => `
      <tr>
        <td>${escapeHtml(displayCorrelationSymbol(data, symbol))}</td>
        ${data.matrix[rowIndex].map((value) => {
          const alpha = Math.min(0.92, Math.max(0.08, Math.abs(value)));
          const color = value >= 0 ? `rgba(24, 121, 78, ${alpha})` : `rgba(193, 62, 62, ${alpha})`;
          const textColor = Math.abs(value) > 0.55 ? "#fff" : "#17201c";
          const label = Math.abs(value) > 0.75 ? "alta" : Math.abs(value) > 0.4 ? "media" : "bassa";
          return `<td class="heatmap-cell" style="background:${color};color:${textColor}" title="Correlazione ${label}: ${number.format(value)}">${number.format(value)}</td>`;
        }).join("")}
      </tr>
    `)
    .join("");
}

function renderPacAnalysis(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#pac-results", "pacAnalysis", section?.current || {});
    return;
  }
  renderPacComparisonChart(section);
  renderPacSummary("#pac-current-summary", section?.current);
  renderPacSummary("#pac-optimized-summary", section?.optimized);
  renderAdvancedComparisonComments("#pac-current-comment", "#pac-optimized-comment", section, { keepCurrent: true });
}

function renderPacComparisonChart(section) {
  const current = section?.current;
  const comparison = section?.optimized;
  const comparisonLabel = latestComparisonContext?.labels?.comparison ||
    (latestComparisonContext?.comparisonMode === "standard_benchmark" ? "Portfolio benchmark" : "Portafoglio efficiente");
  const comparisonColor = latestComparisonContext?.comparisonMode === "standard_benchmark" ? "#6f4dbf" : "#d9822b";
  setChartTitle("#pac-comparison-chart", "Confronto PAC", `Portafoglio inserito vs ${comparisonLabel}`);
  setChartGuidance(
    "#pac-comparison-chart",
    `La linea verde mostra il PAC sul portafoglio inserito. La seconda linea mostra il PAC su ${comparisonLabel}.`
  );
  if (!current?.available || !Array.isArray(current.curve) || !current.curve.length) {
    drawEmptyChart("#pac-comparison-chart", current?.reason || "PAC del portafoglio inserito non disponibile.");
    return;
  }
  drawMultiLineChart(
    [
      { label: "PAC portafoglio inserito", points: current.curve, color: "#18794e", width: 3 },
      ...(comparison?.available && Array.isArray(comparison.curve) && comparison.curve.length
        ? [{ label: `PAC ${comparisonLabel}`, points: comparison.curve, color: comparisonColor, width: 3, dashed: true }]
        : []),
    ],
    "#pac-comparison-chart",
  );
}

function renderPacSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "PAC non disponibile.");
    return;
  }
  container.innerHTML = `
    <article>
      <span>Versamento mensile</span>
      <strong>${euro.format(data.monthlyContribution || 0)}</strong>
      <small>${data.contributionCount || 0} versamenti</small>
    </article>
    <article>
      <span>Capitale investito</span>
      <strong>${euro.format(data.totalInvested || 0)}</strong>
      <small>capitale iniziale + versamenti</small>
    </article>
    <article>
      <span>Valore finale</span>
      <strong>${euro.format(data.finalValue || 0)}</strong>
      <small>${formatPercent(data.gainPercent || 0)}</small>
    </article>
  `;
}

function renderDrawdownAnalysis(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#drawdown-analysis-results", "peakToTrough", section?.current || {});
    return;
  }
  renderDrawdownSummary("#drawdown-current-summary", section?.current);
  renderDrawdownSummary("#drawdown-optimized-summary", section?.optimized);
  renderAdvancedComparisonComments("#drawdown-current-comment", "#drawdown-optimized-comment", section, { keepCurrent: true });
}

function renderDrawdownSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Perdita temporanea non disponibile.");
    return;
  }
  container.innerHTML = `
    <article>
      <span>Peggiore perdita storica</span>
      <strong>${formatPercent(data.maxDrawdown || 0)}</strong>
      <small>${escapeHtml(data.peakDate || "--")} -> ${escapeHtml(data.troughDate || "--")}</small>
    </article>
    <article>
      <span>Peak-to-trough</span>
      <strong>${number.format(data.peakToTroughDays || 0)} giorni</strong>
      <small>dal massimo al minimo</small>
    </article>
    <article>
      <span>Tempo sotto il massimo</span>
      <strong>${number.format(data.maxDrawdownDurationDays || 0)} giorni</strong>
      <small>${data.recovered ? "recuperato" : "non ancora recuperato nel periodo"}</small>
    </article>
  `;
}

function renderFactorRisk(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#factor-risk-results", "factorShockAnalysis", section?.current || {});
    return;
  }
  if (!hiddenRiskScenarioCalculated) {
    renderHiddenRiskAwaiting();
    return;
  }
  renderFactorRiskSummary("#factor-risk-current-summary", section?.current);
  renderFactorRiskSummary("#factor-risk-optimized-summary", section?.optimized);
  renderAdvancedComparisonComments("#factor-risk-current-comment", "#factor-risk-optimized-comment", section, { keepCurrent: true });
}

function renderFactorRiskSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Factor risk non disponibile.");
    return;
  }
  const top = [...(data.factors || [])].sort((a, b) => b.riskShare - a.riskShare)[0];
  container.innerHTML = `
    <article>
      <span>Scenario</span>
      <strong>${escapeHtml(data.scenario || "--")}</strong>
      <small>scenario selezionato</small>
    </article>
    <article>
      <span>Fattore dominante</span>
      <strong>${escapeHtml(top?.factor || "--")}</strong>
      <small>${formatPercent(top?.riskShare || 0)} del rischio fattoriale</small>
    </article>
    <article>
      <span>Contributo</span>
      <strong>${formatPercent(top?.contribution || 0)}</strong>
      <small>impatto stimato</small>
    </article>
  `;
}

function renderHiddenRiskAwaiting() {
  const message = "Seleziona uno scenario e premi Calcola rischi nascosti.";
  const comparisonLabel = activeSecondaryComparisonLabel();
  hideAdvancedUnifiedComment("#factor-risk-current-comment");
  document.querySelector("#factor-risk-current-summary").innerHTML = unavailableCard(message);
  document.querySelector("#factor-risk-optimized-summary").innerHTML = unavailableCard(message);
  document.querySelector("#factor-risk-current-comment").innerHTML = `
    <div class="loading-note">Questa sezione resta ferma dopo l’analisi completa. Serve una scelta esplicita dello scenario.</div>
  `;
  document.querySelector("#factor-risk-optimized-comment").innerHTML = `
    <div class="loading-note">Dopo il calcolo vedrai come ${escapeHtml(comparisonLabel)} distribuisce il rischio nello scenario scelto.</div>
  `;
}

function factorRiskFromScenarioRows(data, scenario) {
  if (!data?.available || !Array.isArray(data.rows)) {
    return { available: false, reason: data?.reason || "Scenario+ non disponibile." };
  }
  const row = data.rows.find((item) => item.scenario === scenario);
  if (!row) return { available: false, reason: "Scenario non trovato nei dati calcolati." };
  const contributions = Array.isArray(row.contributionByFactor) ? row.contributionByFactor : [];
  const totalAbs = contributions.reduce((total, item) => total + Math.abs(Number(item.contribution || 0)), 0);
  return {
    available: true,
    scenario,
    expectedPortfolioReturn: Number(row.expectedPortfolioReturn || 0),
    bestHedge: row.bestHedgeName || displayAnalysisInstrumentName(row.bestHedge),
    worstContributor: row.worstContributorName || displayAnalysisInstrumentName(row.worstContributor),
    factors: contributions.map((item) => ({
      factor: factorLabels[item.factor] || item.factor,
      contribution: Number(item.contribution || 0),
      riskShare: totalAbs ? Math.abs(Number(item.contribution || 0)) / totalAbs : 0,
    })),
  };
}

function hiddenRiskTopFactor(data) {
  return [...(data?.factors || [])].sort((a, b) => b.riskShare - a.riskShare)[0];
}

function renderHiddenRiskComments(currentData, optimizedData) {
  const currentTop = hiddenRiskTopFactor(currentData);
  const optimizedTop = hiddenRiskTopFactor(optimizedData);
  const scenario = currentData?.scenario || optimizedData?.scenario || "--";
  const currentImpact = Number(currentData?.expectedPortfolioReturn || 0);
  const optimizedImpact = Number(optimizedData?.expectedPortfolioReturn || 0);
  const delta = optimizedImpact - currentImpact;
  const unifiedSelector = ensureAdvancedUnifiedCommentPanel("#factor-risk-current-comment", "#factor-risk-optimized-comment");
  clearAdvancedComment("#factor-risk-current-comment");
  clearAdvancedComment("#factor-risk-optimized-comment");
  renderIntelligencePanel(unifiedSelector, {
    structuredExplanation: {
      summary: `Nello scenario ${scenario}, il portafoglio analizzato è guidato soprattutto da ${currentTop?.factor || "un fattore non disponibile"}; il confronto è guidato da ${optimizedTop?.factor || "un fattore non disponibile"}.`,
      meaning: `L'impatto stimato è ${formatPercent(currentImpact)} sul portafoglio analizzato e cambia di ${formatPercent(delta)} nel portafoglio di confronto.`,
      what_to_watch: "L'utente deve confrontare il fattore dominante del portafoglio analizzato con quello del benchmark o portafoglio di confronto.",
      main_strength: "Il punto forte emerge se il portafoglio di confronto riduce la concentrazione sul fattore più vulnerabile.",
      main_weakness: "Il limite emerge se il portafoglio di confronto resta esposto allo stesso fattore dominante.",
      possible_improvement: "Una possibile direzione è usare il risultato come confronto educativo e aprire il Piano di miglioramento per eventuali modifiche simulate.",
      disclaimer: "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
    },
  });
}

function renderSelectedHiddenRiskScenario() {
  const scenario = document.querySelector("#hidden-risk-scenario")?.value || "Global Recession";
  const comparison = latestAdvancedAnalytics?.scenarioComparison;
  const currentData = factorRiskFromScenarioRows(comparison?.current, scenario);
  const optimizedData = factorRiskFromScenarioRows(comparison?.optimized, scenario);
  renderFactorRiskSummary("#factor-risk-current-summary", currentData);
  renderFactorRiskSummary("#factor-risk-optimized-summary", optimizedData);
  if (currentData.available || optimizedData.available) renderHiddenRiskComments(currentData, optimizedData);
  else renderHiddenRiskAwaiting();
}

function runHiddenRiskScenario() {
  runBacktest(null, {
    targetStep: "factorrisk",
    loadingMessage: "Calcolo rischi nascosti in corso...",
    successMessage: "Rischi nascosti aggiornati.",
    includeStress: true,
    afterRender: () => {
      hiddenRiskScenarioCalculated = true;
      renderedAdvancedSteps.delete("factorrisk");
      renderFactorRisk(latestAdvancedAnalytics?.factorRisk, latestAdvancedAnalytics);
      loadVisibleAiPanels();
      rebalancePortfolioComparisons();
    },
  });
}

function renderScenarioComparison(section, advanced) {
  if (advanced?.locked || isLocked(section?.current)) {
    setAdvancedLocked("#scenario-comparison-results", "factorShockAnalysis", section?.current || {});
    return;
  }
  renderScenarioComparisonSummary("#scenario-comparison-current-summary", section?.current);
  renderScenarioComparisonSummary("#scenario-comparison-optimized-summary", section?.optimized);
  renderAdvancedComparisonComments("#scenario-comparison-current-comment", "#scenario-comparison-optimized-comment", section, { keepCurrent: true });
}

function renderScenarioComparisonSummary(selector, data) {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!data?.available) {
    container.innerHTML = unavailableCard(data?.reason || "Scenario comparison non disponibile.");
    return;
  }
  container.innerHTML = `
    <article>
      <span>Scenario peggiore</span>
      <strong>${escapeHtml(data.worstScenario?.scenario || "--")}</strong>
      <small>${formatPercent(data.worstScenario?.expectedPortfolioReturn || 0)}</small>
    </article>
    <article>
      <span>Scenario migliore</span>
      <strong>${escapeHtml(data.bestScenario?.scenario || "--")}</strong>
      <small>${formatPercent(data.bestScenario?.expectedPortfolioReturn || 0)}</small>
    </article>
    <article>
      <span>Scenari calcolati</span>
      <strong>${number.format((data.rows || []).filter((row) => row.expectedPortfolioReturn !== undefined).length)}</strong>
      <small>shock predefiniti</small>
    </article>
  `;
}

function renderAdvisor(advisor) {
  if (!advisor) return;
  latestFinalAnalysisContext = advisor.finalAnalysisContext || null;
  if (advisor.userPlan) {
    document.querySelector("#user-plan").value = advisor.userPlan;
    updatePlanBadge();
    updatePlanVisibility();
  }
  const score = document.querySelector("#advisor-score");
  const scoreType = advisor.intelligence?.healthScore?.scoreType || "Check-up portafoglio";
  score.textContent = `${scoreType} ${advisor.score}/100`;
  score.dataset.ready = "true";
  const aiStatus = document.querySelector("#ai-status");
  if (aiStatus) {
    const labels = {
      openai: "Commenti OpenAI attivi",
      fallback: "Commenti locali: OpenAI non disponibile",
      disabled: "Commenti locali: chiave OpenAI assente",
      error: "Commenti locali: errore OpenAI",
    };
    aiStatus.textContent = labels[advisor.aiStatus] || "Commenti locali";
    aiStatus.dataset.status = advisor.aiStatus || "fallback";
  }
  advisorSections = {
    ...(advisor.sections || {}),
    analysis: advisor.finalAnalysis?.text || advisor.summary,
  };
  if (advisor.finalAnalysis) renderFinalAnalysis(advisor.finalAnalysis);
  if (advisor.intelligence) renderIntelligence(advisor.intelligence);
  if (latestFinalAnalysisContext) renderFinalAnalysisFromContext(latestFinalAnalysisContext);
  rebalancePortfolioComparisons();
  updateComparisonContextStrips();
}

function renderFinalAnalysis(analysis) {
  document.querySelector("#analysis-text").textContent = analysis.text;
}

function renderIntelligence(intelligence) {
  if (latestComparisonContext?.comparisonMode === "standard_only") {
    renderStandardOnlyIntelligence(intelligence);
    return;
  }
  if (latestComparisonContext?.comparisonMode === "standard_benchmark") {
    renderStandardBenchmarkPairIntelligence(intelligence);
    return;
  }
  const isStandardBenchmark = latestComparisonContext?.comparisonMode === "standard_benchmark";
  const comparisonLabel = latestComparisonContext?.labels?.comparison || "Portfolio efficiente";
  const backtestLead = isStandardBenchmark
    ? "Il benchmark standard selezionato è il riferimento principale del confronto storico. Non viene trattato come portfolio ottimale."
    : intelligence.backtest?.optimizedComparison;
  const monteCarloLead = isStandardBenchmark
    ? "La simulazione confronta il portfolio inserito con il portfolio benchmark standard QuantInvest selezionato."
    : intelligence.montecarlo?.optimizedComparison;
  const scenariosLead = isStandardBenchmark
    ? "Gli scenari mostrano come il portfolio inserito e il benchmark standard reagiscono allo stesso shock."
    : intelligence.scenarios?.optimizedComparison;
  renderIntelligencePair("frontier", intelligence.frontier, {
    optimizedOnly: true,
    leadBlocks: [
      [
        isStandardBenchmark ? "Analisi opzionale" : "Alternativa ottimizzata",
        isStandardBenchmark
          ? "La frontiera efficiente resta una simulazione separata: il benchmark principale dell'analisi è il portfolio standard selezionato."
          : intelligence.frontier?.optimizedAlternative,
      ],
    ],
  });
  renderIntelligencePair("backtest", intelligence.backtest, {
    optimizedOnly: true,
    leadBlocks: [[comparisonLabel, backtestLead]],
  });
  renderIntelligencePair("montecarlo", intelligence.montecarlo, {
    optimizedOnly: true,
    leadBlocks: [[comparisonLabel, monteCarloLead]],
  });
  renderIntelligencePair("scenarios", intelligence.scenarios, {
    optimizedOnly: true,
    leadBlocks: [
      [comparisonLabel, scenariosLead],
    ],
  });
  renderIntelligencePair("analysis", intelligence.analysis, {
    healthScore: intelligence.healthScore,
    optimizedHealthScore: intelligence.optimizedHealthScore,
    healthScoreLabel: intelligence.healthScore?.scoreType || "Check-up portafoglio inserito",
    optimizedHealthScoreLabel: intelligence.optimizedHealthScore?.scoreType || "Check-up portafoglio efficiente",
    scoreOnly: true,
  });
  renderScoreComparisonSummary(
    "#analysis-score-comparison",
    intelligence.healthScore,
    intelligence.optimizedHealthScore,
    "Portfolio inserito",
    latestComparisonContext?.labels?.comparison || "Portfolio efficiente"
  );
}

function renderStandardBenchmarkPairIntelligence(intelligence) {
  const benchmarkSection = latestComparisonContext?.benchmarkComment || {
    executiveSummary: "Il portfolio benchmark standard QuantInvest è il confronto educativo selezionato.",
    riskInsight: "Confronta rischio, perdita storica e composizione rispetto al portfolio analizzato.",
    suggestedAction: "Osserva se il portfolio principale assume più rischio o concentrazione rispetto al benchmark.",
    technicalExplanation: "Il benchmark usa metriche calcolate sugli stessi parametri temporali dell’analisi.",
    disclaimer: latestComparisonContext?.disclaimer || "",
  };
  const benchmarkHealthScore = latestComparisonContext?.benchmarkHealthScore || null;
  const benchmarkLabel = latestComparisonContext?.labels?.comparison || "Portfolio benchmark standard QuantInvest";
  const primaryLabel = latestComparisonContext?.labels?.primary || "Portfolio inserito";
  const comparisonExplanation = (section) => ({
    structuredExplanation: {
      summary: `Confronto ${primaryLabel} vs ${benchmarkLabel}.`,
      meaning:
        `${section?.executiveSummary || "Il portafoglio inserito viene letto insieme al portfolio benchmark standard selezionato."} ` +
        `${benchmarkSection.executiveSummary || "Il benchmark standard serve come modello educativo di confronto."}`,
      what_to_watch:
        `L'utente deve confrontare ${primaryLabel} e ${benchmarkLabel} guardando crescita storica, perdita temporanea, rischio e coerenza con il profilo indicato.`,
      main_strength:
        benchmarkSection.mainStrength ||
        section?.mainStrength ||
        "Il punto forte emerge dal confronto tra il comportamento del portfolio inserito e la struttura del benchmark standard.",
      main_weakness:
        benchmarkSection.mainRisk ||
        section?.mainRisk ||
        "Il punto debole emerge se uno dei due portafogli mostra più perdita temporanea, concentrazione o incoerenza con l'obiettivo.",
      possible_improvement:
        benchmarkSection.improvementArea ||
        section?.suggestedAction ||
        "Una possibile area è usare il benchmark standard come riferimento e aprire il Piano di miglioramento per vedere modifiche simulate su pesi e strumenti.",
      technical_detail:
        benchmarkSection.technicalExplanation ||
        section?.technicalExplanation ||
        "Il confronto usa metriche già calcolate sugli stessi parametri temporali. Il portfolio benchmark standard non viene trattato come portfolio ottimale.",
      disclaimer:
        benchmarkSection.disclaimer ||
        latestComparisonContext?.disclaimer ||
        "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
    },
    explanationRequest: section?.explanationRequest,
    executiveSummary: section?.executiveSummary || benchmarkSection.executiveSummary || "",
    riskInsight: benchmarkSection.riskInsight || section?.riskInsight || "",
    suggestedAction: benchmarkSection.suggestedAction || section?.suggestedAction || "",
  });
  const entries = [
    ["backtest", intelligence.backtest],
    ["montecarlo", intelligence.montecarlo],
    ["scenarios", intelligence.scenarios],
  ];
  const frontierCurrent = document.querySelector("#frontier-intelligence-current");
  const frontierOptimized = document.querySelector("#frontier-intelligence-optimized");
  if (frontierCurrent) {
    frontierCurrent.innerHTML = "";
    frontierCurrent.hidden = true;
  }
  if (frontierOptimized) {
    frontierOptimized.innerHTML = "";
    frontierOptimized.hidden = true;
  }
  entries.forEach(([sectionKey, section]) => {
    const currentPanel = document.querySelector(`#${sectionKey}-intelligence-current`);
    const optimizedPanel = document.querySelector(`#${sectionKey}-intelligence-optimized`);
    if (!section) {
      if (currentPanel) {
        currentPanel.innerHTML = "";
        currentPanel.hidden = true;
      }
      if (optimizedPanel) {
        optimizedPanel.innerHTML = "";
        optimizedPanel.hidden = true;
      }
      return;
    }
    if (currentPanel) {
      currentPanel.innerHTML = "";
      currentPanel.hidden = true;
    }
    renderIntelligencePanel(`#${sectionKey}-intelligence-optimized`, comparisonExplanation(section), {
      leadBlocks: [[benchmarkLabel, benchmarkSection.executiveSummary || "Portfolio benchmark standard QuantInvest selezionato."]],
    });
  });
  renderIntelligencePanel("#analysis-intelligence-current", intelligence.analysis, {
    healthScore: intelligence.healthScore,
    healthScoreLabel: intelligence.healthScore?.scoreType || "Check-up portfolio inserito",
    scoreOnly: true,
  });
  renderIntelligencePanel("#analysis-intelligence-optimized", benchmarkSection, {
    healthScore: benchmarkHealthScore,
    healthScoreLabel: `Check-up ${benchmarkLabel}`,
    leadBlocks: [[benchmarkLabel, benchmarkSection.executiveSummary]],
    scoreOnly: true,
  });
  renderScoreComparisonSummary(
    "#analysis-score-comparison",
    intelligence.healthScore,
    benchmarkHealthScore,
    latestComparisonContext?.labels?.primary || "Portfolio inserito",
    benchmarkLabel
  );
}

function renderStandardOnlyIntelligence(intelligence) {
  const entries = [
    ["frontier", intelligence.frontier],
    ["backtest", intelligence.backtest],
    ["montecarlo", intelligence.montecarlo],
    ["scenarios", intelligence.scenarios],
  ];
  entries.forEach(([sectionKey, section]) => {
    const optimizedPanel = document.querySelector(`#${sectionKey}-intelligence-optimized`);
    if (optimizedPanel) {
      optimizedPanel.innerHTML = "";
      optimizedPanel.hidden = true;
    }
    renderIntelligencePanel(`#${sectionKey}-intelligence-current`, {
      ...(section || {}),
      executiveSummary:
        section?.executiveSummary ||
        "Questa analisi riguarda solo il portfolio standard QuantInvest caricato con i pesi indicati.",
      structuredExplanation: {
        ...(section?.structuredExplanation || {}),
        what_to_watch:
          section?.structuredExplanation?.what_to_watch ||
          section?.structuredExplanation?.what_to_look_at ||
          "L'utente deve osservare composizione, pesi, ruolo degli strumenti e coerenza del portfolio standard con il profilo indicato.",
      },
      suggestedAction:
        section?.suggestedAction ||
        "Puoi modificare pesi e strumenti prima di rilanciare il check-up. Non viene creato un portfolio standard ottimale in questa modalità.",
    }, {
      comparisonMode: "standard_only",
    });
  });
  const analysisOptimized = document.querySelector("#analysis-intelligence-optimized");
  if (analysisOptimized) {
    analysisOptimized.innerHTML = "";
    analysisOptimized.hidden = true;
  }
  renderIntelligencePanel("#analysis-intelligence-current", intelligence.analysis, {
    healthScore: intelligence.healthScore,
    healthScoreLabel: intelligence.healthScore?.scoreType || "Check-up portfolio standard",
    scoreOnly: true,
    comparisonMode: "standard_only",
  });
  renderScoreComparisonSummary("#analysis-score-comparison", null, null);
}

function optimizedSection(section) {
  if (!section) return null;
  if (latestComparisonContext?.comparisonMode === "standard_benchmark") {
    const checkup = latestComparisonContext.benchmarkCheckup || {};
    return {
      executiveSummary:
        checkup.benchmarkInsight ||
        "Il portfolio benchmark standard QuantInvest selezionato è il riferimento educativo principale del confronto.",
      riskInsight:
        checkup.mainRisk ||
        latestComparisonContext.benchmarkReason ||
        "Il confronto benchmark viene mostrato quando il portfolio standard ha dati sufficienti.",
      suggestedAction:
        checkup.improvementArea ||
        "Valuta le differenze di rischio, perdita massima e concentrazione rispetto al benchmark standard selezionato.",
      technicalExplanation:
        "Questa sezione confronta metriche già calcolate sul portfolio inserito e sul portfolio benchmark standard QuantInvest. Il portfolio ottimizzato resta un'analisi separata.",
      mainRisk: checkup.mainRisk || "dati benchmark da verificare",
      mainStrength: checkup.strength || "confronto educativo con un modello standard",
      improvementArea: checkup.improvementArea || "monitorare le differenze principali rispetto al benchmark",
      disclaimer:
        latestComparisonContext.disclaimer ||
        "I portfolio standard QuantInvest sono modelli educativi e benchmark di confronto. Non costituiscono consulenza finanziaria personalizzata né raccomandazione di acquisto o vendita.",
    };
  }
  if (section.optimizedStructuredExplanation) {
    return {
      ...section,
      structuredExplanation: section.optimizedStructuredExplanation,
      explanationRequest: section.optimizedExplanationRequest,
      executiveSummary: section.optimizedComparison || section.optimizedAlternative || section.executiveSummary,
      riskInsight: section.optimizedComparison || section.riskInsight,
      suggestedAction: section.suggestedAction,
    };
  }
  return {
    executiveSummary: section.optimizedComparison || section.optimizedAlternative || "Portfolio efficiente non disponibile per questa sezione.",
    riskInsight: section.optimizedComparison || "Il confronto ottimizzato verra mostrato quando la frontiera efficiente e disponibile.",
    suggestedAction: "Esegui l'analisi con piano Plus o Advanced e almeno due asset per calcolare il portfolio efficiente.",
    technicalExplanation: section.technicalExplanation || "Il portfolio efficiente usa gli stessi strumenti con pesi ottimizzati.",
    mainRisk: section.mainRisk || "dati insufficienti",
    mainStrength: "confronto separato rispetto al portafoglio inserito",
    improvementArea: section.improvementArea || "verificare vincoli e pesi",
  };
}

function renderIntelligencePair(sectionKey, section, options = {}) {
  if (options.optimizedOnly) {
    const currentPanel = document.querySelector(`#${sectionKey}-intelligence-current`);
    if (currentPanel) {
      currentPanel.innerHTML = "";
      currentPanel.hidden = true;
    }
  } else {
    renderIntelligencePanel(`#${sectionKey}-intelligence-current`, section, options);
  }
  renderIntelligencePanel(`#${sectionKey}-intelligence-optimized`, optimizedSection(section), {
    healthScore: options.optimizedHealthScore,
    healthScoreLabel: options.optimizedHealthScoreLabel,
    leadBlocks: options.leadBlocks?.filter(([label]) => {
      const normalized = label.toLowerCase();
      return normalized.includes("ottim") || normalized.includes("benchmark") || normalized.includes("analisi opzionale");
    }) || [],
    scoreOnly: options.scoreOnly,
  });
}

function hasUsefulLaneContent(lane) {
  if (!lane) return false;
  const contentNodes = Array.from(lane.children).filter((child) => !child.classList.contains("lane-title"));
  return contentNodes.some((node) => {
    if (node.hidden) return false;
    const html = node.innerHTML.trim();
    const text = node.textContent.trim();
    if (!html && !text) return false;
    if (node.matches(".intelligence-panel") && !html) return false;
    return true;
  });
}

function hasUsefulNonCommentLaneContent(lane) {
  if (!lane) return false;
  const contentNodes = Array.from(lane.children).filter((child) => (
    !child.classList.contains("lane-title") &&
    !child.classList.contains("intelligence-panel")
  ));
  return contentNodes.some((node) => !node.hidden && Boolean(node.innerHTML.trim() || node.textContent.trim()));
}

function panelHasContent(panel) {
  return Boolean(panel && !panel.hidden && panel.innerHTML.trim());
}

function moveAfterLaneTitle(elementSelector, laneSelector) {
  const element = document.querySelector(elementSelector);
  const lane = document.querySelector(laneSelector);
  const title = lane?.querySelector(".lane-title");
  if (!element || !lane || !title || element.parentElement === lane) return;
  title.insertAdjacentElement("afterend", element);
}

function arrangeComparisonMetricCards() {
  moveAfterLaneTitle("#monte-carlo-grid", "#monte-carlo-results .portfolio-lane:not(.efficient)");
  moveAfterLaneTitle("#stress-summary-grid", "#stress-testing-results .portfolio-lane:not(.efficient)");
  moveAfterLaneTitle("#frontier-summary-grid", "#efficient-frontier-results .portfolio-lane.efficient");
}

function widenSingleComparisonComment(comparison) {
  const currentLane = comparison.querySelector(":scope > .portfolio-lane:not(.efficient)");
  const efficientLane = comparison.querySelector(":scope > .portfolio-lane.efficient");
  const currentPanel = currentLane?.querySelector(".intelligence-panel");
  const optimizedPanel = comparison.querySelector(":scope > .wide-comment-panel") || efficientLane?.querySelector(".intelligence-panel");
  if (!currentLane || !efficientLane || !optimizedPanel) return;

  const shouldWiden = (
    !panelHasContent(currentPanel) &&
    panelHasContent(optimizedPanel) &&
    hasUsefulNonCommentLaneContent(currentLane) &&
    hasUsefulNonCommentLaneContent(efficientLane)
  );

  if (shouldWiden) {
    optimizedPanel.classList.add("wide-comment-panel");
    optimizedPanel.hidden = false;
    if (optimizedPanel.parentElement !== comparison) comparison.appendChild(optimizedPanel);
    comparison.classList.add("has-wide-comment");
    return;
  }

  comparison.classList.remove("has-wide-comment");
  if (optimizedPanel.classList.contains("wide-comment-panel") && efficientLane && optimizedPanel.parentElement === comparison) {
    optimizedPanel.classList.remove("wide-comment-panel");
    efficientLane.appendChild(optimizedPanel);
  }
}

function rebalancePortfolioComparisons() {
  document.querySelectorAll(".portfolio-comparison").forEach((comparison) => {
    widenSingleComparisonComment(comparison);
    const lanes = Array.from(comparison.querySelectorAll(":scope > .portfolio-lane"));
    if (!lanes.length) return;
    lanes.forEach((lane) => {
      lane.classList.toggle("is-empty-lane", !hasUsefulLaneContent(lane));
    });
    const filledLanes = lanes.filter((lane) => !lane.classList.contains("is-empty-lane"));
    comparison.classList.toggle("is-empty-comparison", filledLanes.length === 0);
    comparison.classList.toggle("single-lane", filledLanes.length === 1);
    comparison.classList.toggle("only-current-lane", filledLanes.length === 1 && !filledLanes[0].classList.contains("efficient"));
    comparison.classList.toggle("only-efficient-lane", filledLanes.length === 1 && filledLanes[0].classList.contains("efficient"));
  });
}

function comparisonTextFromLanes(comparison) {
  const lanes = Array.from(comparison.querySelectorAll(":scope > .portfolio-lane"))
    .filter((lane) => !lane.hidden && !lane.classList.contains("is-empty-lane"));
  if (lanes.length < 2) return "";
  const labels = lanes.slice(0, 2).map((lane) => {
    const title = lane.querySelector(".lane-title");
    const primary = title?.querySelector("span")?.textContent?.trim();
    const secondary = title?.querySelector("strong")?.textContent?.trim();
    return primary || secondary || "";
  }).filter(Boolean);
  if (labels.length < 2) return "";
  return `Stai confrontando: ${labels[0]} vs ${labels[1]}`;
}

function updateComparisonContextStrips() {
  document.querySelectorAll(".portfolio-comparison").forEach((comparison) => {
    let strip = comparison.querySelector(":scope > .comparison-context-strip");
    const text = comparisonTextFromLanes(comparison);
    if (!text) {
      if (strip) strip.remove();
      return;
    }
    if (!strip) {
      strip = document.createElement("div");
      strip.className = "comparison-context-strip";
      comparison.prepend(strip);
    }
    strip.innerHTML = `<span>Confronto attivo</span><strong>${escapeHtml(text)}</strong>`;
  });
}

function renderIntelligencePanel(selector, section, options = {}) {
  const panel = document.querySelector(selector);
  if (!panel || !section) return;
  panel.hidden = false;
  const explanation = section.structuredExplanation || {};
  const detailPayload = section.explanationRequest || explanation.explanationRequest || null;
  if (detailPayload) {
    explanationDetailPayloads.set(selector, detailPayload);
  } else {
    explanationDetailPayloads.delete(selector);
  }
  const summary = explanation.summary || section.executiveSummary;
  const meaning = completeFinanceComment(explanation.meaning || explanation.meaning_for_user || section.riskInsight);
  const numbersMeaning = completeFinanceComment(explanation.what_the_numbers_mean || explanation.meaning || meaning || summary || "");
  const whatToLookAt =
    completeWhatToWatch(explanation.what_to_watch ||
    explanation.what_to_look_at ||
    defaultWhatToWatchText(options));
  const possibleImprovement =
    completeFinanceComment(explanation.possible_improvement ||
    section.suggestedAction ||
    "Una possibile area di miglioramento sarà mostrata appena il commento OpenAI è disponibile.");
  const disclaimer = explanation.disclaimer || "";
  const localDetail = {
    mainStrength:
      explanation.main_strength ||
      section.mainStrength ||
      section.main_strength ||
      "Il punto di forza emerge dal confronto tra i valori principali della sezione.",
    mainWeakness:
      explanation.main_weakness ||
      explanation.main_risk ||
      section.mainRisk ||
      section.mainWeakness ||
      "Il punto di debolezza emerge se rischio, concentrazione o instabilità risultano più elevati del confronto.",
    technicalNote:
      explanation.technical_detail ||
      section.technicalExplanation ||
      section.technical_detail ||
      "Il dettaglio tecnico usa solo metriche già calcolate nella sezione.",
    disclaimer,
  };
  const hasLocalDetail = Boolean(localDetail.mainStrength || localDetail.mainWeakness || localDetail.technicalNote);
  if (hasLocalDetail) {
    localPanelTechnicalDetails.set(selector, localDetail);
  } else {
    localPanelTechnicalDetails.delete(selector);
  }
  const visibleNumbersMeaning = numbersMeaning || (detailPayload
    ? "Questa sezione traduce le metriche avanzate in una lettura pratica del portafoglio."
    : "");
  const visibleWhatToLookAt = whatToLookAt || (detailPayload
    ? "L'utente deve confrontare i valori principali della sezione e verificare quale portafoglio mostra più rischio, stabilità o diversificazione."
    : "");
  const visiblePossibleImprovement = possibleImprovement || (detailPayload
    ? "Una possibile area è usare il confronto della sezione e aprire il Piano di miglioramento per vedere modifiche simulate su pesi e strumenti."
    : "");
  const leadBlocks = (options.leadBlocks || [])
    .filter(([, text]) => text)
    .map(([label, text]) => `
      <article class="intelligence-lead">
        <span>${escapeHtml(label)}</span>
        <p>${escapeHtml(text)}</p>
      </article>
    `)
    .join("");
  const health = options.healthScore ? renderHealthScore(options.healthScore, options.healthScoreLabel) : "";
  if (options.scoreOnly) {
    panel.innerHTML = health || `
      <section class="health-score-panel">
        <div>
          <span>${escapeHtml(options.healthScoreLabel || "Check-up del portafoglio")}</span>
          <strong>--/100</strong>
        </div>
        <p class="score-rationale">Calcola il portafoglio efficiente per ottenere una valutazione separata.</p>
      </section>
    `;
    return;
  }
  panel.innerHTML = `
    ${health}
    ${leadBlocks ? `<div class="intelligence-leads">${leadBlocks}</div>` : ""}
    <div class="intelligence-grid">
      <article>
        <span>Cosa significano i numeri</span>
        <p data-ai-field="what_the_numbers_mean">${escapeHtml(completeFinanceComment(visibleNumbersMeaning))}</p>
      </article>
      <article>
        <span>Cosa guardare</span>
        <p data-ai-field="what_to_look_at">${escapeHtml(completeWhatToWatch(visibleWhatToLookAt))}</p>
      </article>
      <article>
        ${renderPossibleImprovementGuidance(visiblePossibleImprovement)}
      </article>
    </div>
    ${detailPayload || hasLocalDetail ? `
      <button class="secondary ai-detail-button" type="button" data-panel-selector="${escapeHtml(selector)}">
        Mostra dettagli tecnici
      </button>
      <div class="ai-detail-output" hidden></div>
    ` : ""}
    <div class="intelligence-footer compact">
      ${disclaimer ? `<span><b>Nota:</b> ${escapeHtml(disclaimer)}</span>` : ""}
    </div>
  `;
  if (detailPayload) {
    pendingAiPanelSelectors.add(selector);
    loadVisibleAiPanels();
  }
}

function scoreValue(score) {
  const value = Number(score);
  return Number.isFinite(value) ? Math.round(value) : 0;
}

function healthScorePillars(healthScore) {
  const labels = {
    diversification: "Diversificazione",
    riskReturnEfficiency: "Efficienza rischio/rendimento",
    goalCoherence: "Coerenza con obiettivi",
    scenarioRobustness: "Robustezza agli scenari",
    successProbability: "Probabilità di raggiungere l’obiettivo",
    baseRisk: "Rischio reale base",
    baseEfficiency: "Efficienza base",
    baseDiversification: "Diversificazione base",
    baseCoherence: "Coerenza base",
    realRisk: "Rischio reale",
    trackingRebalancing: "Tracking e ribilanciamento",
    advancedRisk: "Rischio reale avanzato",
    advancedEfficiency: "Efficienza avanzata",
    realDiversification: "Diversificazione reale",
    advancedGoalCoherence: "Coerenza obiettivi/PAC",
    factorScenarioRobustness: "Robustezza fattoriale",
  };
  return Array.isArray(healthScore?.pillars)
    ? healthScore.pillars
    : Object.entries(healthScore?.components || {}).map(([key, value]) => ({
        key,
        name: labels[key] || key,
        score: value,
        question: "",
        interpretation: healthScore?.details?.[key] || "",
        primaryMetric: "",
        status: "",
      }));
}

function weakestHealthPillar(healthScore) {
  const pillars = healthScorePillars(healthScore).filter((pillar) => Number.isFinite(Number(pillar.score)));
  return pillars.sort((a, b) => Number(a.score || 0) - Number(b.score || 0))[0] || null;
}

function strongestHealthPillar(healthScore) {
  const pillars = healthScorePillars(healthScore).filter((pillar) => Number.isFinite(Number(pillar.score)));
  return pillars.sort((a, b) => Number(b.score || 0) - Number(a.score || 0))[0] || null;
}

function diversificationScoreFromHealth(healthScore) {
  const pillar = healthScorePillars(healthScore).find((item) =>
    ["diversification", "baseDiversification", "realDiversification"].includes(item.key)
  );
  return Number.isFinite(Number(pillar?.score)) ? Number(pillar.score) : null;
}

function buildHealthScoreDisplayLabel({
  score,
  reliability,
  equityExposure,
  diversificationScore,
  maxDrawdown,
  maxTemporaryLoss,
  isDemoData,
  dataYears,
}) {
  const rounded = scoreValue(score);
  const base =
    rounded >= 90 ? "Molto forte" :
    rounded >= 75 ? "Buono" :
    rounded >= 60 ? "Discreto" :
    rounded >= 40 ? "Da migliorare" :
    "Critico";
  if (Number(equityExposure || 0) >= 0.9) return `${base}, ma molto esposto all’azionario`;
  if (Number.isFinite(Number(diversificationScore)) && Number(diversificationScore) < 65) {
    return `${base}, con diversificazione da monitorare`;
  }
  if (Number.isFinite(Number(maxDrawdown)) && Number.isFinite(Number(maxTemporaryLoss)) && Math.abs(maxDrawdown) >= Math.abs(maxTemporaryLoss)) {
    return `${base}, vicino alla soglia di perdita dichiarata`;
  }
  if (String(reliability || "").toLowerCase().includes("bassa") || isDemoData || Number(dataYears || 0) > 0 && Number(dataYears || 0) < 3) {
    return `${base} sui dati disponibili, ma affidabilità limitata`;
  }
  return `${base}, sui dati disponibili`;
}

function buildHealthReliabilityText(healthScore) {
  const confidence = healthScore?.confidence || "--";
  const reason = healthScore?.confidenceReason || "Il punteggio usa solo le metriche disponibili e non inventa dati mancanti.";
  const missing = Array.isArray(healthScore?.metricsMissing) && healthScore.metricsMissing.length;
  const demoLike = String(reason).toLowerCase().includes("demo") || String(confidence).toLowerCase().includes("bassa");
  return {
    confidence,
    reason,
    warning: demoLike || missing
      ? "Lo score resta indicativo: dati proxy, demo, incompleti o un periodo storico limitato possono sottostimare rischio, volatilità e perdita temporanea."
      : "",
  };
}

function cleanScoreNarrative(value, fallback) {
  const text = String(value || "").trim();
  if (!text) return fallback;
  if (text.toLowerCase().includes("alcune metriche disponibili nel piano non sono ancora presenti")) {
    return fallback;
  }
  return text;
}

function renderScoreComparisonSummary(selector, currentScore, comparisonScore, currentLabel = "Inserito", comparisonLabel = "Confronto") {
  const container = document.querySelector(selector);
  if (!container) return;
  if (!currentScore || !comparisonScore) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  const currentOverall = scoreValue(currentScore.overall);
  const comparisonOverall = scoreValue(comparisonScore.overall);
  const delta = comparisonOverall - currentOverall;
  const currentPillars = Object.fromEntries(healthScorePillars(currentScore).map((pillar) => [pillar.key, pillar]));
  const comparisonPillars = Object.fromEntries(healthScorePillars(comparisonScore).map((pillar) => [pillar.key, pillar]));
  const rows = [
    ["FitScore", currentOverall, comparisonOverall],
    ["Rischio reale", currentPillars.realRisk || currentPillars.baseRisk || currentPillars.advancedRisk, comparisonPillars.realRisk || comparisonPillars.baseRisk || comparisonPillars.advancedRisk],
    ["Efficienza", currentPillars.riskReturnEfficiency || currentPillars.baseEfficiency || currentPillars.advancedEfficiency, comparisonPillars.riskReturnEfficiency || comparisonPillars.baseEfficiency || comparisonPillars.advancedEfficiency],
    ["Diversificazione", currentPillars.diversification || currentPillars.baseDiversification || currentPillars.realDiversification, comparisonPillars.diversification || comparisonPillars.baseDiversification || comparisonPillars.realDiversification],
    ["Coerenza obiettivi", currentPillars.goalCoherence || currentPillars.baseCoherence || currentPillars.advancedGoalCoherence, comparisonPillars.goalCoherence || comparisonPillars.baseCoherence || comparisonPillars.advancedGoalCoherence],
  ].map(([label, current, comparison]) => {
    const currentValue = typeof current === "number" ? current : current?.score;
    const comparisonValue = typeof comparison === "number" ? comparison : comparison?.score;
    if (!Number.isFinite(Number(currentValue)) || !Number.isFinite(Number(comparisonValue))) return "";
    const rowDelta = scoreValue(comparisonValue) - scoreValue(currentValue);
    const reading = rowDelta > 3 ? "migliora" : rowDelta < -3 ? "peggiora" : "simile";
    return `
      <tr>
        <td>${escapeHtml(label)}</td>
        <td>${scoreValue(currentValue)}</td>
        <td>${scoreValue(comparisonValue)}</td>
        <td>${escapeHtml(reading)}</td>
      </tr>
    `;
  }).join("");
  const mainText = delta > 0
    ? `${escapeHtml(comparisonLabel)} migliora lo score da ${currentOverall} a ${comparisonOverall}.`
    : delta < 0
      ? `${escapeHtml(comparisonLabel)} ha uno score più basso di ${Math.abs(delta)} punti: il confronto va letto area per area.`
      : `${escapeHtml(comparisonLabel)} ha uno score simile al ${escapeHtml(currentLabel)}.`;
  container.innerHTML = `
    <div>
      <span>Confronto sintetico</span>
      <strong>${mainText}</strong>
      <p>Uno score più alto non significa automaticamente migliore in ogni area: la tabella mostra dove cambia davvero il profilo.</p>
    </div>
    <table>
      <thead><tr><th>Area</th><th>${escapeHtml(currentLabel)}</th><th>${escapeHtml(comparisonLabel)}</th><th>Lettura</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  container.hidden = false;
}

function renderScoreComparisonFromContext(selector, context) {
  const container = document.querySelector(selector);
  if (!container) return;
  const comparison = context?.scoreComparison || {};
  if (!comparison.available) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  const primaryLabel = context.primaryPortfolioName || "Portfolio principale";
  const comparisonLabel = context.comparisonPortfolioName || "Confronto";
  const rows = (comparison.pillars || [])
    .filter((row) => row.direction !== "not_available")
    .map((row) => `
      <tr>
        <td>${escapeHtml(row.name || "--")}</td>
        <td>${row.primary === null || row.primary === undefined ? "--" : scoreValue(row.primary)}</td>
        <td>${row.comparison === null || row.comparison === undefined ? "--" : scoreValue(row.comparison)}</td>
        <td>${escapeHtml({
          better: "migliora",
          worse: "peggiora",
          same: "simile",
        }[row.direction] || "non disponibile")}</td>
      </tr>
    `)
    .join("");
  container.innerHTML = `
    <div>
      <span>Confronto sintetico</span>
      <strong>${escapePlainFinance(comparison.summary || "Confronto disponibile.")}</strong>
      ${(comparison.tradeOffs || []).length ? `<p>${escapePlainFinance(comparison.tradeOffs.join(" "))}</p>` : `<p>La tabella mostra dove cambia davvero il profilo del portafoglio.</p>`}
    </div>
    <table>
      <thead><tr><th>Area</th><th>${escapeHtml(primaryLabel)}</th><th>${escapeHtml(comparisonLabel)}</th><th>Lettura</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  container.hidden = false;
}

function renderFinalAnalysisFromContext(context) {
  if (!context) return;
  renderFinalVerdictCard(context);
  const dashboard = context.monitoringDashboard || {};
  const actionRisk = document.querySelector("#action-risk");
  if (actionRisk) {
    actionRisk.textContent = dashboard.riskToMonitor || "Rischio non ancora calcolato";
    document.querySelector("#action-overweight").textContent = dashboard.dominantComponent || "Composizione non disponibile";
    document.querySelector("#action-scenario").textContent = dashboard.criticalScenario || "Scenario non attivo";
    document.querySelector("#action-next-check").textContent = dashboard.nextCheck || "Controllo mensile";
  }
  renderScoreComparisonFromContext("#analysis-score-comparison", context);
  setLaneTitle("#analysis-intelligence-current", context.primaryPortfolioName || "Portfolio principale", context.mainConclusion?.title || "Analisi finale");
  renderIntelligencePanel("#analysis-intelligence-current", {
    executiveSummary: context.mainConclusion?.summary,
    riskInsight: context.mainConclusion?.weakness,
    suggestedAction: context.mainConclusion?.watchOut,
    technicalExplanation: context.dataReliability?.reason,
    disclaimer: "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
  }, {
    healthScore: context.primaryHealthScore,
    healthScoreLabel: context.primaryPortfolioName || "Portfolio principale",
    scoreOnly: true,
  });
  const comparisonPanel = document.querySelector("#analysis-intelligence-optimized");
  const comparisonLane = comparisonPanel?.closest(".portfolio-lane");
  if (context.comparisonHealthScore) {
    if (comparisonPanel) comparisonPanel.hidden = false;
    if (comparisonLane) comparisonLane.hidden = false;
    setLaneTitle("#analysis-intelligence-optimized", context.comparisonPortfolioName || "Confronto", context.scoreComparison?.summary || "Confronto");
    renderIntelligencePanel("#analysis-intelligence-optimized", {
      executiveSummary: context.scoreComparison?.summary,
      riskInsight: (context.scoreComparison?.tradeOffs || []).join(" "),
      suggestedAction: context.improvementContext?.reason,
      technicalExplanation: context.dataReliability?.reason,
      disclaimer: "Questa analisi ha finalità educativa e informativa. Non costituisce consulenza finanziaria personalizzata né raccomandazione di investimento.",
    }, {
      healthScore: context.comparisonHealthScore,
      healthScoreLabel: context.comparisonPortfolioName || "Confronto",
      scoreOnly: true,
    });
  } else {
    if (comparisonPanel) {
      comparisonPanel.innerHTML = "";
      comparisonPanel.hidden = true;
    }
    if (comparisonLane) comparisonLane.hidden = true;
  }
  const finalText = document.querySelector("#analysis-text");
  if (finalText) {
    const reliability = context.dataReliability?.reason ? ` Affidabilità: ${context.dataReliability.reason}` : "";
    finalText.textContent = `${context.mainConclusion?.summary || ""}${reliability}`;
  }
  updateComparisonContextStrips();
}

function renderFinalVerdictCard(context) {
  const card = document.querySelector("#final-verdict-card");
  if (!card) return;
  const conclusion = context.mainConclusion || {};
  const reliability = context.dataReliability || {};
  card.hidden = false;
  const title = document.querySelector("#final-verdict-title");
  const summary = document.querySelector("#final-verdict-summary");
  const risk = document.querySelector("#final-verdict-risk");
  const strength = document.querySelector("#final-verdict-strength");
  const next = document.querySelector("#final-verdict-next");
  if (title) title.textContent = conclusion.title || "Verdetto sintetico";
  if (summary) {
    const prefix = context.scoreComparison?.available
      ? context.scoreComparison.summary
      : conclusion.summary;
    summary.textContent = plainFinanceLanguage(prefix || "La diagnosi sintetizza coerenza, rischio e prossimo passo usando i dati disponibili.");
  }
  if (risk) risk.textContent = plainFinanceLanguage(conclusion.weakness || context.monitoringDashboard?.riskToMonitor || "Da monitorare con i prossimi dati.");
  if (strength) strength.textContent = plainFinanceLanguage(conclusion.strength || "Coerenza e diversificazione da leggere nei dettagli.");
  if (next) next.textContent = plainFinanceLanguage(conclusion.watchOut || context.improvementContext?.reason || "Apri il piano di miglioramento o controlla il tracking mensile.");
  card.dataset.reliability = reliability.level || "medium";
}

function renderHealthScore(healthScore, title = "Check-up del portafoglio") {
  const labels = {
    diversification: "Diversificazione",
    riskReturnEfficiency: "Efficienza rischio/rendimento",
    goalCoherence: "Coerenza con obiettivi",
    scenarioRobustness: "Robustezza agli scenari",
    successProbability: "Probabilità di raggiungere l’obiettivo",
    baseRisk: "Rischio reale base",
    baseEfficiency: "Efficienza base",
    baseDiversification: "Diversificazione base",
    baseCoherence: "Coerenza base",
    realRisk: "Rischio reale",
    diversification: "Diversificazione",
    trackingRebalancing: "Tracking e ribilanciamento",
    advancedRisk: "Rischio reale avanzato",
    advancedEfficiency: "Efficienza avanzata",
    realDiversification: "Diversificazione reale",
    advancedGoalCoherence: "Coerenza obiettivi/PAC",
    factorScenarioRobustness: "Robustezza fattoriale",
  };
  const pillars = healthScorePillars(healthScore);
  const components = pillars.map((pillar) => `
    <div class="fit-pillar-card" data-status="${escapeHtml(pillar.status || "")}">
      <span>${escapeHtml(pillar.name || labels[pillar.key] || pillar.key)}</span>
      ${pillar.question ? `<em>${escapePlainFinance(pillar.question)}</em>` : ""}
      <strong>${Math.round(pillar.score || 0)}/100</strong>
      <small>${escapePlainFinance(pillar.interpretation || healthScore.details?.[pillar.key] || "")}</small>
      ${pillar.primaryMetric ? `<small><b>Dato usato:</b> ${escapePlainFinance(pillar.primaryMetric)}</small>` : ""}
      ${Number.isFinite(Number(pillar.impactPoints)) ? `<small class="score-impact"><b>Impatto:</b> ${escapeHtml(String(pillar.impactPoints))}/${escapeHtml(String(pillar.maxPoints || ""))} punti</small>` : ""}
    </div>
  `).join("");
  const lockedInsights = Array.isArray(healthScore.lockedInsights)
    ? healthScore.lockedInsights.slice(0, 4).map((item) => `
      <article class="locked-score-insight">
        <span>${escapeHtml(item.title || item.feature || "Analisi bloccata")}</span>
        <p>${escapePlainFinance(item.message || "")}</p>
        <small>Sbloccabile con ${escapeHtml(item.requiredPlan || "piano superiore")}</small>
      </article>
    `).join("")
    : "";
  const technicalRows = [
    ["Metriche usate", (healthScore.metricsUsed || []).join(", ") || "--"],
    ["Metriche escluse dal piano", (healthScore.metricsExcludedByPlan || []).join(", ") || "--"],
    ["Metriche mancanti", (healthScore.metricsMissing || []).join(", ") || "--"],
    ["Formula usata", Object.entries(healthScore.scoreFormula || {}).map(([key, value]) => `${key}: ${value} punti`).join(" | ") || "--"],
    ["Cap applicati", (healthScore.capsApplied || []).map((item) => item.reason).join(" | ") || "--"],
    ["Limite", healthScore.technicalDisclaimer || ""],
  ].map(([label, value]) => `<li><b>${escapeHtml(label)}:</b> ${escapePlainFinance(value)}</li>`).join("");
  const strongest = strongestHealthPillar(healthScore);
  const weakest = weakestHealthPillar(healthScore);
  const displayLabel = buildHealthScoreDisplayLabel({
    score: healthScore.overall,
    reliability: healthScore.confidence,
    equityExposure: healthScore.equityExposure,
    diversificationScore: diversificationScoreFromHealth(healthScore),
    maxDrawdown: healthScore.maxDrawdown,
    maxTemporaryLoss: healthScore.maxTemporaryLoss,
    isDemoData: String(healthScore.confidenceReason || "").toLowerCase().includes("demo"),
    dataYears: healthScore.dataYears,
  });
  const reliability = buildHealthReliabilityText(healthScore);
  const narrativeCards = [
    [
      "Cosa funziona",
      cleanScoreNarrative(
        healthScore.whatWorks,
        strongest
          ? `${strongest.name} è l’area più solida, con ${scoreValue(strongest.score)}/100.`
          : "Il sistema non ha ancora abbastanza dati per isolare un punto di forza."
      ),
      strongest?.primaryMetric || strongest?.name || "",
    ],
    [
      "Cosa penalizza",
      cleanScoreNarrative(
        healthScore.whatPenalizes,
        weakest
          ? `${weakest.name} è l’area più debole, con ${scoreValue(weakest.score)}/100.`
          : "Non emergono penalizzazioni esplicite dalle metriche disponibili."
      ),
      weakest?.primaryMetric || weakest?.name || "",
    ],
    [
      "Cosa monitorare",
      cleanScoreNarrative(
        healthScore.whatToMonitor,
        "Controlla nel tempo perdita temporanea, concentrazione e reazione agli scenari negativi."
      ),
      "Controllo periodico",
    ],
  ].filter(([, value]) => value).map(([label, value, metric]) => `
    <article>
      <span>${escapeHtml(label)}</span>
      <p>${escapePlainFinance(value)}</p>
      ${metric ? `<small>${escapePlainFinance(metric)}</small>` : ""}
    </article>
  `).join("");
  const reducingFactors = Array.isArray(healthScore.scoreReducingFactors) && healthScore.scoreReducingFactors.length
    ? healthScore.scoreReducingFactors.slice(0, 5).map((item) => `
      <li>
        <span>${escapePlainFinance(item.label || "")}</span>
        ${item.impact ? `<small>${escapePlainFinance(item.impact)}</small>` : ""}
      </li>
    `).join("")
    : "";
  const warningBadges = [
    Number(healthScore.equityExposure || 0) >= 0.9
      ? ["Molto esposto all’azionario", "Il portafoglio dipende quasi interamente dalla componente azionaria. Questo può aumentare la sensibilità alle fasi negative di mercato."]
      : null,
    reliability.warning
      ? [String(reliability.confidence).toLowerCase().includes("bassa") ? "Dati da leggere con cautela" : "Periodo o dati limitati", reliability.warning]
      : null,
  ].filter(Boolean).map(([label, text]) => `
    <article class="score-context-warning">
      <span>${escapeHtml(label)}</span>
      <p>${escapePlainFinance(text)}</p>
    </article>
  `).join("");
  return `
    <section class="health-score-panel fit-score-panel">
      <div class="fit-score-head">
        <div class="fit-score-main">
          <span>${escapeHtml(healthScore.scoreType || title)}</span>
          <strong><b>${Math.round(healthScore.overall || 0)}</b><small>/100</small></strong>
          <small>${escapePlainFinance(displayLabel)} · Piano ${escapeHtml(healthScore.plan || currentPlan())}</small>
        </div>
        <div class="fit-score-summary-copy">
          <strong>${escapePlainFinance(healthScore.label || displayLabel || "")}</strong>
          <p>${escapePlainFinance(healthScore.diagnosis || healthScore.confidenceReason || "")}</p>
          <ul>
            <li><b>Rischio principale:</b> ${escapePlainFinance(weakest?.name || "da monitorare con i prossimi dati")}</li>
            <li><b>Punto forte:</b> ${escapePlainFinance(strongest?.name || "non ancora isolato")}</li>
            <li><b>Da monitorare:</b> ${escapePlainFinance(healthScore.whatToMonitor || "completezza dei dati e concentrazione")}</li>
          </ul>
        </div>
      </div>
      <div class="score-confidence-note">
        <strong>Affidabilità: ${escapeHtml(reliability.confidence)}</strong>
        <span><b>Come leggerla:</b> ${escapePlainFinance(reliability.reason)}</span>
        ${reliability.warning ? `<small>${escapePlainFinance(reliability.warning)}</small>` : ""}
      </div>
      ${warningBadges ? `<div class="score-context-warning-grid">${warningBadges}</div>` : ""}
      ${narrativeCards ? `
        <div class="score-reading-section">
          <div>
            <span>Come leggere questo score</span>
            <p>Prima guarda sintesi, penalizzazione e area da monitorare. I dettagli numerici sono sotto, nel pannello tecnico.</p>
          </div>
          <div class="score-narrative-grid">${narrativeCards}</div>
        </div>
      ` : ""}
      ${healthScore.mainTradeoff ? `
        <article class="optimization-tradeoff-card">
          <span>Compromesso principale</span>
          <p>${escapePlainFinance(healthScore.mainTradeoff)}</p>
        </article>
      ` : ""}
      ${lockedInsights ? `<div class="locked-score-grid">${lockedInsights}</div>` : ""}
      <details class="technical-score-details">
        <summary>Dettaglio tecnico dello score</summary>
        <div class="health-components">${components}</div>
        ${reducingFactors ? `
          <article class="score-reducing-factors">
            <span>Fattori che hanno ridotto lo score</span>
            <ul>${reducingFactors}</ul>
          </article>
        ` : ""}
        <ul>${technicalRows}</ul>
        <p class="score-disclaimer">Il Portfolio FitScore è una diagnosi quantitativa educativa basata sui dati disponibili. Non costituisce consulenza finanziaria personalizzata né raccomandazione di acquisto o vendita.</p>
      </details>
    </section>
  `;
}

function aiDetailCacheKey(payload) {
  return JSON.stringify(payload || {});
}

function applyAiDetailToPanel(selector, result) {
  const panel = selector ? document.querySelector(selector) : null;
  if (!panel || !result) return;
  const detail = result.detail || {};
  const hasOpenAiDetail = result.aiStatus === "openai";
  const setField = (field, value) => {
    const node = panel.querySelector(`[data-ai-field="${field}"]`);
    if (!node) return;
    if (field === "what_to_look_at") {
      node.textContent = completeWhatToWatch(value || "--");
    } else if (field === "possible_improvement") {
      node.outerHTML = renderPossibleImprovementGuidance(value || "--");
    } else {
      node.textContent = completeFinanceComment(value || "--");
    }
  };
  if (hasOpenAiDetail) {
    setField("what_the_numbers_mean", detail.what_the_numbers_mean || detail.meaning);
    setField("what_to_look_at", detail.what_to_look_at || detail.what_to_watch);
    setField("possible_improvement", detail.possible_improvement);
  }
  const button = panel.querySelector(".ai-detail-button");
  if (button) {
    button.disabled = false;
    button.textContent = "Mostra dettagli tecnici";
  }
}

async function fetchAiDetailForPanel(selector) {
  const payload = explanationDetailPayloads.get(selector);
  if (!payload) return null;
  const cacheKey = aiDetailCacheKey(payload);
  if (explanationDetailCache.has(cacheKey)) return explanationDetailCache.get(cacheKey);
  if (explanationDetailInFlight.has(cacheKey)) return explanationDetailInFlight.get(cacheKey);
  const request = fetch("/api/ai/explanation-detail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (response) => {
      const result = await response.json();
      if (!response.ok) throw new Error(result.openaiError || result.error || "Commento OpenAI non disponibile.");
      explanationDetailCache.set(cacheKey, result);
      return result;
    })
    .finally(() => explanationDetailInFlight.delete(cacheKey));
  explanationDetailInFlight.set(cacheKey, request);
  return request;
}

async function loadAiPanelCards(selector) {
  const payload = explanationDetailPayloads.get(selector);
  const panel = selector ? document.querySelector(selector) : null;
  if (!payload || !panel) return;
  try {
    const result = await fetchAiDetailForPanel(selector);
    applyAiDetailToPanel(selector, result);
  } catch (error) {
    panel.querySelectorAll("[data-ai-field]").forEach((node) => {
      if (node.textContent.includes("Analisi in preparazione") || node.textContent.includes("Il sistema sta") || node.textContent.includes("Stiamo ")) {
        node.textContent = "Commento OpenAI non disponibile: usa i dati della sezione oppure riprova dopo.";
      }
    });
    const button = panel.querySelector(".ai-detail-button");
    if (button) {
      button.disabled = false;
      button.textContent = "Mostra dettagli tecnici";
    }
  }
}

function renderTechnicalDetailOutput({ mainStrength, mainWeakness, technicalNote, disclaimer, openaiError = "" }) {
  return `
    ${openaiError ? `
      <article class="intelligence-lead">
        <span>Dettagli tecnici locali</span>
        <p>${escapeHtml(openaiError)}</p>
      </article>
    ` : ""}
    <div class="intelligence-footer technical-detail-summary">
      <span><b>Punto di forza:</b> ${escapePlainFinance(mainStrength || "--")}</span>
      <span><b>Punto di debolezza:</b> ${escapePlainFinance(mainWeakness || "--")}</span>
      ${disclaimer ? `<span><b>Nota:</b> ${escapeHtml(disclaimer)}</span>` : ""}
    </div>
    <details class="technical-details">
      <summary>Dato tecnico</summary>
      <p>${escapeHtml(technicalNote || "--")}</p>
    </details>
  `;
}

function isPanelVisibleForCurrentStep(selector) {
  const panel = document.querySelector(selector);
  if (!panel) return false;
  const stepContainer = panel.closest(".result-step, .step-panel");
  if (!stepContainer) return !panel.hidden;
  return stepContainer.classList.contains("active") && !stepContainer.classList.contains("plan-hidden");
}

function loadVisibleAiPanels() {
  [...pendingAiPanelSelectors].forEach((selector) => {
    if (!isPanelVisibleForCurrentStep(selector)) return;
    pendingAiPanelSelectors.delete(selector);
    loadAiPanelCards(selector);
  });
}

async function loadAiDetail(button) {
  const selector = button.dataset.panelSelector;
  const panel = selector ? document.querySelector(selector) : null;
  const output = panel?.querySelector(".ai-detail-output");
  if (!output) return;
  if (!output.hidden) {
    output.hidden = true;
    button.textContent = "Mostra dettagli tecnici";
    return;
  }
  const localDetail = localPanelTechnicalDetails.get(selector) || {};
  const payload = explanationDetailPayloads.get(selector);
  if (!payload) {
    output.hidden = false;
    output.innerHTML = renderTechnicalDetailOutput(localDetail);
    button.disabled = false;
    button.textContent = "Nascondi dettagli tecnici";
    return;
  }
  button.disabled = true;
  button.textContent = "Elaborazione in corso...";
  output.hidden = false;
  output.innerHTML = `<div class="loading-note">Stiamo preparando il dettaglio tecnico della sezione.</div>`;
  try {
    const result = await fetchAiDetailForPanel(selector);
    const detail = result.detail || {};
    const technicalFallback = result.technicalFallback || {};
    const technicalNote = technicalFallback.technical_note || detail.technical_note || detail.technical_detail || "--";
    const disclaimer = detail.disclaimer || technicalFallback.disclaimer || "";
    applyAiDetailToPanel(selector, result);
    output.innerHTML = renderTechnicalDetailOutput({
      mainStrength: detail.main_strength || localDetail.mainStrength,
      mainWeakness: detail.main_weakness || detail.main_risk || localDetail.mainWeakness,
      technicalNote,
      disclaimer,
      openaiError: result.aiStatus === "openai"
        ? ""
        : result.openaiError || "OpenAI non disponibile. Il dettaglio tecnico resta calcolato localmente.",
    });
    button.disabled = false;
    button.textContent = "Nascondi dettagli tecnici";
  } catch (error) {
    output.innerHTML = renderTechnicalDetailOutput({
      ...localDetail,
      openaiError: error.message || "OpenAI non disponibile. Il dettaglio tecnico resta calcolato localmente.",
    });
    button.disabled = false;
    button.textContent = "Nascondi dettagli tecnici";
  }
}

function unavailableCard(message) {
  return `
    <article>
      <span>Non disponibile</span>
      <strong>--</strong>
      <small>${escapeHtml(message || "Esegui la frontiera efficiente per calcolare il portfolio ottimizzato.")}</small>
    </article>
  `;
}

function stressAwaitingCard(message = "Seleziona uno scenario e premi Calcola crisi simulate.") {
  return `
    <article>
      <span>In attesa</span>
      <strong>--</strong>
      <small>${escapeHtml(message)}</small>
    </article>
  `;
}

function renderOptimizationAsSecondary(result) {
  const frontierSection = document.querySelector("#frontier-results");
  if (!frontierSection) return;
  const isStandardBenchmark = result?.analysisComparisonContext?.comparisonMode === "standard_benchmark";
  const isStandardOnly = result?.analysisComparisonContext?.comparisonMode === "standard_only";
  let note = frontierSection.querySelector(".optional-optimization-note");
  if (!note) {
    note = document.createElement("div");
    note.className = "optional-optimization-note";
    const title = frontierSection.querySelector(".table-title");
    title?.insertAdjacentElement("afterend", note);
  }
  if (isStandardBenchmark) {
    note.hidden = false;
    note.innerHTML = `
      <strong>Ottimizzazione non calcolata</strong>
      <p>Hai scelto un portfolio benchmark standard QuantInvest. In questa analisi QuantInvest confronta il portfolio inserito con lo standard selezionato e non calcola un portfolio ottimale.</p>
    `;
  } else if (isStandardOnly) {
    note.hidden = false;
    note.innerHTML = `
      <strong>Analisi solo portfolio standard</strong>
      <p>Hai scelto di analizzare un portfolio standard QuantInvest con i pesi indicati. In questa modalità QuantInvest non calcola un portfolio standard ottimale.</p>
    `;
  } else {
    note.hidden = true;
    note.innerHTML = "";
  }
}

function renderComparisonBacktest(comparisonPortfolio, label = "Backtest ottimizzato") {
  const container = document.querySelector("#optimized-backtest-metrics");
  if (!container) return;
  if (!comparisonPortfolio?.available || !comparisonPortfolio.metrics) {
    container.innerHTML = unavailableCard(comparisonPortfolio?.reason || "Benchmark selezionato non disponibile per il backtest.");
    return;
  }
  const metrics = comparisonPortfolio.metrics;
  container.innerHTML = `
    <article>
      <span>Valore finale</span>
      <strong>${euro.format(metrics.finalValue)}</strong>
      <small>${escapeHtml(label || "confronto")}</small>
    </article>
    <article>
      <span>Crescita media annua storica simulata</span>
      <strong>${formatPercent(metrics.cagr)}</strong>
      <small>rendimento annuo composto</small>
    </article>
    <article>
      <span>Peggiore perdita storica</span>
      <strong>${formatPercent(metrics.maxDrawdown)}</strong>
      <small>peggiore fase storica</small>
    </article>
    <article>
      <span>Il rendimento compensa il rischio?</span>
      <strong>${metrics.sharpe === null ? "--" : number.format(metrics.sharpe)}</strong>
      <small>rendimento/rischio</small>
    </article>
  `;
}

function renderEfficientFrontier(frontier) {
  const container = document.querySelector("#frontier-summary-grid");
  const currentContainer = document.querySelector("#current-frontier-grid");
  const weightsBody = document.querySelector("#frontier-weights-body");
  const chart = document.querySelector("#frontier-chart");
  if (isLocked(frontier)) {
    container.innerHTML = lockedFeatureCard("efficientFrontier", frontier);
    if (currentContainer) currentContainer.innerHTML = lockedFeatureCard("efficientFrontier", frontier);
    renderFrontierDelta(null);
    weightsBody.innerHTML = `<tr><td colspan="4">Sblocca Plus per vedere il portfolio ottimizzato.</td></tr>`;
    chart.innerHTML = `<text x="430" y="210" text-anchor="middle" font-size="16" fill="#657080">Efficient Frontier disponibile dal piano Plus</text>`;
    return;
  }
  if (!frontier || !frontier.available) {
    container.innerHTML = `
      <article>
        <span>Combinazione più efficiente</span>
        <strong>--</strong>
        <small>${frontier?.reason || "Non disponibile"}</small>
      </article>
    `;
    if (currentContainer) currentContainer.innerHTML = unavailableCard(frontier?.reason || "Dati insufficienti.");
    renderFrontierDelta(null, frontier?.reason || "Servono almeno due asset con dati comuni.");
    weightsBody.innerHTML = `<tr><td colspan="4">Servono almeno 2 asset.</td></tr>`;
    chart.innerHTML = "";
    return;
  }

  if (currentContainer) {
    currentContainer.innerHTML = `
      <article>
        <span>Crescita media annua storica simulata</span>
        <strong>${formatPercent(frontier.current.return)}</strong>
        <small>portfolio inserito</small>
      </article>
      <article>
        <span>Oscillazione media annua</span>
        <strong>${formatPercent(frontier.current.risk)}</strong>
        <small>rischio storico</small>
      </article>
      <article>
        <span>Efficienza rischio-rendimento</span>
        <strong>${number.format(frontier.current.sharpe)}</strong>
        <small>efficienza attuale</small>
      </article>
    `;
  }

  container.innerHTML = `
    <article>
      <span>Migliore efficienza</span>
      <strong>${number.format(frontier.best.sharpe)}</strong>
      <small>portafoglio simulato</small>
      <button class="explain-button" type="button" aria-expanded="false">Spiegami</button>
      <div class="explain-cloud" hidden>È il portafoglio della simulazione con il miglior rapporto tra rendimento atteso e rischio. Cerca una combinazione più efficiente, non una previsione certa.</div>
    </article>
    <article>
      <span>Crescita media annua storica simulata</span>
      <strong>${formatPercent(frontier.best.return)}</strong>
      <small>atteso da storico</small>
    </article>
    <article>
      <span>Oscillazione media annua</span>
      <strong>${formatPercent(frontier.best.risk)}</strong>
      <small>deviazione standard</small>
    </article>
    <article>
      <span>Vincoli ottimizzazione</span>
      <strong>${(frontier.constraints || []).some((item) => item.minWeight > 0 || item.maxWeight < 1) ? "Attivi" : "Liberi"}</strong>
      <small>min/max per asset</small>
    </article>
  `;
  const symbols = Array.isArray(frontier.symbols) ? frontier.symbols : [];
  const bestWeights = Array.isArray(frontier.best?.weights) ? frontier.best.weights : [];
  const constraints = Array.isArray(frontier.constraints) ? frontier.constraints : [];
  weightsBody.innerHTML = symbols
    .map((symbol, index) => {
      const constraint = constraints.find((item) => item.symbol === symbol) || {};
      return `
        <tr>
          <td>${escapeHtml(displayAnalysisInstrumentName(symbol))}</td>
          <td>${formatPercent(bestWeights[index])}</td>
          <td>${formatPercent(constraint.minWeight ?? 0)}</td>
          <td>${formatPercent(constraint.maxWeight ?? 1)}</td>
        </tr>
      `;
    })
    .join("");
  drawFrontierChart(frontier);
  renderFrontierDelta(frontier);
}

function renderFrontierDelta(frontier, message = "") {
  const container = document.querySelector("#frontier-delta-card");
  if (!container) return;
  if (!frontier?.available || !frontier.current || !frontier.best) {
    container.innerHTML = `
      <span>Differenza principale</span>
      <strong>${escapeHtml(message || "Calcola la frontiera per confrontare rischio e rendimento.")}</strong>
      <small>Il confronto apparirà qui prima del grafico.</small>
    `;
    return;
  }
  const returnDelta = frontier.best.return - frontier.current.return;
  const riskDelta = frontier.best.risk - frontier.current.risk;
  const efficiencyDelta = frontier.best.sharpe - frontier.current.sharpe;
  const riskDirection = riskDelta <= 0 ? "riduce" : "aumenta";
  container.innerHTML = `
    <span>Differenza principale</span>
    <strong>Il portfolio efficiente ${riskDirection} l’oscillazione di ${formatPercent(Math.abs(riskDelta))} e cambia la crescita media annua di ${formatPercent(returnDelta)}.</strong>
    <small>Delta rapporto rischio-rendimento: ${efficiencyDelta >= 0 ? "+" : ""}${number.format(efficiencyDelta)}. Valuta se il miglioramento è rilevante rispetto al tuo profilo.</small>
  `;
}

function drawFrontierChart(frontier) {
  const chart = document.querySelector("#frontier-chart");
  const width = 860;
  const height = 420;
  const pad = { left: 70, right: 28, top: 24, bottom: 56 };
  const simulatedPoints = Array.isArray(frontier.points) ? frontier.points : [];
  const frontierLine = Array.isArray(frontier.frontier) ? frontier.frontier : [];
  const bestPoint = frontier.best;
  const currentPoint = frontier.current;
  const allItems = [...simulatedPoints, ...frontierLine, bestPoint, currentPoint].filter(
    (item) => item && Number.isFinite(item.risk) && Number.isFinite(item.return),
  );
  if (!allItems.length || !bestPoint || !currentPoint) {
    chart.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle" font-size="16" fill="#657080">Dati insufficienti per il grafico</text>`;
    return;
  }
  const risks = allItems.map((item) => item.risk);
  const returns = allItems.map((item) => item.return);
  const xMin = Math.min(...risks) * 0.92;
  const xMax = Math.max(...risks) * 1.08;
  const yMin = Math.min(...returns) * 0.92;
  const yMax = Math.max(...returns) * 1.08;
  const x = (value) => pad.left + ((value - xMin) / (xMax - xMin || 1)) * (width - pad.left - pad.right);
  const y = (value) => height - pad.bottom - ((value - yMin) / (yMax - yMin || 1)) * (height - pad.top - pad.bottom);
  const ticks = (min, max, count) => Array.from({ length: count }, (_, index) => min + ((max - min) / Math.max(count - 1, 1)) * index);

  const grid = [
    ...ticks(xMin, xMax, 6).map((tick) => `
      <line x1="${x(tick).toFixed(2)}" y1="${pad.top}" x2="${x(tick).toFixed(2)}" y2="${height - pad.bottom}" stroke="#e4ebe8" />
      <text x="${x(tick).toFixed(2)}" y="${height - 22}" text-anchor="middle" font-size="12" fill="#657080">${formatPercent(tick)}</text>
    `),
    ...ticks(yMin, yMax, 5).map((tick) => `
      <line x1="${pad.left}" y1="${y(tick).toFixed(2)}" x2="${width - pad.right}" y2="${y(tick).toFixed(2)}" stroke="#e4ebe8" />
      <text x="${pad.left - 10}" y="${(y(tick) + 4).toFixed(2)}" text-anchor="end" font-size="12" fill="#657080">${formatPercent(tick)}</text>
    `),
  ].join("");

  const points = simulatedPoints
    .map((item) => `<circle cx="${x(item.risk).toFixed(2)}" cy="${y(item.return).toFixed(2)}" r="2" fill="#7d928c" opacity="0.32" />`)
    .join("");
  const line = frontierLine
    .map((item) => `${x(item.risk).toFixed(2)},${y(item.return).toFixed(2)}`)
    .join(" ");
  const currentX = x(currentPoint.risk);
  const currentY = y(currentPoint.return);
  const bestX = x(bestPoint.risk);
  const bestY = y(bestPoint.return);
  const riskDelta = bestPoint.risk - currentPoint.risk;
  const returnDelta = bestPoint.return - currentPoint.return;
  const arrowLabel = riskDelta < 0 && returnDelta >= 0
    ? "meno rischio, rendimento simile/migliore"
    : riskDelta < 0
      ? "meno rischio"
      : returnDelta > 0
        ? "più rendimento, più oscillazione"
        : "miglioramento marginale";

  chart.innerHTML = `
    <defs>
      <marker id="frontier-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
        <path d="M0,0 L0,6 L9,3 z" fill="#5b5aa8" />
      </marker>
    </defs>
    ${grid}
    <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" stroke="#9ca7a4" />
    <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" stroke="#9ca7a4" />
    ${points}
    <polyline points="${line}" fill="none" stroke="#18794e" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />
    <line x1="${currentX.toFixed(2)}" y1="${currentY.toFixed(2)}" x2="${bestX.toFixed(2)}" y2="${bestY.toFixed(2)}" stroke="#5b5aa8" stroke-width="3" stroke-dasharray="7 5" marker-end="url(#frontier-arrow)" />
    <rect x="${Math.min(currentX, bestX) + Math.abs(bestX - currentX) / 2 - 104}" y="${Math.min(currentY, bestY) - 30}" width="208" height="24" rx="8" fill="#ffffff" stroke="#d7d6f4" />
    <text x="${Math.min(currentX, bestX) + Math.abs(bestX - currentX) / 2}" y="${Math.min(currentY, bestY) - 14}" text-anchor="middle" font-size="12" font-weight="800" fill="#5b5aa8">${arrowLabel}</text>
    <circle cx="${bestX.toFixed(2)}" cy="${bestY.toFixed(2)}" r="8" fill="#c13e3e" stroke="#ffffff" stroke-width="2">
      <title>Portfolio efficiente · rendimento ${formatPercent(bestPoint.return)} · oscillazione ${formatPercent(bestPoint.risk)}</title>
    </circle>
    <circle cx="${currentX.toFixed(2)}" cy="${currentY.toFixed(2)}" r="8" fill="#2f66c5" stroke="#ffffff" stroke-width="2">
      <title>Portfolio inserito · rendimento ${formatPercent(currentPoint.return)} · oscillazione ${formatPercent(currentPoint.risk)}</title>
    </circle>
    <text x="${(bestX + 12).toFixed(2)}" y="${(bestY - 10).toFixed(2)}" font-size="12" font-weight="800" fill="#c13e3e">efficiente</text>
    <text x="${(currentX + 12).toFixed(2)}" y="${(currentY + 20).toFixed(2)}" font-size="12" font-weight="800" fill="#2f66c5">inserito</text>
    <text x="${width / 2}" y="${height - 8}" text-anchor="middle" font-size="13" font-weight="700" fill="#34403d">Oscillazione annua</text>
    <text x="18" y="${height / 2}" text-anchor="middle" font-size="13" font-weight="700" fill="#34403d" transform="rotate(-90 18 ${height / 2})">Crescita annua</text>
  `;
}

function drawBarChart(canvasId, items, labelKey, valueKey) {
  const canvas = document.querySelector(canvasId);
  if (!canvas) return;
  ChartLite.clearTooltip(canvas);
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const pad = { left: 70, right: 20, top: 24, bottom: 44 };
  const sortedItems = [...(items || [])].sort((a, b) => (a[valueKey] || 0) - (b[valueKey] || 0));
  context.clearRect(0, 0, width, height);
  if (!sortedItems.length) {
    context.fillStyle = "#657080";
    context.font = "16px system-ui";
    context.textAlign = "center";
    context.fillText("In attesa", width / 2, height / 2);
    return;
  }
  const values = sortedItems.map((item) => item[valueKey]);
  const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.01);
  const zeroY = pad.top + (height - pad.top - pad.bottom) / 2;
  const slot = (width - pad.left - pad.right) / sortedItems.length;
  const barWidth = Math.max(16, slot * 0.58);
  context.strokeStyle = "#d9e1e8";
  context.beginPath();
  context.moveTo(pad.left, zeroY);
  context.lineTo(width - pad.right, zeroY);
  context.stroke();
  const tooltipPoints = [];
  sortedItems.forEach((item, index) => {
    const value = item[valueKey];
    const x = pad.left + slot * index + (slot - barWidth) / 2;
    const barHeight = Math.abs(value) / maxAbs * ((height - pad.top - pad.bottom) / 2);
    const y = value >= 0 ? zeroY - barHeight : zeroY;
    context.fillStyle = value >= 0 ? "#18794e" : "#c13e3e";
    context.fillRect(x, y, barWidth, barHeight);
    context.fillStyle = "#657080";
    context.font = "12px system-ui";
    context.textAlign = "center";
    const label = labelKey === "symbol" ? displayAnalysisInstrumentName(item) : String(item[labelKey]);
    const shortLabel = ChartLite.shortLabel(label, 16);
    context.fillText(shortLabel, x + barWidth / 2, height - 16);
    const impact = valueKey.includes("contribution") ? ChartLite.impactLabel(value) : formatPercent(value);
    context.fillText(impact, x + barWidth / 2, value >= 0 ? y - 6 : y + barHeight + 16);
    tooltipPoints.push({
      x: x + barWidth / 2,
      y: value >= 0 ? y : y + barHeight,
      html: `
        <b>${escapeHtml(label)}</b>
        <span>Impatto: ${escapeHtml(impact)}</span>
        <span>${value >= 0 ? "Contributo difensivo/positivo" : "Contributo negativo"}</span>
      `,
    });
  });
  ChartLite.attachTooltip(canvas, tooltipPoints);
}

function drawRollingChart(selector, rollingData) {
  const canvas = document.querySelector(selector);
  if (!canvas) return;
  ChartLite.clearTooltip(canvas);
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const pad = { left: 70, right: 22, top: 24, bottom: 42 };
  const points = Array.isArray(rollingData?.points) ? rollingData.points : [];
  context.clearRect(0, 0, width, height);
  if (!points.length) {
    drawEmptyChart(canvas, rollingData?.reason || "Rolling return in attesa");
    return;
  }
  const values = points.map((item) => item.rollingReturn || 0);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 0.01;
  const x = (index) => pad.left + (index / Math.max(1, points.length - 1)) * (width - pad.left - pad.right);
  const y = (value) => pad.top + ((max - value) / range) * (height - pad.top - pad.bottom);
  const zeroY = y(0);
  const minIndex = values.indexOf(Math.min(...values));
  const maxIndex = values.indexOf(Math.max(...values));
  context.strokeStyle = "#d9e1e8";
  context.beginPath();
  context.moveTo(pad.left, zeroY);
  context.lineTo(width - pad.right, zeroY);
  context.stroke();
  context.beginPath();
  points.forEach((point, index) => {
    const xx = x(index);
    const yy = y(Math.min(point.rollingReturn || 0, 0));
    if (index === 0) context.moveTo(xx, zeroY);
    context.lineTo(xx, yy);
  });
  for (let index = points.length - 1; index >= 0; index -= 1) {
    context.lineTo(x(index), zeroY);
  }
  context.closePath();
  context.fillStyle = "rgba(193, 62, 62, 0.1)";
  context.fill();
  context.beginPath();
  points.forEach((point, index) => {
    const xx = x(index);
    const yy = y(point.rollingReturn || 0);
    if (index === 0) context.moveTo(xx, yy);
    else context.lineTo(xx, yy);
  });
  context.strokeStyle = "#2f66c5";
  context.lineWidth = 3;
  context.stroke();
  [
    { index: minIndex, color: "#c13e3e", label: "minimo" },
    { index: maxIndex, color: "#18794e", label: "massimo" },
  ].forEach((marker) => {
    const point = points[marker.index];
    if (!point) return;
    context.beginPath();
    context.fillStyle = marker.color;
    context.arc(x(marker.index), y(point.rollingReturn || 0), 6, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = "#fff";
    context.lineWidth = 2;
    context.stroke();
    context.fillStyle = marker.color;
    context.font = "12px system-ui";
    context.textAlign = "center";
    context.fillText(marker.label, x(marker.index), y(point.rollingReturn || 0) - 10);
  });
  context.fillStyle = "#657080";
  context.font = "12px system-ui";
  context.textAlign = "right";
  context.fillText(formatPercent(max), pad.left - 8, pad.top + 4);
  context.fillText(formatPercent(min), pad.left - 8, height - pad.bottom + 4);
  context.textAlign = "center";
  const windowLabel = rollingData.windowLabel || `${rollingData.window || 252} giorni`;
  context.fillText(`Rolling ${windowLabel}`, width / 2, height - 12);
  ChartLite.attachTooltip(canvas, points.map((point, index) => ({
    x: x(index),
    y: y(point.rollingReturn || 0),
    html: `
      <b>${escapeHtml(point.date || `Punto ${index + 1}`)}</b>
      <span>Rendimento rolling: ${formatPercent(point.rollingReturn || 0)}</span>
      ${index === minIndex ? "<span>Minimo del periodo selezionato</span>" : ""}
      ${index === maxIndex ? "<span>Massimo del periodo selezionato</span>" : ""}
    `,
  })));
}

function drawMonteCarloChart(monteCarlo, selector = "#monte-carlo-chart") {
  const canvas = document.querySelector(selector);
  if (!canvas) return;
  ChartLite.clearTooltip(canvas);
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const pad = { left: 82, right: 34, top: 34, bottom: 58 };
  const projection = Array.isArray(monteCarlo.projection) ? monteCarlo.projection : [];
  context.clearRect(0, 0, width, height);
  if (!projection.length) {
    drawEmptyChart(canvas, "Proiezione Monte Carlo in attesa");
    return;
  }
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const values = projection.flatMap((item) => [item.p5, item.median, item.mean, item.p95, monteCarlo.initialCapital]);
  const min = Math.min(...values) * 0.96;
  const max = Math.max(...values) * 1.04;
  const lastYear = Math.max(...projection.map((item) => item.year), 1);
  const x = (year) => pad.left + (year / lastYear) * plotWidth;
  const y = (value) => pad.top + (1 - (value - min) / (max - min || 1)) * plotHeight;
  const initialY = y(monteCarlo.initialCapital);

  context.strokeStyle = "#d9e1e8";
  context.lineWidth = 1;
  context.beginPath();
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (i / 4) * plotHeight;
    context.moveTo(pad.left, y);
    context.lineTo(width - pad.right, y);
  }
  context.stroke();

  context.fillStyle = "#657080";
  context.font = "12px system-ui";
  context.textAlign = "right";
  for (let i = 0; i <= 4; i += 1) {
    const tick = max - (i / 4) * (max - min);
    context.fillText(euro.format(tick), pad.left - 10, pad.top + (i / 4) * plotHeight + 4);
  }

  context.fillStyle = "rgba(193, 62, 62, 0.08)";
  context.fillRect(pad.left, initialY, plotWidth, height - pad.bottom - initialY);
  context.fillStyle = "#c13e3e";
  context.font = "12px system-ui";
  context.textAlign = "left";
  context.fillText("area sotto capitale iniziale", pad.left + 8, Math.min(height - pad.bottom - 8, initialY + 18));

  const band = [
    ...projection.map((item) => [x(item.year), y(item.p95)]),
    ...projection.slice().reverse().map((item) => [x(item.year), y(item.p5)]),
  ];
  context.beginPath();
  band.forEach(([xx, yy], index) => {
    if (index === 0) context.moveTo(xx, yy);
    else context.lineTo(xx, yy);
  });
  context.closePath();
  context.fillStyle = "rgba(47, 102, 197, 0.12)";
  context.fill();

  function strokePath(key, color, width = 3, dash = []) {
    context.save();
    context.strokeStyle = color;
    context.lineWidth = width;
    context.setLineDash(dash);
    context.beginPath();
    projection.forEach((item, index) => {
      const xx = x(item.year);
      const yy = y(item[key]);
      if (index === 0) context.moveTo(xx, yy);
      else context.lineTo(xx, yy);
    });
    context.stroke();
    context.restore();
  }

  strokePath("p5", "#c13e3e", 2, [6, 5]);
  strokePath("median", "#2f66c5", 4);
  strokePath("mean", "#18794e", 3, [2, 5]);
  strokePath("p95", "#18794e", 2, [6, 5]);

  context.strokeStyle = "#9ca7a4";
  context.lineWidth = 2.5;
  context.setLineDash([5, 5]);
  context.beginPath();
  context.moveTo(pad.left, initialY);
  context.lineTo(width - pad.right, initialY);
  context.stroke();
  context.setLineDash([]);
  context.fillStyle = "#34403d";
  context.textAlign = "right";
  context.fillText(`Capitale iniziale ${euro.format(monteCarlo.initialCapital)}`, width - pad.right, initialY - 8);

  context.fillStyle = "#657080";
  context.font = "12px system-ui";
  context.textAlign = "left";
  projection.forEach((item) => {
    if (item.year % 5 !== 0 && item.year !== lastYear) return;
    context.fillText(`${item.year}a`, x(item.year) - 8, height - 26);
  });
  context.textAlign = "center";
  context.fillText("Anni futuri simulati", width / 2, height - 12);

  const legend = [
    ["P5", "#c13e3e"],
    ["Mediana", "#2f66c5"],
    ["Scenario medio", "#18794e"],
    ["P95", "#18794e"],
  ];
  let legendX = pad.left;
  legend.forEach(([label, color]) => {
    context.fillStyle = color;
    context.fillRect(legendX, 14, 14, 4);
    context.fillStyle = "#34403d";
    context.textAlign = "left";
    context.fillText(label, legendX + 20, 18);
    legendX += label.length * 8 + 72;
  });
  ChartLite.attachTooltip(canvas, projection.map((item) => ({
    x: x(item.year),
    y: y(item.median),
    html: `
      <b>Anno ${item.year}</b>
      <span>Scenario negativo: ${euro.format(item.p5 || 0)}</span>
      <span>Mediana: ${euro.format(item.median || 0)}</span>
      <span>Scenario medio: ${euro.format(item.mean || 0)}</span>
      <span>Scenario positivo: ${euro.format(item.p95 || 0)}</span>
    `,
  })));
}

function renderStressTesting(stressTesting, optimizedStressTesting, comparisonLabel = "Scenario ottimizzato") {
  const container = document.querySelector("#stress-summary-grid");
  const optimizedContainer = document.querySelector("#optimized-stress-grid");
  const betaHead = document.querySelector("#stress-beta-head");
  const betaBody = document.querySelector("#stress-beta-body");
  if (!container || !betaHead || !betaBody) return;
  container.innerHTML = "";
  if (optimizedContainer) optimizedContainer.innerHTML = "";
  betaHead.innerHTML = "";
  betaBody.innerHTML = `<tr><td>Nessuno stress test eseguito.</td></tr>`;
  drawBarChart("#stress-asset-chart", [], "symbol", "weighted_contribution");
  drawBarChart("#stress-factor-chart", [], "factor", "contribution");
  drawBarChart("#optimized-stress-asset-chart", [], "symbol", "weighted_contribution");
  drawBarChart("#optimized-stress-factor-chart", [], "factor", "contribution");
  if (isLocked(stressTesting)) {
    container.innerHTML = lockedFeatureCard(stressTesting.feature || "stressTesting", stressTesting);
    if (optimizedContainer) optimizedContainer.innerHTML = lockedFeatureCard(stressTesting.feature || "stressTesting", stressTesting);
    betaBody.innerHTML = `<tr><td>Stress testing disponibile dal piano Plus.</td></tr>`;
    return;
  }
  if (!stressTesting || !stressTesting.enabled || !stressTesting.result) {
    const detail = stressTesting?.error || "Non attivo";
    drawEmptyChart("#stress-asset-chart", detail);
    drawEmptyChart("#stress-factor-chart", detail);
    container.innerHTML = `
      <article>
        <span>Stress testing</span>
        <strong>--</strong>
        <small>${detail}</small>
      </article>
    `;
    if (optimizedContainer) optimizedContainer.innerHTML = unavailableCard(optimizedStressTesting?.error || detail);
    return;
  }

  const result = stressTesting.result;
  const mc = stressTesting.monteCarlo;
  const cards = [
    ["Impatto simulato dello scenario", formatPercent(result.expected_portfolio_return), result.scenario],
    ["Strumento più difensivo", result.best_hedge_name || displayAnalysisInstrumentName(result.best_hedge), "assorbe meglio lo shock"],
    ["Strumento più vulnerabile", result.worst_contributor_name || displayAnalysisInstrumentName(result.worst_contributor), "pesa di più nello scenario"],
    ["Probabilità di perdita", mc ? formatPercent(mc.probability_of_loss) : "--", mc ? "Monte Carlo fattoriale" : "non eseguita"],
  ];
  cards.forEach(([label, value, detail]) => {
    const article = document.createElement("article");
    article.innerHTML = `
      <span>${label}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${detail}</small>
    `;
    container.appendChild(article);
  });
  drawBarChart("#stress-asset-chart", result.contribution_by_asset, "symbol", "weighted_contribution");
  drawBarChart("#stress-factor-chart", result.contribution_by_factor, "factor", "contribution");
  betaHead.innerHTML = `<tr><th>Asset</th>${factorOrder.map((factor) => `<th>${factorLabels[factor]}</th>`).join("")}<th>R2</th><th>p-value</th></tr>`;
  betaBody.innerHTML = result.factor_analysis.exposures
    .map((item) => `
      <tr>
        <td>${escapeHtml(displayAnalysisInstrumentName(item))}</td>
        ${factorOrder.map((factor) => `<td>${number.format(item.betas[factor])}</td>`).join("")}
        <td>${number.format(item.r_squared)}</td>
        <td>${item.model_p_value === null ? "--" : item.model_p_value.toExponential(2)}</td>
      </tr>
    `)
    .join("");
  if (optimizedContainer) renderOptimizedStress(optimizedContainer, optimizedStressTesting, comparisonLabel);
}

function renderStressScenarioAwaiting(stressTesting, optimizedStressTesting) {
  const message = "Seleziona uno scenario e premi Calcola crisi simulate.";
  const container = document.querySelector("#stress-summary-grid");
  const optimizedContainer = document.querySelector("#optimized-stress-grid");
  const currentComment = document.querySelector("#scenarios-intelligence-current");
  const optimizedComment = document.querySelector("#scenarios-intelligence-optimized");
  const betaHead = document.querySelector("#stress-beta-head");
  const betaBody = document.querySelector("#stress-beta-body");
  if (container) {
    if (isLocked(stressTesting)) {
      container.innerHTML = lockedFeatureCard(stressTesting.feature || "stressTesting", stressTesting);
    } else {
      container.innerHTML = stressAwaitingCard(message);
    }
  }
  if (optimizedContainer) {
    optimizedContainer.innerHTML = isLocked(stressTesting)
      ? lockedFeatureCard(stressTesting.feature || "stressTesting", stressTesting)
      : stressAwaitingCard(message);
  }
  if (currentComment) {
    currentComment.hidden = false;
    currentComment.innerHTML = `
      <div class="loading-note">Questa sezione resta ferma dopo l’analisi completa. Serve una scelta esplicita dello scenario.</div>
    `;
  }
  if (optimizedComment) {
    optimizedComment.hidden = false;
    optimizedComment.innerHTML = `
      <div class="loading-note">Dopo il calcolo vedrai il confronto con il portfolio efficiente o con il benchmark selezionato.</div>
    `;
  }
  if (betaHead) betaHead.innerHTML = "";
  if (betaBody) betaBody.innerHTML = `<tr><td>${escapeHtml(message)}</td></tr>`;
  drawEmptyChart("#stress-asset-chart", message);
  drawEmptyChart("#stress-factor-chart", message);
  drawEmptyChart("#optimized-stress-asset-chart", message);
  drawEmptyChart("#optimized-stress-factor-chart", message);
}

function renderOptimizedStress(container, optimizedStressTesting, comparisonLabel = "Scenario ottimizzato") {
  if (!optimizedStressTesting || isLocked(optimizedStressTesting) || !optimizedStressTesting.result) {
    const detail = optimizedStressTesting?.error || optimizedStressTesting?.reason || `${comparisonLabel} non disponibile.`;
    container.innerHTML = unavailableCard(detail);
    drawEmptyChart("#optimized-stress-asset-chart", detail);
    drawEmptyChart("#optimized-stress-factor-chart", detail);
    return;
  }
  const result = optimizedStressTesting.result;
  const mc = optimizedStressTesting.monteCarlo;
  container.innerHTML = `
    <article>
      <span>Impatto dello scenario</span>
      <strong>${formatPercent(result.expected_portfolio_return)}</strong>
      <small>${escapeHtml(comparisonLabel)}</small>
    </article>
    <article>
      <span>Strumento più difensivo</span>
      <strong>${escapeHtml(result.best_hedge_name || displayAnalysisInstrumentName(result.best_hedge))}</strong>
      <small>contributo migliore</small>
    </article>
    <article>
      <span>Strumento più vulnerabile</span>
      <strong>${escapeHtml(result.worst_contributor_name || displayAnalysisInstrumentName(result.worst_contributor))}</strong>
      <small>contributo peggiore</small>
    </article>
    <article>
      <span>Probabilità di perdita</span>
      <strong>${mc ? formatPercent(mc.probability_of_loss) : "--"}</strong>
      <small>Monte Carlo fattoriale</small>
    </article>
  `;
  drawBarChart("#optimized-stress-asset-chart", result.contribution_by_asset || [], "symbol", "weighted_contribution");
  drawBarChart("#optimized-stress-factor-chart", result.contribution_by_factor || [], "factor", "contribution");
}

function renderMonteCarlo(monteCarlo, optimizedMonteCarlo, comparisonLabel = "Monte Carlo ottimizzato") {
  const container = document.querySelector("#monte-carlo-grid");
  const optimizedContainer = document.querySelector("#optimized-monte-carlo-grid");
  container.innerHTML = "";
  if (optimizedContainer) optimizedContainer.innerHTML = "";
  if (isLocked(monteCarlo)) {
    container.innerHTML = lockedFeatureCard("monteCarlo", monteCarlo);
    if (optimizedContainer) optimizedContainer.innerHTML = lockedFeatureCard("monteCarlo", monteCarlo);
    drawMonteCarloChart({ projection: [] });
    drawMonteCarloChart({ projection: [] }, "#optimized-monte-carlo-chart");
    return;
  }
  const cards = [
    [
      "Scenario medio",
      euro.format(monteCarlo.expectedFinalValue),
      `${monteCarlo.settings.simulations} simulazioni`,
      "È la media dei valori finali di tutte le simulazioni Monte Carlo. Non è una promessa e non è per forza il risultato più probabile: è il baricentro statistico degli scenari simulati.",
    ],
    [
      "Mediana",
      euro.format(monteCarlo.median),
      `${monteCarlo.settings.horizonYears} anni`,
      "È il valore centrale: metà delle simulazioni finisce sopra questo numero e metà sotto. Spesso descrive meglio lo scenario tipico rispetto alla media, perché pesa meno gli estremi.",
    ],
    [
      "Percentile 5%",
      euro.format(monteCarlo.p5),
      "coda sfavorevole",
      "Indica uno scenario molto negativo ma plausibile nella simulazione: solo il 5% dei percorsi termina sotto questo valore, mentre il 95% termina sopra.",
    ],
    [
      "Percentile 95%",
      euro.format(monteCarlo.p95),
      "coda favorevole",
      "Indica uno scenario molto positivo nella simulazione: solo il 5% dei percorsi termina sopra questo valore, mentre il 95% termina sotto.",
    ],
    [
      "Probabilità di profitto",
      formatPercent(monteCarlo.probabilityGain),
      "valore finale > capitale iniziale",
      "È la percentuale di simulazioni in cui il portafoglio termina con un valore superiore al capitale iniziale. Misura quante traiettorie simulate chiudono in guadagno.",
    ],
  ];
  cards.forEach(([label, value, detail, explanation]) => {
    const article = document.createElement("article");
    article.innerHTML = `
      <span>${label}</span>
      <strong>${value}</strong>
      <small>${detail}</small>
      <button class="explain-button" type="button" aria-expanded="false">Spiegami</button>
      <div class="explain-cloud" hidden>${explanation}</div>
    `;
    container.appendChild(article);
  });
  if (optimizedContainer) renderOptimizedMonteCarlo(optimizedContainer, optimizedMonteCarlo, comparisonLabel);
  drawMonteCarloChart(monteCarlo);
  drawMonteCarloChart(optimizedMonteCarlo || { projection: [] }, "#optimized-monte-carlo-chart");
}

function renderOptimizedMonteCarlo(container, optimizedMonteCarlo, comparisonLabel = "Monte Carlo ottimizzato") {
  if (!optimizedMonteCarlo || isLocked(optimizedMonteCarlo) || !optimizedMonteCarlo.projection) {
    container.innerHTML = unavailableCard(optimizedMonteCarlo?.reason || optimizedMonteCarlo?.message);
    drawMonteCarloChart({ projection: [] }, "#optimized-monte-carlo-chart");
    return;
  }
  const cards = [
    ["Scenario medio", euro.format(optimizedMonteCarlo.expectedFinalValue), `${comparisonLabel} · ${optimizedMonteCarlo.settings.simulations} simulazioni`],
    ["Mediana", euro.format(optimizedMonteCarlo.median), `${optimizedMonteCarlo.settings.horizonYears} anni`],
    ["Percentile 5%", euro.format(optimizedMonteCarlo.p5), "coda sfavorevole"],
    ["Probabilità di profitto", formatPercent(optimizedMonteCarlo.probabilityGain), "finale > capitale iniziale"],
  ];
  container.innerHTML = cards
    .map(([label, value, detail]) => `
      <article>
        <span>${label}</span>
        <strong>${value}</strong>
        <small>${detail}</small>
      </article>
    `)
    .join("");
}

async function runBacktest(event, options = {}) {
  event?.preventDefault();
  if (analysisInFlight) {
    setStatus("Analisi già in corso: attendi il risultato prima di avviarne un'altra.", "error");
    return;
  }
  if (!validateBacktestForm()) {
    setStatus("Completa i campi richiesti prima di avviare l'analisi.", "error");
    return;
  }
  if (!options.standardBenchmarkOverride && !syncStandardModeSelection({ requireSelection: true })) {
    return;
  }
  const targetStep = options.targetStep || "diagnosis";
  const loadingMessage = options.loadingMessage || "Backtest in corso...";
  const successMessage = options.successMessage || null;
  let portfolio;
  try {
    portfolio = Array.isArray(options.portfolioOverride) ? options.portfolioOverride : collectPortfolio();
    validatePortfolioIsins(portfolio);
  } catch (error) {
    setStatus(error.message, "error");
    return;
  }
  const activeStandardBenchmark = Object.prototype.hasOwnProperty.call(options, "standardBenchmarkOverride")
    ? options.standardBenchmarkOverride
    : selectedStandardBenchmark;
  const activeStandardPortfolioMode = Object.prototype.hasOwnProperty.call(options, "standardPortfolioModeOverride")
    ? options.standardPortfolioModeOverride
    : selectedStandardPortfolioMode;
  const payload = {
    portfolio,
    investorProfile: collectInvestorProfile(),
    userPlan: currentPlan(),
    start: document.querySelector("#start").value,
    end: document.querySelector("#end").value,
    initialCapital: Number(document.querySelector("#initial-capital").value),
    rebalanceFrequency: DEFAULT_REBALANCE_FREQUENCY,
    monteCarlo: collectMonteCarlo(),
    stressTesting: collectStressTesting(Boolean(options.includeStress)),
    demo: document.querySelector("#demo").checked,
    standardBenchmark: activeStandardBenchmark ? {
      id: activeStandardBenchmark.id,
      name: activeStandardBenchmark.name,
      holdings: activeStandardBenchmark.holdings,
    } : null,
    standardPortfolioMode: activeStandardPortfolioMode,
    improvementComparisonMode: options.improvementComparisonModeOverride || null,
  };

  setAnalysisBusy(true, loadingMessage);
  try {
    const response = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || "Backtest non riuscito.");
    setStep(targetStep);
    renderResult(result);
    if (typeof options.afterRender === "function") {
      options.afterRender(result);
    }
    if (result.dataWarning) {
      setStatus(result.dataWarning, "error");
    } else {
      setStatus(successMessage || (result.dataSource === "demo" ? "Backtest completato con dati demo." : "Backtest completato con dati EODHD."), "ok");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setAnalysisBusy(false);
  }
}

window.validateBacktestForm = validateBacktestForm;

function runScenarioAnalysis(event) {
  runBacktest(event, {
    targetStep: "scenarios",
    loadingMessage: "Simulazione scenari in corso...",
    successMessage: "Simulazione scenari aggiornata.",
    includeStress: true,
  });
}

function runStressScenarioAnalysis(event) {
  stressScenarioCalculated = true;
  runBacktest(event, {
    targetStep: "scenarios",
    loadingMessage: "Calcolo crisi simulate in corso...",
    successMessage: "Crisi simulate aggiornata.",
    includeStress: true,
  });
}

mountConfigurationPanels();
document.querySelector("#add-position").addEventListener("click", () => addPosition());
document.querySelector("#save-portfolio").addEventListener("click", saveCurrentPortfolio);
document.querySelector("#delete-portfolio").addEventListener("click", deleteCurrentPortfolio);
document.querySelector("#load-portfolio").addEventListener("click", () => {
  loadSelectedPortfolio();
});
document.querySelector("#monitor-portfolio").addEventListener("click", monitorSavedPortfolio);
document.querySelector("#save-portfolio-inline").addEventListener("click", saveCurrentPortfolio);
document.querySelector("#delete-portfolio-inline").addEventListener("click", deleteCurrentPortfolio);
document.querySelector("#load-portfolio-inline").addEventListener("click", loadSelectedPortfolio);
document.querySelector("#monitor-portfolio-inline").addEventListener("click", monitorSavedPortfolio);
document.querySelector("#monitor-portfolio-tracking").addEventListener("click", monitorSavedPortfolio);
document.querySelector("#standard-portfolio-select")?.addEventListener("change", () => {
  renderStandardSelectorPreview();
  syncStandardModeSelection({ announce: true });
});
document.querySelector("#standard-mode")?.addEventListener("change", () => {
  syncStandardModeSelection({ announce: false });
});
document.querySelector("#saved-portfolios").addEventListener("change", (event) => syncSavedPortfolioSelects(event.target));
document.querySelector("#saved-portfolios-inline").addEventListener("change", (event) => syncSavedPortfolioSelects(event.target));
document.querySelector("#saved-portfolios-tracking").addEventListener("change", (event) => syncSavedPortfolioSelects(event.target));
document.querySelector("#run-scenarios")?.addEventListener("click", runScenarioAnalysis);
document.querySelector("#calculate-stress-scenario")?.addEventListener("click", runStressScenarioAnalysis);
document.querySelector("#rolling-window-select")?.addEventListener("change", (event) => {
  currentRollingWindow = event.target.value || "1y";
  if (latestAdvancedAnalytics?.rollingSortino && currentStep === "rolling") {
    renderRollingSortino(latestAdvancedAnalytics.rollingSortino, latestAdvancedAnalytics);
    rebalancePortfolioComparisons();
  }
});
document.querySelector("#user-plan").addEventListener("change", () => {
  updatePlanBadge();
  updatePlanVisibility();
  loadStandardPortfolios();
});
["#initial-capital", "#investor-age", "#investor-horizon", "#investor-risk", "#investor-max-loss", "#investor-pac", "#investor-goal-priority", "#investor-experience", "#investor-liquidity-need", "#investor-objective"].forEach((selector) => {
  document.querySelector(selector)?.addEventListener("input", updateProfileSummary);
  document.querySelector(selector)?.addEventListener("change", updateProfileSummary);
});
document.addEventListener("click", (event) => {
  const stepLink = event.target.closest("[data-step-link]");
  if (stepLink) {
    setStep(stepLink.dataset.stepLink);
    return;
  }
  const button = event.target.closest(".explain-button");
  if (button) toggleExplainCloud(button);
  const aiDetailButton = event.target.closest(".ai-detail-button");
  if (aiDetailButton) loadAiDetail(aiDetailButton);
  if (event.target.closest("[data-close-drawer]")) closeTechnicalDrawer();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeTechnicalDrawer();
});
document.querySelectorAll(".step-button").forEach((button) => {
  button.addEventListener("click", () => setStep(button.dataset.stepTarget));
});
document.querySelectorAll("[data-flow-step]").forEach((item) => {
  const navigate = () => {
    if (!item.hidden && item.getAttribute("aria-disabled") !== "true") setStep(item.dataset.flowStep);
  };
  item.addEventListener("click", navigate);
  item.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      navigate();
    }
  });
});
document.querySelectorAll(".next-step").forEach((button) => {
  button.addEventListener("click", () => setStep(button.dataset.nextStep));
});
form.addEventListener("submit", runBacktest);
document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    const end = new Date();
    let start = new Date(end);
    if (button.dataset.range === "ytd") start = new Date(end.getFullYear(), 0, 1);
    if (button.dataset.range === "5y") start = addYears(end, -5);
    if (button.dataset.range === "10y") start = addYears(end, -10);
    if (button.dataset.range === "15y") start = addYears(end, -15);
    document.querySelector("#start").value = start.toISOString().slice(0, 10);
    document.querySelector("#end").value = todayISO();
  });
});
document.querySelector("#hidden-risk-scenario")?.addEventListener("change", () => {
  const stressScenario = document.querySelector("#stress-scenario");
  if (stressScenario) stressScenario.value = document.querySelector("#hidden-risk-scenario")?.value || stressScenario.value;
  syncStressSlidersFromScenario();
  hiddenRiskScenarioCalculated = false;
  if (currentStep === "factorrisk") renderHiddenRiskAwaiting();
});
document.querySelector("#stress-scenario")?.addEventListener("change", () => {
  const hiddenScenario = document.querySelector("#hidden-risk-scenario");
  if (hiddenScenario) hiddenScenario.value = document.querySelector("#stress-scenario")?.value || hiddenScenario.value;
  syncStressSlidersFromScenario();
  stressScenarioCalculated = false;
  renderStressScenarioAwaiting();
});
document.querySelector("#run-hidden-risk")?.addEventListener("click", runHiddenRiskScenario);
document.querySelectorAll("[data-factor-shock]").forEach((input) => {
  input.addEventListener("input", () => {
    const output = document.querySelector(`[data-factor-value="${input.dataset.factorShock}"]`);
    if (output) output.textContent = `${input.value}%`;
  });
});

setDefaultDates();
initializeMobileStepNav();
enhanceAdvancedAccordions();
addPosition("US0378331005", 40, "equity");
addPosition("US5949181045", 35, "equity");
addPosition("US78462F1030", 25, "equity");
updateProfileSummary();
updatePortfolioLiveCheck();
syncStressSlidersFromScenario();
updateMobileStepNav();
updateEmptyStateGuidance();
arrangeComparisonMetricCards();
renderStressScenarioAwaiting();
rebalancePortfolioComparisons();
setStep("profile");
updatePlanVisibility();
loadPlanConfig();
loadSavedPortfolios();
loadStandardPortfolios();
drawEmptyChart();
