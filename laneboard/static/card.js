// One card in the side sheet: what it holds, its notes (saved as you type, kept as drafts when they can't be), its
// checklist ticks, archiving and duplicating.

import { cardBy, cardsIn, drafts, groupOf, index, latest, setHash, sheet, state } from "./state.js";
import { when } from "./util.js";
import { $, announce, closeSheet, copyText, groupLink, h, rich, sheetHead, showSheet, toast } from "./dom.js";
import { send } from "./api.js";
import { setColumn, shiftCard } from "./move.js";
import { openCardForm } from "./cardform.js";
import { openArchive } from "./archive.js";

const NOTES_DELAY = 900;

export async function saveDrafts() {
  for (const [ref, value] of [...drafts]) {
    const [id, key] = ref.split("/");
    if (state.board?.id !== id || !cardBy(key)) continue;
    drafts.delete(ref);
    const base = cardBy(key).notes;
    try {
      await send("PATCH", `/cards/${key}`, { notes: value }, { retry: (fresh) => fresh.cards.find((c) => c.key === key)?.notes === base });
      toast(`Saved your notes on ${key}.`);
    } catch (e) {
      keepDraft(id, key, value, e.message);
    }
  }
}

function keepDraft(id, key, value, why) {
  const onBoard = state.board?.id === id && cardBy(key);
  if (onBoard) drafts.set(`${id}/${key}`, value);
  toast(`Your notes on ${key} weren't saved: ${why}`, {
    error: true,
    action: onBoard ? { label: "Open them", run: () => openCard(key) } : { label: "Copy them", run: () => copyText(value) },
  });
}

// The parts of a card view that can only be redrawn whole. Column, ticks and notes update in place.
function viewPart(card) {
  const b = state.board;
  return JSON.stringify([card.title, card.group, card.summary, card.blocked, card.fields, card.sections, card.archived,
    b.columns, b.checklists, b.groups, b.groupLabel]);
}

export function openCard(key, { keepPlace = false } = {}) {
  const card = cardBy(key);
  if (!card) {
    toast(`There's no card ${key} on this board.`, { error: true });
    setHash(state.board.id);
    return;
  }
  const dlg = $("#sheet");
  const place = keepPlace ? { top: dlg.scrollTop, focusNotes: document.activeElement?.id === "card-notes",
    start: $("#card-notes")?.selectionStart, end: $("#card-notes")?.selectionEnd } : null;
  setHash(state.board.id, key);
  const boardId = state.board.id;
  const draftRef = `${boardId}/${key}`;
  const notes = h("textarea", { id: "card-notes", rows: 6, value: drafts.get(draftRef) ?? card.notes, placeholder: "Working notes. They save as you type." });
  drafts.delete(draftRef);
  const status = h("div", { class: "savestate", "aria-live": "polite" });
  let timer = null;
  let saved = card.notes;     // the text as last saved, to tell whether there's anything new to save
  let baseline = card.notes;  // what the card held then, to tell whether someone else has changed it since
  const differs = () => notes.value.trimEnd() !== saved.trimEnd();
  const busy = () => timer !== null || differs();
  const otherNotes = () => (latest?.id === boardId ? latest.cards.find((c) => c.key === key)?.notes : null) ?? "";
  const otherChanged = () => otherNotes() !== baseline;
  const showConflict = () => {
    if (status.querySelector("button")) return;  // clicking one of them blurs the box, which lands here again
    status.replaceChildren("These notes also changed somewhere else. Your text is still here. ",
      h("button", { type: "button", text: "Keep my text", onclick: () => { baseline = otherNotes(); status.textContent = ""; save(); } }),
      " ",
      h("button", { type: "button", text: "Use the other text", onclick: () => { saved = baseline = otherNotes(); notes.value = saved; status.textContent = ""; } }));
  };
  const save = () => {
    clearTimeout(timer);
    timer = null;
    if (!differs()) return;
    if (otherChanged()) {
      showConflict();
      return;
    }
    const value = notes.value;
    const base = baseline;
    status.textContent = "Saving…";
    send("PATCH", `/cards/${key}`, { notes: value }, { retry: (fresh) => fresh.cards.find((c) => c.key === key)?.notes === base })
      .then((data) => {
        saved = value.trimEnd();
        baseline = data.board?.cards.find((c) => c.key === key)?.notes ?? baseline;
        if (status.isConnected && !timer && !differs()) status.textContent = "Saved";
      })
      .catch((e) => {
        if (!status.isConnected) keepDraft(boardId, key, value, e.message);
        else if (e.status === 412) showConflict();
        else status.textContent = `Not saved: ${e.message} Your text is still here.`;
      });
  };
  // Closing has nowhere to ask about a conflict, so the text is kept for the person to come back to.
  const leave = () => {
    clearTimeout(timer);
    timer = null;
    if (differs() && otherChanged()) keepDraft(boardId, key, notes.value, "they also changed somewhere else.");
    else save();
  };
  notes.addEventListener("input", () => {
    status.textContent = "";
    clearTimeout(timer);
    timer = setTimeout(save, NOTES_DELAY);
  });
  notes.addEventListener("blur", save);
  const ticks = checklistsView(card);
  const shown = viewPart(card);
  const sync = () => {
    const now = cardBy(key);
    if (!now) {
      closeSheet(true);
      if (!$("#toast").classList.contains("error") || $("#toast").hidden) toast("That card was deleted somewhere else.");
    } else if (viewPart(now) !== shown) {
      if (!busy()) openCard(key, { keepPlace: true });
    } else {
      $("#card-column").value = now.column;
      ticks.sync(now);
      if (!busy() && now.notes !== baseline) {
        saved = baseline = now.notes;
        notes.value = saved;
        status.textContent = "";
      }
    }
  };
  showSheet("view", key, cardView(card, notes, status, ticks.nodes), { onClose: leave, unsaved: busy, retry: save, sync });
  if (place) {
    dlg.scrollTop = place.top;
    if (place.focusNotes) {
      notes.focus({ preventScroll: true });
      notes.setSelectionRange(place.start, place.end);
    }
  }
  if (differs()) status.textContent = "These are your notes that weren't saved. They replace what's on the card when you leave this box.";
}

