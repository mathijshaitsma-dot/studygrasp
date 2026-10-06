// App-shell: router + globale sneltoetsen + instellingen-drawer.
import { el, icon, openModal, toast } from "./util.js";
import { prefs, savePrefs, applyTheme } from "./state.js";
import { t, uiLang } from "./i18n.js";
import { renderHome } from "./views/home.js";
import { renderWorkspace } from "./views/study.js";
import { renderFolder } from "./views/folder.js";
import { renderPrivacy } from "./views/privacy.js";
import { renderBilling } from "./views/billing.js";
import { renderAdmin } from "./views/admin.js";
import { renderQuick } from "./views/quick.js";
import { renderWordlist } from "./views/wordlist.js";
import { renderSavedOverview } from "./views/overview.js";
import { renderSmartAnswer } from "./views/smartanswer.js";
import { openSearch } from "./search.js";
import { renderEmailVerification, renderLogin, renderReset } from "./views/login.js";
import { api, getToken, setToken } from "./api.js";

applyTheme();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

// Dubbel laden hard afvangen. Deze module mag maar één keer draaien: een tweede
// exemplaar (bijv. doordat het entry-script een ?v=-query had en de views
// "../app.js" zonder query importeren) registreert een tweede hashchange-
// listener en rendert daardoor elke pagina twee keer.
if (window.__studygraspBooted) {
  throw new Error("app.js is al geladen — tweede exemplaar gestopt");
}
window.__studygraspBooted = true;

const appRoot = document.getElementById("app");

// ---------- offline-indicator ----------
// De service worker houdt laatst bekeken content offline bekijkbaar, maar AI-
// generatie kan sowieso niet zonder netwerk — dit maakt dat herkenbaar i.p.v.
// een onduidelijke foutmelding bij elke mislukte aanvraag.
const offlineBanner = el("div", { class: "offline-banner", style: "display:none" }, t("offline_banner"));
document.body.prepend(offlineBanner);
function updateOfflineBanner() {
  offlineBanner.style.display = navigator.onLine ? "none" : "flex";
}
window.addEventListener("online", updateOfflineBanner);
window.addEventListener("offline", updateOfflineBanner);
updateOfflineBanner();

export function navigate(hash) {
  location.hash = hash;
}

// ---------- focusmodus (minimale UI + echt volledig scherm) ----------
// Gebruikt de Fullscreen API i.p.v. F11, zodat de browserbalk niet over de
// app heen schuift als je muis de bovenkant van het scherm raakt.
export function setFocusMode(on) {
  document.body.classList.toggle("focus-mode", on);
  if (on) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  } else if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
}
export function toggleFocusMode() {
  setFocusMode(!document.body.classList.contains("focus-mode"));
}
// Esc (of ander verlaten van volledig scherm) sluit ook de focusmodus.
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && document.body.classList.contains("focus-mode")) {
    setFocusMode(false);
  }
});

// Wie is er ingelogd? Wordt één keer opgehaald en daarna hergebruikt, zodat
// niet elke navigatie een extra call kost.
let currentUser = null;
export function getCurrentUser() { return currentUser; }
let guestActivation = null;

async function activateGuest() {
  if (currentUser?.guest) return currentUser;
  if (!guestActivation) {
    guestActivation = api.createGuest().then((guest) => {
      setToken(guest.token);
      currentUser = guest.user;
      return currentUser;
    }).finally(() => { guestActivation = null; });
  }
  return guestActivation;
}

let authModalClose = null;
export function openAuthModal() {
  if (!currentUser?.guest) return;
  if (authModalClose && document.querySelector(".guest-auth-host")) return;
  authModalClose = null;
  const host = el("div", { class: "guest-auth-host" });
  renderLogin(host, (user) => {
    currentUser = user;
    authModalClose?.();
    authModalClose = null;
    route();
  }, { embedded: true, neutral: true });
  const close = openModal(host, { center: true, small: true, label: t("auth_login_btn") });
  authModalClose = () => { close(); authModalClose = null; };
}

