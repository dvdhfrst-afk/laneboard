// Drawing the open board: the header, the columns and the cards on them, the groups table, and what an empty page offers.

import { activeCards, cardsIn, filterParams, groupOf, index, isDone, reindex, setBoard, state, store, stored, syncListEntry } from "./state.js";
import { countBy, plural } from "./util.js";
import { $, groupLink, groupTag, h, rich } from "./dom.js";
import { renderMenu } from "./menu.js";
import { drawPlan, matchCount, renderTools, searchSoon } from "./search.js";
import { openCard, progress } from "./card.js";
import { openCardForm } from "./cardform.js";
import { openNewBoard, startingPoints } from "./settings.js";
import { chooseImportFile } from "./transfer.js";
import { labelChips } from "./labels.js";
import { propFace } from "./properties.js";

const COLUMN_PAGE = 300;   // cards drawn per column before a "show more" button; keeps huge boards quick
const COLLAPSED = "laneboard:collapsed:";

export function showBoard(board) {
  setBoard(board);
  syncListEntry();
  render();
}

export function render() {
  const b = state.board;
  reindex();
  document.title = b ? `${b.name} - Laneboard` : "Laneboard";
  $("#board-name").textContent = b ? b.name : "Laneboard";
  renderPicker();
  renderMenu();
  const desc = $("#board-desc");
  desc.textContent = b?.description || "";
  desc.hidden = !b?.description;
  $("#new-card").disabled = !b;
  $("#board-settings").disabled = !b;
  $("#tools").hidden = !b;
  if (!b) {
    $("#groups").hidden = true;
    $("#welcome").hidden = true;
    renderEmpty();
    return;
  }
  renderWelcome();
  renderTools();
  renderColumns();
  renderGroups();
  searchSoon();
}

export function renderPicker() {
  const pick = $("#board-pick");
  const others = state.boards.filter((b) => b.id !== state.board?.id);
  const signature = JSON.stringify(others.map((b) => [b.id, b.name, b.cards]));
  if (pick.dataset.signature === signature) return;  // redrawing would close the list if it's open
  pick.dataset.signature = signature;
  pick.hidden = !others.length;
  pick.replaceChildren(h("option", { value: "", text: "Switch board" }),
    ...others.map((b) => h("option", { value: b.id, text: `${b.name} (${plural(b.cards, "card")})` })));
  pick.value = "";
}

function renderEmpty() {
  const main = $("#board");
  main.style.gridTemplateColumns = "";
  main.replaceChildren(h("div", { class: "start" },
    h("p", { text: "There are no boards yet." }),
    h("div", { class: "start-actions" },
      h("button", { type: "button", class: "primary", text: "Create a board", onclick: () => openNewBoard() }),
      h("button", { type: "button", text: "Import a file", onclick: chooseImportFile })),
    startingPoints()));
}

// First run: an empty board says what to do next and offers the examples.
function renderWelcome() {
  const box = $("#welcome");
  const empty = !state.board.cards.length;
  box.hidden = !empty;
  if (!empty || box.dataset.board === state.board.id) return;
  box.dataset.board = state.board.id;
  box.replaceChildren(
    h("p", { class: "welcome-lead", text: "This board is empty." }),
    h("p", { text: "Press n or use New card to add your first card. Drag cards between columns, click one to open it, and press ? for keyboard shortcuts." }),
    startingPoints());
}

function collapsedColumns() {
  try { return new Set(JSON.parse(stored(COLLAPSED + state.board.id, "[]"))); } catch { return new Set(); }
}

function toggleCollapsed(columnId) {
  const set = collapsedColumns();
  set.has(columnId) ? set.delete(columnId) : set.add(columnId);
  store(COLLAPSED + state.board.id, JSON.stringify([...set]));
  renderColumns();
  $(`[data-col="${CSS.escape(columnId)}"] .collapse`)?.focus();
}

