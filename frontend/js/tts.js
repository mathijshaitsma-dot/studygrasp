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
export function speechFromMarkdown(markdown) {
  const div = document.createElement("div");
  div.innerHTML = renderMarkdown(markdown);
  div.querySelectorAll(".katex-display, .katex").forEach(k => k.replaceWith(" (formule) "));
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
export function plainText(markdown) {
  return speechFromMarkdown(markdown).text;
}

export const ttsSupported = () => true; // backend-stem óf browserstem: er is altijd iets

// Warm de voorleesaudio (+ tijdmarkeringen) vast in de cache, zonder af te
// spelen. Roep dit aan als de gebruiker voorlezen daadwerkelijk gebruikt, zodat
// de vólgende dia meteen klinkt i.p.v. seconden te laden. Fire-and-forget.
export function prewarmSpeech(markdown, language) {
  const text = plainText(markdown);
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
  const n = Math.min(blocks.length, blockStarts.length);
  const startMs = [];
  for (let i = 0; i < n; i++) {
    const charAt = blockStarts[i];
    let j = 0;
    for (let k = 0; k < markStartChar.length; k++) { if (markStartChar[k] <= charAt) j = k; else break; }
    startMs.push(marks[j]?.t ?? 0);
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
  const { text, blockStarts } = speechFromMarkdown(markdown);
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
