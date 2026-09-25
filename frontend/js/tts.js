// Uitleg voorlezen. Twee lagen:
// 1) Neurale stem via de backend (/tts, edge-tts) — natuurlijk en prettig,
//    per taal een eigen stem (Fenna, Aria, Katja, Denise, Elvira).
// 2) Fallback: de beste beschikbare browserstem (Web Speech API), voor als de
//    backend niet bereikbaar is of geen internet heeft.
import { API_BASE } from "./config.js";
import { renderMarkdown } from "./markdown.js";
import { uiLocale } from "./i18n.js";

const LANG_MAP = {
  Nederlands: "nl-NL", English: "en-US",
  Deutsch: "de-DE", "Français": "fr-FR", "Español": "es-ES",
};

// ---------- formules uitspreekbaar maken ----------
// Formules werden vervangen door het woord "(formule)", dus alles wat als functie
// in de uitleg stond werd simpelweg niet voorgelezen. KaTeX bewaart de originele
// LaTeX in een <annotation>-element; die zetten we hier om naar woorden.
// Bewust bescheiden: de constructies die in studiemateriaal veruit het meest
// voorkomen. Wat we niet kennen, spreken we uit als de naam van het commando —
// dat is altijd nog beter dan het overslaan.
const MATH_WORDS = {
  nl: { over: "gedeeld door", upto: "tot", sqrt: "wortel", squared: "kwadraat", cubed: "tot de derde", power: "tot de macht",
        op: { "=": "is gelijk aan", "+": "plus", "-": "min", "<": "kleiner dan", ">": "groter dan" },
        sym: { cdot: "keer", times: "keer", div: "gedeeld door", pm: "plus of min", leq: "kleiner dan of gelijk aan",
               le: "kleiner dan of gelijk aan", geq: "groter dan of gelijk aan", ge: "groter dan of gelijk aan",
               neq: "ongelijk aan", ne: "ongelijk aan", approx: "ongeveer", infty: "oneindig", sum: "som van",
               int: "integraal van", partial: "partieel", Delta: "delta", pi: "pi", alpha: "alfa", beta: "bèta",
               gamma: "gamma", theta: "thèta", lambda: "lambda", mu: "mu", sigma: "sigma", omega: "omega",
               rightarrow: "geeft", to: "naar", log: "logaritme", ln: "natuurlijke logaritme" } },
  en: { over: "divided by", upto: "to", sqrt: "the square root of", squared: "squared", cubed: "cubed", power: "to the power",
        op: { "=": "equals", "+": "plus", "-": "minus", "<": "less than", ">": "greater than" },
        sym: { cdot: "times", times: "times", div: "divided by", pm: "plus or minus", leq: "less than or equal to",
               le: "less than or equal to", geq: "greater than or equal to", ge: "greater than or equal to",
               neq: "not equal to", ne: "not equal to", approx: "approximately", infty: "infinity", sum: "the sum of",
               int: "the integral of", partial: "partial", Delta: "delta", pi: "pi", alpha: "alpha", beta: "beta",
               gamma: "gamma", theta: "theta", lambda: "lambda", mu: "mu", sigma: "sigma", omega: "omega",
               rightarrow: "gives", to: "to", log: "log", ln: "natural log" } },
  de: { over: "geteilt durch", upto: "bis", sqrt: "Wurzel aus", squared: "zum Quadrat", cubed: "hoch drei", power: "hoch",
        op: { "=": "ist gleich", "+": "plus", "-": "minus", "<": "kleiner als", ">": "größer als" },
        sym: { cdot: "mal", times: "mal", div: "geteilt durch", pm: "plus minus", leq: "kleiner oder gleich",
               le: "kleiner oder gleich", geq: "größer oder gleich", ge: "größer oder gleich",
               neq: "ungleich", ne: "ungleich", approx: "ungefähr", infty: "unendlich", sum: "Summe von",
               int: "Integral von", partial: "partiell", Delta: "Delta", pi: "Pi", alpha: "Alpha", beta: "Beta",
               gamma: "Gamma", theta: "Theta", lambda: "Lambda", mu: "My", sigma: "Sigma", omega: "Omega",
               rightarrow: "ergibt", to: "nach", log: "Logarithmus", ln: "natürlicher Logarithmus" } },
  fr: { over: "divisé par", upto: "jusqu'à", sqrt: "racine de", squared: "au carré", cubed: "au cube", power: "puissance",
        op: { "=": "égale", "+": "plus", "-": "moins", "<": "inférieur à", ">": "supérieur à" },
        sym: { cdot: "fois", times: "fois", div: "divisé par", pm: "plus ou moins", leq: "inférieur ou égal à",
               le: "inférieur ou égal à", geq: "supérieur ou égal à", ge: "supérieur ou égal à",
               neq: "différent de", ne: "différent de", approx: "environ", infty: "infini", sum: "somme de",
               int: "intégrale de", partial: "partiel", Delta: "delta", pi: "pi", alpha: "alpha", beta: "bêta",
               gamma: "gamma", theta: "thêta", lambda: "lambda", mu: "mu", sigma: "sigma", omega: "oméga",
               rightarrow: "donne", to: "vers", log: "logarithme", ln: "logarithme naturel" } },
  es: { over: "dividido por", upto: "hasta", sqrt: "raíz de", squared: "al cuadrado", cubed: "al cubo", power: "elevado a",
        op: { "=": "igual a", "+": "más", "-": "menos", "<": "menor que", ">": "mayor que" },
        sym: { cdot: "por", times: "por", div: "dividido por", pm: "más o menos", leq: "menor o igual que",
               le: "menor o igual que", geq: "mayor o igual que", ge: "mayor o igual que",
               neq: "distinto de", ne: "distinto de", approx: "aproximadamente", infty: "infinito", sum: "suma de",
               int: "integral de", partial: "parcial", Delta: "delta", pi: "pi", alpha: "alfa", beta: "beta",
               gamma: "gamma", theta: "theta", lambda: "lambda", mu: "mu", sigma: "sigma", omega: "omega",
               rightarrow: "da", to: "a", log: "logaritmo", ln: "logaritmo natural" } },
};

