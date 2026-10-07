// Markdown + LaTeX rendering.
// Wiskunde wordt vóór het markdown-parsen gemaskeerd zodat marked geen
// underscores/backslashes in formules kapotmaakt, en daarna met KaTeX gerenderd.
import { renderCharts } from "./charts.js";

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// AI-output is onbetrouwbare tekst. Normaliseer bekende providerartefacten
// voordat marked/KaTeX ze ziet, zodat `null`, dubbele heading-markers en een
// eenzame ** nooit als zichtbare studiestof eindigen.
export function cleanMarkdown(markdownText) {
  let text = String(markdownText ?? "");
  text = text
    .replace(/^\s*(?:null|undefined)\s*$/gim, "")
    .replace(/^(#{1,6})\s+(?:#{1,6}\s+)+/gm, "$1 ")
    .replace(/\u0000/g, "")
    .trim();
  const strongMarkers = text.match(/\*\*/g)?.length || 0;
  if (strongMarkers % 2) text = text.replace(/\*\*/g, "");
  return text;
}

// Token omsloten door Private-Use-Area-tekens (/): overleeft
// marked ongewijzigd en komt nooit in gewone tekst voor.
const MATH_TOKEN = (i) => "" + i + "";
const MATH_TOKEN_RE = /(\d+)/g;

function maskMath(src) {
  const chunks = [];
  const push = (tex, display) => {
    chunks.push({ tex, display });
    return MATH_TOKEN(chunks.length - 1);
  };
  const out = src
    // $$ ... $$ (mag over meerdere regels)
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => push(tex, true))
    // \[ ... \]
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => push(tex, true))
    // \( ... \)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => push(tex, false))
    // $...$ op één regel; niet "$5 en $6" (valuta): eis niet-spatie na/voor de $
    .replace(/\$(?!\s)((?:\\.|[^$\n])+?)(?<!\s)\$/g, (_, tex) => push(tex, false));
  return { out, chunks };
}

function renderMathChunk({ tex, display }) {
  if (!window.katex) {
    const esc = tex.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    return display ? `<pre>${esc}</pre>` : `<code>${esc}</code>`;
  }
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: false });
  } catch {
    return `<code>${tex}</code>`;
  }
}

let markedConfigured = false;

export function renderMarkdown(markdownText) {
  const cleaned = cleanMarkdown(markdownText);
  const { out, chunks } = maskMath(cleaned);

  let html;
  if (window.marked) {
    if (!markedConfigured) {
      marked.setOptions({ gfm: true, breaks: false });
      markedConfigured = true;
    }
    html = marked.parse(out);
  } else {
    html = `<p>${out.replace(/\n\n/g, "</p><p>")}</p>`;
  }

  if (window.DOMPurify) {
    html = DOMPurify.sanitize(html, { ADD_ATTR: ["target"] });
  } else {
    // Fail-closed: zonder sanitizer nooit door marked gegenereerde HTML
    // injecteren (AI-/documenttekst is niet te vertrouwen). Toon de ruwe tekst
    // geëscaped i.p.v. een XSS-gat te openen. DOMPurify is lokaal gevendord en
    // dus normaal altijd aanwezig; dit is een laatste vangnet.
    return escapeHtml(cleaned);
  }

  // KaTeX-output pas ná de sanitisatie invoegen. Dat is veilig omdat KaTeX zijn
  // eigen (AI-geleverde) invoer rendert zonder scripts of willekeurige HTML
  // (trust:false, strict:false) — het is per ontwerp een sanitizer van tex.
  html = html.replace(MATH_TOKEN_RE, (_, i) => renderMathChunk(chunks[+i] || { tex: "", display: false }));
  return html;
}

// Rendert streamende markdown in `container`, maximaal één keer per frame.
// Gebruik: const s = createStreamRenderer(container); s.append("tekst"); s.finish();
export function createStreamRenderer(container) {
  let text = "";
  let scheduled = false;
  let done = false;

  const paint = () => {
    scheduled = false;
    container.innerHTML = renderMarkdown(text) + (done ? "" : '<span class="stream-caret"></span>');
    // Grafieken pas tekenen als het antwoord compleet is: tijdens het streamen
    // her-rendert dit elke frame de hele DOM, dus een half ```chart-blok zou
    // steeds opnieuw ontstaan en verdwijnen.
    if (done) renderCharts(container);
  };
  const schedule = () => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(paint); }
  };

  return {
    append(delta) { text += delta; schedule(); },
    set(full) { text = full; schedule(); },
    finish() { done = true; paint(); },
    get text() { return text; },
  };
}

export { renderCharts };
