// Finding cards: the search box, the group, label and Blocked filters, and matching cards against them.

import { activeCards, groupOf, index, labelsOf, state } from "./state.js";
import { countBy } from "./util.js";
import { $, h } from "./dom.js";
import { render, renderColumns } from "./board.js";
import { propText } from "./properties.js";
import { labelChip } from "./labels.js";

const SEARCH_DELAY = 150;  // on boards over LARGE_BOARD cards; smaller boards search on every key
const LARGE_BOARD = 500;

export function renderFilters() {
  const b = state.board;
  const active = activeCards();
  const counts = countBy(active, (c) => c.group || "");
  for (const id of [...state.groups]) {
    if (id && !b.groups.some((g) => g.id === id)) state.groups.delete(id);
  }
  for (const id of [...state.labels]) {
    if (!index.labels.has(id)) state.labels.delete(id);
  }
  const groupChip = (id, name, color, n) => h("button", {
    type: "button", "aria-pressed": String(state.groups.has(id)),
    onclick: () => { state.groups.has(id) ? state.groups.delete(id) : state.groups.add(id); render(); },
  }, h("span", { class: `dot c-${color}` }), name, h("span", { class: "count", text: String(n) }));
  const chips = b.groups.map((g) => groupChip(g.id, g.name, g.color, counts.get(g.id) || 0));
  if (b.groups.length && counts.get("")) chips.push(groupChip("", `No ${b.groupLabel.toLowerCase()}`, "slate", counts.get("")));
  // Labels on no card have nothing to filter, so only the ones in use show.
  const onCards = countBy(active.flatMap((c) => c.labels), (id) => id);
  for (const label of b.labels.filter((l) => onCards.get(l.id) || state.labels.has(l.id))) {
    chips.push(h("button", {
      type: "button", class: "label-filter", "aria-pressed": String(state.labels.has(label.id)),
      onclick: () => { state.labels.has(label.id) ? state.labels.delete(label.id) : state.labels.add(label.id); render(); },
    }, h("span", { class: "sr", text: "Label " }), labelChip(label), h("span", { class: "count", text: String(onCards.get(label.id) || 0) })));
  }
  const blocked = active.filter((c) => c.blocked).length;
  if (blocked || state.blockedOnly) {
    chips.push(h("button", {
      type: "button", "aria-pressed": String(state.blockedOnly),
      onclick: () => { state.blockedOnly = !state.blockedOnly; render(); },
    }, "Blocked", h("span", { class: "count", text: String(blocked) })));
  }
  if (state.groups.size || state.labels.size || state.blockedOnly) {
    chips.push(h("button", { type: "button", class: "link", text: "Show all",
      onclick: () => { state.groups.clear(); state.labels.clear(); state.blockedOnly = false; render(); } }));
  }
  const box = $("#filters");
  box.hidden = !chips.length;
  box.replaceChildren(...chips);
}

function searchText(card) {
  let text = index.text.get(card);
  if (text === undefined) {
    text = [card.key, card.title, card.summary, card.blocked, card.notes, groupOf(card)?.name,
      ...card.fields.map((f) => `${f.label} ${f.value}`), ...card.sections.map((s) => `${s.label} ${s.body}`),
      ...labelsOf(card.labels).map((l) => l.name), ...card.tasks.map((t) => t.text),
      ...Object.entries(card.properties).map(([id, value]) => (index.props.has(id) ? propText(index.props.get(id), value) : ""))]
      .join("\n").toLowerCase();
    index.text.set(card, text);
  }
  return text;
}

export function matchesQuery(card, words) {
  return words.every((w) => searchText(card).includes(w));
}

export function queryWords() {
  return state.query.toLowerCase().split(/\s+/).filter(Boolean);
}

// Whether anything narrows down the cards shown.
export const filtering = (words = queryWords()) => state.groups.size > 0 || state.labels.size > 0 || state.blockedOnly || words.length > 0;

export function matches(card, words = queryWords()) {
  if (state.groups.size && !state.groups.has(card.group || "")) return false;
  if (state.labels.size && !card.labels.some((id) => state.labels.has(id))) return false;
  if (state.blockedOnly && !card.blocked) return false;
  return !words.length || matchesQuery(card, words);
}

export function bindSearch() {
  const search = $("#search");
  let searchTimer = null;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    state.query = search.value;
    if (state.board && state.board.cards.length > LARGE_BOARD) searchTimer = setTimeout(renderColumns, SEARCH_DELAY);
    else renderColumns();
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && search.value) {
      search.value = "";
      state.query = "";
      renderColumns();
    }
  });
}
