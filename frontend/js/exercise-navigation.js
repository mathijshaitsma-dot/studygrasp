const KEY = "sc.exercise-return";
export function exerciseReturn() {
  try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch { return null; }
}
export function saveExerciseReturn(value) {
  try { sessionStorage.setItem(KEY, JSON.stringify(value)); } catch { /* private browser */ }
}
export function clearExerciseReturn() {
  try { sessionStorage.removeItem(KEY); } catch { /* private browser */ }
}
