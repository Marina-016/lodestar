import { sourceGroups } from "./sources.mjs";

const rows = (value) => Array.isArray(value) ? value : [];

// These are activity and provenance signals, never a score for answer quality
// or user mastery. Keeping this pure makes the dashboard easy to audit.
export function workspaceMetrics(state = {}) {
  const messages = rows(state.messages);
  const answers = messages.filter((message) => message?.role === "assistant");
  const sources = new Set();
  for (const answer of answers) {
    for (const source of sourceGroups(answer, messages).references) {
      if (source?.url) sources.add(source.url);
    }
  }
  const learningEvidence = rows(state.learning).reduce((total, item) => {
    const count = Number(item?.event_count);
    return total + (Number.isFinite(count) ? Math.max(0, count) : rows(item?.evidence).length);
  }, 0);
  const activeTasks = rows(state.items).filter(
    (item) => item?.id !== "main" && item?.status === "active",
  ).length;
  const currentContext = rows(state.context_notes).length;
  const running = ["queued", "running", "cancelling"].includes(state.run?.status);
  return { answers: answers.length, sources: sources.size, learningEvidence, activeTasks, currentContext, running };
}

export function latestCitedMessage(messages = []) {
  const history = rows(messages);
  return [...history].reverse().find(
    (message) => message?.role === "assistant" && sourceGroups(message, history).references.length,
  )?.id ?? null;
}
