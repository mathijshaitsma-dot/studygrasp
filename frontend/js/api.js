// API-client voor de StudyGrasp-backend (zie API_DOCS.md).
import { API_BASE } from "./config.js";
import { t } from "./i18n.js";

// De backend stuurt Nederlandse meldingen, maar wél een taal-onafhankelijke
// error_code. We vertalen op de code (err_<CODE>) in de taal van de gebruiker;
// staat er geen vertaling, dan valt het terug op de servertekst (nooit leeg).
function localizeError(code, serverMsg, details) {
  if (!code) return serverMsg || t("err_generic");
  const key = `err_${code}`;
  const translated = t(key, details || {});
  return translated === key ? (serverMsg || t("err_generic")) : translated;
}

// Sessietoken van het ingelogde account. De backend leidt hier identiteit én
// plan uit af — bewust niet uit een header die de client zelf kan kiezen, want
// dan kon iedereen zijn eigen limiet ophogen.
const TOKEN_KEY = "sc.token";
export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(token) {
  try { token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY); } catch { /* privémodus */ }
}
function authHeaders(extra = {}) {
  const token = getToken();
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...extra };
}

// Netwerkfouten (bv. een kort wifi-hikje) een paar keer met oplopende backoff
// proberen — nooit bij een normale HTTP-foutstatus of ok:false-body (die komen
// terug als een resolved Response, geen reject), want een 404 of
// QUOTA_EXCEEDED moet niet herhaald worden.
async function fetchWithRetry(url, options, retries = 2, delayMs = 300) {
  try {
    return await fetch(url, options);
  } catch (err) {
    if (retries <= 0) throw err;
    await new Promise((r) => setTimeout(r, delayMs));
    return fetchWithRetry(url, options, retries - 1, delayMs * 3);
  }
}

async function jsonOrThrow(resp) {
  let data = null;
  try { data = await resp.json(); } catch { /* geen JSON */ }
  if (!resp.ok || (data && data.ok === false)) {
    const code = data?.error_code || resp.status;
    // Maandcredits op: laat de hele app reageren met de upgrade-melding.
    if (code === "QUOTA_EXCEEDED") {
      window.dispatchEvent(new CustomEvent("sc:quota", { detail: data?.details || {} }));
    }
    // Sessie verlopen of elders uitgelogd: terug naar het inlogscherm i.p.v. de
    // gebruiker in een half-werkende app laten zitten.
    if (code === "NOT_AUTHENTICATED") {
      window.dispatchEvent(new CustomEvent("sc:unauthenticated"));
    }
    const serverMsg = data?.message || data?.error?.message || t("err_http", { status: resp.status });
    const err = new Error(localizeError(data?.error_code, serverMsg, data?.details));
    err.code = code;
    throw err;
  }
  return data;
}

async function post(path, body) {
  const resp = await fetchWithRetry(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
  });
  return jsonOrThrow(resp);
}

async function get(path) {
  return jsonOrThrow(await fetchWithRetry(`${API_BASE}${path}`, { headers: authHeaders() }));
}

// Een <img src="..."> kan geen Authorization-header meesturen. Sinds
// documenten accountgebonden zijn gaf elke rechtstreekse dia-URL daarom 401,
// terwijl tekstuele API-aanvragen wel werkten. Haal beschermde afbeeldingen
// als blob op en geef het element een lokale object-URL. De volgnummercontrole
// voorkomt dat een trage vorige dia een nieuwere dia weer overschrijft.
const imageState = new WeakMap();
let imageRequestId = 0;

async function setAuthenticatedImage(element, url, { cacheBust = false } = {}) {
  const requestId = ++imageRequestId;
  const previous = imageState.get(element);
  imageState.set(element, { requestId, objectUrl: previous?.objectUrl || "" });

  const resp = await fetchWithRetry(url, {
    headers: authHeaders(),
    cache: cacheBust ? "reload" : "default",
  });
  if (!resp.ok) await jsonOrThrow(resp);
  const objectUrl = URL.createObjectURL(await resp.blob());
  const current = imageState.get(element);
  if (!current || current.requestId !== requestId) {
    URL.revokeObjectURL(objectUrl);
    return false;
  }

  const oldUrl = current.objectUrl;
  imageState.set(element, { requestId, objectUrl });
  element.src = objectUrl;
  if (oldUrl) setTimeout(() => URL.revokeObjectURL(oldUrl), 0);
  return true;
}

async function authenticatedObjectUrl(url, { cacheBust = false } = {}) {
  const resp = await fetchWithRetry(url, {
    headers: authHeaders(),
    cache: cacheBust ? "reload" : "default",
  });
  if (!resp.ok) await jsonOrThrow(resp);
  return URL.createObjectURL(await resp.blob());
}

