import test from "node:test";
import assert from "node:assert/strict";
import { latestCitedMessage, workspaceMetrics } from "../lodestar/web/static/insights.mjs";

test("workspace metrics count audit signals without claiming answer quality", () => {
  const paper = "https://arxiv.org/abs/2601.00001";
  const state = {
    messages: [
      { role: "user", content: "Read this" },
      { id: 1, role: "assistant", content: `[paper](${paper})`, metadata: { candidates: [{ url: paper, title: "Paper" }] } },
      { id: 2, role: "assistant", content: `[same paper](${paper})` },
    ],
    context_notes: [{ id: 1 }, { id: 2 }],
    learning: [{ event_count: 2 }, { evidence: [{ id: 1 }] }],
    items: [{ id: "main", status: "active" }, { id: "task-1", status: "active" }, { id: "task-2", status: "completed" }],
    run: { status: "running" },
  };
  assert.deepEqual(workspaceMetrics(state), {
    answers: 2, sources: 1, currentContext: 2, learningEvidence: 3, activeTasks: 1, running: true,
  });
  assert.equal(latestCitedMessage(state.messages), 2);
});
