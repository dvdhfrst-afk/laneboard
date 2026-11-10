// The More menu in the header, and closing whichever menu is open (More, or Views by the search box).

import { index, state } from "./state.js";
import { $, h } from "./dom.js";
import { openArchive } from "./archive.js";
import { duplicateBoard } from "./settings.js";
import { exportLinks, formats, loadFormats, openImport } from "./transfer.js";
import { openHelp } from "./help.js";
import { openSaveTemplate, openTemplates } from "./templates.js";
import { archiveOpenBoard, openOverview } from "./boards.js";
import { openBackups } from "./backups.js";

export function renderMenu() {
  const b = state.board;
  const item = (label, run, disabled) => h("button", { type: "button", text: label, disabled, onclick: () => { closeMenu(); run(); } });
  // A download: the server names the file and sends it as an attachment, so the page never shows it.
  const link = (label, href, extension) => h("a", { href, download: "", class: "menu-file", onclick: closeMenu },
    h("span", { text: label }), extension && h("span", { class: "hint", text: extension }));
  if (!formats.length) loadFormats().then((list) => { if (list.length) renderMenu(); });
  const boardItems = b ? [
    item(`Archived cards (${index.archived.length})`, openArchive),
    ...exportLinks(b, link),
    item("Duplicate this board", duplicateBoard),
    item("Save as template", openSaveTemplate),
    item(b.archived ? "Restore this board" : "Archive this board", () => archiveOpenBoard(!b.archived)),
  ] : [];
  $("#menu-items").replaceChildren(...boardItems,
    item("All boards", openOverview),
    item("Templates", openTemplates),
    link("Export all boards", "/api/export", ".json"),
    item("Import a file", openImport),
    item("Backups", openBackups),
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
