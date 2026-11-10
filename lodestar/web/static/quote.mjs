export function quoteMarkdown(text) {
  return String(text || "").trim().split(/\r?\n/).filter((line, index, rows) => line || rows.length > 1)
    .map((line) => `> ${line}`).join("\n");
}
