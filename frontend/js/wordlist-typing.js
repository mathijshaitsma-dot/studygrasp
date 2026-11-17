// Zuivere hulplogica voor de typmodus. Los van de DOM gehouden zodat de
// antwoordvergelijking en de exacte herhaalvolgorde eenvoudig te testen zijn.

export function normalizeTypedAnswer(value) {
  return String(value || "")
    .normalize("NFKC")
    .trim()
    .toLocaleLowerCase()
    .replace(/[.!?]+$/g, "")
    .replace(/\s+/g, " ");
}

export function isTypedAnswerCorrect(answer, expected) {
  const actual = normalizeTypedAnswer(answer);
  if (!actual) return false;
  // Een woordenlijst mag meerdere correcte varianten met /, ; of | bevatten.
  return String(expected || "").split(/\s*(?:\/|;|\|)\s*/)
    .some(option => normalizeTypedAnswer(option) === actual);
}

export function makeTypingQueue(cards) {
  return cards.map(card => ({ card, phase: "initial" }));
}

export function scheduleIncorrectCard(queue, currentIndex, card) {
  // current + 4 = eerst drie andere vragen tonen. Bij een erg korte lijst
  // komt de herkansing na alle nog beschikbare andere woorden. Korte
  // herkansingen blijven altijd vóór de gezamenlijke eindronde staan.
  const firstFinal = queue.findIndex(entry => entry.phase === "final");
  const retryLimit = firstFinal < 0 ? queue.length : firstFinal;
  const retryAt = Math.min(currentIndex + 4, retryLimit);
  queue.splice(retryAt, 0, { card, phase: "retry" });
  queue.push({ card, phase: "final" });
  return retryAt;
}
