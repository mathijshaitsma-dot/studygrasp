// Basis-URL van de backend. Op een publieke HTTPS-origin staan frontend en API
// altijd in dezelfde container. Een oude ontwikkeloverride in localStorage mag
// de geïnstalleerde telefoonapp dus nooit terugsturen naar een laptopadres.
const isLoopback = ["localhost", "127.0.0.1"].includes(location.hostname);
const isPrivateLan = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(location.hostname);
const localDev = (isLoopback || isPrivateLan) && location.port !== "8000";
let storedOverride = "";
try {
  if (localDev) storedOverride = localStorage.getItem("sc.apiBase") || "";
  else localStorage.removeItem("sc.apiBase");
} catch { /* localStorage kan in privémodus geblokkeerd zijn */ }

export const API_BASE = (
  storedOverride ||
  window.SC_API_BASE ||
  (localDev ? "http://localhost:8000" : location.origin)
).replace(/\/+$/, "");
