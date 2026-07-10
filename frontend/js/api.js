// API-client voor de StudyCopilot-backend (zie API_DOCS.md).
import { API_BASE } from "./config.js";
import { userId, prefs } from "./state.js";

// Identificeer de gebruiker (voor de freemium-teller). Meegestuurd op elke call;
// de backend telt alleen verse generaties, cache-hits blijven gratis.
function authHeaders(extra = {}) {
  return { "X-User-Id": userId, "X-User-Plan": prefs.plan || "free", ...extra };
}

async function jsonOrThrow(resp) {
  let data = null;
  try { data = await resp.json(); } catch { /* geen JSON */ }
  if (!resp.ok || (data && data.ok === false)) {
    const code = data?.error_code || resp.status;
    // Dagbudget op: laat de hele app reageren met de upgrade-melding.
    if (code === "QUOTA_EXCEEDED") {
      window.dispatchEvent(new CustomEvent("sc:quota", { detail: data?.details || {} }));
    }
    const msg = data?.message || data?.error?.message || `Serverfout (${resp.status})`;
    const err = new Error(msg);
    err.code = code;
    throw err;
  }
  return data;
}

async function post(path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
  });
  return jsonOrThrow(resp);
}

async function get(path) {
  return jsonOrThrow(await fetch(`${API_BASE}${path}`, { headers: authHeaders() }));
}

async function send(method, path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: authHeaders(body !== undefined ? { "Content-Type": "application/json" } : {}),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return jsonOrThrow(resp);
}

// ---------- SSE-streaming ----------
// Leest een text/event-stream response en roept per JSON-event `onEvent` aan.
// Onbekende event-types worden door de aanroeper genegeerd (toekomstbestendig).
async function readSSE(resp, onEvent) {
  if (!resp.ok) {
    // Fout vóór het streamen begint komt als normale JSON-body.
    await jsonOrThrow(resp);
    throw new Error("Stream kon niet worden geopend.");
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
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

// Start een streamende POST. handlers: { onStart, onDelta, onDone, onError }.
// Retourneert een abort-functie.
function streamPost(path, body, handlers) {
  const ctrl = new AbortController();
  (async () => {
    try {
      const resp = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...body, stream: true }),
        signal: ctrl.signal,
      });
      let finished = false;
      await readSSE(resp, (ev) => {
        if (ev.type === "start") handlers.onStart?.(ev);
        else if (ev.type === "delta") handlers.onDelta?.(ev.text ?? "");
        else if (ev.type === "done") { finished = true; handlers.onDone?.(ev); }
        else if (ev.type === "error") { finished = true; handlers.onError?.(new Error(ev.message || "AI-fout")); }
      });
      if (!finished) handlers.onDone?.({});
    } catch (err) {
      if (err.name !== "AbortError") handlers.onError?.(err);
    }
  })();
  return () => ctrl.abort();
}

// ---------- publieke API ----------
export const api = {
  base: API_BASE,

  slideImageUrl: (hash, pageIndex, resolution = "display") =>
    `${API_BASE}/slide-image/${hash}/${pageIndex}?resolution=${resolution}`,

  // Upload met voortgang (XHR, want fetch geeft geen upload-progress).
  upload(file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/upload`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress?.(e.loaded / e.total);
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { /* leeg */ }
        if (xhr.status >= 200 && xhr.status < 300 && data?.ok !== false) resolve(data);
        else reject(new Error(data?.message || `Upload mislukt (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Netwerkfout — draait de backend?"));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  },

  getDocuments: () => get("/documents"),
  getDocument: (hash) => get(`/document/${hash}`),
  deleteDocument: (hash) => fetch(`${API_BASE}/document/${hash}`, { method: "DELETE" }).then(jsonOrThrow),
  saveProgress: (hash, pageIndex) => post(`/document/${hash}/progress`, { page_index: pageIndex }),

  // mappen (vakken)
  folders: () => get("/folders"),
  folderCreate: (name) => post("/folders", { name }),
  folderRename: (id, name) => send("PATCH", `/folders/${id}`, { name }),
  folderDelete: (id) => send("DELETE", `/folders/${id}`),
  setDocumentFolder: (hash, folderId) => post(`/document/${hash}/folder`, { folder_id: folderId }),

  // tentamenmodus (scope = { file_hash } of { folder_id })
  examGenerate: (body) => post("/exam/generate", body),
  examAttempt: (body) => post("/exam/attempt", body),
  examPlan: (scope) => get(`/exam/plan?${scope.file_hash ? `file_hash=${scope.file_hash}` : `folder_id=${scope.folder_id}`}`),

  explainStream: (body, handlers) => streamPost("/explain", body, handlers),
  askRegionStream: (body, handlers) => streamPost("/ask-region", body, handlers),
  summaryStream: (body, handlers) => streamPost("/summary", body, handlers),

  quizGenerate: (body) => post("/quiz/generate", body),
  quizGrade: (body) => post("/quiz/grade", body),

  flashcardsGenerate: (body) => post("/flashcards/generate", body),
  flashcardsGet: (hash) => get(`/flashcards/${hash}`),
  flashcardsReview: (body) => post("/flashcards/review", body),

  search: (q, hash = "", limit = 20) =>
    get(`/search?q=${encodeURIComponent(q)}${hash ? `&file_hash=${hash}` : ""}&limit=${limit}`),

  usage: () => get("/usage"),

  health: () => get("/"),
};
