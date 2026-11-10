// Templates: the choice of a template in the New board form, saving the open board's setup as a template, and the
// Templates sheet, where saved templates are renamed, described and deleted. Built-in templates are listed there too,
// locked.

import { activeCards, sheet, state } from "./state.js";
import { plural } from "./util.js";
import { askDiscard, closeSheet, confirmBox, field, h, sheetHead, showSheet, toast } from "./dom.js";
import { api } from "./api.js";
import { openNewBoard } from "./settings.js";

const loadTemplates = async () => (await api("GET", "/api/templates")).templates;

// "5 columns, 4 starter cards".
const counts = (t) => `${plural(t.columns, "column")}, ${t.cards ? plural(t.cards, "starter card") : "no starter cards"}`;
const tag = (t) => h("span", { class: t.builtIn ? "tag" : "tag own", text: t.builtIn ? "Built in" : "Yours" });

// The "Start from" choice in the New board form: an empty board, then every template with what it holds.
// `picked(template)` hears each change, with null for the empty board. `chosen` is a template id to start on.
export function templateChoices(picked, chosen = null) {
  const box = h("fieldset", { class: "template-pick" }, h("legend", { class: "lbl", text: "Start from" }));
  const choice = (value, template, ...text) => h("label", { class: "template-choice" },
    h("input", { type: "radio", name: "template", value, checked: value === (chosen || ""), onchange: () => picked(template) }),
    h("span", { class: "template-text" }, ...text));
  box.append(choice("", null, h("span", { class: "template-name", text: "Empty board" }),
    h("span", { class: "hint", text: "Backlog, Ready, In progress, Review and Done, with nothing else set up." })));
  const note = h("p", { class: "hint", text: "Loading the templates…" });
  box.append(note);
  loadTemplates().then((list) => {
    note.remove();
    box.append(...list.map((t) => choice(t.id, t,
      h("span", { class: "template-name" }, t.name, " ", tag(t)),
      t.description && h("span", { class: "hint", text: t.description }),
      h("span", { class: "hint counts", text: counts(t) }))));
    const start = list.find((t) => t.id === chosen);
    if (start) picked(start);
  }).catch((e) => {
    note.textContent = `The templates couldn't be loaded: ${e.message}`;
  });
  return box;
}

// Keep the open board's setup as a template of its own, with its cards or without.
export function openSaveTemplate() {
  const b = state.board;
  const n = activeCards().length;
  const name = h("input", { maxLength: 120, value: b.name, autofocus: true });
  const desc = h("textarea", { rows: 2, maxLength: 1000, value: b.description });
  const cards = h("input", { type: "checkbox", disabled: !n });
  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: "Save template" });
  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    submit.disabled = true;
    try {
      const { template } = await api("POST", "/api/templates", { board: b.id, name: name.value, description: desc.value, cards: cards.checked });
      sheet.dirty = false;
      closeSheet(true);
      toast(`Saved the template ${template.name}. New board offers it now.`, { action: { label: "Templates", run: openTemplates } });
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };
  const form = h("form", { class: "form", novalidate: true, oninput: () => { sheet.dirty = true; }, onsubmit },
    h("p", { class: "hint", text: "A template keeps this board's columns, groups, labels, properties, saved views, checklists, new card sections and settings. Boards made from it start with all of that." }),
    field("Template name", name),
    field("Description", desc, "Shows next to the template when you make a new board."),
    h("label", { class: "check-line" }, cards, n ? `Keep its ${plural(n, "card")} as starter cards` : "This board has no cards to keep"),
    n > 0 && h("p", { class: "hint", text: "Starter cards keep their column, group, labels, properties, tasks and ticks. Their notes are left out, and so are archived cards." }),
    error,
    h("div", { class: "form-actions" }, submit, h("button", { type: "button", text: "Cancel", onclick: () => closeSheet() })));
  showSheet("save-template", null, h("div", { class: "form" }, sheetHead("Save as template"), form));
}

