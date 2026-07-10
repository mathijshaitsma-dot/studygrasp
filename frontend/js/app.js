// App-shell: router + globale sneltoetsen + instellingen-drawer.
import { el, icon, openModal, toast } from "./util.js";
import { prefs, savePrefs, applyTheme } from "./state.js";
import { t, uiLang } from "./i18n.js";
import { renderHome } from "./views/home.js";
import { renderWorkspace } from "./views/study.js";
import { renderFolder } from "./views/folder.js";
import { openSearch } from "./search.js";

applyTheme();

const app = document.getElementById("app");

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

function route() {
  setFocusMode(false);
  document.documentElement.lang = uiLang();
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  app.replaceChildren();

  if (parts[0] === "doc" && parts[1]) {
    const tab = parts[2] || "study";
    const page = parts[3] != null ? parseInt(parts[3], 10) : null;
    renderWorkspace(app, parts[1], tab, page);
  } else if (parts[0] === "folder" && parts[1]) {
    renderFolder(app, parts[1], parts[2] || null);
  } else {
    renderHome(app);
  }
}

// Volledige her-render van de huidige view (bijv. na een taalwissel).
export function rerender() {
  route();
}

window.addEventListener("hashchange", route);
route();

// ---------- upgrade-melding (dagbudget op) ----------
// Wordt vanuit de api-laag getriggerd zodra de backend QUOTA_EXCEEDED geeft.
// Al een melding open? Dan niet nog een keer (bij meerdere gelijktijdige calls).
window.addEventListener("sc:quota", () => {
  if (document.querySelector(".upgrade-modal")) return;
  const close = openModal(el("div", { class: "upgrade-modal" },
    el("div", { class: "upgrade-badge" }, icon("sparkle", "lg")),
    el("h3", {}, t("upgrade_title")),
    el("p", {}, t("upgrade_body")),
    el("div", { class: "upgrade-foot" },
      el("button", { class: "btn ghost", onclick: () => close() }, t("upgrade_later")),
      // Placeholder voor de prijzenpagina — hier komt straks de checkout-link.
      el("button", { class: "btn primary", onclick: () => { close(); toast(t("upgrade_soon"), "info", 4000); } },
        icon("sparkle", "sm"), t("upgrade_cta")),
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
  const close = () => { scrim.remove(); drawer.remove(); document.removeEventListener("keydown", onKey); };
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
    ...[["auto", t("lang_auto")], ["Nederlands", "Nederlands"], ["English", "English"], ["Deutsch", "Deutsch"], ["Français", "Français"], ["Español", "Español"]]
      .map(([v, l]) => el("option", { value: v, selected: prefs.language === v }, l)),
  );

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
  );

  drawer.append(
    el("div", { class: "drawer-head" },
      el("h3", {}, t("settings")),
      el("button", { class: "btn ghost icon-btn", onclick: close }, icon("x")),
    ),
    body,
  );
  root.append(scrim, drawer);
  return close;
}

function shortcutRow(keys, label) {
  return el("div", { style: "display:flex;justify-content:space-between;align-items:center" },
    el("span", {}, label),
    el("span", { style: "display:flex;gap:4px" }, ...keys.split(" ").map(k => el("kbd", {}, k))),
  );
}
