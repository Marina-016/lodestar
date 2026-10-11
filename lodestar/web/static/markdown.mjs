// Small safe Markdown renderer. Raw HTML is always text; links allow HTTP(S) only.
export const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export function safeUrl(value) {
  try {
    const u = new URL(value);
    return ["http:", "https:"].includes(u.protocol) ? u.href : "";
  } catch {
    return "";
  }
}
function inline(text, links) {
  const token =
    /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\)|https?:\/\/[^\s<>]+)/g;
  let out = "",
    cursor = 0;
  for (const match of text.matchAll(token)) {
    out += escape(text.slice(cursor, match.index));
    const part = match[0];
    if (part.startsWith("`"))
      out += `<code>${escape(part.slice(1, -1))}</code>`;
    else if (part.startsWith("**"))
      out += `<strong>${escape(part.slice(2, -2))}</strong>`;
    else {
      let label = part,
        url = part;
      if (part.startsWith("[")) {
        const p = part.lastIndexOf("](");
        label = part.slice(1, p);
        url = part.slice(p + 2, -1);
      }
      const safe = safeUrl(url);
      if (safe) links?.push(safe);
      out += safe
        ? `<a href="${escape(safe)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`
        : escape(label);
    }
    cursor = match.index + part.length;
  }
  return out + escape(text.slice(cursor));
}
export function markdown(source, links = null) {
  const renderInline = (text) => inline(text, links);
  const lines = String(source ?? "")
    .replace(/\r/g, "")
    .split("\n");
  let out = "",
    p = [];
  const flush = () => {
    if (p.length) {
      out += `<p>${p.map(renderInline).join("<br>")}</p>`;
      p = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush();
      let code = [];
      while (++i < lines.length && !/^```/.test(lines[i])) code.push(lines[i]);
      out += `<pre><code>${escape(code.join("\n"))}</code></pre>`;
    } else if (/^#{1,4}\s/.test(line)) {
      flush();
      const m = line.match(/^(#{1,4})\s+(.*)$/);
      out += `<h${m[1].length}>${renderInline(m[2])}</h${m[1].length}>`;
    } else if (/^\s*[-*_]{3,}\s*$/.test(line)) {
      flush();
      out += "<hr>";
    } else if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[i + 1])
    ) {
      flush();
      const cells = (l) =>
        l
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map((c) => c.trim());
      out +=
        '<div class="table-wrap"><table><thead><tr>' +
        cells(line)
          .map((c) => `<th>${renderInline(c)}</th>`)
          .join("") +
        "</tr></thead><tbody>";
      i++;
      while (i + 1 < lines.length && lines[i + 1].includes("|"))
        out +=
          "<tr>" +
          cells(lines[++i])
            .map((c) => `<td>${renderInline(c)}</td>`)
            .join("") +
          "</tr>";
      out += "</tbody></table></div>";
    } else if (/^\s*([-*+] |\d+\. )/.test(line)) {
      flush();
      const ordered = /^\s*\d+\./.test(line),
        tag = ordered ? "ol" : "ul";
      out += `<${tag}>`;
      let start = i;
      while (
        i < lines.length &&
        (ordered ? /^\s*\d+\.\s/ : /^\s*[-*+]\s/).test(lines[i])
      ) {
        out += `<li>${renderInline(lines[i].replace(/^\s*(?:[-*+]|\d+\.)\s+/, ""))}</li>`;
        i++;
      }
      i--;
      out += `</${tag}>`;
    } else if (/^>\s?/.test(line)) {
      flush();
      out += `<blockquote>${renderInline(line.replace(/^>\s?/, ""))}</blockquote>`;
    } else if (!line.trim()) flush();
    else p.push(line);
  }
  flush();
  return out;
}

// Use the same parser as the displayed answer; code samples are not citations.
export function linkedUrls(source) {
  const links = [];
  markdown(source, links);
  return [...new Set(links)];
}
