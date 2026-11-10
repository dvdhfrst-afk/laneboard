// The Backups sheet: the copies of the boards file Laneboard keeps (the same list as the backups command), and going
// back to one. Restoring goes through the server's restore, which keeps the current file as a backup first; then the
// page reads the boards again. It works when the boards file is broken, which is when it's most wanted.

import { setBoard, sheet, state } from "./state.js";
import { closeSheet, h, sheetHead, showSheet, toast } from "./dom.js";
import { api, loadBoards } from "./api.js";
import { plural, when } from "./util.js";
import { route } from "./nav.js";

const holds = (b) => (b.boards === null ? "Can't be read" : `${plural(b.boards, "board")}, ${plural(b.cards, "card")}`);
const size = (bytes) => (bytes < 1000 ? `${bytes} bytes` : `${Math.round(bytes / 1000)} KB`);

export async function openBackups() {
  const list = h("div", { class: "backup-list" }, h("p", { class: "hint", text: "Looking for backups…" }));
  const info = state.health || {};
  showSheet("backups", null, h("div", { class: "form" },
    sheetHead("Backups"),
    h("p", { class: "hint", text: "Laneboard keeps the version before the last save, a copy for each day you made changes, "
      + "and a copy before a board is deleted or a backup is restored. Restoring one makes it your boards file; "
      + "what you have now is kept as a backup first, so you can come back to it." }),
    info.backups && h("p", { class: "hint" }, "Kept in ", h("code", { text: info.backups }), " and next to the boards file."),
    list));
  let backups;
  try {
    backups = (await api("GET", "/api/backups")).backups;
  } catch (e) {
    list.replaceChildren(h("p", { class: "form-error", role: "alert", text: e.message }));
    return;
  }
  if (sheet.mode !== "backups") return;  // closed, or something else opened, while the list was on its way
  list.replaceChildren(...(backups.length ? backups.map((b) => row(b))
    : [h("p", { class: "hint", text: "No backups yet. They appear after the first change." })]));
  list.querySelector("button")?.focus();
}

function row(backup) {
  const said = h("p", { class: "form-error", role: "alert" });
  const readable = backup.boards !== null;
  const restore = h("button", { type: "button", text: "Restore", disabled: !readable,
    "aria-label": `Restore ${backup.name}`, onclick: () => ask() });
  const line = h("div", { class: "backup-row" },
    h("div", { class: "backup-text" },
      h("span", { class: "backup-name", text: backup.name === "previous" ? "previous (before the last save)" : backup.name }),
      h("span", { class: "hint", text: `${when(backup.modified)} · ${size(backup.bytes)} · ${holds(backup)}` })),
    restore, said);
  const ask = () => {
    line.querySelector(".confirm")?.remove();
    const keep = h("button", { type: "button", text: "Keep what I have", onclick: () => { box.remove(); restore.focus(); } });
    const go = h("button", {
      type: "button", class: "danger solid", text: "Restore it",
      onclick: async () => {
        go.disabled = true;
        said.textContent = "";
        try {
          const done = (await api("POST", "/api/backups/restore", { name: backup.name })).restored;
          sheet.dirty = false;
          closeSheet(true);
          setBoard(null);
          await loadBoards();
          await route();
          toast(`Restored ${backup.name}: ${plural(done.boards, "board")}, ${plural(done.cards, "card")}.`
            + (done.kept ? ` What you had is kept as ${done.kept}.` : ""));
        } catch (e) {
          said.textContent = e.message;
          go.disabled = false;
        }
      },
    });
    const box = h("div", { class: "confirm", role: "alert" },
      h("span", { text: `Make ${backup.name} your boards file? It holds ${holds(backup).toLowerCase()}. `
        + "Every board goes back to how it was then. What you have now is kept as a backup first." }),
      go, keep);
    said.before(box);
    keep.focus();
  };
  return line;
}