async function send(method, path, body) {
  const resp = await fetchWithRetry(`${API_BASE}${path}`, {
    method,
    headers: authHeaders(body !== undefined ? { "Content-Type": "application/json" } : {}),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return jsonOrThrow(resp);
}

// ---------- SSE-streaming ----------
// Leest een text/event-stream response en roept per JSON-event `onEvent` aan.
// Onbekende event-types worden door de aanroeper genegeerd (toekomstbestendig).
async function readSSE(resp, onEvent, onActivity) {
  if (!resp.ok) {
    // Fout vóór het streamen begint komt als normale JSON-body.
    await jsonOrThrow(resp);
    throw new Error(t("err_stream_open"));
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onActivity?.();
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try { onEvent(JSON.parse(payload)); } catch { /* kapotte regel overslaan */ }
    }
  }
}

// Een stream die stilvalt — een provider die blijft hangen, of een verbinding
// die wegvalt zonder netjes te sluiten — liet de UI eindeloos op "AI is een
// uitleg aan het maken" staan: reader.read() resolvet dan nooit én rejecteert
// nooit. Deze waakhond breekt af zodra er zo lang niets binnenkomt, zodat de
// gewone foutmelding mét retry-knop verschijnt. Elk binnenkomend event reset
// hem, inclusief de heartbeats die de backend stuurt terwijl hij op een andere
// generatie van dezelfde dia wacht.
const STREAM_STALL_MS = 30000;

// Start een streamende POST.
// handlers: { onStart, onWaiting, onDelta, onDone, onError }.
// Retourneert een abort-functie.
function streamPost(path, body, handlers) {
  const ctrl = new AbortController();
  let stalled = false;
  let timer = null;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { stalled = true; ctrl.abort(); }, STREAM_STALL_MS);
  };
  const disarm = () => clearTimeout(timer);

  (async () => {
    arm();
    try {
      const resp = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...body, stream: true }),
        signal: ctrl.signal,
      });
      arm();
      let finished = false;
      await readSSE(resp, (ev) => {
        if (ev.type === "start") handlers.onStart?.(ev);
        else if (ev.type === "waiting") handlers.onWaiting?.(ev);
        else if (ev.type === "delta") handlers.onDelta?.(ev.text ?? "");
        else if (ev.type === "done") { finished = true; handlers.onDone?.(ev); }
        else if (ev.type === "error") { finished = true; handlers.onError?.(new Error(localizeError(ev.code, ev.message, ev.details))); }
      }, arm);
      disarm();
      if (!finished) handlers.onDone?.({});
    } catch (err) {
      disarm();
      // Afgebroken door de waakhond => wél een foutmelding (met retry).
      // Afgebroken door de gebruiker (wegnavigeren) => stil.
      if (stalled) handlers.onError?.(new Error(t("err_stalled")));
      else if (err.name !== "AbortError") handlers.onError?.(err);
    }
  })();

  return () => { disarm(); ctrl.abort(); };
}

