// Whole boards: the Board settings form, making a new board, duplicating one, and the example boards to start from.

import { COLORS, activeCards, setBoard, sheet, state } from "./state.js";
import { countBy, pairsText, parsePairs, plural } from "./util.js";
import { $, closeSheet, confirmBox, field, h, sheetHead, showSheet, tabs, toast } from "./dom.js";
import { api, loadBoards, send } from "./api.js";
import { goToBoard, route } from "./nav.js";
import { chooseImportFile } from "./transfer.js";
import { faceTab, labelsTab, propertiesTab } from "./boardparts.js";

const POLICIES = [["warn", "Flag the column, and still take cards"], ["refuse", "Take no more cards until one leaves"]];

function upper(input) {
  input.autocapitalize = "characters";
  input.addEventListener("input", () => {
    const at = input.selectionStart;
    input.value = input.value.toUpperCase();
    input.setSelectionRange(at, at);
  });
  return input;
}

function autoPrefix(name) {
  const words = name.toUpperCase().replace(/[^A-Z0-9]+/g, " ").trim().split(" ").filter(Boolean);
  const raw = words.length > 1 ? words.map((w) => w[0]).join("") : (words[0] || "").slice(0, 3);
  return raw.replace(/^[0-9]+/, "").slice(0, 8) || (name.trim() ? "G" : "");
}

// Lines from the settings box back into checklist items. Lines that didn't change keep their ids,
// and with them the ticks on every card.
function checklistFrom(row) {
  const old = state.board.checklists.find((cl) => cl.id === row.dataset.id);
  const keyOf = (heading, text) => `${heading ? "#" : "-"}${text}`;
  const ids = new Map();
  for (const i of old?.items || []) ids.set(keyOf(i.heading, i.text), [...(ids.get(keyOf(i.heading, i.text)) || []), i.id]);
  const items = $(".k-items", row).value.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const heading = line.startsWith("#");
    const text = heading ? line.replace(/^#+\s*/, "") : line.replace(/^(?:[-*•]|\[[ xX]?\])\s+/, "");
    return { id: ids.get(keyOf(heading, text))?.shift(), heading, text };
  });
  return { id: row.dataset.id || undefined, name: $(".k-name", row).value, items };
}

