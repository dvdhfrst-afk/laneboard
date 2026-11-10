// Opening boards and cards: following the address (#<board id> or #<board id>/<card key>) and going to a board.

import { parseHash, recall, resetView, setBoard, setHash, sheet, state } from "./state.js";
import { askDiscard, closeSheet, toast } from "./dom.js";
import { loadBoard, loadBoards } from "./api.js";
import { render } from "./board.js";
import { openCard } from "./card.js";

export async function route() {
  if (sheet.dirty) {  // arrived here by the back button or a typed address; don't drop what's being typed
    const want = location.hash;
    if (state.board) setHash(state.board.id, sheet.key);
    askDiscard(() => {
      sheet.dirty = false;
      closeSheet(true);
      location.hash = want;
    });
    return;
  }
  const want = parseHash();
  const ids = state.boards.map((b) => b.id);
  const id = [want.board, recall()].find((x) => ids.includes(x)) || ids[0];
  if (!id) {
    setBoard(null);
    closeSheet(true);
    render();
    return;
  }
  try {
    if (!state.board || state.board.id !== id) {
      closeSheet(true);
      resetView();
      await loadBoard(id);
    }
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  const key = want.board === id ? want.key : "";
  setHash(id, key);
  render();
  if (key) openCard(key);
  else if (sheet.mode === "view") closeSheet(true);
}

export async function goToBoard(id) {
  sheet.dirty = false;
  closeSheet(true);
  await loadBoards();
  resetView();
  await loadBoard(id);
  setHash(id);
  render();
}
