// The command palette (Ctrl+K or Cmd+K): one box that finds an action, a board or a card on the open board, and runs
// or opens it. It is an ARIA combobox: the text box keeps focus, Up and Down move through the list, Enter runs the
// highlighted entry, Esc closes. The actions are the page's own: the header buttons, every item in More (read from
// the menu itself, so a new item shows up here by itself), and what the keyboard shortcuts do.

import { activeCards, cardBy, sheet, state } from "./state.js";
import { $, askDiscard, closeSheet, h } from "./dom.js";
import { fold } from "./search.js";
import { openCardForm } from "./cardform.js";
import { openNewBoard, openSettings } from "./settings.js";
import { openEverywhere } from "./everywhere.js";
import { openHelp } from "./help.js";
import { openCard } from "./card.js";
import { openBackups } from "./backups.js";
import { openOverview } from "./boards.js";
import { renderMenu } from "./menu.js";

const LISTED = 50;  // entries shown at once; typing narrows them
let dialog = null;

function actions() {
  const b = state.board;
  const list = [
    b && { label: "New card", hint: "n", run: () => openCardForm(null) },
    b && { label: "Search this board", hint: "/", run: () => { $("#search").focus(); $("#search").select(); } },
    b && { label: "Sort", hint: "s", run: () => $("#sort-by").focus() },
    b && { label: "Saved views", hint: "v", run: () => { $("#views").open = true; $("#view-items button")?.focus(); } },
    b && { label: "Board settings", run: openSettings },
    { label: "New board", run: () => openNewBoard() },
    { label: "All boards", hint: "The boards overview", run: openOverview },
    state.boards.length > 1 && { label: "Search every board", run: openEverywhere },
    { label: "Backups", run: openBackups },
    { label: "Keyboard shortcuts and help", hint: "?", run: openHelp },
  ].filter(Boolean);
  renderMenu();  // what More holds right now, for the open board
  const taken = new Set(list.map((a) => fold(a.label)));
  for (const item of $("#menu-items").querySelectorAll("button, a")) {
    const label = item.textContent.trim();
    if (!label || item.disabled || taken.has(fold(label))) continue;
    taken.add(fold(label));
    list.push({ label, hint: "In More", run: () => item.click() });
  }
  return list.map((a) => ({ ...a, kind: "Action" }));
}

function boards() {
  return state.boards.filter((b) => b.id !== state.board?.id).map((b) => ({
    kind: b.archivedAt ? "Archived board" : "Board", label: b.name, hint: `${b.cards} cards`,
    run: () => { location.hash = b.id; },
  }));
}

function cards() {
  if (!state.board) return [];
  const columns = new Map(state.board.columns.map((c) => [c.id, c.name]));
  return activeCards().map((c) => ({ kind: "Card", key: c.key, label: c.title, hint: columns.get(c.column) || "",
    run: () => { if (cardBy(c.key)) openCard(c.key); } }));
}

// Every word typed must be in the entry's text; a card key typed whole, or the start of one, comes first.
function matches(entries, text) {
  const words = fold(text).split(/\s+/).filter(Boolean);
  if (!words.length) return entries.filter((e) => e.kind !== "Card");
  const keyed = [];
  const rest = [];
  for (const e of entries) {
    const hay = fold(`${e.key || ""} ${e.label} ${e.kind}`);
    if (!words.every((w) => hay.includes(w))) continue;
    (e.key && fold(e.key).startsWith(words[0]) ? keyed : rest).push(e);
  }
  return [...keyed, ...rest];
}

export function openPalette() {
  if (dialog?.open) {
    dialog.querySelector("input").select();
    return;
  }
  const entries = [...actions(), ...boards(), ...cards()];
  const input = h("input", {
    type: "text", id: "palette-input", role: "combobox", autocomplete: "off", spellcheck: false, maxLength: 200,
    "aria-autocomplete": "list", "aria-expanded": "true", "aria-controls": "palette-list",
    "aria-label": "Find an action, a board or a card", placeholder: "Find an action, a board or a card",
  });
  const list = h("ul", { id: "palette-list", role: "listbox", "aria-label": "Matches" });
  const status = h("p", { class: "sr", "aria-live": "polite" });
  let shown = [];
  let active = 0;

  const highlight = (i) => {
    if (!shown.length) {
      input.removeAttribute("aria-activedescendant");
      return;
    }
    active = (i + shown.length) % shown.length;
    list.querySelectorAll("[role=option]").forEach((el, n) => el.setAttribute("aria-selected", String(n === active)));
    const el = list.children[active];
    input.setAttribute("aria-activedescendant", el.id);
    el.scrollIntoView({ block: "nearest" });
  };
  const draw = () => {
    const found = matches(entries, input.value);
    shown = found.slice(0, LISTED);
    list.replaceChildren(...shown.map((e, i) => h("li", {
      id: `palette-${i}`, role: "option", class: "palette-option", "aria-selected": "false",
      onclick: () => choose(i), onmousemove: () => { if (active !== i) highlight(i); },
    }, h("span", { class: "palette-kind", text: e.kind }), e.key && h("span", { class: "key", text: e.key }),
    h("span", { class: "palette-label", text: e.label }), e.hint && h("span", { class: "palette-hint", text: e.hint }))));
    if (!shown.length) list.append(h("li", { class: "palette-none", text: "Nothing matches.", role: "presentation" }));
    status.textContent = found.length ? `${found.length} ${found.length === 1 ? "match" : "matches"}` : "Nothing matches.";
    highlight(0);
  };
  const close = () => { if (dialog.open) dialog.close(); };
  const choose = (i) => {
    const entry = shown[i];
    if (!entry) return;
    close();
    const go = () => {
      if (sheet.mode && entry.kind !== "Card") closeSheet(true);
      entry.run();
    };
    if (sheet.dirty) askDiscard(() => { sheet.dirty = false; closeSheet(true); go(); });
    else go();
  };

  input.addEventListener("input", draw);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      highlight(active + (e.key === "ArrowDown" ? 1 : -1));
    } else if (e.key === "PageDown" || e.key === "PageUp") {
      e.preventDefault();
      highlight(Math.max(0, Math.min(shown.length - 1, active + (e.key === "PageDown" ? 8 : -8))));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(active);
    }
  });

  dialog?.remove();
  dialog = h("dialog", { class: "palette", id: "palette", "aria-label": "Command palette" },
    h("div", { class: "palette-inner" }, input, list, status,
      h("p", { class: "palette-help hint", text: "Up and Down to choose, Enter to run, Esc to close." })));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); });  // a click outside the box
  dialog.addEventListener("close", () => {
    const gone = dialog;
    setTimeout(() => { if (!gone.open) gone.remove(); });
  });
  document.body.append(dialog);
  dialog.showModal();
  draw();
  input.focus();
}
