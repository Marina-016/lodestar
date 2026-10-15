import test from "node:test";
import assert from "node:assert/strict";
import { actionHTML } from "../lodestar/web/static/actions.mjs";

test("legacy messages are not mislabeled as audited no-ops", () => {
  assert.equal(actionHTML(undefined), "");
  assert.match(actionHTML([]), /本轮未派发任务、未更新记忆/);
  assert.match(actionHTML([], { pending: true }), /尚无.*回执/);
});
test("submission is not completion, and partial creation is not a dispatch", () => {
  const submitted = actionHTML([{ action: "create_task", id: "a", name: "Harness", status: "submitted", external_retrieval: "blocked" }]);
  assert.match(submitted, /已提交任务：Harness/);
  assert.match(submitted, /不联网检索/);
  assert.doesNotMatch(submitted, /研究完成/);
  assert.match(actionHTML([{ action: "create_task", id: "b", name: "RAG", status: "created_not_submitted" }]), /未启动/);
});
test("receipts escape task identifiers and names, never show memory content", () => {
  const html = actionHTML([{ action: "create_task", id: '\"><script>', name: "<img src=x>", status: "submitted" }, { action: "manage_memory", id: "m", revision: 2, content: "PRIVATE_CANARY" }]);
  assert.doesNotMatch(html, /<script>|<img|PRIVATE_CANARY/);
  assert.match(html, /修订 2/);
  assert.match(actionHTML([{ action: "manage_memory", deleted: true }]), /已遗忘记忆/);
});
