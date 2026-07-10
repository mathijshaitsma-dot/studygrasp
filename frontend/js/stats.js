// Leerdata per document (localStorage): markeringen, notities en quizscores
// per dia. Hiermee weet de app welke dia's extra aandacht verdienen.

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

  // flag: "star" (belangrijk) of "unclear" (snap ik nog niet)
  toggleFlag(hash, page, flag) {
    const data = load(hash);
    const p = pageEntry(data, page);
    p[flag] = !p[flag];
    save(hash, data);
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