// Argument na ^ _ \frac enz.: {…} met balans, of anders één teken (x^2).
function braceArg(s, i) {
  while (s[i] === " ") i++;
  if (s[i] !== "{") return [s[i] || "", i + 1];
  let depth = 0, j = i;
  for (; j < s.length; j++) {
    if (s[j] === "{") depth++;
    else if (s[j] === "}" && --depth === 0) break;
  }
  return [s.slice(i + 1, j), j + 1];
}

function texToWords(tex, W) {
  let out = "";
  let i = 0;
  while (i < tex.length) {
    const c = tex[i];
    if (c === "\\") {
      const m = /^\\([a-zA-Z]+)/.exec(tex.slice(i));
      if (!m) { i++; continue; }                       // \, \! e.d.: weglaten
      const cmd = m[1];
      i += m[0].length;
      if (cmd === "frac" || cmd === "dfrac" || cmd === "tfrac") {
        const [a, i1] = braceArg(tex, i);
        const [b, i2] = braceArg(tex, i1);
        out += ` ${texToWords(a, W)} ${W.over} ${texToWords(b, W)} `;
        i = i2;
      } else if (cmd === "sqrt") {
        const [a, i1] = braceArg(tex, i);
        out += ` ${W.sqrt} ${texToWords(a, W)} `;
        i = i1;
      } else if (cmd === "left" || cmd === "right") {
        // alleen een haakje-modifier; het haakje zelf volgt hierna
      } else if (cmd === "int" || cmd === "sum" || cmd === "prod") {
        // Grenzen bij een integraal/som horen als "van a tot b" te klinken, niet
        // als "a tot de macht b" (wat de gewone ^-regel ervan zou maken).
        out += ` ${W.sym[cmd] ?? cmd} `;
        if (tex[i] === "_") {
          const [lo, i1] = braceArg(tex, i + 1);
          out += ` ${texToWords(lo, W)} `;
          i = i1;
          if (tex[i] === "^") {
            const [hi, i2] = braceArg(tex, i + 1);
            out += ` ${W.upto} ${texToWords(hi, W)} `;
            i = i2;
          }
        }
      } else {
        out += ` ${W.sym[cmd] ?? cmd} `;
      }
      continue;
    }
    if (c === "^") {
      const [a, i1] = braceArg(tex, i + 1);
      const t = a.trim();
      out += t === "2" ? ` ${W.squared} ` : t === "3" ? ` ${W.cubed} ` : ` ${W.power} ${texToWords(a, W)} `;
      i = i1;
      continue;
    }
    if (c === "_") {
      const [a, i1] = braceArg(tex, i + 1);
      out += ` ${texToWords(a, W)} `;                  // index gewoon uitspreken
      i = i1;
      continue;
    }
    if (c === "{" || c === "}" || c === "&") { out += " "; i++; continue; }
    if (W.op[c] != null) { out += ` ${W.op[c]} `; i++; continue; }
    out += c;
    i++;
  }
  return out.replace(/\s+/g, " ").trim();
}