function cardView(card, notes, status, checklists) {
  const b = state.board;
  const g = groupOf(card);
  const column = h("select", { id: "card-column", onchange: (e) => setColumn(card.key, e.target.value) },
    b.columns.map((c) => h("option", { value: c.id, text: c.name })));
  column.value = card.column;
  const fact = (label, ...value) => h("div", { class: "fact" }, h("span", { class: "lbl", text: label }), h("span", { class: "val" }, value));
  const section = (label, text, list) => {
    const lines = text.split("\n").filter((l) => l.trim());
    return h("div", { class: "sec" }, h("h3", { text: label }), list && lines.length > 1
      ? h("ul", {}, lines.map((l) => h("li", {}, rich(l.trim().replace(/^[-*•]\s+/, "")))))
      : h("p", {}, rich(text)));
  };
  const nudge = (label, delta) => h("button", { type: "button", class: "icon", "aria-label": label, title: label, text: delta < 0 ? "↑" : "↓",
    disabled: !!card.archived, onclick: () => { if (shiftCard(card.key, delta)) announce(`${card.key} moved ${delta < 0 ? "up" : "down"}.`); } });
  const frag = document.createDocumentFragment();
  frag.append(...[
    sheetHead(card.title, card.key,
      h("button", { type: "button", text: "Edit", onclick: () => openCardForm(card.key) }),
      h("button", { type: "button", text: "Duplicate", onclick: () => duplicateCard(card.key) }),
      h("button", { type: "button", text: card.archived ? "Restore" : "Archive", onclick: () => setArchived(card.key, !card.archived) })),
    card.archived && h("div", { class: "archived-box" }, `Archived ${when(card.archived)}. It stays off the board until you restore it.`),
    h("div", { class: "facts" },
      h("div", { class: "fact" }, h("label", { class: "lbl", for: "card-column", text: "Column" }),
        h("div", { class: "position" }, column, nudge("Move up", -1), nudge("Move down", 1))),
      g && fact(b.groupLabel, groupLink(g)),
      card.fields.map((f) => fact(f.label, rich(f.value)))),
    card.blocked && h("div", { class: "blocked-box" }, h("strong", { text: "Blocked: " }), rich(card.blocked)),
    card.summary && section("Summary", card.summary, false),
    card.sections.map((s) => section(s.label, s.body, true)),
    !card.summary && !card.sections.length && h("p", { class: "hint", text: "No details yet. Edit the card to add a summary or sections." }),
    checklists,
    h("div", { class: "sec notes" }, h("label", { class: "lbl", for: "card-notes", text: "Notes" }), notes, status),
    h("p", { class: "hint", text: `Created ${when(card.created)}. Updated ${when(card.updated)}.` }),
  ].flat().filter(Boolean));
  return frag;
}