function syncGuestLoginButton() {
  const guest = Boolean(currentUser?.guest);
  document.body.classList.toggle("guest-mode", guest);
}

// De knop hoort bij de navigatie waar hij visueel voorspelbaar blijft. Views
// plaatsen hem vlak vóór zoeken (of, als zoeken ontbreekt, vóór de acties).
export function guestLoginButton({ compact = false, label = "login" } = {}) {
  if (!currentUser?.guest) return null;
  return el("button", {
    class: `btn primary guest-login-inline${compact ? " compact" : ""}`,
    onclick: openAuthModal,
  }, icon("right", "sm"), t(label === "signin" ? "auth_signin_cta" : "auth_login_btn"));
}

export function loginRequiredAction() {
  return el("button", { class: "btn primary", onclick: openAuthModal },
    icon("right", "sm"), t("auth_signin_cta"));
}

// Views renderen asynchroon: ze zetten eerst een spinner neer en vullen zichzelf
// pas aan na hun API-calls. Navigeer je in de tussentijd verder, dan zou die
// trage render alsnog in #app landen. Elke route krijgt daarom een eigen
// container (display:contents, dus lay-outneutraal); die van de vorige route is
// dan al losgekoppeld en wat erin belandt is onzichtbaar.
function route() {
  setFocusMode(false);
  document.documentElement.lang = uiLang();
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const app = el("div", { class: "route-view" });
  appRoot.replaceChildren(app);
  syncGuestLoginButton();

  // Herstellink uit de mail: werkt juist zónder te zijn ingelogd.
  if (parts[0] === "reset" && parts[1]) {
    renderReset(app, parts[1], () => navigate("#/"));
    return;
  }

  // Registratielink uit de mail: account activeren en meteen inloggen.
  if (parts[0] === "verify-email" && parts[1]) {
    renderEmailVerification(app, parts[1], (user) => {
      currentUser = user;
      navigate("#/");
    });
    return;
  }

  // Het privacybeleid moet vóór registratie leesbaar zijn. Hiervoor is bewust
  // geen sessie nodig; er staat uitsluitend openbare productinformatie.
  if (parts[0] === "privacy") {
    renderPrivacy(app);
    return;
  }

  // Niet ingelogd? Dan is er niets te zien: alle data hoort bij een account.
  if (!currentUser) {
    renderLogin(app, (user) => { currentUser = user; route(); });
    return;
  }

  if (parts[0] === "doc" && parts[1]) {
    const tab = parts[2] || "study";
    const page = parts[3] != null ? parseInt(parts[3], 10) : null;
    renderWorkspace(app, parts[1], tab, page);
  } else if (parts[0] === "folder" && parts[1]) {
    renderFolder(app, parts[1], parts[2] || null);
  } else if (parts[0] === "pick" && parts[1]) {
    // Home in "kies documenten voor deze map"-stand: zie renderHome.
    renderHome(app, parts[1]);
  } else if (parts[0] === "quick") {
    renderQuick(app, parts[1] || null);
  } else if (parts[0] === "wordlist" && parts[1]) {
    renderWordlist(app, parts[1]);
  } else if (parts[0] === "overview" && parts[1]) {
    renderSavedOverview(app, parts[1]);
  } else if (parts[0] === "answer" && parts[1]) {
    renderSmartAnswer(app, parts[1]);
  } else if (parts[0] === "billing") {
    renderBilling(app, currentUser);
  } else if (parts[0] === "admin" && currentUser?.plan === "owner") {
    renderAdmin(app);
  } else {
    renderHome(app);
  }
}

// Volledige her-render van de huidige view (bijv. na een taalwissel).
export function rerender() {
  route();
}

window.addEventListener("hashchange", route);

// Bij het opstarten: is het bewaarde token nog geldig? Zo ja, meteen door naar
// de app; zo nee (verlopen of uitgelogd elders), dan het inlogscherm.
(async () => {
  if (getToken()) {
    try { currentUser = (await api.me()).user; }
    catch { setToken(""); currentUser = null; }
  }
  if (!currentUser) {
    try { await activateGuest(); }
    catch { /* bij een onbereikbare backend blijft het gewone inlogscherm beschikbaar */ }
  }
  route();
})();

