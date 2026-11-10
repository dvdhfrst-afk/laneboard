// Small helpers for counting, wording and "Label: value" lines. Nothing here touches the page or its state.

export function countBy(items, fn) {
  const m = new Map();
  for (const x of items) m.set(fn(x), (m.get(fn(x)) || 0) + 1);
  return m;
}

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
export const isWebLink = (url) => /^https?:\/\//i.test(url);

export function when(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// "Label: value" lines, as used for card fields and group details.
export function parsePairs(text, what) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const i = line.indexOf(":");
    if (i < 1) throw new Error(`${what}: write each line as Label: value. "${line.slice(0, 40)}" has no label.`);
    return { label: line.slice(0, i).trim(), value: line.slice(i + 1).trim() };
  });
}

export const pairsText = (rows) => rows.map((r) => `${r.label}: ${r.value.replace(/\s*\n\s*/g, " ")}`).join("\n");
