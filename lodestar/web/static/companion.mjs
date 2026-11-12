// Browser-local companion. It never submits prompts or calls a model.
const KEY = 'lodestar-companion-v1';
export function companionPreference(value) {
  const point = value?.position;
  const position = point && Number.isFinite(point.x) && Number.isFinite(point.y)
    ? {x: Math.max(0, Math.min(1, point.x)), y: Math.max(0, Math.min(1, point.y))} : null;
  const size = Number.isFinite(value?.size) ? Math.max(64, Math.min(240, Math.round(value.size))) : 'auto';
  return {visible: value?.visible !== false, position, size, motion: value?.motion !== false};
}
export function companionPose(text, busy) {
  if (!busy || /停/.test(text)) return 'idle';
  if (/写|输出/.test(text)) return 'writing';
  if (/阅读|读取|检索|搜索|核查|引用|资料|记忆/.test(text)) return 'reading';
  return 'thinking';
}
export function initializeCompanion() {
  const root = document.querySelector('#lolo-companion');
  if (!root) return {status() {}};
  const pet = root.querySelector('.companion-pet'), menu = root.querySelector('.companion-menu');
  const toggle = document.querySelector('#companion-visible');
  const sizeControl = document.querySelector('#companion-size'), autoSize = document.querySelector('#companion-auto-size');
  const motion = document.querySelector('#companion-motion'), sizeLabel = document.querySelector('#companion-size-value');
  const read = () => {try {return companionPreference(JSON.parse(localStorage.getItem(KEY) || '{}'));} catch {return companionPreference();}};
  let preference = read(), drag = null, suppressClick = false, frame = null, greetingTimer;
  let pose = 'idle', wasBusy = false;
  const captions = {idle: 'lolo 在这里陪你', thinking: '正在组织回答', reading: '正在看资料', writing: '正在写给你', happy: '这一轮写好啦'};
  function paintPose(value) {
    pose = value; root.dataset.pose = value;
    root.querySelector('.companion-caption').textContent = captions[value];
    pet.setAttribute('aria-label', `${captions[value]}，打开小 lolo 菜单`);
  }
  function save() {try {localStorage.setItem(KEY, JSON.stringify(preference));} catch {}}
  function closeMenu(focus = false) {
    menu.hidden = true; pet.setAttribute('aria-expanded', 'false');
    if (focus) pet.focus({preventScroll: true});
  }
  function bounds() {
    const main = document.querySelector('main').getBoundingClientRect();
    const composer = document.querySelector('#composer').getBoundingClientRect();
    const top = document.querySelector('.topbar').getBoundingClientRect().bottom + 12;
    const rail = Math.max(0, main.right - composer.right);
    const desired = preference.size === 'auto' ? Math.max(44, Math.min(164, rail - 12)) : preference.size;
    // Explicit sizing may use more of the page; always keep the input clear.
    const size = Math.min(desired, Math.max(44, composer.top - top - 14));
    return {size, left: main.left + 8, right: main.right - size - 8,
      top, bottom: Math.max(top, composer.top - size - 14)};
  }
  function place(x, y, area = bounds()) {
    x = Math.max(area.left, Math.min(area.right, x));
    y = Math.max(area.top, Math.min(area.bottom, y));
    root.style.setProperty('--companion-size', `${area.size}px`);
    root.style.left = `${x}px`; root.style.top = `${y}px`;
    menu.style.left = `${Math.max(8 - x, Math.min(area.size - 218, innerWidth - 226 - x))}px`;
    root.dataset.menuBelow = String(y < 210);
    return {x, y};
  }
  function layout() {
    frame = null;
    if (drag) return;
    root.hidden = !preference.visible;
    if (toggle) toggle.checked = preference.visible;
    autoSize.checked = preference.size === 'auto';
    sizeControl.disabled = preference.size === 'auto';
    sizeControl.value = preference.size === 'auto' ? 164 : preference.size;
    sizeLabel.textContent = preference.size === 'auto' ? '自动适应留白' : `${preference.size} px`;
    motion.checked = preference.motion;
    root.classList.toggle('motion-off', !preference.motion);
    if (!preference.visible) {closeMenu(); return;}
    const area = bounds(), point = preference.position;
    place(point ? area.left + point.x * (area.right - area.left) : area.right,
      point ? area.top + point.y * (area.bottom - area.top) : area.bottom, area);
  }
  function schedule() {if (frame === null) frame = requestAnimationFrame(layout);}
  function remember(point, area) {
    preference.position = {x: (point.x - area.left) / Math.max(1, area.right - area.left),
      y: (point.y - area.top) / Math.max(1, area.bottom - area.top)};
    save();
  }
  function reset() {preference.position = null; save(); closeMenu(); layout();}
  function visibility(value) {
    preference.visible = value; save(); layout();
  }
  pet.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !event.isPrimary) return;
    const rect = root.getBoundingClientRect();
    suppressClick = false;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false};
    pet.setPointerCapture(event.pointerId);
  });
  pet.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.hypot(dx, dy) > 5) {drag.moved = true; closeMenu(); root.classList.add('is-dragging');}
    if (drag.moved) place(drag.left + dx, drag.top + dy);
  });
  function release(event) {
    if (!drag || event.pointerId !== drag.id) return;
    suppressClick = drag.moved;
    if (drag.moved) {
      const rect = root.getBoundingClientRect(); remember({x: rect.left, y: rect.top}, bounds());
    }
    drag = null; root.classList.remove('is-dragging');
    if (pet.hasPointerCapture(event.pointerId)) pet.releasePointerCapture(event.pointerId);
    schedule();
  }
  pet.addEventListener('pointerup', release);
  pet.addEventListener('pointercancel', release);
  pet.addEventListener('lostpointercapture', release);
  pet.addEventListener('click', event => {
    if (suppressClick && event.detail !== 0) {suppressClick = false; return;}
    menu.hidden = !menu.hidden; pet.setAttribute('aria-expanded', String(!menu.hidden));
    if (!menu.hidden) menu.querySelector('button').focus({preventScroll: true});
  });
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape') {closeMenu(true); return;}
    if (event.target !== pet) return;
    const step = event.shiftKey ? 32 : 12;
    const delta = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[event.key];
    if (event.key === 'Home') {event.preventDefault(); reset();}
    if (delta) {
      event.preventDefault(); closeMenu(); const area = bounds(), rect = root.getBoundingClientRect();
      remember(place(rect.left + delta[0], rect.top + delta[1], area), area);
    }
  });
  root.querySelector('[data-companion="compose"]').onclick = () => {closeMenu(); document.querySelector('#prompt').focus({preventScroll: true});};
  root.querySelector('[data-companion="reset"]').onclick = () => {reset(); pet.focus({preventScroll: true});};
  root.querySelector('[data-companion="hide"]').onclick = () => {visibility(false); document.querySelector('#appearance-settings').focus({preventScroll: true});};
  document.querySelector('#companion-reset').onclick = reset;
  toggle.addEventListener('change', () => visibility(toggle.checked));
  sizeControl.addEventListener('input', () => {preference.size = Number(sizeControl.value); save(); layout();});
  autoSize.addEventListener('change', () => {preference.size = autoSize.checked ? 'auto' : Number(sizeControl.value); save(); layout();});
  motion.addEventListener('change', () => {preference.motion = motion.checked; save(); layout();});
  document.addEventListener('pointerdown', event => {if (!root.contains(event.target)) closeMenu();});
  document.addEventListener('focusin', event => {if (!root.contains(event.target)) closeMenu();});
  document.addEventListener('visibilitychange', () => {root.classList.toggle('is-paused', document.hidden); if (document.hidden) closeMenu();});
  window.addEventListener('resize', schedule);
  window.addEventListener('storage', event => {if (event.key === KEY || event.key === null) {preference = read(); layout();}});
  const observer = new ResizeObserver(schedule);
  observer.observe(document.querySelector('main')); observer.observe(document.querySelector('#composer'));
  // Sidebar/reading width preferences may change without a window resize.
  new MutationObserver(schedule).observe(document.documentElement, {attributes: true, attributeFilter: ['class', 'data-chat-width']});
  paintPose('idle'); layout();
  return {status(text, busy) {
    clearTimeout(greetingTimer);
    const next = companionPose(text, busy);
    if (!busy && !text && wasBusy && pose !== 'idle') {
      paintPose('happy'); greetingTimer = setTimeout(() => paintPose('idle'), 4500);
    } else paintPose(next);
    wasBusy = busy;
  }};
}