// Raakt de sessie onderweg ongeldig (verlopen of elders uitgelogd), dan willen
// we niet dat de gebruiker in een half-werkende app achterblijft.
window.addEventListener("sc:unauthenticated", async () => {
  setToken("");
  currentUser = null;
  try { await activateGuest(); }
  catch { /* route toont dan het volledige inlogscherm */ }
  route();
});

// Een AI-poging als gast opent niet onverwacht een modaal venster. We tonen
// een rustige, niet-blokkerende melding; aanmelden gebeurt pas na een klik.
window.addEventListener("sc:login-required", () => {
  if (!currentUser?.guest) return;
  document.querySelector(".login-required-notice")?.remove();
  const notice = el("div", { class: "login-required-notice", role: "status" },
    el("div", { class: "login-required-copy" },
      el("strong", {}, t("login_required_title")),
      el("span", {}, t("login_required_body"))),
    loginRequiredAction(),
    el("button", {
      class: "btn ghost icon-btn", "aria-label": t("close"),
      onclick: () => notice.remove(),
    }, icon("x", "sm")),
  );
  document.getElementById("toast-root")?.append(notice);
});

export async function logout() {
  try { await api.logout(); } catch { /* token was al ongeldig */ }
  setToken("");
  currentUser = null;
  try { await activateGuest(); }
  catch { /* fallback: volledig inlogscherm */ }
  navigate("#/");
  route();
}

function accountInitials(email = "") {
  const name = String(email || "").split("@")[0].replace(/[^a-z0-9]+/gi, " ").trim();
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length > 1) return (parts[0][0] + parts.at(-1)[0]).toUpperCase();
  return (name.slice(0, 2) || "SG").toUpperCase();
}

function accountPlanLabel(plan = "free") {
  const normalized = ({ plus: "premium", pro: "ultra", unlimited: "ultra" })[plan] || plan;
  const key = ["owner", "premium", "ultra"].includes(normalized)
    ? `account_plan_${normalized}` : "account_plan_free";
  return t(key);
}

function accountAvatarColor(email = "") {
  const colors = ["#dc2626", "#16a34a", "#2563eb", "#d97706", "#7c3aed", "#db2777", "#0d9488"];
  const hash = [...String(email || "").toLowerCase()].reduce((total, char) => total + char.charCodeAt(0), 0);
  return colors[hash % colors.length];
}

// ---------- upgrade-melding (creditbudget op) ----------
// Wordt vanuit de api-laag getriggerd zodra de backend QUOTA_EXCEEDED geeft.
// Al een melding open? Dan niet nog een keer (bij meerdere gelijktijdige calls).
window.addEventListener("sc:quota", () => {
  if (document.querySelector(".upgrade-modal")) return;
  const close = openModal(el("div", { class: "upgrade-modal" },
    el("div", { class: "upgrade-badge" }, icon("cards", "lg")),
    el("h3", {}, t("upgrade_title")),
    el("p", {}, t("upgrade_body")),
    el("div", { class: "upgrade-foot" },
      el("button", { class: "btn ghost", onclick: () => close() }, t("upgrade_later")),
      // Placeholder voor de prijzenpagina — hier komt straks de checkout-link.
      el("button", { class: "btn primary", onclick: () => { close(); navigate("#/billing"); } }, t("upgrade_cta")),
    ),
  ), { center: true, small: true });
});

// Globale sneltoets: Ctrl/Cmd+K → zoeken.
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openSearch({
      fileHash: "",
      onPick: (hit) => navigate(`#/doc/${hit.file_hash}/study/${hit.page_index}`),
    });
  }
});

