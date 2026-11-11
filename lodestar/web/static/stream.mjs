// Spread network batches across frames, with bounded catch-up and one render per
// frame. Array.from keeps emoji/surrogate pairs intact.
export function streamDisplay(render, {schedule = requestAnimationFrame, cancel = cancelAnimationFrame} = {}) {
  let target = [], shown = 0, frame = null, last = 0, stopped = false;
  function tick(now) {
    frame = null;
    if (stopped) return;
    if (now - last >= 32) {
      shown = Math.min(target.length, shown + Math.max(2, Math.ceil((target.length - shown) / 4)));
      render(target.slice(0, shown).join(''));
      last = now;
    }
    if (shown < target.length) frame = schedule(tick);
  }
  return {
    update(text) {
      target = Array.from(text);
      shown = Math.min(shown, target.length);
      if (!stopped && frame === null && shown < target.length) frame = schedule(tick);
    },
    stop() {
      stopped = true;
      if (frame !== null) cancel(frame);
      frame = null;
    },
  };
}
