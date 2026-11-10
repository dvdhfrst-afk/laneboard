// Finding cards. The search box speaks the search language, and only the server reads it: the page sends what is
// typed, with the sort, and draws the keys that come back in their order. While an answer is on its way the board
// keeps the last one, except that plain words are matched here at once so typing feels instant; the server's answer
// then replaces that. The group, label and Blocked chips write their terms into the box, so there is one filter.
// The sort picker and saved views are in views.js, searching every board in everywhere.js.

import { activeCards, byRank, cardBy, cardsIn, groupOf, index, labelsOf, noteAddress, state } from "./state.js";
import { countBy, plural } from "./util.js";
import { $, announce, h, toast } from "./dom.js";
import { api } from "./api.js";
import { renderColumns } from "./board.js";
import { propText } from "./properties.js";
import { labelChip } from "./labels.js";
import { openViews, renderSort, renderViews, sortName } from "./views.js";
import { openEverywhere } from "./everywhere.js";

const TYPING_DELAY = 120;  // ms after the last key before the server is asked
const LARGE_BOARD = 500;   // on boards with more cards than this, drawing also waits for a pause in typing
const VALUE = String.raw`(?:"(?:[^"\\]|\\.)*"|[^\s()"]+)`;
const CHIP_TERM = new RegExp(String.raw`^(group|labels?):(${VALUE})$`, "i");

// Text as the search compares it: capitals and accents don't matter.
export const fold = (text) => String(text).normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

// ---- plain words, matched on the page ----------------------------------------------------------

function searchText(card) {
  let text = index.text.get(card);
  if (text === undefined) {
    const props = Object.entries(card.properties).map(([id, value]) => {
      const prop = index.props.get(id);
      return prop ? `${prop.name} ${propText(prop, value)}` : "";
    });
    text = fold([card.key, card.title, card.summary, card.blocked, card.notes, groupOf(card)?.name,
      ...card.fields.map((f) => `${f.label} ${f.value}`), ...card.sections.map((s) => `${s.label} ${s.body}`),
      ...labelsOf(card.labels).map((l) => l.name), ...card.tasks.map((t) => t.text), ...props].join("\n"));
    index.text.set(card, text);
  }
  return text;
}

export const wordsOf = (text) => fold(text).split(/\s+/).filter(Boolean);
export const matchesWords = (card, words) => words.every((w) => searchText(card).includes(w));

// The words of a search with nothing of the search language in it (no quotes, brackets, name:value terms, minus
// signs, OR, AND or NOT), or null. Only these the page matches by itself, and only until the server answers.
function plainWords(query) {
  const words = query.split(/\s+/).filter(Boolean);
  const plain = words.every((w) => !/[():"]/.test(w) && !w.startsWith("-") && w !== "OR" && w !== "AND" && w !== "NOT");
  return plain ? words.map(fold) : null;
}

// ---- reading the box just enough for the chips -------------------------------------------------

// The search cut at spaces outside quotes and brackets, with where each piece starts and ends. Used only to find and
// rewrite the terms the chips write; what a search means is the server's to say.
function pieces(text) {
  const out = [];
  let i = 0;
  while (i < text.length) {
    if (/\s/.test(text[i])) {
      i++;
      continue;
    }
    const start = i;
    let depth = 0;
    let quoted = false;
    for (; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === "\\") i++;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === "(") depth++;
      else if (c === ")") depth = Math.max(0, depth - 1);
      else if (/\s/.test(c) && !depth) break;
    }
    out.push({ start, end: Math.min(i, text.length), text: text.slice(start, i) });
  }
  return out;
}

const manualOrder = (sort) => sort.split(",").every((part) => /^\s*\+?\s*(rank)?\s*$/i.test(part));

// Whether cards are put in an order other than the board's own, by the sort picker or by a sort: term typed in the
// search. Then a card can't be placed within a column by hand, since the sort decides where it shows.
export function sortActive(query = state.query, sort = state.sort) {
  if (!manualOrder(sort)) return true;
  const bare = query.replace(/"(?:[^"\\]|\\.)*"?/g, '""');
  return [...bare.matchAll(/(?:^|[\s(])sort:(\S*)/gi)].some((m) => !manualOrder(m[1]));
}

