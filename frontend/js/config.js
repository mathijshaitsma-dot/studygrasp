// Basis-URL van de backend.
// Volgorde: expliciete override (localStorage) > window-var > automatisch.
// Online staan frontend en API in dezelfde container en gebruiken we dus de
// huidige origin. Alleen de losse lokale dev-server op 5173 praat met :8000.
const localDev = ["localhost", "127.0.0.1"].includes(location.hostname)
  && location.port !== "8000";
export const API_BASE = (
  localStorage.getItem("sc.apiBase") ||
  window.SC_API_BASE ||
  (localDev ? "http://localhost:8000" : location.origin)
).replace(/\/+$/, "");
