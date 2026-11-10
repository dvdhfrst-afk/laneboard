// The More menu in the header, and closing whichever menu is open (More, or Views by the search box).

import { index, state } from "./state.js";
import { $, h } from "./dom.js";
import { openArchive } from "./archive.js";
import { duplicateBoard } from "./settings.js";
import { chooseImportFile } from "./transfer.js";
import { openHelp } from "./help.js";
import { openSaveTemplate, openTemplates } from "./templates.js";

export function renderMenu() {
  const b = state.board;
  const item = (label, run, disabled) => h("button", { type: "button", text: label, disabled, onclick: () => { closeMenu(); run(); } });
  const link = (label, href) => h("a", { href, download: "", text: label, onclick: closeMenu });
  const boardItems = b ? [
    item(`Archived cards (${index.archived.length})`, openArchive),
    link("Export this board", `/api/boards/${b.id}/export`),
    item("Duplicate this board", duplicateBoard),
    item("Save as template", openSaveTemplate),
  ] : [];
  $("#menu-items").replaceChildren(...boardItems,
    item("Templates", openTemplates),
    link("Export all boards", "/api/export"),
    item("Import a file", chooseImportFile),
    item("Keyboard shortcuts and help", openHelp));
}

export const openMenu = () => document.querySelector("details.menu[open]");

export function closeMenu() {
  for (const menu of document.querySelectorAll("details.menu[open]")) menu.removeAttribute("open");
}

// A click anywhere else closes an open menu, and opening one closes the other.
export function bindMenu() {
  document.addEventListener("click", (e) => {
    const inside = e.target.closest?.("details.menu");
    for (const menu of document.querySelectorAll("details.menu[open]")) if (menu !== inside) menu.removeAttribute("open");
  });
}
