// Drawing the open board: the header, the columns and the cards on them, the groups table, and what an empty page offers.

import { activeCards, cardsIn, groupOf, reindex, setBoard, state, store, stored, syncListEntry } from "./state.js";
import { countBy, plural } from "./util.js";
import { $, groupLink, groupTag, h, rich } from "./dom.js";
import { renderMenu } from "./menu.js";
import { matches, queryWords, renderFilters } from "./search.js";
import { openCard, progress } from "./card.js";
import { openCardForm } from "./cardform.js";
import { openNewBoard, startingPoints } from "./settings.js";
import { chooseImportFile } from "./transfer.js";

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
  renderFilters();
  renderColumns();
  renderGroups();
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
      h("button", { type: "button", class: "primary", text: "Create a board", onclick: openNewBoard }),
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
  const words = queryWords();
  const filtering = state.groups.size > 0 || state.blockedOnly || words.length > 0;
  const collapsed = collapsedColumns();
  main.style.gridTemplateColumns = b.columns.map((c) => (collapsed.has(c.id) ? "minmax(130px, 150px)" : "minmax(230px, 1fr)")).join(" ");
  let matched = 0;
  let total = 0;
  const columns = b.columns.map((col) => {
    const all = cardsIn(col.id);
    const shown = filtering ? all.filter((c) => matches(c, words)) : all;
    total += all.length;
    matched += shown.length;
    const over = col.limit && all.length > col.limit;
    const closed = collapsed.has(col.id);
    const limit = state.shown.get(col.id) || COLUMN_PAGE;
    const drawn = shown.length > limit ? shown.slice(0, limit) : shown;
    return h("section", { class: closed ? "col collapsed" : "col", "data-col": col.id, "aria-label": col.name },
      h("div", { class: "col-head" },
        h("button", { type: "button", class: "collapse", "aria-expanded": String(!closed), title: `${closed ? "Show" : "Hide"} the cards in ${col.name}`,
          "aria-label": `${closed ? "Show" : "Hide"} the cards in ${col.name}`, text: closed ? "▸" : "▾", onclick: () => toggleCollapsed(col.id) }),
        h("h2", { text: col.name }),
        h("div", { class: "right" },
          h("span", { class: over ? "count over" : "count", title: col.limit ? `Limit ${col.limit}` : null,
            text: filtering ? `${shown.length} of ${all.length}` : col.limit ? `${all.length} / ${col.limit}` : String(all.length) }),
          h("button", { type: "button", title: `Add a card to ${col.name}`, "aria-label": `Add a card to ${col.name}`,
            text: "+", onclick: () => openCardForm(null, { column: col.id }) }))),
      !closed && (drawn.length ? drawn.map(cardEl)
        : h("div", { class: "empty", text: filtering && all.length ? "No matching cards" : "No cards" })),
      !closed && shown.length > drawn.length && h("button", {
        type: "button", class: "more", text: `Show ${Math.min(COLUMN_PAGE, shown.length - drawn.length)} more of ${shown.length - drawn.length}`,
        onclick: () => { state.shown.set(col.id, limit + COLUMN_PAGE); renderColumns(); },
      }));
  });
  main.replaceChildren(...columns);
  for (const el of columns) {
    if (scroll.get(el.dataset.col)) el.scrollTop = scroll.get(el.dataset.col);
  }
  settleFocus(main);
  const count = $("#search-count");
  count.textContent = words.length || state.groups.size || state.blockedOnly ? `${matched} of ${plural(total, "card")}` : "";
}

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

// A card as it shows in a column.
function cardEl(card) {
  const g = groupOf(card);
  const first = card.fields.find((f) => f.value);
  const ticks = progress(card);
  return h("a", {
    class: "card", href: `#${state.board.id}/${card.key}`, draggable: true, "data-key": card.key, tabIndex: -1,
    onclick: (e) => {
      if (e.button || e.ctrlKey || e.metaKey || e.shiftKey) return;  // let the browser open a new tab
      e.preventDefault();
      openCard(card.key);
    },
  },
  h("span", { class: "key", text: card.key }),
  h("span", { class: "title", text: card.title }),
  (g || first || card.blocked || ticks.total > 0) && h("span", { class: "meta" },
    g && groupTag(g),
    first && h("span", { class: "first-field", text: first.value }),
    ticks.total > 0 && h("span", { class: `ticks${ticks.done === ticks.total ? " all" : ""}`,
      title: "Checklist lines ticked", text: `${ticks.done}/${ticks.total}` }),
    card.blocked && h("span", { class: "flag", text: "Blocked" })));
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