function mathWordsFor(language) {
  const locale = LANG_MAP[language] || uiLocale();
  return MATH_WORDS[locale.slice(0, 2).toLowerCase()] || MATH_WORDS.en;
}

// Markdown → { text, blockStarts }. `text` is de voorleesbare platte tekst
// (formules/code vervangen, elk blok afgesloten met een leesteken zodat edge-tts
// pauzeert na een kop en tussen lijst-items). `blockStarts[i]` is het
// startkarakter van blok i in díe tekst.
//
// Belangrijk: de blok-offsets komen uit EXACT dezelfde getransformeerde tekst als
// die naar de TTS gaat. Vroeger berekende de indicator de offsets opnieuw uit de
// losse DOM-tekst (met echte formule-/codetekst en zónder toegevoegde punten),
// waardoor de tekens niet meer overeenkwamen met de tijdmarkeringen en de
// highlight cumulatief scheefliep. Nu delen beide kanten dezelfde bron.
export function speechFromMarkdown(markdown, language) {
  const W = mathWordsFor(language);
  const div = document.createElement("div");
  div.innerHTML = renderMarkdown(markdown);
  // Formules uitspreken i.p.v. overslaan. KaTeX zet de originele LaTeX in een
  // <annotation>; de zichtbare HTML eromheen is voor de ogen, niet voor de oren
  // (die levert bij een breuk bijvoorbeeld "ab" op). Let op: .katex-display bevat
  // zelf een .katex, dus dit dekt zowel losse als inline formules.
  div.querySelectorAll(".katex").forEach(k => {
    const tex = k.querySelector('annotation[encoding="application/x-tex"]')?.textContent || "";
    k.replaceWith(document.createTextNode(tex ? ` ${texToWords(tex, W)} ` : " "));
  });
  div.querySelectorAll("pre").forEach(k => k.replaceWith(" (code) "));

  const blocks = div.querySelectorAll("h1,h2,h3,h4,h5,h6,p,li,blockquote");
  if (!blocks.length) {
    return { text: (div.textContent || "").replace(/\s+/g, " ").trim(), blockStarts: [] };
  }

  const parts = [];
  const blockStarts = [];
  let at = 0;
  for (const b of blocks) {
    let s = (b.textContent || "").replace(/\s+/g, " ").trim();
    if (!s) continue;
    // Kop of zin zonder eindleesteken: punt toevoegen → de stem pauzeert.
    if (!/[.!?:…]$/.test(s)) s += ".";
    blockStarts.push(at);
    parts.push(s);
    at += s.length + 1; // +1 voor de verbindende spatie bij join(" ")
  }
  return { text: parts.join(" "), blockStarts };
}

// Alleen de tekst — voor plekken waar de blok-offsets niet nodig zijn.
// Alleen de tekst — voor plekken waar de blok-offsets niet nodig zijn.
// De taal MOET mee: hij bepaalt hoe formules worden uitgesproken, en het
// voorwarmen en het echte voorlezen moeten letterlijk dezelfde tekst opleveren,
// anders wijkt de cache-sleutel af en doet het voorwarmen stilletjes niets.
export function plainText(markdown, language) {
  return speechFromMarkdown(markdown, language).text;
}

export const ttsSupported = () => true; // backend-stem óf browserstem: er is altijd iets