// A value as the search takes it: in quotes when it has spaces, brackets or quotes in it.
const quoted = (value) => (/[\s()"]/.test(value) || !value ? `"${value.replace(/["\\]/g, "\\$&")}"` : value);
const unquoted = (value) => (value.startsWith('"') ? value.slice(1, -1).replace(/\\(["\\])/g, "$1") : value);
const optionsOf = (family) => (family === "label" ? state.board.labels : state.board.groups);

// What a chip writes for a group or label: its name, or its id when the name could be taken for another one's id.
function nameFor(option, options) {
  const name = fold(option.name);
  return options.some((o) => o !== option && (fold(o.id) === name || fold(o.name) === name)) ? option.id : option.name;
}

function entryText(family, id) {
  if (family === "blocked") return "is:blocked";
  if (family === "group" && id === "") return "-has:group";
  const options = optionsOf(family);
  return `${family}:${quoted(nameFor(options.find((o) => o.id === id), options))}`;
}

// The chip a single term stands for: {family, id}, or null.
function entryOf(term) {
  if (/^-has:group$/i.test(term)) return { family: "group", id: "" };
  if (/^is:blocked$/i.test(term)) return { family: "blocked", id: "" };
  const m = term.match(CHIP_TERM);
  if (!m) return null;
  const family = m[1].toLowerCase() === "group" ? "group" : "label";
  const want = fold(unquoted(m[2]));
  const options = optionsOf(family);
  const found = options.find((o) => fold(o.id) === want) || options.find((o) => fold(o.name) === want);
  return found ? { family, id: found.id } : null;
}

// The chips in one piece: a term like label:Bug, or terms of one kind joined by OR in brackets, as the chips write
// them. {family, ids}, or null for anything else, which isn't the chips' to change.
function chipPiece(text) {
  const inner = text.startsWith("(") && text.endsWith(")") ? pieces(text.slice(1, -1)).map((p) => p.text) : null;
  if (inner && (inner.length % 2 === 0 || inner.some((t, i) => (i % 2 === 1) !== (t === "OR")))) return null;
  const entries = (inner ? inner.filter((_, i) => i % 2 === 0) : [text]).map(entryOf);
  if (entries.some((e) => !e) || new Set(entries.map((e) => e.family)).size !== 1) return null;
  if (inner && entries[0].family === "blocked") return null;
  return { family: entries[0].family, ids: entries.map((e) => e.id) };
}

// The pieces the chips own: standing on their own, not after NOT and not either side of OR.
function chipPieces(query) {
  const list = pieces(query);
  return list.map((p, i) => {
    const alone = list[i - 1]?.text !== "NOT" && list[i - 1]?.text !== "OR" && list[i + 1]?.text !== "OR";
    return alone ? { ...p, chip: chipPiece(p.text) } : null;
  }).filter((p) => p?.chip);
}

// Which chips the search holds, as {group: Set of ids ("" for no group), label: Set of ids, blocked}. A new card
// starts in a group or with a label picked here.
export function chipPicks(query = state.query) {
  const picks = { group: new Set(), label: new Set(), blocked: false };
  if (!state.board) return picks;
  for (const { chip } of chipPieces(query)) {
    if (chip.family === "blocked") picks.blocked = true;
    else chip.ids.forEach((id) => picks[chip.family].add(id));
  }
  return picks;
}

function replacePiece(query, piece, text) {
  if (text) return query.slice(0, piece.start) + text + query.slice(piece.end);
  const before = query.slice(0, piece.start).trimEnd();
  const after = query.slice(piece.end).trimStart();
  return before && after ? `${before} ${after}` : before + after;
}

const termFor = (family, ids) => (ids.length === 1 ? entryText(family, ids[0]) : ids.length ? `(${ids.map((id) => entryText(family, id)).join(" OR ")})` : "");

// Add one chip's term to the search, or take it out. Chips of one kind join with OR, since a card has one group and
// any of the labels picked will do; different kinds narrow each other, like any terms side by side.
function toggleChip(family, id) {
  let query = state.query;
  const mine = chipPieces(query).filter((p) => p.chip.family === family);
  const holding = mine.filter((p) => family === "blocked" || p.chip.ids.includes(id));
  if (holding.length) {
    for (const p of holding.reverse()) query = replacePiece(query, p, family === "blocked" ? "" : termFor(family, p.chip.ids.filter((x) => x !== id)));
  } else if (mine.length) {
    query = replacePiece(query, mine[0], termFor(family, [...mine[0].chip.ids, id]));
  } else {
    query = query.trim() ? `${query.trimEnd()} ${entryText(family, id)}` : entryText(family, id);
  }
  setQuery(query);
}

// ---- the chips ---------------------------------------------------------------------------------

export function renderFilters() {
  const b = state.board;
  const active = activeCards();
  const picks = chipPicks();
  const focused = document.activeElement?.closest?.("#filters button")?.dataset.chip;
  const counts = countBy(active, (c) => c.group || "");
  const chip = (family, id, cls, n, ...inside) => {
    const on = family === "blocked" ? picks.blocked : picks[family].has(id);
    const term = entryText(family, id);
    return h("button", {
      type: "button", class: cls, "aria-pressed": String(on), "data-chip": `${family}:${id}`,
      title: on ? `Take ${term} out of the search` : `Add ${term} to the search`, onclick: () => toggleChip(family, id),
    }, ...inside, h("span", { class: "count", text: String(n) }));
  };
  const chips = b.groups.map((g) => chip("group", g.id, null, counts.get(g.id) || 0, h("span", { class: `dot c-${g.color}` }), g.name));
  if (b.groups.length && (counts.get("") || picks.group.has(""))) {
    chips.push(chip("group", "", null, counts.get("") || 0, h("span", { class: "dot c-slate" }), `No ${b.groupLabel.toLowerCase()}`));
  }
  // Labels on no card have nothing to find, so only the ones in use show.
  const onCards = countBy(active.flatMap((c) => c.labels), (id) => id);
  for (const label of b.labels.filter((l) => onCards.get(l.id) || picks.label.has(l.id))) {
    chips.push(chip("label", label.id, "label-filter", onCards.get(label.id) || 0, h("span", { class: "sr", text: "Label " }), labelChip(label)));
  }
  const blocked = active.filter((c) => c.blocked).length;
  if (blocked || picks.blocked) chips.push(chip("blocked", "", null, blocked, "Blocked"));
  if (state.query.trim()) chips.push(h("button", { type: "button", class: "link", text: "Show all", onclick: () => setQuery("") }));
  const box = $("#filters");
  box.hidden = !chips.length;
  box.replaceChildren(...chips);
  if (focused) box.querySelector(`[data-chip="${CSS.escape(focused)}"]`)?.focus();
}

// ---- asking the server -------------------------------------------------------------------------

let asked = 0;         // numbers the requests, so only the newest answer is used
let inFlight = null;   // {controller, want}
let timer = null;

export const searching = () => !!(state.query.trim() || state.sort.trim());
const wanted = () => ({ board: state.board.id, rev: state.board.rev, query: state.query, sort: state.sort });
const sameSearch = (a, b) => !!a && !!b && a.board === b.board && a.query === b.query && a.sort === b.sort;
const sameAll = (a, b) => sameSearch(a, b) && a.rev === b.rev;

function cancel() {
  clearTimeout(timer);
  timer = null;
  asked++;
  inFlight?.controller.abort();
  inFlight = null;
}

// Make sure the server is asked about the search as it is now: after new text, a new sort, or a change to the board
// from anywhere. Asking again for what was just answered or refused, or what is on its way, does nothing.
export function searchSoon(delay = 0) {
  if (!state.board) return;
  if (!searching()) {
    cancel();
    state.found = null;
    state.searchError = null;
    return;
  }
  const want = wanted();
  if (sameAll(state.found, want) || sameAll(state.searchError, want)) {
    cancel();
    return;
  }
  if (inFlight && sameAll(inFlight.want, want)) return;
  clearTimeout(timer);
  timer = delay ? setTimeout(() => { timer = null; searchSoon(); }, delay) : null;
  if (!delay) ask(want);
}

async function ask(want) {
  cancel();
  const n = asked;
  const controller = new AbortController();
  inFlight = { controller, want };
  const params = new URLSearchParams({ q: want.query, sort: want.sort });
  try {
    const data = await api("GET", `/api/boards/${encodeURIComponent(want.board)}/search?${params}`, undefined, undefined, controller.signal);
    if (n !== asked || !Array.isArray(data.keys)) return;
    state.found = { ...want, keys: new Map(data.keys.map((key, i) => [key, i])), matched: data.matched, total: data.total };
    state.searchError = null;
  } catch (e) {
    if (n !== asked || e.name === "AbortError") return;
    if (e.status !== 400 || !e.detail?.part) return;  // gone or broken: the banner says so, and the next draw asks again
    state.searchError = { ...want, message: e.message, position: Number(e.detail.position) || 0, part: e.detail.part };
  } finally {
    if (n === asked) inFlight = null;
  }
  if (state.board?.id !== want.board) return;
  renderColumns();
  renderSearchNote();
}

// ---- what the board shows ----------------------------------------------------------------------

// The cards each column shows: null when nothing narrows or orders the board, or {columns: Map column id -> cards,
// matched, total}. The server's answer for this very search is drawn as it came; until it arrives, plain words in the
// board's own order are matched here, and anything else keeps the last answer on screen. Cards stay in the column
// they are in now, so a card just moved shows where it went until the next answer.
export function drawPlan() {
  if (!searching()) return null;
  const b = state.board;
  const found = state.found?.board === b.id ? state.found : null;
  const current = sameSearch(found, wanted());
  const words = !current && !state.sort.trim() ? plainWords(state.query) : null;
  const columns = new Map(b.columns.map((c) => [c.id, []]));
  if (words) {
    for (const col of b.columns) columns.set(col.id, cardsIn(col.id).filter((c) => matchesWords(c, words)));
  } else if (found) {
    for (const key of found.keys.keys()) {
      const card = cardBy(key);
      if (card && columns.has(card.column)) columns.get(card.column).push(card);
    }
    if (!sortActive(found.query, found.sort)) for (const list of columns.values()) list.sort(byRank);
  } else {
    for (const col of b.columns) columns.set(col.id, cardsIn(col.id));
  }
  let matched = 0;
  for (const list of columns.values()) matched += list.length;
  const total = current && found.rev === b.rev ? found.total : activeCards().length;
  return { columns, matched, total };
}

// ---- the message under the box -----------------------------------------------------------------

// A search with the spot the server stopped at marked, from there to the end of that word. Clicking it puts the
// cursor there.
export function markedText(text, position, onPick) {
  const at = Math.max(0, Math.min(Number(position) || 0, text.length));
  let end = at;
  while (end < text.length && !/\s/.test(text[end])) end++;
  const spot = text.slice(at, end);
  return h("code", { class: "query-text", title: onPick ? "Go to this spot" : null, onclick: onPick ? () => onPick(at, end) : null },
    text.slice(0, at), h("span", { class: "sr", text: " (the problem starts here:) " }),
    h("mark", { class: spot ? null : "at-end", text: spot || " " }), text.slice(end));
}

function pickSpot(at, end) {
  const box = $("#search");
  box.focus();
  box.setSelectionRange(at, end);
}

let announced = null;  // the error last read out, so a redraw doesn't repeat it

export function renderSearchNote() {
  const b = state.board;
  const err = state.searchError;
  const box = $("#search-error");
  const show = b && sameSearch(err, wanted());
  if (show && announced !== err) announce(err.message);
  announced = show ? err : null;
  $("#search").setAttribute("aria-invalid", String(!!show && err.part === "query"));
  $("#sort-by").setAttribute("aria-invalid", String(!!show && err.part === "sort"));
  box.hidden = !show;
  if (!show) box.replaceChildren();
  else {
    const view = b.views.find((v) => v.id === state.viewId && v.query === state.query && v.sort === state.sort);
    const sortPart = err.part === "sort";
    const text = sortPart ? err.sort : err.query;
    box.replaceChildren(
      h("p", { class: "search-message", text: sortPart ? `Sort: ${err.message}` : err.message }),
      text.trim() && markedText(text, err.position, sortPart ? () => $("#sort-by").focus() : pickSpot),
      h("div", { class: "search-fixes" },
        view && h("button", { type: "button", text: `Edit the view ${view.name}`, onclick: () => openViews(view.id) }),
        h("span", { class: "hint", text: "The board shows the last search that worked." })));
  }
  const sorted = b && sortActive();
  const hint = $("#sort-hint");
  hint.hidden = !sorted;
  if (sorted) {
    // While the search is refused, the board still shows the last one that worked, which may be in another order.
    const by = sortName(state.sort);
    const what = by ? `Sorted by ${by}` : "Sorted by the search";
    hint.replaceChildren(h("span", { text: show ? `${what}${by ? "," : ""} once the search works. While the board is sorted, dragging moves a card to another column but not up or down. `
      : `${what}. Cards keep that order in each column, so dragging moves a card to another column but not up or down. ` }),
      h("button", { type: "button", class: "link", text: "Use manual order", onclick: useManualOrder }));
  }
  const all = $("#search-all");
  all.hidden = !b || !state.query.trim() || state.boards.length < 2;
}

// ---- the box -----------------------------------------------------------------------------------

// Put a whole new search in the box, as the chips, saved views and the address do, and ask the server straight away.
export function setQuery(query, { sort = state.sort, viewId = state.viewId } = {}) {
  Object.assign(state, { query, sort, viewId });
  const box = $("#search");
  if (box.value !== query) box.value = query;
  noteAddress();
  redraw();
  searchSoon();
}

function useManualOrder() {
  const words = state.query.replace(/(^|\s)sort:\S*/gi, "$1").replace(/\s+/g, " ").trim();
  setQuery(sortActive(state.query, "") ? words : state.query, { sort: "" });
  announce("Cards are in manual order again.");
}

export function redraw() {
  renderFilters();
  renderSort();
  renderViews();
  renderColumns();
  renderSearchNote();
}

// Everything around the box, after the board changed.
export function renderTools() {
  const box = $("#search");
  if (box.value !== state.query) box.value = state.query;
  renderFilters();
  renderSort();
  renderViews();
  renderSearchNote();
}

// Why the keyboard can't move a card up or down right now.
export function sortedNote() {
  const by = sortName(state.sort);
  const message = `The board is sorted${by ? ` by ${by}` : ""}, so cards can't be moved up or down by hand. Choose Manual order under Sort to move them.`;
  toast(message);
  announce(message);
}

export function bindSearch() {
  const box = $("#search");
  let drawTimer = null;
  box.addEventListener("input", () => {
    state.query = box.value;
    noteAddress();
    clearTimeout(drawTimer);
    const draw = () => {
      renderFilters();
      renderViews();
      renderColumns();
      renderSearchNote();
    };
    if (state.board && state.board.cards.length > LARGE_BOARD) drawTimer = setTimeout(draw, TYPING_DELAY);
    else draw();
    searchSoon(TYPING_DELAY);
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && box.value) {
      e.preventDefault();
      setQuery("");
    } else if (e.key === "Enter" && e.shiftKey) {
      e.preventDefault();
      openEverywhere();
    } else if (e.key === "Enter") {
      e.preventDefault();
      searchSoon();
    }
  });
  $("#search-all").addEventListener("click", openEverywhere);
}

export const matchCount = (plan) => (plan ? `${plan.matched} of ${plural(plan.total, "card")}` : "");
