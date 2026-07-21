// Voorkeuren (persistent) en sessie-caches.

const PREFS_KEY = "sc.prefs";

const defaults = {
  theme: "dark",              // "dark" | "light"
  language: "auto",           // "auto" | "Nederlands" | "English" | ...
  detailLevel: "normal",      // "short" | "normal" | "long"
  audienceLevel: "intermediate", // "beginner" | "intermediate" | "advanced"
  panelWidth: 0,              // 0 = automatische verdeling dia/uitleg
  plan: "free",               // "free" | "plus" | "premium" (later via account)
};

function load() {
  try { return { ...defaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; }
  catch { return { ...defaults }; }
}

export const prefs = load();

// Anonieme, blijvende gebruikers-id voor de freemium-teller. Nog geen account:
// dit identificeert deze browser, zodat het gratis dagbudget per persoon telt.
const USER_KEY = "sc.uid";
export const userId = (() => {
  let id = localStorage.getItem(USER_KEY);
  if (!id) {
    id = crypto.randomUUID?.() || (String(Math.random()).slice(2) + Date.now().toString(36));
    localStorage.setItem(USER_KEY, id);
  }
  return id;
})();

export function savePrefs(patch = {}) {
  Object.assign(prefs, patch);
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  if (patch.theme) applyTheme();
}

export function applyTheme() {
  document.documentElement.dataset.theme = prefs.theme;
}

// Cache van uitleg per dia+instellingen, zodat wisselen van modus of
// terugbladeren instant is. Persistent in localStorage (begrensde LRU), zodat
// ook het heropenen van een document na een herstart geen netwerk-roundtrip
// per dia meer kost — de backend cachet óók, maar dit scheelt de wachttijd.
const EXPLAIN_STORE_KEY = "sc.explain.v1";
const EXPLAIN_MAX_ENTRIES = 150;

const explainCache = (() => {
  try { return new Map(JSON.parse(localStorage.getItem(EXPLAIN_STORE_KEY) || "[]")); }
  catch { return new Map(); }
})();

function persistExplainCache() {
  try { localStorage.setItem(EXPLAIN_STORE_KEY, JSON.stringify([...explainCache])); }
  catch { /* quota vol: dan alleen in-memory — geen probleem */ }
}

export function explainKey(hash, page, mode, audience, detail, language) {
  return [hash, page, mode, audience, detail, language].join("|");
}
export function getCachedExplain(key) { return explainCache.get(key); }
export function setCachedExplain(key, markdown) {
  explainCache.delete(key); // opnieuw invoegen = achteraan (LRU op invoegvolgorde)
  explainCache.set(key, markdown);
  while (explainCache.size > EXPLAIN_MAX_ENTRIES) {
    explainCache.delete(explainCache.keys().next().value);
  }
  persistExplainCache();
}

// Chatgeschiedenis per dia (alleen deze sessie).
const chatStore = new Map();
export function getChat(hash, page) {
  const key = `${hash}|${page}`;
  if (!chatStore.has(key)) chatStore.set(key, []);
  return chatStore.get(key);
}