// Warm de voorleesaudio (+ tijdmarkeringen) vast in de cache, zonder af te
// spelen. Roep dit aan als de gebruiker voorlezen daadwerkelijk gebruikt, zodat
// de vólgende dia meteen klinkt i.p.v. seconden te laden. Fire-and-forget.
export function prewarmSpeech(markdown, language) {
  const text = plainText(markdown, language);
  if (!text) return;
  const locale = LANG_MAP[language] || uiLocale();
  fetch(`${API_BASE}/tts-marks`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, language: locale }),
  }).catch(() => {});
}

// Sessieteller: elke nieuwe speak/stop maakt lopende (asynchrone) pogingen ongeldig.
let session = 0;
let currentAudio = null;
let currentUrl = null;

function cleanupAudio() {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = null;
  currentAudio = null;
}

// ---------- meeleesindicator (highlight volgt de audio) ----------
let hlRAF = 0;
let hlBlocks = null;
let hlActive = -1;

function stopHighlight() {
  if (hlRAF) cancelAnimationFrame(hlRAF);
  hlRAF = 0;
  if (hlBlocks) hlBlocks.forEach(b => b.classList.remove("tts-reading"));
  hlBlocks = null;
  hlActive = -1;
}

async function fetchMarks(text, locale) {
  try {
    const r = await fetch(`${API_BASE}/tts-marks`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language: locale }),
    });
    if (r.ok) return (await r.json()).marks || [];
  } catch { /* geen highlight, voorlezen werkt gewoon door */ }
  return [];
}

// Highlight het blok (kop/alinea/lijst-item) dat op dit moment wordt voorgelezen.
// De marks zijn zin-tijdstempels (edge-tts SentenceBoundary) in leesvolgorde.
// We mappen blok -> mark op TEKSTPOSITIE (karakter-offset). `blockStarts` komt uit
// speechFromMarkdown en is berekend uit exact dezelfde tekst als de marks, dus de
// offsets kloppen precies (ook met formules, code en meerdere zinnen per alinea).
// Live blok i hoort per index bij blockStarts[i]: beide zijn dezelfde markdown in
// dezelfde volgorde, alleen mist de losse DOM-tekst de TTS-transformaties.
function startHighlight(root, marks, blockStarts, my) {
  const blocks = [...root.querySelectorAll("h1,h2,h3,h4,h5,h6,p,li,blockquote")]
    .filter(b => (b.textContent || "").trim());
  if (!blocks.length || !marks.length || !blockStarts.length) return;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  // Startkarakter van elke mark in de doorlopende tekst (marks in leesvolgorde,
  // gescheiden door één spatie — net als de blokken in speechFromMarkdown).
  const markStartChar = [];
  let mc = 0;
  for (const m of marks) { markStartChar.push(mc); mc += norm(m.w).length + 1; }
  // Voor elk blok: welke mark dekt zijn startkarakter?
  //
  // Eén voorgelezen zin kan méérdere blokken omspannen: een introzin die op een
  // dubbele punt eindigt wordt door edge-tts aan het eerste lijst-item geplakt.
  // Namen we dan simpelweg de starttijd van die mark, dan kregen intro én eerste
  // bolletje dezelfde tijd en sprong de indicator meteen naar het bolletje terwijl
  // de intro nog voorgelezen werd. Daarom schatten we binnen de mark bij: hoe
  // verder het blok in de zin begint, hoe later in die zin het klinkt.
  const n = Math.min(blocks.length, blockStarts.length);
  const startMs = [];
  for (let i = 0; i < n; i++) {
    const charAt = blockStarts[i];
    let j = 0;
    for (let k = 0; k < markStartChar.length; k++) { if (markStartChar[k] <= charAt) j = k; else break; }
    const mark = marks[j];
    const markLen = Math.max(1, norm(mark?.w).length);
    const into = Math.min(1, Math.max(0, (charAt - markStartChar[j]) / markLen));
    const dur = marks[j + 1]?.t != null ? Math.max(0, marks[j + 1].t - (mark?.t ?? 0)) : 0;
    startMs.push((mark?.t ?? 0) + into * dur);
  }

  hlBlocks = blocks;
  hlActive = -1;
  const tick = () => {
    if (my !== session || !currentAudio) return;
    const ms = currentAudio.currentTime * 1000;
    let idx = 0;
    for (let i = 0; i < startMs.length; i++) { if (ms >= startMs[i]) idx = i; else break; }
    if (idx !== hlActive) {
      if (hlActive >= 0) blocks[hlActive]?.classList.remove("tts-reading");
      blocks[idx]?.classList.add("tts-reading");
      blocks[idx]?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      hlActive = idx;
    }
    hlRAF = requestAnimationFrame(tick);
  };
  hlRAF = requestAnimationFrame(tick);
}

