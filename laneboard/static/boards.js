// The boards overview (#/boards): every board with its counts, what its columns hold, and what can be done to it,
// in two parts, the boards in use and the archived ones. Archiving keeps a board whole and puts it out of the way:
// it leaves the board picker, Move to board and Search every board, and it can be opened here or by its address.

import { setBoard, state } from "./state.js";
import { $, h, toast } from "./dom.js";
import { api, loadBoards, send } from "./api.js";
import { renderPicker } from "./board.js";
import { plural } from "./util.js";

export const OVERVIEW = "/boards";  // the address part after #, which no board id can be
let shown = null;                    // the last answer drawn, as text, so polling redraws only on a change
let editing = null;                  // the board being renamed, so a redraw keeps the box open

export const isOverview = () => location.hash.slice(1).split("?")[0] === OVERVIEW;
export const openOverview = () => { location.hash = OVERVIEW; };

// Called by route() when the address is the overview, and by the regular refresh while it shows.
export async function showOverview({ quiet = false } = {}) {
  state.overview = true;
  if (state.board) setBoard(null);
  let boards;
  try {
    boards = (await api("GET", "/api/overview")).boards;
  } catch (e) {
    if (!quiet) toast(e.message, { error: true });
    return;
  }
  renderPicker();
  const text = JSON.stringify(boards);
  if (quiet && (editing || (text === shown && $("#overview").childElementCount))) return;  // nothing new, or typing
  shown = text;
  // A redraw replaces every row, so put focus back on the same control of the same board.
  const active = document.activeElement;
  const li = active?.closest?.("#overview [data-board]");
  const where = li && [li.dataset.board, active.dataset.action || (active.closest(".ov-name") ? "name" : "")];
  draw(boards);
  if (where) focusIn(...where);
}

function focusIn(id, action) {
  const li = id && $(`#overview [data-board="${CSS.escape(id)}"]`);
  if (!li) {
    $("#overview h2")?.focus();
    return null;
  }
  const moves = action === "up" || action === "down";
  const target = (action === "rename-box" && li.querySelector(".ov-rename input"))
    || li.querySelector(`[data-action="${action}"]:not(:disabled)`)
    || (moves && li.querySelector('[data-action="up"]:not(:disabled), [data-action="down"]:not(:disabled)'))
    || li.querySelector(".ov-name a, .ov-rename input");
  target?.focus();
  return target;
}

export function leaveOverview() {
  state.overview = false;
  shown = null;
  editing = null;
  $("#overview").replaceChildren();
}

// Archive or restore a board; used here and by the note an archived board shows at its top.
export async function setBoardArchived(board, archived) {
  await api("PATCH", `/api/boards/${encodeURIComponent(board.id)}`, { archived }, board.rev);
  await loadBoards();
  toast(archived ? `Archived ${board.name}. It's under Archived boards on the boards overview.` : `Restored ${board.name}.`);
}

