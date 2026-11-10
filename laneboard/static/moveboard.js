// Moving a card to another board, from the card view: pick the board and a column on it, then say what didn't carry
// over, with a way to follow the card there.

import { revs, sheet, state } from "./state.js";
import { closeSheet, field, h, toast } from "./dom.js";
import { api, loadBoards, send } from "./api.js";
import { renderPicker } from "./board.js";
import { goToBoard } from "./nav.js";
import { openCard } from "./card.js";

export const otherBoards = () => state.boards.filter((b) => b.id !== state.board?.id && !b.archivedAt);

// The panel that opens under the card's buttons. `flush` saves whatever the card view still holds first, so
// nothing typed on the card is left behind.
export function movePanel(card, flush, close) {
  const key = card.key;
  const boards = otherBoards();
  const board = h("select", { id: "move-board" }, boards.map((b) => h("option", { value: b.id, text: b.name })));
  const column = h("select", { id: "move-column" });
  const error = h("div", { class: "form-error", role: "alert" });
  const go = h("button", { type: "button", class: "primary", text: "Move" });
  const ownColumn = state.board.columns.find((c) => c.id === card.column)?.name.toLowerCase();
  const columns = async () => {
    error.textContent = "";
    column.disabled = go.disabled = true;
    column.replaceChildren(h("option", { text: "Loading…" }));
    const wanted = board.value;
    try {
      const target = (await api("GET", `/api/boards/${encodeURIComponent(wanted)}`)).board;
      if (board.value !== wanted) return;  // another board was picked meanwhile
      revs.set(target.id, target.rev);
      column.replaceChildren(...target.columns.map((c) => h("option", { value: c.id, text: c.limit ? `${c.name} (limit ${c.limit})` : c.name })));
      column.value = (target.columns.find((c) => c.name.toLowerCase() === ownColumn) || target.columns[0]).id;
      column.disabled = go.disabled = false;
    } catch (e) {
      column.replaceChildren();
      error.textContent = e.message;
    }
  };
  board.addEventListener("change", columns);
  go.addEventListener("click", async () => {
    const target = board.value;
    const name = board.selectedOptions[0]?.textContent || "the other board";
    error.textContent = "";
    go.disabled = true;
    flush();
    const sync = sheet.sync;
    sheet.sync = null;  // the card leaving this board isn't the card being deleted somewhere else
    try {
      const data = await send("POST", `/cards/${key}/transfer`, () => ({ board: target, column: column.value, targetRev: revs.get(target) }),
        { retry: true, other: target });
      sheet.dirty = false;
      closeSheet(true);
      const lost = data.dropped.length ? ` These didn't carry over: ${data.dropped.join(", ")}.` : "";
      toast(`Moved ${key} to ${data.target.name} as ${data.key}.${lost}`, {
        action: { label: "Go to it", run: async () => { await goToBoard(data.target.id); openCard(data.key); } },
      });
      loadBoards().then(renderPicker, () => {});  // card counts in the board list
    } catch (e) {
      sheet.sync = sync;
      sync?.();
      error.textContent = e.status === 412 ? `${name} or this board changed at the same moment. Press Move again.` : e.message;
      go.disabled = false;
    }
  });
  columns();
  return h("div", { class: "move-panel", role: "group", "aria-labelledby": "move-title" },
    h("h3", { id: "move-title", text: "Move to another board" }),
    h("div", { class: "row2" }, field("Board", board), field("Column", column)),
    h("p", { class: "hint", text: "It goes to the bottom of that column with a new key. Its group, labels, property values and ticks come along where that board has ones with the same names, and you'll see what didn't." }),
    error,
    h("div", { class: "form-actions" }, go, h("button", { type: "button", text: "Cancel", onclick: close })));
}
