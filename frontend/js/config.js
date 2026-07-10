// Basis-URL van de backend.
// Volgorde: expliciete override (localStorage) > window-var > zelfde origin als
// de pagina op poort 8000 draait > localhost:8000 (lokale ontwikkeling).
export const API_BASE = (
  localStorage.getItem("sc.apiBase") ||
  window.SC_API_BASE ||
  (location.port === "8000" ? location.origin : "http://localhost:8000")
).replace(/\/+$/, "");
