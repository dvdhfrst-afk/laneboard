// Moving cards: drag and drop, the move buttons and Shift+arrow keys, and saving the new place.

import { cardBy, cardsIn, state } from "./state.js";
import { $, announce, h, toast } from "./dom.js";
import { send } from "./api.js";
import { render } from "./board.js";
import { matches, queryWords } from "./search.js";

export function bindDrag() {
  const main = $("#board");
  const line = h("div", { class: "drop-line" });
  const nextCard = (el, dir) => {
    let n = el[dir];
    while (n && !(n.classList.contains("card") && !n.classList.contains("dragging"))) n = n[dir];
    return n;
  };
  const clear = (except) => {
    for (const col of main.querySelectorAll(".drop-target")) if (col !== except) col.classList.remove("drop-target");
  };
  const end = () => {
    state.drag = null;
    line.remove();
    clear(null);
    main.querySelector(".dragging")?.classList.remove("dragging");
  };

  main.addEventListener("dragstart", (e) => {
    const el = e.target.closest?.(".card");
    if (!el) return;
    state.drag = el.dataset.key;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", el.dataset.key);
    requestAnimationFrame(() => el.classList.add("dragging"));
  });
  main.addEventListener("dragover", (e) => {
    if (!state.drag) return;
    const col = e.target.closest(".col");
    clear(col);
    if (!col) {
      line.remove();
      return;
    }
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    col.classList.add("drop-target");
    const before = [...col.querySelectorAll(".card:not(.dragging)")].find((c) => {
      const r = c.getBoundingClientRect();
      return e.clientY < r.top + r.height / 2;
    }) || col.querySelector(".more");
    if (line.parentNode !== col || line.nextElementSibling !== before) col.insertBefore(line, before);
  });
  main.addEventListener("dragleave", (e) => {
    if (!main.contains(e.relatedTarget)) {
      line.remove();
      clear(null);
    }
  });
  main.addEventListener("drop", (e) => {
    const col = e.target.closest(".col");
    if (!state.drag || !col) return;
    e.preventDefault();
    const key = state.drag;
    const prev = line.parentNode ? nextCard(line, "previousElementSibling") : null;
    const next = line.parentNode ? nextCard(line, "nextElementSibling") : null;
    end();
    moveCard(key, col.dataset.col, prev?.dataset.key, next?.dataset.key);
  });
  main.addEventListener("dragend", end);
}

// Put a card in `column` right after prevKey, or right before nextKey, or at the bottom. Those are
// the neighbours on screen; cards hidden by a filter or a "show more" button may sit between them.
function moveCard(key, column, prevKey, nextKey) {
  const card = cardBy(key);
  if (!card) return;
  const current = cardsIn(column);
  const others = current.filter((c) => c.key !== key);
  let at = others.length;
  if (prevKey && others.some((c) => c.key === prevKey)) at = others.findIndex((c) => c.key === prevKey) + 1;
  else if (nextKey && others.some((c) => c.key === nextKey)) at = others.findIndex((c) => c.key === nextKey);
  if (card.column === column && current.indexOf(card) === at) return;
  const p = others[at - 1]?.rank;
  const n = others[at]?.rank;
  const rank = p !== undefined && n !== undefined ? (p + n) / 2 : p !== undefined ? p + 1 : n !== undefined ? n - 1 : card.rank;
  saveMove(card, column, rank);
}

export function setColumn(key, column) {
  const card = cardBy(key);
  if (!card || card.column === column) return;
  moveCard(key, column);
}

// Move up or down among the cards showing, as the buttons on a card and Shift+arrow keys do.
export function shiftCard(key, delta) {
  const card = cardBy(key);
  if (!card) return false;
  const words = queryWords();
  const visible = cardsIn(card.column).filter((c) => c.key === key || matches(c, words));
  const at = visible.indexOf(card);
  const target = visible[at + delta];
  if (!target) return false;
  if (delta < 0) moveCard(key, card.column, null, target.key);
  else moveCard(key, card.column, target.key, null);
  return true;
}

export function shiftColumn(key, delta) {
  const card = cardBy(key);
  const columns = state.board.columns;
  const at = columns.findIndex((c) => c.id === card?.column);
  const target = columns[at + delta];
  if (!card || !target) return false;
  moveCard(key, target.id);
  announce(`${key} moved to ${target.name}.`);
  return true;
}

function saveMove(card, column, rank) {
  const before = { column: card.column, rank: card.rank };
  Object.assign(card, { column, rank });
  render();
  send("PATCH", `/cards/${card.key}`, { column, rank }, { retry: true })
    .then(() => {
      const col = state.board?.columns.find((c) => c.id === column);
      const n = cardsIn(column).length;
      if (col?.limit && n > col.limit) toast(`${col.name} now holds ${n} cards; its limit is ${col.limit}.`);
    })
    .catch((e) => {
      Object.assign(card, before);
      if (state.board) render();
      toast(e.message, { error: true });
    });
}
