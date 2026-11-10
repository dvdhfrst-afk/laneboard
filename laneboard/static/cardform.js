// The form for a new card or for editing one, and deleting a card.

import { cardBy, cardsIn, sheet, state } from "./state.js";
import { pairsText, parsePairs } from "./util.js";
import { $, askDiscard, closeSheet, confirmBox, field, h, sheetHead, showSheet, toast } from "./dom.js";
import { send } from "./api.js";
import { openCard } from "./card.js";

function onlyFilteredGroup() {
  return state.groups.size === 1 ? [...state.groups][0] || null : null;
}

// The part of a card the edit form shows, for telling whether someone else has changed it.
const formPart = (card) => (card ? JSON.stringify([card.title, card.group, card.column, card.summary, card.blocked, card.fields, card.sections]) : "");

export function openCardForm(key, defaults = {}) {
  const b = state.board;
  const card = key ? cardBy(key) : null;
  let startedFrom = formPart(card);
  const v = card || { title: "", group: onlyFilteredGroup(), column: defaults.column || b.columns[0].id,
    summary: "", blocked: "", fields: [], sections: b.template.map((label) => ({ label, body: "" })) };
  const title = h("input", { maxLength: 200, value: v.title, autofocus: true });
  const group = h("select", {}, h("option", { value: "", text: "None" }), b.groups.map((g) => h("option", { value: g.id, text: g.name })));
  group.value = v.group || "";
  const column = h("select", {}, b.columns.map((c) => h("option", { value: c.id, text: c.name })));
  column.value = v.column;
  const summary = h("textarea", { rows: 3, maxLength: 1000, value: v.summary });
  const blocked = h("input", { maxLength: 500, value: v.blocked });
  const fields = h("textarea", { rows: Math.max(3, v.fields.length + 1), value: pairsText(v.fields), placeholder: "Size: 3 to 4 hours" });
  const rows = h("div", { class: "list-rows" });
  const addSection = (s = { label: "", body: "" }) => {
    const row = h("div", { class: "list-row" });
    row.append(
      h("div", { class: "line" },
        field("Section", h("input", { class: "s-label", maxLength: 60, value: s.label, placeholder: "Acceptance criteria" })),
        h("button", { type: "button", class: "danger", text: "Remove", onclick: () => { row.remove(); sheet.dirty = true; } })),
      h("textarea", { class: "s-body", "aria-label": "Section text", rows: Math.min(12, Math.max(3, s.body.split("\n").length + 1)), value: s.body }));
    rows.append(row);
    return row;
  };
  v.sections.forEach((s) => addSection(s));

  const error = h("div", { class: "form-error", role: "alert" });
  const submit = h("button", { type: "submit", class: "primary", text: card ? "Save" : "Create card" });
  const actions = h("div", { class: "form-actions" }, submit,
    h("button", {
      type: "button", text: "Cancel",
      onclick: () => {
        const back = card ? () => openCard(card.key) : () => closeSheet(true);
        sheet.dirty ? askDiscard(back) : back();
      },
    }),
    h("span", { class: "spacer" }),
    card && h("button", {
      type: "button", class: "danger", text: "Delete card",
      onclick: () => confirmBox(actions, `Delete ${card.key}? This can't be undone. Archive keeps a card out of the way without losing it.`, "Delete", async () => {
        await send("DELETE", `/cards/${card.key}`, undefined, { retry: (fresh) => formPart(fresh.cards.find((c) => c.key === key)) === startedFrom });
        sheet.dirty = false;
        closeSheet(true);
        toast(`Deleted ${card.key}.`);
      }),
    }));

  const onsubmit = async (e) => {
    e.preventDefault();
    error.textContent = "";
    const values = { title: title.value.trim(), group: group.value || null, column: column.value,
      summary: summary.value, blocked: blocked.value };
    try {
      values.fields = parsePairs(fields.value, "Fields");
    } catch (err) {
      error.textContent = err.message;
      fields.focus();
      return;
    }
    values.sections = [...rows.children]
      .map((r) => ({ label: $(".s-label", r).value.trim(), body: $(".s-body", r).value }))
      .filter((s) => s.body.trim());
    if (!values.title) {
      error.textContent = "The card needs a title.";
      title.focus();
      return;
    }
    if (values.sections.some((s) => !s.label)) {
      error.textContent = "Give every section a name, or remove the ones you don't need.";
      return;
    }
    if (card && values.column !== card.column) {
      const ranks = cardsIn(values.column).map((c) => c.rank);
      if (ranks.length) values.rank = ranks[ranks.length - 1] + 1;
    }
    submit.disabled = true;
    try {
      if (card) {
        // Another change to the board is no reason to stop, unless it changed this card too.
        await send("PATCH", `/cards/${card.key}`, values, {
          retry: (fresh) => {
            const now = fresh.cards.find((c) => c.key === card.key);
            if (formPart(now) === startedFrom) return true;
            startedFrom = formPart(now);
            return false;
          },
        });
        sheet.dirty = false;
        openCard(card.key);
      } else {
        const data = await send("POST", "/cards", values, { retry: true });
        sheet.dirty = false;
        openCard(data.key);
        toast(`Created ${data.key}.`);
      }
    } catch (err) {
      error.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  };

  const form = h("form", { class: "form", novalidate: true, oninput: () => { sheet.dirty = true; }, onsubmit },
    field("Title", title),
    h("div", { class: "row2" }, field(b.groupLabel, group), field("Column", column)),
    field("Summary", summary, "A sentence or two about what this card is for."),
    field("Blocked by", blocked, "Leave empty when nothing blocks it."),
    field("Fields", fields, "One per line, as Label: value. The first one shows on the card."),
    h("div", { class: "field" },
      h("span", { class: "lbl", text: "Sections" }),
      h("span", { class: "hint", text: "Wrap code in `backticks`. A section with several lines shows as a list." }),
      rows,
      h("div", {}, h("button", { type: "button", text: "Add section", onclick: () => { $(".s-label", addSection()).focus(); sheet.dirty = true; } }))),
    error, actions);
  showSheet(card ? "edit" : "new", key, h("div", { class: "form" },
    sheetHead(card ? "Edit card" : "New card", card?.key), form));
}
