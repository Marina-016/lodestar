const DAY_MS = 24 * 60 * 60 * 1000;

function timestamp(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : null;
}

export function beginsNewChat(previous, current, itemId) {
  if (itemId !== "main" || current?.role !== "user" || !previous) return false;
  const earlier = timestamp(previous.created_at);
  const later = timestamp(current.created_at);
  return earlier !== null && later !== null && later - earlier > DAY_MS;
}

export function timestampLabel(value) {
  const parsed = timestamp(value);
  if (parsed === null) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(parsed));
}

export function sessionDateLabel(value) {
  const parsed = timestamp(value);
  if (parsed === null) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "long", day: "numeric", weekday: "short",
  }).format(new Date(parsed));
}
