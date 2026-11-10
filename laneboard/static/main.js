// Laneboard's page starts here: it binds the controls and keys, keeps the open board fresh, and opens the first
// board. Every other module is imported from here; nothing imports this one.

import { APP_VERSION, cardBy, drafts, setBoard, sheet, state } from "./state.js";
import { $, announce, bindSheet, closeSheet, setBannerAction, toast } from "./dom.js";
import { api, checkVersion, loadBoards, pending } from "./api.js";
import { renderPicker, showBoard } from "./board.js";
import { bindMenu, closeMenu, openMenu, renderMenu } from "./menu.js";
import { bindSearch } from "./search.js";
import { bindSort } from "./views.js";
import { bindDrag, shiftCard, shiftColumn } from "./move.js";
import { openCardForm } from "./cardform.js";
import { openNewBoard, openSettings } from "./settings.js";
import { bindImport } from "./transfer.js";
import { openHelp } from "./help.js";
import { route } from "./nav.js";
import { showOverview } from "./boards.js";
import { openBackups } from "./backups.js";
import { openPalette } from "./palette.js";

const POLL_EVERY = 5000;

// Pick up changes made elsewhere (another tab, a command, the file itself).
let refreshing = false;
async function refresh() {
  if (refreshing || pending || state.drag) return;
  refreshing = true;
  try {
    const hadBoards = state.boards.length || state.board;
    await loadBoards();
    if (state.overview) {
      renderPicker();
      await showOverview({ quiet: true });
      return;
    }
    if (!state.board) {
      if (state.boards.length || !hadBoards) await route();
      return;
    }
    if (sheet.mode && sheet.mode !== "view" && sheet.mode !== "archive" && sheet.mode !== "help") return;  // a form holds its own copy until saved
    if (!state.boards.some((b) => b.id === state.board.id)) {
      setBoard(null);
      closeSheet(true);
      await route();
      return;
    }
    const entry = state.boards.find((b) => b.id === state.board.id);
    if (entry.rev !== undefined && entry.rev === state.board.rev) {
      renderPicker();
      return;
    }
    const fresh = (await api("GET", `/api/boards/${state.board.id}`)).board;
    if (fresh.rev === state.board.rev || state.drag || pending) {
      renderPicker();
      return;
    }
    showBoard(fresh);
    sheet.sync?.();
  } catch {
    // Being stopped or having a broken file shows in the banner; the next round tries again.
  } finally {
    refreshing = false;
  }
}

function focusNeighbour(el, key) {
  const main = $("#board");
  const siblings = (col) => [...col.querySelectorAll(".card")];
  if (!el) {
    main.querySelector('.card[tabindex="0"]')?.focus();
    return;
  }
  const col = el.closest(".col");
  const list = siblings(col);
  const at = list.indexOf(el);
  let target = null;
  if (key === "ArrowUp") target = list[at - 1];
  else if (key === "ArrowDown") target = list[at + 1];
  else {
    const cols = [...main.querySelectorAll(".col")];
    const step = key === "ArrowLeft" ? -1 : 1;
    for (let i = cols.indexOf(col) + step; i >= 0 && i < cols.length && !target; i += step) {
      const other = siblings(cols[i]);
      target = other[Math.min(at, other.length - 1)];
    }
  }
  target?.focus();
}

function bindKeyboard() {
  const search = $("#search");
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {  // anywhere, even in a box
      e.preventDefault();
      closeMenu();
      openPalette();
      return;
    }
    const menu = openMenu();
    if (e.key === "Escape" && menu) {
      closeMenu();
      menu.querySelector("summary").focus();
      return;
    }
    if ($("#sheet").open || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.("input, textarea, select")) return;
    if (e.key === "?") {
      e.preventDefault();
      openHelp();
    } else if (e.key === "/" && state.board) {
      e.preventDefault();
      search.focus();
      search.select();
    } else if (e.key === "v" && state.board) {
      e.preventDefault();
      $("#views").open = true;
      $("#view-items button")?.focus();
    } else if (e.key === "s" && state.board) {
      e.preventDefault();
      $("#sort-by").focus();
    } else if (e.key === "n" && state.board) {
      e.preventDefault();
      openCardForm(null, { column: cardBy(e.target.closest?.(".card")?.dataset.key)?.column });
    } else if (e.key.startsWith("Arrow") && state.board) {
      const el = e.target.closest?.(".card");
      if (!el && e.target !== document.body) return;  // arrows keep their usual meaning on buttons and lists
      e.preventDefault();
      if (el && e.shiftKey) {
        const key = el.dataset.key;
        state.focusKey = key;
        state.wantFocus = true;
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          const delta = e.key === "ArrowUp" ? -1 : 1;
          if (shiftCard(key, delta)) announce(`${key} moved ${delta < 0 ? "up" : "down"}.`);
        } else {
          shiftColumn(key, e.key === "ArrowLeft" ? -1 : 1);
        }
      } else {
        focusNeighbour(el, e.key);
      }
    }
  });
  $("#board").addEventListener("focusin", (e) => {
    const el = e.target.closest?.(".card");
    if (!el) return;
    $('#board .card[tabindex="0"]')?.setAttribute("tabindex", "-1");
    el.tabIndex = 0;
    state.focusKey = el.dataset.key;
  });
}

function bindPage() {
  $("#new-card").addEventListener("click", () => openCardForm(null));
  $("#board-settings").addEventListener("click", openSettings);
  $("#new-board").addEventListener("click", () => openNewBoard());
  $("#board-pick").addEventListener("change", (e) => { if (e.target.value) location.hash = e.target.value; });
  bindImport();
  bindSearch();
  bindSort();
  bindMenu();
  window.addEventListener("hashchange", route);
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, POLL_EVERY);
  window.addEventListener("beforeunload", (e) => {
    if (sheet.dirty || pending || drafts.size || sheet.unsaved?.()) e.preventDefault();
  });
}

async function start() {
  bindPage();
  bindKeyboard();
  bindSheet();
  bindDrag();
  $("#version").textContent = `Laneboard ${APP_VERSION}`;
  setBannerAction({ label: "Backups", run: openBackups });
  checkVersion();
  renderMenu();
  try {
    await loadBoards();
  } catch (e) {
    if (e.status !== 503 && e.status !== 0) toast(e.message, { error: true });
    return;  // the banner says what's wrong, and the next round tries again
  }
  await route();
}

start();
