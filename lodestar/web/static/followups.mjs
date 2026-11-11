// Optional response metadata emitted by the model. The host validates and stores
// it; this copy also keeps an in-progress streaming trailer out of the answer.
const TRAILER = /(?:\r?\n)?<!--lodestar-followups:(\{[\s\S]*?\})-->\s*$/;
const INCOMPLETE_TRAILER = /(?:\r?\n)<!--lodestar-followups:/;

function validatedItems(value) {
  const raw = value?.items;
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const items = [];
  for (const item of raw) {
    const prompt = typeof item?.prompt === "string" ? item.prompt.trim() : "";
    const key = prompt.toLocaleLowerCase();
    if (prompt.length < 4 || prompt.length > 400 || prompt.includes("<!--") || seen.has(key)) continue;
    seen.add(key);
    items.push({ prompt });
    if (items.length === 3) break;
  }
  return items;
}

export function splitModelFollowups(text) {
  const source = String(text ?? "");
  const match = source.match(TRAILER);
  if (!match) return { answer: source, items: [] };
  try {
    const items = validatedItems(JSON.parse(match[1]));
    const answer = source.slice(0, match.index).trimEnd();
    return answer && items.length ? { answer, items } : { answer: source, items: [] };
  } catch {
    return { answer: source, items: [] };
  }
}

export function visibleAnswer(text) {
  const source = String(text ?? "");
  const parsed = splitModelFollowups(source);
  if (parsed.answer !== source) return parsed.answer;
  const start = source.search(INCOMPLETE_TRAILER);
  return start === -1 ? source : source.slice(0, start).trimEnd();
}

export function storedFollowups(value) {
  return validatedItems({ items: value });
}