// Redrawing replaces every card, so remember where the reader is: the card holding focus and how
// far each column is scrolled.
export function renderColumns() {
  const b = state.board;
  const main = $("#board");
  const focusedCard = document.activeElement?.closest?.("#board .card")?.dataset.key;
  if (focusedCard) {
    state.focusKey = focusedCard;
    state.wantFocus = true;
  }
  const scroll = new Map([...main.querySelectorAll(".col")].map((c) => [c.dataset.col, c.scrollTop]));
  const plan = drawPlan();
  const narrowed = !!plan;
  drawn = plan ? new Set([...plan.columns.values()].flat().map((c) => c.key)) : null;
  const params = filterParams();
  const href = (key) => `#${b.id}/${key}${params ? "?" + params : ""}`;
  const collapsed = collapsedColumns();
  const face = faceParts();
  const refuse = b.settings.limitPolicy === "refuse";
  main.style.gridTemplateColumns = b.columns.map((c) => (collapsed.has(c.id) ? "minmax(130px, 150px)" : "minmax(230px, 1fr)")).join(" ");
  const columns = b.columns.map((col) => {
    const all = cardsIn(col.id);
    const shown = plan ? plan.columns.get(col.id) : all;
    const over = col.limit && all.length > col.limit;
    const full = refuse && col.limit && all.length >= col.limit;
    const closed = collapsed.has(col.id);
    const note = state.columnNote?.column === col.id ? state.columnNote.message : "";
    const limit = state.shown.get(col.id) || COLUMN_PAGE;
    const page = shown.length > limit ? shown.slice(0, limit) : shown;
    return h("section", { class: closed ? "col collapsed" : "col", "data-col": col.id, "aria-label": col.name },
      h("div", { class: "col-head" },
        h("button", { type: "button", class: "collapse", "aria-expanded": String(!closed), title: `${closed ? "Show" : "Hide"} the cards in ${col.name}`,
          "aria-label": `${closed ? "Show" : "Hide"} the cards in ${col.name}`, text: closed ? "▸" : "▾", onclick: () => toggleCollapsed(col.id) }),
        h("h2", { text: col.name }),
        h("div", { class: "right" },
          index.done.has(col.id) && h("span", { class: "done-mark", role: "img", "aria-label": "Done column",
            title: "A done column: its cards count as done", text: "✓" }),
          h("span", { class: over ? "count over" : full ? "count full" : "count",
            title: col.limit ? (full ? `Full: limit ${col.limit}, and this board takes no more cards` : `Limit ${col.limit}`) : null,
            text: narrowed ? `${shown.length} of ${all.length}` : col.limit ? `${all.length} / ${col.limit}` : String(all.length) }),
          h("button", { type: "button", title: `Add a card to ${col.name}`, "aria-label": `Add a card to ${col.name}`,
            text: "+", onclick: () => openCardForm(null, { column: col.id }) }))),
      note && h("div", { class: "col-note", role: "alert", text: note }),
      !closed && (page.length ? page.map((card) => cardEl(card, face, href(card.key)))
        : h("div", { class: "empty", text: narrowed && all.length ? "No matching cards" : "No cards" })),
      !closed && shown.length > page.length && h("button", {
        type: "button", class: "more", text: `Show ${Math.min(COLUMN_PAGE, shown.length - page.length)} more of ${shown.length - page.length}`,
        onclick: () => { state.shown.set(col.id, limit + COLUMN_PAGE); renderColumns(); },
      }));
  });
  main.replaceChildren(...columns);
  for (const el of columns) {
    if (scroll.get(el.dataset.col)) el.scrollTop = scroll.get(el.dataset.col);
  }
  settleFocus(main);
  $("#search-count").textContent = matchCount(plan);
}

// The keys of the cards the last draw showed (whether or not a "show more" button hides some), or null for all of
// them. Moving a card up or down steps over the cards in between that aren't showing.
let drawn = null;
export const isDrawn = (key) => !drawn || drawn.has(key);

// Only one card is a tab stop; arrow keys go between cards. Otherwise a big board takes a thousand Tab presses to cross.
function settleFocus(main) {
  const keyed = state.focusKey ? main.querySelector(`.card[data-key="${CSS.escape(state.focusKey)}"]`) : null;
  const stop = keyed || main.querySelector(".card");
  if (stop) stop.tabIndex = 0;
  if (keyed && state.wantFocus) {
    keyed.focus({ preventScroll: true });
    keyed.scrollIntoView({ block: "nearest" });
  }
  state.wantFocus = false;
}

// What a card shows under its title, in the board's order (settings.cardFace): each entry is a function of
// the card giving a node, or nothing when the card has nothing to show for it.
function faceParts() {
  const parts = {
    group: (card) => {
      const g = groupOf(card);
      return g && groupTag(g);
    },
    labels: (card) => card.labels.length > 0 && labelChips(card.labels),
    field: (card) => {
      const first = card.fields.find((f) => f.value);
      return first && h("span", { class: "first-field", text: first.value });
    },
    progress: (card) => {
      const p = progress(card);
      const what = p.ticks && p.tasks ? "Checklist lines ticked and tasks done" : p.tasks ? "Tasks done" : "Checklist lines ticked";
      const done = isDone(card);
      return p.total > 0 && h("span", { class: `ticks${p.done === p.total ? " all" : ""}`,
        title: done ? `${what}. The card is in a done column.` : what, text: `${done ? "✓ " : ""}${p.done}/${p.total}` });
    },
    blocked: (card) => card.blocked && h("span", { class: "flag", text: "Blocked" }),
  };
  return state.board.settings.cardFace.map((part) => {
    if (parts[part]) return parts[part];
    const prop = index.props.get(part);
    return prop ? (card) => propFace(prop, card.properties[prop.id]) : null;
  }).filter(Boolean);
}

// A card as it shows in a column. An archived card shows only when a search asks for archived cards, and stays put.
function cardEl(card, face, href) {
  const meta = face.map((part) => part(card)).filter(Boolean);
  if (card.archived) meta.unshift(h("span", { class: "flag archived-flag", text: "Archived" }));
  const el = h("a", {
    class: card.archived ? "card archived" : "card", href, "data-key": card.key, tabIndex: -1,
    onclick: (e) => {
      if (e.button || e.ctrlKey || e.metaKey || e.shiftKey) return;  // let the browser open a new tab
      e.preventDefault();
      openCard(card.key);
    },
  },
  h("span", { class: "key", text: card.key }),
  h("span", { class: "title", text: card.title }),
  meta.length > 0 && h("span", { class: "meta" }, meta));
  el.draggable = !card.archived;
  return el;
}

function renderGroups() {
  const b = state.board;
  $("#groups").hidden = !b.groups.length;
  if (!b.groups.length) return;
  $("#groups-title").textContent = `${b.groupLabel} details`;
  const labels = [...new Set(b.groups.flatMap((g) => g.details.map((d) => d.label)))];
  const counts = countBy(activeCards(), (c) => c.group);
  $("#groups-table").replaceChildren(
    h("thead", {}, h("tr", {}, [b.groupLabel, "Key", "Cards", ...labels].map((t) => h("th", { scope: "col", text: t })))),
    h("tbody", {}, b.groups.map((g) => h("tr", {},
      h("td", {}, groupLink(g)),
      h("td", {}, h("code", { text: g.prefix })),
      h("td", { text: String(counts.get(g.id) || 0) }),
      labels.map((l) => h("td", {}, rich(g.details.find((d) => d.label === l)?.value || "")))))));
}
