import test from "node:test";
import assert from "node:assert/strict";
import { beginsNewChat, timestampLabel } from "../lodestar/web/static/timeline.mjs";

const at = (hour) => new Date(Date.UTC(2026, 10, 9, hour)).toISOString();

test("a main-chat user message after two idle hours starts a visual new-chat segment", () => {
  const previous = { role: "assistant", created_at: at(0) };
  assert.equal(beginsNewChat(previous, { role: "user", created_at: at(1) }, "main"), false);
  assert.equal(beginsNewChat(previous, { role: "user", created_at: at(2) }, "main"), true);
  assert.equal(beginsNewChat(previous, { role: "assistant", created_at: at(25) }, "main"), false);
  assert.equal(beginsNewChat(previous, { role: "user", created_at: at(25) }, "task-1"), false);
  assert.equal(beginsNewChat(previous, { role: "user", created_at: "not-a-date" }, "main"), false);
});

test("timestamps are absent rather than guessed when source data is invalid", () => {
  assert.equal(timestampLabel("not-a-date"), "");
  assert.notEqual(timestampLabel(at(0)), "");
});