// Every template: the built-in ones locked, the saved ones to rename, describe or delete. Each saved one saves on
// its own, so one change doesn't wait on another.
export function openTemplates() {
  const list = h("div", { class: "list-rows" });
  const status = h("p", { class: "hint", text: "Loading the templates…" });
  const edited = new Set();
  const mark = (row, changed) => {
    if (changed) edited.add(row);
    else edited.delete(row);
    sheet.dirty = edited.size > 0;
  };
  const fromThis = (t) => h("button", { type: "button", text: "New board from this",
    onclick: () => (sheet.dirty ? askDiscard(() => openNewBoard(t.id)) : openNewBoard(t.id)) });

  const locked = (t) => h("div", { class: "list-row template-row" },
    h("div", { class: "template-head" }, h("h3", { class: "template-name" }, t.name, " ", tag(t)), h("span", { class: "hint counts", text: counts(t) })),
    t.description && h("p", { class: "template-about", text: t.description }),
    h("div", { class: "mini" }, fromThis(t), h("span", { class: "hint", text: "Built-in templates can't be changed." })));

  const own = (t) => {
    let saved = { name: t.name, description: t.description };
    const name = h("input", { maxLength: 120, value: t.name });
    const desc = h("textarea", { rows: 2, maxLength: 1000, value: t.description });
    const said = h("div", { class: "form-error", role: "alert" });
    const save = h("button", { type: "button", class: "primary", text: "Save changes", disabled: true });
    const row = h("div", { class: "list-row template-row", "data-id": t.id });
    const changed = () => name.value !== saved.name || desc.value !== saved.description;
    const typed = () => {
      save.disabled = !changed();
      mark(row, changed());
    };
    name.addEventListener("input", typed);
    desc.addEventListener("input", typed);
    save.addEventListener("click", async () => {
      said.textContent = "";
      save.disabled = true;
      try {
        const { template } = await api("PATCH", `/api/templates/${encodeURIComponent(t.id)}`, { name: name.value, description: desc.value });
        saved = { name: template.name, description: template.description };
        name.value = saved.name;
        desc.value = saved.description;
        toast(`Saved the template ${template.name}.`);
      } catch (e) {
        said.textContent = e.message;
      }
      typed();
    });
    const tools = h("div", { class: "mini" });
    const remove = h("button", { type: "button", class: "danger", text: "Delete",
      onclick: () => confirmBox(tools, `Delete the template ${saved.name}? Boards made from it stay as they are, and a copy is kept in your backups.`,
        "Delete template", async () => {
          await api("DELETE", `/api/templates/${encodeURIComponent(t.id)}`);
          mark(row, false);
          row.remove();
          toast(`Deleted the template ${saved.name}.`);
        }) });
    row.append(
      h("div", { class: "template-head" }, h("span", {}, tag(t)), h("span", { class: "hint counts", text: counts(t) })),
      field("Name", name), field("Description", desc), said,
      tools);
    tools.append(save, fromThis(t), h("span", { class: "spacer" }), remove);
    return row;
  };

  showSheet("templates", null, h("div", { class: "form" }, sheetHead("Templates"),
    h("p", { class: "hint", text: "New board starts from any of these. Save a board's setup as a template of your own with Save as template in the More menu." }),
    status, list));
  loadTemplates().then((templates) => {
    status.remove();
    const mine = templates.filter((t) => !t.builtIn);
    list.replaceChildren(
      h("h3", { class: "list-title", text: "Yours" }),
      ...(mine.length ? mine.map(own) : [h("p", { class: "hint", text: "No saved templates yet." })]),
      h("h3", { class: "list-title", text: "Built in" }),
      ...templates.filter((t) => t.builtIn).map(locked));
  }).catch((e) => {
    status.textContent = `The templates couldn't be loaded: ${e.message}`;
  });
}