// ---------- instellingen-drawer (gedeeld door alle views) ----------
export function openSettings({ extra } = {}) {
  const root = document.getElementById("modal-root");
  const scrim = el("div", { class: "drawer-scrim" });
  const drawer = el("div", { class: "drawer" });
  const close = () => {
    scrim.remove();
    drawer.remove();
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("mousedown", onAccountOutside, true);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  scrim.addEventListener("mousedown", close);
  document.addEventListener("keydown", onKey);

  const seg = (options, value, onPick) => {
    const wrap = el("div", { class: "seg", style: "width:100%" });
    for (const [val, label] of options) {
      const b = el("button", { class: val === value ? "on" : "", style: "flex:1", onclick: () => {
        onPick(val);
        wrap.querySelectorAll("button").forEach(x => x.classList.remove("on"));
        b.classList.add("on");
      } }, label);
      wrap.append(b);
    }
    return wrap;
  };

  // Taal geldt voor de hele app: interface én AI-uitleg. Na een wissel wordt
  // de view opnieuw opgebouwd en de drawer heropend in de nieuwe taal.
  const langSelect = el("select", { class: "field", onchange: (e) => {
    savePrefs({ language: e.target.value });
    close();
    rerender();
    openSettings({ extra });
  } },
    ...[["Nederlands", "Nederlands"], ["English", "English"], ["Deutsch", "Deutsch"], ["Français", "Français"], ["Español", "Español"]]
      .map(([v, l]) => el("option", { value: v, selected: prefs.language === v }, l)),
  );

  const usageValue = el("span", { class: "account-row-value" }, t("account_usage_loading"));
  const passwordButton = el("button", {
    class: "account-menu-row",
    style: "display:none",
    onclick: async () => {
      passwordButton.disabled = true;
      try {
        await api.forgotPassword(currentUser?.email || "");
        toast(t("account_password_sent"), "ok", 4500);
      } catch (err) {
        toast(err.message || t("err_generic"), "err");
      } finally {
        passwordButton.disabled = false;
      }
    },
  },
    el("span", { class: "account-row-icon" }, icon("refresh", "sm")),
    el("span", { class: "account-row-main" }, t("account_change_password")),
    icon("right", "sm"),
  );

  const accountMenu = el("div", { class: "account-menu", id: "settings-account-menu", hidden: true },
    el("div", { class: "account-menu-row static" },
      el("span", { class: "account-row-icon" }, icon("zap", "sm")),
      el("span", { class: "account-row-main" }, t("account_usage")),
      usageValue,
    ),
    el("button", { class: "account-menu-row", onclick: () => { close(); navigate("#/billing"); } },
      el("span", { class: "account-row-icon" }, icon("cards", "sm")),
      el("span", { class: "account-row-main" }, t("account_plan_billing")),
      el("span", { class: "account-row-value" }, accountPlanLabel(currentUser?.plan)),
      icon("right", "sm"),
    ),
    passwordButton,
    el("button", { class: "account-menu-row", onclick: () => { close(); navigate("#/privacy"); } },
      el("span", { class: "account-row-icon" }, icon("doc", "sm")),
      el("span", { class: "account-row-main" }, t("account_privacy")),
      icon("right", "sm"),
    ),
    currentUser?.plan === "owner" ? el("button", {
      class: "account-menu-row", onclick: () => { close(); navigate("#/admin"); },
    },
      el("span", { class: "account-row-icon" }, icon("settings", "sm")),
      el("span", { class: "account-row-main" }, t("admin_menu")),
      icon("right", "sm"),
    ) : null,
    el("button", { class: "account-menu-row danger", onclick: () => { close(); logout(); } },
      el("span", { class: "account-row-icon" }, icon("right", "sm")),
      el("span", { class: "account-row-main" }, t("auth_logout")),
    ),
  );
  const setAccountMenuOpen = (open) => {
    accountToggle.setAttribute("aria-expanded", String(open));
    accountMenu.hidden = !open;
  };
  const accountToggle = el("button", {
    class: "account-card-head",
    "aria-expanded": "false",
    "aria-haspopup": "true",
    "aria-controls": "settings-account-menu",
    onclick: () => {
      const expanded = accountToggle.getAttribute("aria-expanded") !== "true";
      setAccountMenuOpen(expanded);
    },
  },
      el("div", { class: "account-avatar", "aria-hidden": "true" }, accountInitials(currentUser?.email)),
      el("div", { class: "account-identity" },
        el("strong", {}, currentUser?.email?.split("@")[0] || t("auth_account")),
        el("span", {}, currentUser?.email || ""),
      ),
      el("span", { class: `account-plan${currentUser?.plan === "owner" ? " owner" : ""}` }, accountPlanLabel(currentUser?.plan)),
      el("span", { class: "account-toggle-icon" }, icon("up", "sm")),
  );
  accountToggle.querySelector(".account-avatar").style.setProperty("--avatar-color", accountAvatarColor(currentUser?.email));

  const accountCard = currentUser?.guest
    ? el("section", { class: "account-card", "aria-label": t("auth_account") },
        el("button", { class: "account-card-head", onclick: () => { close(); openAuthModal(); } },
          el("span", { class: "account-row-icon" }, icon("right", "sm")),
          el("span", { class: "account-row-main" }, t("auth_login_btn")),
        ))
    : el("section", { class: "account-card", "aria-label": t("auth_account") },
        accountMenu,
        accountToggle,
      );

  // Het menu zweeft boven het vaste accountblok. Een klik ergens anders in de
  // instellingen (of op de rest van de pagina) klapt alleen dit menu dicht.
  // Klikken op een menu-optie zelf blijft ongemoeid, zodat de actie kan lopen.
  function onAccountOutside(event) {
    if (accountMenu.hidden) return;
    if (accountMenu.contains(event.target) || accountToggle.contains(event.target)) return;
    setAccountMenuOpen(false);
  }
  document.addEventListener("mousedown", onAccountOutside, true);

  const body = el("div", { class: "drawer-body" },
    el("div", { class: "drawer-sec" },
      el("label", {}, t("lang_label")),
      langSelect,
    ),
    el("div", { class: "drawer-sec" },
      el("label", {}, t("detail_label")),
      seg([["short", t("short")], ["normal", t("normal")], ["long", t("long")]], prefs.detailLevel, (v) => savePrefs({ detailLevel: v })),
    ),
    el("div", { class: "drawer-sec" },
      el("label", {}, t("theme")),
      seg([["dark", t("dark")], ["light", t("light")]], prefs.theme, (v) => savePrefs({ theme: v })),
    ),
    extra || null,
    el("div", { class: "drawer-sec" },
      el("label", {}, t("shortcuts")),
      el("div", { style: "display:flex;flex-direction:column;gap:8px;font-size:13px;color:var(--text-soft)" },
        shortcutRow("← →", t("sc_nav")),
        shortcutRow(t("space_key"), t("sc_space")),
        shortcutRow("F", t("sc_focus")),
        shortcutRow("Ctrl K", t("sc_search")),
      ),
    ),
    el("div", { class: "drawer-account-section" }, accountCard),
  );

  drawer.append(
    el("div", { class: "drawer-head" },
      el("h3", {}, t("settings")),
      el("button", { class: "btn ghost icon-btn", title: t("close"), onclick: close }, icon("x")),
    ),
    body,
  );
  root.append(scrim, drawer);

  api.usage().then((data) => {
    if (!data.enabled || data.limit == null) {
      usageValue.textContent = t("account_usage_unlimited");
      return;
    }
    usageValue.textContent = t("account_usage_remaining", {
      remaining: data.remaining,
      limit: data.limit,
    });
  }).catch(() => { usageValue.textContent = t("account_usage_unavailable"); });

  api.authConfig().then((config) => {
    if (config.password_reset) passwordButton.style.display = "flex";
  }).catch(() => {});

  return close;
}

function shortcutRow(keys, label) {
  return el("div", { style: "display:flex;justify-content:space-between;align-items:center" },
    el("span", {}, label),
    el("span", { style: "display:flex;gap:4px" }, ...keys.split(" ").map(k => el("kbd", {}, k))),
  );
}
