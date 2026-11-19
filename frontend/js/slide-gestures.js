// The same wheel sensitivity for lecture slides and exercise pages.
export const SLIDE_PER_NOTCH = 0.28;
export function wheelZoomFactor(event, perNotch = SLIDE_PER_NOTCH) {
  const perUnit = event.deltaMode === 1 ? 24 : event.deltaMode === 2 ? 200 : 1;
  return Math.exp(-(event.deltaY * perUnit / 100) * perNotch);
}

export function attachSlideGestures(holder, image, { getScale, setScale, navigate }) {
  const pointers = new Map();
  let pan = null, pinch = null, swipe = null;
  const clamp = value => Math.max(0, Math.min(1, value));
  const pair = () => [...pointers.values()].slice(0, 2);
  const distance = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
  const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  holder.addEventListener("wheel", event => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    setScale(getScale() * wheelZoomFactor(event), event.clientX, event.clientY);
  }, { passive: false });
  holder.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    try { holder.setPointerCapture(event.pointerId); } catch { /* detached surface */ }
    if (event.pointerType === "touch") {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size >= 2) {
        event.preventDefault(); pan = swipe = null;
        const [a, b] = pair();
        const rect = image.getBoundingClientRect();
        const center = mid(a, b);
        pinch = { distance: Math.max(1, distance(a, b)), scale: getScale(),
          relX: clamp((center.x - rect.left) / Math.max(1, rect.width)),
          relY: clamp((center.y - rect.top) / Math.max(1, rect.height)) };
        return;
      }
      if (getScale() <= 1.001) {
        swipe = { id: event.pointerId, x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY };
        return;
      }
    }
    if (getScale() <= 1.001) return;
    event.preventDefault();
    pan = { id: event.pointerId, x: event.clientX, y: event.clientY, left: holder.scrollLeft, top: holder.scrollTop };
    holder.classList.add("panning");
  });
  holder.addEventListener("pointermove", event => {
    if (event.pointerType === "touch" && pointers.has(event.pointerId)) {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pinch && pointers.size >= 2) {
        event.preventDefault();
        const [a, b] = pair();
        const center = mid(a, b);
        setScale(pinch.scale * distance(a, b) / pinch.distance);
        const rect = image.getBoundingClientRect();
        holder.scrollLeft += rect.left + pinch.relX * rect.width - center.x;
        holder.scrollTop += rect.top + pinch.relY * rect.height - center.y;
        return;
      }
      if (swipe?.id === event.pointerId) { swipe.lastX = event.clientX; swipe.lastY = event.clientY; }
    }
    if (!pan || pan.id !== event.pointerId) return;
    holder.scrollLeft = pan.left - (event.clientX - pan.x);
    holder.scrollTop = pan.top - (event.clientY - pan.y);
  });
  const stop = (event, cancelled = false) => {
    pointers.delete(event.pointerId);
    if (pinch && pointers.size < 2) { pinch = pan = swipe = null; }
    if (swipe?.id === event.pointerId) {
      const dx = swipe.lastX - swipe.x, dy = swipe.lastY - swipe.y;
      swipe = null;
      if (!cancelled && Math.abs(dx) >= 55 && Math.abs(dx) > Math.abs(dy) * 1.35) navigate(dx < 0 ? 1 : -1);
    }
    if (pan?.id === event.pointerId) pan = null;
    if (!pan) holder.classList.remove("panning");
  };
  holder.addEventListener("pointerup", event => stop(event));
  holder.addEventListener("pointercancel", event => stop(event, true));
  holder.addEventListener("lostpointercapture", event => stop(event, true));
}