// Archive or restore the open board, from More, the palette or the note an archived board shows. It stays open.
export async function archiveOpenBoard(archived) {
  const name = state.board.name;
  try {
    await send("PATCH", "", { archived });
    await loadBoards();
    renderPicker();
    toast(archived ? `Archived ${name}. It stays open here, and All boards lists it under Archived boards.` : `Restored ${name}.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

function columnBar(board) {
  const total = board.columns.reduce((n, c) => n + c.cards, 0);
  const words = board.columns.map((c) => `${c.name} ${c.cards}${c.done ? " (done)" : ""}`).join(", ");
  const bar = h("div", { class: "col-bar", role: "img", "aria-label": `Cards per column: ${words}` });
  if (!total) bar.classList.add("empty");
  board.columns.forEach((c, i) => {
    if (!c.cards) return;
    const part = h("span", { class: c.done ? "seg done" : `seg s${i % 3}`, title: `${c.name}: ${plural(c.cards, "card")}${c.done ? ", done" : ""}` });
    part.style.flexGrow = String(c.cards);  // a style property, which the page's content policy allows
    bar.append(part);
  });
  return h("div", { class: "col-bar-wrap" }, bar, h("p", { class: "col-words", text: words }));
}

function row(board, place, count, refresh) {
  const said = h("p", { class: "form-error", role: "alert" });
  const act = (label, action, run, extra = {}) => h("button", {
    type: "button", text: label, "data-action": action, ...extra,
    onclick: async (e) => {
      said.textContent = "";
      const button = e.currentTarget;
      button.disabled = true;
      try {
        await run();
      } catch (err) {
        said.textContent = err.message;
      } finally {
        button.disabled = false;
      }
    },
  });
  const archived = !!board.archivedAt;
  const name = h("h3", { class: "ov-name" }, h("a", { href: `#${board.id}`, text: board.name }));
  const renaming = editing === board.id;
  const rename = () => {
    const box = h("input", { value: board.name, maxLength: 120, "aria-label": `New name for ${board.name}` });
    const save = async () => {
      await api("PATCH", `/api/boards/${encodeURIComponent(board.id)}`, { name: box.value }, board.rev);
      editing = null;
      await loadBoards();
      await refresh(board.id, "rename");
    };
    const cancel = () => { editing = null; refresh(board.id, "rename", true); };
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        save().catch((err) => { said.textContent = err.message; });
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        cancel();
      }
    });
    return h("form", { class: "ov-rename", onsubmit: (e) => { e.preventDefault(); save().catch((err) => { said.textContent = err.message; }); } },
      box, h("button", { type: "submit", class: "primary", text: "Save" }), h("button", { type: "button", text: "Cancel", onclick: cancel }));
  };
  const counts = `${plural(board.cards, "card")}: ${board.done} done, ${board.notDone} not done`
    + (board.archived ? `, and ${board.archived} archived` : "");
  const actions = h("div", { class: "ov-actions" },
    h("a", { class: "button", href: `#${board.id}`, "data-action": "open", text: "Open" }),
    act("Rename", "rename", () => { editing = board.id; refresh(board.id, "rename-box", true); }),
    act("Duplicate", "duplicate", async () => {
      const full = (await api("GET", `/api/boards/${encodeURIComponent(board.id)}`)).board;
      const made = await api("POST", "/api/import", { ...full, name: `Copy of ${board.name}`.slice(0, 120) });
      await loadBoards();
      await refresh(made.boards[0].id, "open");
      toast(`Made a copy of ${board.name}: ${made.boards[0].name}.`);
    }),
    h("a", { class: "button", href: `/api/boards/${encodeURIComponent(board.id)}/export`, download: "", "data-action": "export", text: "Export" }),
    act(archived ? "Restore" : "Archive", archived ? "restore" : "archive", async () => {
      await setBoardArchived(board, !archived);
      await refresh(board.id, archived ? "archive" : "restore");
    }),
    count > 1 && act("Move up", "up", () => move(board, place - 1, refresh, "up"),
      { disabled: place === 0, "aria-label": `Move ${board.name} up` }),
    count > 1 && act("Move down", "down", () => move(board, place + 1, refresh, "down"),
      { disabled: place === count - 1, "aria-label": `Move ${board.name} down` }),
    act("Delete", "delete", async () => askDelete(board, said, refresh), { class: "danger" }));
  return h("li", { class: archived ? "ov-board archived" : "ov-board", "data-board": board.id },
    h("div", { class: "ov-top" }, renaming ? rename() : name,
      h("span", { class: "ov-counts", text: counts })),
    board.description && h("p", { class: "ov-desc", text: board.description }),
    columnBar(board), actions, said);
}

async function move(board, position, refresh, action) {
  await api("POST", `/api/boards/${encodeURIComponent(board.id)}/move`, { position });
  await loadBoards();
  await refresh(board.id, action);
}

// Deleting asks first, in the row, and says what it keeps: the server takes a backup before it deletes.
function askDelete(board, said, refresh) {
  const li = said.closest("li");
  li.querySelector(".confirm")?.remove();
  const keep = h("button", { type: "button", text: "Keep it", onclick: () => { box.remove(); li.querySelector('[data-action="delete"]')?.focus(); } });
  const go = h("button", {
    type: "button", class: "danger solid", text: "Delete board",
    onclick: async () => {
      go.disabled = true;
      try {
        await api("DELETE", `/api/boards/${encodeURIComponent(board.id)}`, undefined, board.rev);
        await loadBoards();
        toast(`Deleted ${board.name}. A backup was kept first; Backups in More can bring it back.`);
        await refresh(null, null);
      } catch (e) {
        said.textContent = e.message;
        go.disabled = false;
      }
    },
  });
  const box = h("div", { class: "confirm", role: "alert" },
    h("span", { text: `Delete ${board.name} and its ${plural(board.cards + board.archived, "card")}? A backup of the file is kept first.` }),
    go, keep);
  said.before(box);
  keep.focus();
}

function draw(boards) {
  const root = $("#overview");
  const using = boards.filter((b) => !b.archivedAt);
  const archived = boards.filter((b) => b.archivedAt);
  // After a change, draw again and put focus back where it was: the same button on the same board, or the board's name.
  const refresh = async (id, action, local = false) => {
    if (!local) await showOverview();
    else draw(JSON.parse(shown));
    const target = focusIn(id, action);
    if (action === "rename-box") target?.select();
  };
  const list = (items, empty) => (items.length
    ? h("ol", { class: "ov-list" }, items.map((b, i) => row(b, i, items.length, refresh)))
    : h("p", { class: "hint", text: empty }));
  root.replaceChildren(
    h("div", { class: "ov-head" },
      h("h2", { tabIndex: -1, text: `Boards in use (${using.length})` }),
      h("p", { class: "hint", text: "Open, rename, copy, export, archive, delete and order your boards. The order here is the order everywhere." })),
    list(using, archived.length ? "Every board is archived. Restore one below, or make a new board." : "There are no boards yet. Make one with New board."),
    h("h2", { class: "ov-archived-head", text: `Archived boards (${archived.length})` }),
    h("p", { class: "hint", text: "An archived board keeps all its cards and settings. It stays out of the board picker and Search every board until you restore it." }),
    list(archived, "Nothing is archived. Archive a board to put it out of the way without deleting it."));
}
