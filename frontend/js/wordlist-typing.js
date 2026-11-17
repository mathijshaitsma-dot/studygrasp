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

export function wordlistSides(card, direction = "term_first") {
  return direction === "definition_first"
    ? { prompt: card.definition, answer: card.term, promptSide: "definition", answerSide: "term" }
    : { prompt: card.term, answer: card.definition, promptSide: "term", answerSide: "definition" };
}

export function scheduleIncorrectCard(queue, currentIndex, card, phase = "retry") {
  // Na precies twee andere vragen terugvragen (current + 3). Gewone
  // herkansingen blijven vóór de eindronde; een fout ín de eindronde wordt
  // tussen de resterende eindvragen opnieuw ingepland.
  const firstFinal = phase === "final"
    ? -1
    : queue.findIndex((entry, index) => index > currentIndex && entry.phase === "final");
  const retryLimit = firstFinal < 0 ? queue.length : firstFinal;
  const retryAt = Math.min(currentIndex + 3, retryLimit);
  queue.splice(retryAt, 0, { card, phase });
  return retryAt;
}
