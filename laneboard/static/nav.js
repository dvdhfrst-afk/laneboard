// Opening boards and cards: following the address (#<board id> or #<board id>/<card key>, then the search and sort,
// see parseHash) and going to a board. A board opening with no search in the address uses its default view.

import { parseHash, recall, resetView, setBoard, setHash, sheet, state } from "./state.js";
import { askDiscard, closeSheet, toast } from "./dom.js";
import { loadBoard, loadBoards } from "./api.js";
import { render } from "./board.js";
import { openCard } from "./card.js";
import { useDefaultView, useFilter } from "./views.js";

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
  let opened = false;
  try {
    if (!state.board || state.board.id !== id) {
      closeSheet(true);
      resetView();
      await loadBoard(id);
      opened = true;
    }
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  const filter = want.board === id ? want.filter : null;
  if (filter) useFilter(filter);
  else if (opened) useDefaultView();
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
  useDefaultView();
  setHash(id);
  render();
}
