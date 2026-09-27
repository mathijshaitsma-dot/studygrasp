// Voorkeuren (persistent) en sessie-caches.

const PREFS_KEY = "sc.prefs";

const BROWSER_LANGUAGE = {
  nl: "Nederlands", en: "English", de: "Deutsch", fr: "Français", es: "Español",
};

export function browserLanguagePreference() {
  const code = (navigator.languages?.[0] || navigator.language || "en").slice(0, 2).toLowerCase();
  return BROWSER_LANGUAGE[code] || "English";
}

const defaults = {
  theme: "dark",              // "dark" | "light"
  language: browserLanguagePreference(), // browsertaal; handmatig wijzigbaar
  detailLevel: "normal",      // "short" | "normal" | "long"
  audienceLevel: "intermediate", // "beginner" | "intermediate" | "advanced"
  panelWidth: 0,              // 0 = automatische verdeling dia/uitleg
  explainScale: 1,            // tekstgrootte van de uitleg (0.8 - 1.6)
  slideScale: 1,              // inzoomen op de dia zelf (1 - 3; 1 = passend)
  plan: "free",               // legacy lokale voorkeur; accountplan komt van de backend
};

function load() {
  try {
    const stored = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    // Oudere versies gebruikten "auto": de UI volgde dan de browser, maar de
    // AI volgde de dia. Migreer naar één concrete taal zodat werkelijk alles
    // dezelfde browsertaal gebruikt.
    if (!stored.language || stored.language === "auto") stored.language = browserLanguagePreference();
    const loaded = { ...defaults, ...stored };
    localStorage.setItem(PREFS_KEY, JSON.stringify(loaded));
    return loaded;
  }
  catch { return { ...defaults }; }
}

export const prefs = load();

// Legacy browser-id voor oudere lokale data. Credits gebruiken nu altijd het
// ingelogde account-id; deze waarde telt dus niet meer als quota-identiteit.
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
// v2 wist uitleg uit oudere promptversies uit de browser. De backend-cache
// gebruikt PROMPT_VERSION, maar zonder deze bump zou localStorage alsnog een
// oude, langere uitleg kunnen tonen zonder de backend te raadplegen.
const EXPLAIN_STORE_KEY = "sc.explain.v2";
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
