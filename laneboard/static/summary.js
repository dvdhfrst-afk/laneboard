// The summary panel above the board: the board's numbers as the server counts them (cards per column, group, label
// and property choice, number totals, blocked and archived cards, tasks and ticks). Every count that a search can
// show is a button that puts that search in the box. The numbers come again whenever the board changes, and only
// while the panel is open; until they arrive the last ones stay on screen.

import { state, store, stored } from "./state.js";
import { $, announce, h } from "./dom.js";
import { api } from "./api.js";
import { propText } from "./properties.js";
import { propertyName, setQuery, qualifierFor } from "./search.js";

const OPEN = "laneboard:summary";

let shown = null;     // {board, rev} of the numbers on screen
let inFlight = null;  // {board, rev, controller}

let open = stored(OPEN, "") === "open";  // remembered in this browser when it can be

export function renderSummary() {
  const b = state.board;
  $("#summary-toggle").setAttribute("aria-expanded", String(open));
  const panel = $("#summary");
  panel.hidden = !b || !open;
  if (panel.hidden) {
    inFlight?.controller.abort();
    inFlight = null;
    return;
  }
  if (shown?.board === b.id && shown.rev === b.rev) return;
  if (inFlight?.board === b.id && inFlight.rev === b.rev) return;
  if (shown?.board !== b.id) {
    shown = null;
    panel.replaceChildren(head(), h("p", { class: "hint", text: "Counting…" }));
  }
  ask(b.id, b.rev);
}

async function ask(board, rev) {
  inFlight?.controller.abort();
  const controller = new AbortController();
  const mine = { board, rev, controller };
  inFlight = mine;
  try {
    const { summary } = await api("GET", `/api/boards/${encodeURIComponent(board)}/summary`, undefined, undefined, controller.signal);
    if (inFlight !== mine || state.board?.id !== board) return;
    shown = { board, rev };
    $("#summary").replaceChildren(head(), draw(summary));
  } catch (e) {
    if (e.name === "AbortError" || inFlight !== mine) return;
    shown = null;
    $("#summary").replaceChildren(head(), h("p", { class: "form-error", text: `The summary couldn't be counted: ${e.message}` }));
  } finally {
    if (inFlight === mine) inFlight = null;
  }
  // The board may have moved on while the numbers were on their way.
  if (state.board?.id === board && state.board.rev !== rev) renderSummary();
}

function head() {
  return h("div", { class: "sum-head" },
    h("h2", { id: "summary-title", text: "Summary" }),
    h("p", { class: "hint", text: "Every card on the board, whatever the search shows. Pick a count to search for those cards." }));
}

// A line with a count. With `query` it's a button that searches for those cards.
function row(name, count, query = null, extra = null) {
  const parts = [h("span", { class: "sum-name" }, name), extra, h("span", { class: "sum-count", text: count })];
  if (!query) return h("div", { class: "sum-row" }, parts);
  return h("button", { type: "button", class: "sum-row link-row", "data-query": query, title: `Search ${query}`,
    onclick: () => {
      setQuery(query);
      announce(`Searching ${query}.`);
    } }, parts);
}

const block = (title, ...rows) => h("section", { class: "sum-block" }, h("h3", { text: title }), rows);
const dot = (color) => h("span", { class: `dot c-${color}`, "aria-hidden": "true" });
const named = (color, name) => h("span", {}, dot(color), name);

function draw(s) {
  const b = state.board;
  const props = new Map(b.properties.map((p) => [p.id, p]));
  const numbers = s.properties.filter((p) => p.type === "number" && p.cards > 0);
  const blocks = [];

  blocks.push(block("Cards",
    row("On the board", String(s.cards)),
    row("Blocked", String(s.blocked), "is:blocked"),
    row("Archived", String(s.archived), "is:archived"),
    s.tasks.total > 0 && row("Tasks done", `${s.tasks.done} of ${s.tasks.total}`),
    s.checks.total > 0 && row("Checklist ticks", `${s.checks.done} of ${s.checks.total}`)));

  blocks.push(block("Columns", s.columns.map((c) => {
    const sums = numbers.map((p) => props.get(p.id) && h("span", { class: "sum-sum" }, `${p.name} ${propText(props.get(p.id), c.sums[p.id] ?? 0)}`));
    return h("div", { class: c.over ? "sum-item over" : "sum-item" },
      row(c.name, c.limit ? `${c.cards} / ${c.limit}` : String(c.cards), qualifierFor("column", c, s.columns),
        c.over && h("span", { class: "flag", text: "Over its limit" })),
      c.cards > 0 && sums.some(Boolean) && h("div", { class: "sum-sums" }, sums));
  })));

  if (s.groups.length) {
    blocks.push(block(b.groupLabel,
      s.groups.map((g) => row(named(g.color, g.name), String(g.cards), qualifierFor("group", g, s.groups))),
      row(`No ${b.groupLabel.toLowerCase()}`, String(s.noGroup), "-has:group")));
  }
  if (s.labels.length) {
    blocks.push(block("Labels",
      s.labels.map((l) => row(named(l.color, l.name), String(l.cards), qualifierFor("label", l, s.labels))),
      row("No labels", String(s.noLabels), "-has:labels")));
  }
  for (const p of s.properties) {
    const prop = props.get(p.id);
    if (prop) blocks.push(propertyBlock(p, prop, s.cards));
  }
  return h("div", { class: "sum-grid" }, blocks);
}

// One property: its choices, its totals or its average, and how many cards have a value.
function propertyBlock(p, prop, cards) {
  const name = propertyName(prop);
  const unset = row("Not set", String(cards - p.cards), `-has:${name}`);
  const num = (value) => (value === null || value === undefined ? "none" : propText(prop, value));
  switch (p.type) {
    case "choice":
    case "multi":
      return block(p.name, p.options.map((o) => row(named(o.color, o.name), String(o.cards), qualifierFor(name, o, p.options))), unset);
    case "number":
      return block(p.name,
        row("Total", num(p.total)), row("Average", num(p.average)),
        p.cards > 0 && row("Lowest to highest", `${num(p.min)} to ${num(p.max)}`),
        row("Set", String(p.cards), `has:${name}`), unset);
    case "rating":
      return block(p.name,
        row("Average", p.average === null ? "none" : `${p.average} out of ${p.outOf}`),
        row("Rated", String(p.cards), `has:${name}`), unset);
    case "checkbox":
      return block(p.name, row("Ticked", String(p.cards), `${name}:yes`), row("Not ticked", String(cards - p.cards), `-${name}:yes`));
    default:
      return block(p.name, row("Set", String(p.cards), `has:${name}`), unset);
  }
}

export function bindSummary() {
  $("#summary-toggle").addEventListener("click", () => {
    open = !open;
    store(OPEN, open ? "open" : "");
    renderSummary();
  });
}
