// Rendert ```chart-blokken (JSON) als echte grafieken met Chart.js.
// Chart.js wordt pas geladen zodra de eerste grafiek verschijnt (lazy), zodat
// de app licht blijft; de service worker cachet de CDN-versie voor offline.
// Net als KaTeX bouwen we de canvas zelf via createElement — dat omzeilt de
// DOMPurify-sanitisatie op precies dezelfde manier.
import { evaluateFunction } from "./plot.js";

const CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js";
let chartJsPromise = null;

function loadChartJs() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (!chartJsPromise) {
    chartJsPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = CHART_JS_URL;
      s.onload = () => resolve(window.Chart);
      s.onerror = () => reject(new Error("Chart.js kon niet geladen worden"));
      document.head.appendChild(s);
    });
  }
  return chartJsPromise;
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function themeColors() {
  return {
    text: cssVar("--text-soft", "#b6bfd1"),
    grid: cssVar("--border", "rgba(148,163,196,.14)"),
    accent: cssVar("--accent", "#6d9bff"),
    green: cssVar("--green", "#45d6b2"),
    amber: cssVar("--amber", "#f2b95c"),
    red: cssVar("--red", "#f27e7e"),
  };
}

function buildConfig(spec, Chart) {
  const c = themeColors();
  const palette = [c.accent, c.green, c.amber, c.red];
  const common = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: (spec.series?.length || 0) > 1 || spec.kind === "function", labels: { color: c.text } },
      title: spec.title ? { display: true, text: spec.title, color: c.text } : { display: false },
    },
    scales: {
      x: {
        type: spec.kind === "function" || spec.kind === "scatter" ? "linear" : "category",
        title: { display: !!spec.xlabel, text: spec.xlabel || "", color: c.text },
        ticks: { color: c.text }, grid: { color: c.grid },
      },
      y: {
        title: { display: !!spec.ylabel, text: spec.ylabel || "", color: c.text },
        ticks: { color: c.text }, grid: { color: c.grid },
      },
    },
  };

  if (spec.kind === "function") {
    const pts = evaluateFunction(spec.fn, spec.domain || [-10, 10]);
    return {
      type: "line",
      data: { datasets: [{ label: spec.fn, data: pts, borderColor: c.accent, backgroundColor: "transparent", borderWidth: 2, pointRadius: 0, tension: 0.15, spanGaps: false }] },
      options: common,
    };
  }

  const datasets = (spec.series || []).map((s, i) => {
    const color = palette[i % palette.length];
    const data = (s.points || []).map((p) => Array.isArray(p) ? { x: p[0], y: p[1] } : p);
    return {
      label: s.label || "",
      data: spec.kind === "bar" ? data.map((d) => d.y) : data,
      borderColor: color,
      backgroundColor: spec.kind === "scatter" ? color : (spec.kind === "bar" ? color : "transparent"),
      borderWidth: 2,
      pointRadius: spec.kind === "scatter" ? 4 : 2,
      showLine: spec.kind !== "scatter",
      tension: 0.15,
    };
  });
  return {
    type: spec.kind === "bar" ? "bar" : (spec.kind === "scatter" ? "scatter" : "line"),
    data: { labels: spec.labels || undefined, datasets },
    options: common,
  };
}

// Zoekt alle ```chart-codeblokken in `container` en vervangt ze door een canvas
// met de getekende grafiek. Ongeldige JSON/expressie: het codeblok blijft staan.
export function renderCharts(container) {
  if (!container) return;
  const blocks = container.querySelectorAll("pre > code.language-chart");
  if (!blocks.length) return;

  blocks.forEach((code) => {
    let spec;
    try { spec = JSON.parse(code.textContent); }
    catch { return; } // laat het codeblok zichtbaar staan bij kapotte JSON
    const pre = code.parentElement;
    if (!pre || pre.dataset.chartDone) return;
    pre.dataset.chartDone = "1";

    const wrap = document.createElement("div");
    wrap.className = "chart-wrap";
    const canvas = document.createElement("canvas");
    wrap.appendChild(canvas);
    pre.replaceWith(wrap);

    loadChartJs().then((Chart) => {
      try {
        // eslint-disable-next-line no-new
        new Chart(canvas.getContext("2d"), buildConfig(spec, Chart));
      } catch (err) {
        wrap.replaceWith(Object.assign(document.createElement("div"), {
          className: "chart-error", textContent: "Grafiek kon niet worden getekend.",
        }));
      }
    }).catch(() => {
      wrap.replaceWith(Object.assign(document.createElement("div"), {
        className: "chart-error", textContent: "Grafiek kon niet worden geladen.",
      }));
    });
  });
}
