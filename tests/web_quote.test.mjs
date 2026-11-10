import test from "node:test";
import assert from "node:assert/strict";
import { quoteMarkdown } from "../lodestar/web/static/quote.mjs";

test("whole answers and selected passages become readable markdown quotes", () => {
  assert.equal(quoteMarkdown("A short phrase"), "> A short phrase");
  assert.equal(quoteMarkdown("First line\n\nLast line"), "> First line\n> \n> Last line");
  assert.equal(quoteMarkdown("   \n  "), "");
});