export async function setArchived(key, archived) {
  try {
    await send("PATCH", `/cards/${key}`, { archived }, { retry: true });
    sheet.dirty = false;
    if (archived) {
      closeSheet(true);
      toast(`Archived ${key}.`, { action: { label: "Restore", run: () => setArchived(key, false) } });
    } else {
      if (sheet.mode === "view" && sheet.key === key) openCard(key);
      toast(`Restored ${key}. It's at the bottom of its column.`);
      if (sheet.mode === "archive") openArchive();
    }
  } catch (e) {
    toast(e.message, { error: true });
  }
}

async function duplicateCard(key) {
  const card = cardBy(key);
  if (!card) return;
  const after = cardsIn(card.column);
  const next = after[after.indexOf(card) + 1];
  const values = { title: `Copy of ${card.title}`.slice(0, 200), group: card.group, column: card.column, summary: card.summary,
    blocked: card.blocked, fields: card.fields, sections: card.sections,
    rank: next ? (card.rank + next.rank) / 2 : card.rank + 1 };
  try {
    const data = await send("POST", "/cards", values, { retry: true });
    openCard(data.key);
    toast(`Created ${data.key} from ${key}. Its notes and ticks start empty.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ---- checklists --------------------------------------------------------------------------------

const tickable = (cl) => cl.items.filter((i) => !i.heading);

export function progress(card) {
  let done = 0;
  for (const id of card.checks) if (index.checkIds.has(id)) done++;
  return { done, total: index.checkIds.size };
}

// The board's checklists, ticked separately on every card. Each tick saves straight away, and a tick
// is sent as a change to what the server has, so ticks made in two tabs both stay.
function checklistsView(card) {
  let ticked = new Set(card.checks);
  const boxes = new Map();
  const recounts = [];
  const save = (id, on) => send("PATCH", `/cards/${card.key}`, (board) => {
    const current = new Set(board?.cards.find((c) => c.key === card.key)?.checks ?? ticked);
    if (on) current.add(id);
    else current.delete(id);
    return { checks: [...current] };
  }, { retry: true }).catch((e) => {
    toast(`Ticks on ${card.key} weren't saved: ${e.message}`, { error: true });
    sheet.sync?.();  // show what the server has
  });
  const nodes = state.board.checklists.filter((cl) => tickable(cl).length).map((cl) => {
    const tally = h("span", { class: "count" });
    const recount = () => { tally.textContent = `${tickable(cl).filter((i) => ticked.has(i.id)).length}/${tickable(cl).length}`; };
    recounts.push(recount);
    const blocks = [];
    let list = null;
    for (const item of cl.items) {
      if (item.heading) {
        blocks.push(h("h4", {}, rich(item.text)));
        list = null;
        continue;
      }
      if (!list) blocks.push(list = h("ul", { class: "checks" }));
      const box = h("input", {
        type: "checkbox", checked: ticked.has(item.id),
        onchange: () => {
          if (box.checked) ticked.add(item.id);
          else ticked.delete(item.id);
          recount();
          save(item.id, box.checked);
        },
      });
      boxes.set(item.id, box);
      list.append(h("li", {}, h("label", {}, box, h("span", {}, rich(item.text)))));
    }
    recount();
    return h("div", { class: "sec checklist" }, h("h3", {}, cl.name, " ", tally), blocks);
  });
  const sync = (now) => {
    ticked = new Set(now.checks);
    for (const [id, box] of boxes) box.checked = ticked.has(id);
    recounts.forEach((recount) => recount());
  };
  return { nodes, sync };
}