// ---------- publieke API ----------
export const api = {
  base: API_BASE,

  slideImageUrl: (hash, pageIndex, resolution = "display") =>
    `${API_BASE}/slide-image/${hash}/${pageIndex}?resolution=${resolution}`,

  setImage: (element, url, options) => setAuthenticatedImage(element, url, options),
  setSlideImage: (element, hash, pageIndex, resolution = "display", options) =>
    setAuthenticatedImage(
      element,
      `${API_BASE}/slide-image/${hash}/${pageIndex}?resolution=${resolution}`,
      options,
    ),
  slideImageObjectUrl: (hash, pageIndex, resolution = "display", options) =>
    authenticatedObjectUrl(
      `${API_BASE}/slide-image/${hash}/${pageIndex}?resolution=${resolution}`,
      options,
    ),

  // Upload met voortgang (XHR, want fetch geeft geen upload-progress).
  // kind="quick" = losse huiswerkfoto; kind="exercise" = opgave gekoppeld aan een
  // college (opts.sourceFileHash) en/of vak (opts.folderId).
  upload(file, onProgress, kind = null, opts = {}) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/upload`);
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress?.(e.loaded / e.total);
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { /* leeg */ }
        if (xhr.status >= 200 && xhr.status < 300 && data?.ok !== false) resolve(data);
        else reject(new Error(localizeError(data?.error_code, data?.message || t("err_upload_failed", { status: xhr.status }), data?.details)));
      };
      xhr.onerror = () => reject(new Error(t("err_network")));
      const form = new FormData();
      form.append("file", file);
      if (kind) form.append("kind", kind);
      if (opts.folderId) form.append("folder_id", opts.folderId);
      if (opts.sourceFileHash) form.append("source_file_hash", opts.sourceFileHash);
      xhr.send(form);
    });
  },

  getDocuments: () => get("/documents"),
  getDocument: (hash) => get(`/document/${hash}`),
  deleteDocument: (hash) => send("DELETE", `/document/${hash}`),
  saveProgress: (hash, pageIndex) => post(`/document/${hash}/progress`, { page_index: pageIndex }),

  // mappen (vakken)
  folders: () => get("/folders"),
  folderCreate: (name) => post("/folders", { name }),
  folderRename: (id, name) => send("PATCH", `/folders/${id}`, { name }),
  folderDelete: (id) => send("DELETE", `/folders/${id}`),
  setDocumentFolder: (hash, folderId) => post(`/document/${hash}/folder`, { folder_id: folderId }),
  folderProgress: (id) => get(`/folders/${id}/progress`),

  // tentamenmodus (scope = { file_hash } of { folder_id })
  examGenerate: (body) => post("/exam/generate", body),
  examAttempt: (body) => post("/exam/attempt", body),
  examPlan: (scope) => get(`/exam/plan?${scope.file_hash ? `file_hash=${scope.file_hash}` : `folder_id=${scope.folder_id}`}`),

  // Uitleg van een dia alvast op de achtergrond laten genereren (fire-and-forget).
  // Voor het vast warmen van bv. de Kernpunten-versie (mode:"study") zodra de
  // gebruiker die modus gebruikt — dan is omschakelen instant.
  prefetchExplain(hash, page, { language = "auto", detailLevel = "normal", mode = "explain", audienceLevel = "intermediate" } = {}) {
    const qs = `language=${encodeURIComponent(language)}&detail_level=${detailLevel}&mode=${mode}&audience_level=${audienceLevel}`;
    fetch(`${API_BASE}/prefetch/${hash}/${page}?${qs}`, { method: "POST", headers: authHeaders() }).catch(() => {});
  },

  // ---- account ----
  authConfig: () => get("/auth/config"),
  register: (email, password) => post("/auth/register", { email, password }),
  loginWithGoogle: (idToken) => post("/auth/google", { id_token: idToken }),
  forgotPassword: (email) => post("/auth/forgot", { email }),
  resetPassword: (token, password) => post("/auth/reset", { token, password }),
  login: (email, password) => post("/auth/login", { email, password }),
  logout: () => post("/auth/logout", {}),
  me: () => get("/auth/me"),

  explainStream: (body, handlers) => streamPost("/explain", body, handlers),
  explain: (body) => post("/explain", { ...body, stream: false }),

  // Haal een uitleg ALLEEN op als die al in de cache staat: de backend genereert
  // dan niets en schrijft geen tegoed af. Bedoeld om te polsen of de volgende
  // dia al klaar is, zodat we daar de voorleesaudio mee kunnen voorwarmen.
  // Geeft de markdown terug, of null als de dia er nog niet is.
  async explainCached(body) {
    try {
      const data = await post("/explain", { ...body, cache_only: true, stream: false });
      return data?.markdown || null;
    } catch { return null; }
  },
  askRegionStream: (body, handlers) => streamPost("/ask-region", body, handlers),
  summaryStream: (body, handlers) => streamPost("/summary", body, handlers),

  quizGenerate: (body) => post("/quiz/generate", body),
  quizGrade: (body) => post("/quiz/grade", body),
  quizRecovery: (body) => post("/quiz/recovery", body),

  // opgaven: koppel een opgavenblad/oefententamen aan een college en vind de
  // bijbehorende dia's terug ("waar staat dit ook alweer?").
  exercisesList: (sourceHash) => get(`/exercises/${sourceHash}`),
  exercisesInFolder: (folderId) => get(`/exercises-in-folder/${folderId}`),
  exerciseQuestions: (body) => post("/exercise/questions", body),
  exerciseLocate: (body) => post("/exercise/locate", body),
  exerciseHelpStream: (body, handlers) => streamPost("/exercise/help", body, handlers),

  flashcardsGenerate: (body) => post("/flashcards/generate", body),
  flashcardsGet: (hash, language = "auto") => get(`/flashcards/${hash}?language=${encodeURIComponent(language)}`),
  flashcardsReview: (body) => post("/flashcards/review", body),

  notesGet: (hash) => get(`/document/${hash}/notes`),
  notesUpdate: (hash, body) => post(`/document/${hash}/notes`, body),

  // woordenlijsten
  wordlists: () => get("/wordlists"),
  wordlistGet: (id) => get(`/wordlists/${id}`),
  wordlistCreate: (body) => post("/wordlists", body),
  wordlistUpdate: (id, body) => send("PATCH", `/wordlists/${id}`, body),
  wordlistDelete: (id) => send("DELETE", `/wordlists/${id}`),
  wordlistReview: (id, body) => post(`/wordlists/${id}/review`, body),
  wordlistGenerate: (body) => post("/wordlists/generate", body),

  search: (q, hash = "", limit = 20) =>
    get(`/search?q=${encodeURIComponent(q)}${hash ? `&file_hash=${hash}` : ""}&limit=${limit}`),

  usage: () => get("/usage"),

  health: () => get("/"),
};