export function openSettings() {
  const b = state.board;
  const active = activeCards();
  const colCards = countBy(active, (c) => c.column);
  const groupCards = countBy(active, (c) => c.group);
  const dirty = () => { sheet.dirty = true; };
  const name = h("input", { maxLength: 120, value: b.name });
  const desc = h("textarea", { rows: 2, maxLength: 1000, value: b.description });
  const prefix = upper(h("input", { maxLength: 8, value: b.prefix }));
  const groupLabel = h("input", { maxLength: 30, value: b.groupLabel });
  const template = h("textarea", { rows: Math.max(2, b.template.length + 1), value: b.template.join("\n"), placeholder: "Acceptance criteria" });

  const cols = h("div", { class: "list-rows" });
  const addCol = (c = { id: "", name: "", limit: null }) => {
    const n = colCards.get(c.id) || 0;
    const row = h("div", { class: "col-row", "data-id": c.id });
    const moveTo = h("select", { class: "c-move" });
    const removing = h("div", { class: "removing", hidden: true },
      h("span", { text: `Its ${plural(n, "card")} will move to:` }), moveTo,
      h("button", { type: "button", text: "Keep this column", onclick: () => {
        delete row.dataset.removing;
        removing.hidden = true;
        row.classList.remove("removed");
      } }));
    const remove = () => {
      dirty();
      if (!n) {
        row.remove();
        return;
      }
      const targets = [...cols.children].filter((r) => r !== row && r.dataset.id && !r.dataset.removing);
      if (!targets.length) {
        error.textContent = `A column has to stay for the cards in ${c.name} to move to.`;
        return;
      }
      moveTo.replaceChildren(...targets.map((r) => h("option", { value: r.dataset.id, text: $(".c-name", r).value })));
      row.dataset.removing = "1";
      row.classList.add("removed");
      removing.hidden = false;
    };
    row.append(
      field("Column", h("input", { class: "c-name", maxLength: 40, value: c.name })),
      field("Limit", h("input", { class: "c-limit", type: "number", min: 1, max: 999, step: 1, value: c.limit ?? "", placeholder: "None" })),
      h("div", { class: "mini" },
        h("button", { type: "button", class: "icon", title: "Move up", "aria-label": "Move up", text: "↑",
          onclick: () => { row.previousElementSibling?.before(row); dirty(); } }),
        h("button", { type: "button", class: "icon", title: "Move down", "aria-label": "Move down", text: "↓",
          onclick: () => { row.nextElementSibling?.after(row); dirty(); } }),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: remove })),
      removing);
    cols.append(row);
    return row;
  };
  b.columns.forEach((c) => addCol(c));

  const groups = h("div", { class: "list-rows" });
  const addGroup = (g = { id: "", name: "", prefix: "", color: COLORS[groups.children.length % COLORS.length], link: "", details: [] }) => {
    const n = groupCards.get(g.id) || 0;
    const gname = h("input", { class: "g-name", maxLength: 60, value: g.name });
    const gprefix = upper(h("input", { class: "g-prefix", maxLength: 8, value: g.prefix }));
    if (!g.id) gname.addEventListener("input", () => { if (!gprefix.dataset.typed) gprefix.value = autoPrefix(gname.value); });
    gprefix.addEventListener("input", () => { gprefix.dataset.typed = "1"; });
    const color = h("select", { class: "g-color" }, COLORS.map((c) => h("option", { value: c, text: c[0].toUpperCase() + c.slice(1) })));
    color.value = g.color;
    const row = h("div", { class: "list-row", "data-id": g.id },
      h("div", { class: "group-row-top" },
        field("Name", gname), field("Key prefix", gprefix), field("Color", color),
        h("div", { class: "mini" },
          h("button", { type: "button", class: "icon", title: "Move up", "aria-label": "Move up", text: "↑",
            onclick: () => { row.previousElementSibling?.before(row); dirty(); } }),
          h("button", { type: "button", class: "icon", title: "Move down", "aria-label": "Move down", text: "↓",
            onclick: () => { row.nextElementSibling?.after(row); dirty(); } }),
          h("button", { type: "button", class: "danger", text: "Remove", disabled: n > 0,
            title: n ? `${plural(n, "card")} still belong to it` : null, onclick: () => { row.remove(); dirty(); } }))),
      field("Link", h("input", { class: "g-link", type: "url", maxLength: 500, value: g.link, placeholder: "https://" })),
      field("Details", h("textarea", { class: "g-details", rows: Math.max(2, g.details.length + 1), value: pairsText(g.details) }),
        "One per line, as Label: value. They show in the table under the board."));
    groups.append(row);
    return row;
  };
  b.groups.forEach((g) => addGroup(g));

  const lists = h("div", { class: "list-rows" });
  const addList = (cl = { id: "", name: "", items: [] }) => {
    const row = h("div", { class: "list-row", "data-id": cl.id },
      h("div", { class: "line" },
        field("Checklist", h("input", { class: "k-name", maxLength: 60, value: cl.name, placeholder: "Before release" })),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: () => { row.remove(); dirty(); } })),
      h("textarea", { class: "k-items", "aria-label": "Checklist lines", rows: Math.min(16, Math.max(4, cl.items.length + 1)),
        value: cl.items.map((i) => (i.heading ? "# " : "") + i.text).join("\n") }));
    lists.append(row);
    return row;
  };
  b.checklists.forEach((cl) => addList(cl));

  const policy = h("fieldset", { class: "policy" }, h("legend", { class: "lbl", text: "When a column is at its limit" }),
    POLICIES.map(([value, text]) => h("label", {}, h("input", { type: "radio", name: "limit-policy", value, checked: b.settings.limitPolicy === value }), text)));
  const labels = labelsTab(b, dirty);
  const props = propertiesTab(b, dirty);
  const face = faceTab(b, props, dirty);

  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Save" });
  const actions = h("div", { class: "form-actions" }, submit,
    h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() }),
    h("span", { class: "spacer" }),
    h("button", {
      type: "button", class: "danger", text: "Delete board",
      onclick: () => confirmBox(actions, `Delete ${b.name} and its ${plural(b.cards.length, "card")}? A copy is kept in your backups, but it won't be on any board.`,
        "Delete board", async () => {
          await send("DELETE", "");
          sheet.dirty = false;
          closeSheet(true);
          await loadBoards();
          setBoard(null);
          toast(`Deleted ${b.name}.`);
          history.replaceState(null, "", "#");
          await route();
        }),
    }));

  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    let changes;
    try {
      const rows = [...cols.children];
      const staying = rows.filter((r) => !r.dataset.removing);
      changes = {
        name: name.value, description: desc.value, prefix: prefix.value.trim(), groupLabel: groupLabel.value,
        template: template.value.split("\n").map((l) => l.trim()).filter(Boolean),
        columns: staying.map((r) => {
          const limit = $(".c-limit", r).value;
          return { id: r.dataset.id || undefined, name: $(".c-name", r).value, limit: limit === "" ? null : Number(limit) };
        }),
        groups: [...groups.children].map((r) => ({
          id: r.dataset.id || undefined, name: $(".g-name", r).value, prefix: $(".g-prefix", r).value.trim(),
          color: $(".g-color", r).value, link: $(".g-link", r).value.trim(),
          details: parsePairs($(".g-details", r).value, `${$(".g-name", r).value || "Group"} details`),
        })),
        checklists: [...lists.children].map(checklistFrom).filter((cl) => cl.name.trim() || cl.items.length),
      };
      const leaving = rows.filter((r) => r.dataset.removing);
      if (leaving.length) changes.moveCards = Object.fromEntries(leaving.map((r) => [r.dataset.id, $(".c-move", r).value]));
    } catch (err) {
      error.textContent = err.message;
      return;
    }
    Object.assign(changes, labels.changes(), props.changes());
    const settings = {};
    const limitPolicy = $('input[name="limit-policy"]:checked', policy).value;
    if (limitPolicy !== b.settings.limitPolicy) settings.limitPolicy = limitPolicy;
    // A property added just now has no id yet, so a card face showing it goes once the property is saved.
    const shown = face.face();
    const later = shown.face.some((part) => typeof part !== "string");
    if (shown.changed && !later) settings.cardFace = shown.face;
    if (Object.keys(settings).length) changes.settings = settings;
    submit.disabled = true;
    try {
      const data = await send("PATCH", "", changes);
      if (shown.changed && later) {
        const idOf = (part) => (typeof part === "string" ? part
          : data.board.properties.find((p) => p.name.toLowerCase() === part.name.trim().toLowerCase())?.id);
        await send("PATCH", "", { settings: { cardFace: shown.face.map(idOf).filter(Boolean) } });
      }
      sheet.dirty = false;
      closeSheet(true);
      toast("Board settings saved.");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };

  const general = h("div", { class: "form" },
    field("Board name", name),
    field("Description", desc, "Shows under the board name."),
    h("div", { class: "row2" },
      field("Key prefix", prefix, "For cards with no group, as in T-12."),
      field("Group label", groupLabel, "What a group is on this board: Repo, Client, Project.")),
    field("New card sections", template, "One name per line. Every new card starts with these sections; any left empty aren't saved."),
    h("div", { class: "field" }, h("span", { class: "lbl", text: "Columns" }),
      h("span", { class: "hint", text: "Top to bottom here is left to right on the board. A limit is how many cards a column should hold." }),
      cols, h("div", {}, h("button", { type: "button", text: "Add column", onclick: () => { $(".c-name", addCol()).focus(); dirty(); } }))),
    policy,
    h("div", { class: "field" }, h("span", { class: "lbl", text: "Groups" }),
      h("span", { class: "hint", text: "Each group gets a color and its own card keys, like VM-1." }),
      groups, h("div", {}, h("button", { type: "button", text: "Add group", onclick: () => { $(".g-name", addGroup()).focus(); dirty(); } }))),
    h("div", { class: "field" }, h("span", { class: "lbl", text: "Checklists" }),
      h("span", { class: "hint", text: "One line per item. Start a line with # to make it a heading. Every card ticks its own copy, and a line you reword starts unticked." }),
      lists, h("div", {}, h("button", { type: "button", text: "Add checklist", onclick: () => { $(".k-name", addList()).focus(); dirty(); } }))));
  const parts = tabs([
    { id: "general", label: "General", panel: general },
    { id: "labels", label: "Labels", panel: labels.panel },
    { id: "properties", label: "Properties", panel: props.panel },
    { id: "face", label: "Card face", panel: face.panel },
  ], { onSelect: (id) => { if (id === "face") face.refresh(); } });
  const form = h("form", { class: "form", novalidate: true, oninput: dirty, onchange: dirty, onsubmit },
    general, labels.panel, props.panel, face.panel, error, actions);
  showSheet("settings", null, h("div", { class: "form" }, sheetHead("Board settings"), parts.bar, form));
}

