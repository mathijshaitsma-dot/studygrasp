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

// Markdown → voorleesbare platte tekst (formules worden overgeslagen).
export function plainText(markdown) {
  const div = document.createElement("div");
  div.innerHTML = renderMarkdown(markdown);
  div.querySelectorAll(".katex-display, .katex").forEach(k => k.replaceWith(" (formule) "));
  div.querySelectorAll("pre").forEach(k => k.replaceWith(" (code) "));
  return (div.textContent || "").replace(/\s+/g, " ").trim();
}

export const ttsSupported = () => true; // backend-stem óf browserstem: er is altijd iets

// Sessieteller: elke nieuwe speak/stop maakt lopende (asynchrone) pogingen ongeldig.
let session = 0;
let currentAudio = null;
let currentUrl = null;

function cleanupAudio() {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = null;
  currentAudio = null;
}

export function stopSpeech() {
  session++;
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
export async function speak(markdown, language, onEnd, onStart) {
  stopSpeech();
  const my = ++session;
  const text = plainText(markdown);
  if (!text) { onEnd?.(); return; }
  const locale = LANG_MAP[language] || uiLocale();

  try {
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
      currentAudio.onended = () => { cleanupAudio(); onEnd?.(); };
      currentAudio.onerror = () => { cleanupAudio(); onEnd?.(); };
      onStart?.();
      await currentAudio.play();
      return;
    }
  } catch { /* backend niet bereikbaar → browserstem */ }

  if (my !== session) return;
  onStart?.();
  speakInBrowser(text, locale, onEnd);
}