export function stopSpeech() {
  session++;
  stopHighlight();
  if (currentAudio) {
    currentAudio.pause();
    cleanupAudio();
  }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

// ---------- fallback: beste browserstem kiezen ----------
// Stemmen laden vaak asynchroon; alvast opvragen zodat ze er zijn bij gebruik.
if ("speechSynthesis" in window) {
  speechSynthesis.getVoices();
  speechSynthesis.addEventListener?.("voiceschanged", () => speechSynthesis.getVoices());
}

function pickBrowserVoice(locale) {
  if (!("speechSynthesis" in window)) return null;
  const lang2 = locale.slice(0, 2).toLowerCase();
  const candidates = speechSynthesis.getVoices()
    .filter(v => v.lang.replace("_", "-").toLowerCase().startsWith(lang2));
  // "Natural" (Edge) klinkt het best, dan Google, dan andere online stemmen,
  // en pas als laatste de lokale (robotachtige) systeemstem.
  const score = (v) =>
    (/natural/i.test(v.name) ? 8 : 0) +
    (/google/i.test(v.name) ? 4 : 0) +
    (/online|neural/i.test(v.name) ? 2 : 0) +
    (v.lang.replace("_", "-").toLowerCase() === locale.toLowerCase() ? 1 : 0);
  return candidates.sort((a, b) => score(b) - score(a))[0] || null;
}

function speakInBrowser(text, locale, onEnd) {
  const u = new SpeechSynthesisUtterance(text);
  u.lang = locale;
  const voice = pickBrowserVoice(locale);
  if (voice) u.voice = voice;
  u.rate = 1.03;
  u.onend = () => onEnd?.();
  u.onerror = () => onEnd?.();
  speechSynthesis.speak(u);
}

// ---------- hoofdingang ----------
// onStart wordt aangeroepen zodra er echt geluid komt (na het laden van de
// neurale stem), zodat de knop een laadstatus kan tonen.
export async function speak(markdown, language, onEnd, onStart, highlightRoot) {
  stopSpeech();
  const my = ++session;
  const { text, blockStarts } = speechFromMarkdown(markdown, language);
  if (!text) { onEnd?.(); return; }
  const locale = LANG_MAP[language] || uiLocale();

  try {
    // Marks eerst: dat genereert (en cachet) meteen de audio, dus de audio-fetch
    // erna is een cache-hit — geen dubbele generatie. Zonder marks-endpoint
    // (oudere backend) komt gewoon [] terug en werkt voorlezen zonder highlight.
    const marks = highlightRoot ? await fetchMarks(text, locale) : [];
    if (my !== session) return;
    const resp = await fetch(`${API_BASE}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language: locale }),
    });
    if (my !== session) return; // ondertussen gestopt of opnieuw gestart
    if (resp.ok && (resp.headers.get("content-type") || "").includes("audio")) {
      const blob = await resp.blob();
      if (my !== session) return;
      currentUrl = URL.createObjectURL(blob);
      currentAudio = new Audio(currentUrl);
      currentAudio.onended = () => { stopHighlight(); cleanupAudio(); onEnd?.(); };
      currentAudio.onerror = () => { stopHighlight(); cleanupAudio(); onEnd?.(); };
      onStart?.();
      if (highlightRoot && marks.length) startHighlight(highlightRoot, marks, blockStarts, my);
      await currentAudio.play();
      return;
    }
  } catch { /* backend niet bereikbaar → browserstem */ }

  if (my !== session) return;
  onStart?.();
  speakInBrowser(text, locale, onEnd);
}