export function openNewBoard() {
  const name = h("input", { maxLength: 120, autofocus: true, placeholder: "Client work" });
  const desc = h("textarea", { rows: 2, maxLength: 1000 });
  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Create board" });
  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    submit.disabled = true;
    try {
      const { board } = await api("POST", "/api/boards", { name: name.value, description: desc.value });
      await goToBoard(board.id);
      toast("Board created. Use Board settings to change its columns and add groups.");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };
  const form = h("form", { class: "form", novalidate: true, oninput: () => { sheet.dirty = true; }, onsubmit },
    field("Board name", name),
    field("Description", desc, "Optional. Shows under the board name."),
    h("p", { class: "hint", text: "New boards start with Backlog, Ready, In progress, Review and Done. You can change them later." }),
    error,
    h("div", { class: "form-actions" }, submit, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })),
    h("div", { class: "start-actions" }, h("button", { type: "button", text: "Import a file", onclick: chooseImportFile })),
    startingPoints());
  showSheet("new-board", null, h("div", { class: "form" }, sheetHead("New board"), form));
}

export async function duplicateBoard() {
  const b = state.board;
  try {
    const data = await api("POST", "/api/import", { ...b, name: `Copy of ${b.name}`.slice(0, 120) });
    await goToBoard(data.boards[0].id);
    toast(`Made a copy of ${b.name}.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ---- example boards ---------------------------------------------------------------------------

// Buttons for the example boards, filled in once the list arrives.
export function startingPoints() {
  const box = h("div", { class: "start-actions" });
  loadSamples().then((samples) => {
    if (!samples.length) return;
    box.append(h("span", { class: "hint", text: "Or look around an example:" }),
      ...samples.map((s) => h("button", { type: "button", text: s.title, title: s.description, onclick: () => addSample(s) })));
  });
  return box;
}

let samplesLoaded = null;
function loadSamples() {
  samplesLoaded = samplesLoaded || api("GET", "/api/samples").then((d) => d.samples).catch(() => { samplesLoaded = null; return []; });
  return samplesLoaded;
}

async function addSample(sample) {
  try {
    const { board } = await api("POST", `/api/samples/${sample.name}`);
    await goToBoard(board.id);
    toast(`Added the example ${board.name}. Delete it from Board settings when you're done with it.`);
  } catch (e) {
    toast(e.message, { error: true });
  }
}
