// Leerdata per document (localStorage): markeringen, notities en quizscores
// per dia. Hiermee weet de app welke dia's extra aandacht verdienen.
//
// Notities/markeringen worden ook (gedebounced, fire-and-forget) naar de
// backend gesynct — zie backend.py /document/{hash}/notes — zodat ze een
// cache-wipe overleven en op een ander apparaat terugkomen. localStorage
// blijft de snelle/offline-eerste laag; een mislukte sync is nooit fataal.
import { api } from "./api.js";

const storeKey = (hash) => `sc.study.${hash}`;

function load(hash) {
  try { return JSON.parse(localStorage.getItem(storeKey(hash))) || {}; }
  catch { return {}; }
}
function save(hash, data) {
  localStorage.setItem(storeKey(hash), JSON.stringify(data));
}
function pageEntry(data, page) {
  if (!data.pages) data.pages = {};
  if (!data.pages[page]) data.pages[page] = {};
  return data.pages[page];
}

export const study = {
  getPage(hash, page) {
    return load(hash).pages?.[page] || {};
  },

  // Eén keer bij het openen van een document: vult lokaal ontbrekende
  // pagina's/velden aan met wat de backend al had. Bij conflict wint de
  // lokale waarde (die kan net nog niet gesynct zijn).
  hydrate(hash, remotePages) {
    if (!remotePages) return;
    const data = load(hash);
    data.pages = data.pages || {};
    for (const [page, remoteEntry] of Object.entries(remotePages)) {
      data.pages[page] = { ...remoteEntry, ...(data.pages[page] || {}) };
    }
    save(hash, data);
  },

  // flag: "star" (belangrijk) of "unclear" (snap ik nog niet)
  toggleFlag(hash, page, flag) {
    const data = load(hash);
    const p = pageEntry(data, page);
    p[flag] = !p[flag];
    save(hash, data);
    api.notesUpdate(hash, { page_index: +page, [flag]: p[flag] }).catch(() => {});
    return p[flag];
  },

  getNote(hash, page) {
    return this.getPage(hash, page).note || "";
  },
  setNote(hash, page, text) {
    const data = load(hash);
    const p = pageEntry(data, page);
    if (text.trim()) p.note = text;
    else delete p.note;
    save(hash, data);
    api.notesUpdate(hash, { page_index: +page, note: text.trim() }).catch(() => {});
  },

  // score 0-100 van een quizvraag of flashcard-beoordeling over deze dia
  recordScore(hash, page, score) {
    if (page == null) return;
    const data = load(hash);
    const p = pageEntry(data, page);
    p.attempts = (p.attempts || 0) + 1;
    p.scoreSum = (p.scoreSum || 0) + score;
    save(hash, data);
  },

  // "Zwak" = gemarkeerd als onduidelijk, of gemiddelde score onder de 60.
  isWeak(hash, page) {
    const p = this.getPage(hash, page);
    if (p.unclear) return true;
    return (p.attempts || 0) > 0 && p.scoreSum / p.attempts < 60;
  },
};
