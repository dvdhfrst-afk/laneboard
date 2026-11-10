// The More menu in the header.

import { index, state } from "./state.js";
import { $, h } from "./dom.js";
import { openArchive } from "./archive.js";
import { duplicateBoard } from "./settings.js";
import { chooseImportFile } from "./transfer.js";
import { openHelp } from "./help.js";

export function renderMenu() {
  const b = state.board;
  const item = (label, run, disabled) => h("button", { type: "button", text: label, disabled, onclick: () => { closeMenu(); run(); } });
  const link = (label, href) => h("a", { href, download: "", text: label, onclick: closeMenu });
  const boardItems = b ? [
    item(`Archived cards (${index.archived.length})`, openArchive),
    link("Export this board", `/api/boards/${b.id}/export`),
    item("Duplicate this board", duplicateBoard),
  ] : [];
  $("#menu-items").replaceChildren(...boardItems,
    link("Export all boards", "/api/export"),
    item("Import a file", chooseImportFile),
    item("Keyboard shortcuts and help", openHelp));
}

export function closeMenu() {
  $("#menu").removeAttribute("open");
}

// A click anywhere else closes the menu.
export function bindMenu() {
  document.addEventListener("click", (e) => {
    if ($("#menu").open && !e.target.closest?.("#menu")) closeMenu();
  });
}
