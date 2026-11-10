// Labels on cards: the chips, the picker that finds a label by name or makes a new one, and the labels part of the
// card view (saved at once) and of the card form (saved with the form).

import { labelsOf, sheet, state } from "./state.js";
import { announce, chip, h } from "./dom.js";
import { send } from "./api.js";

export const labelChip = (label) => chip(label.name, label.color, "label");

export function labelChips(ids) {
  const labels = labelsOf(ids);
  return labels.length ? h("span", { class: "chips" }, labels.map(labelChip)) : null;
}

// Add a label to the board. The list sent is the server's, plus the new one, so labels changed in another
// tab stay as they are. Resolves to the new label.
async function createLabel(name) {
  const data = await send("PATCH", "", (board) => ({ labels: [...(board || state.board).labels, { name }] }), { retry: true });
  const folded = name.trim().toLowerCase();
  return data.board.labels.find((l) => l.name.toLowerCase() === folded);
}

// The picker: a search box over the board's labels, each a box to tick, and a button that makes a label with
// the name typed. Enter ticks or unticks the first label that matches, or makes the new one. Up and Down go
// through the list, Esc closes it. `picked(id)` says whether a label is on; `toggle(id, on)` changes it.
export function labelPicker({ picked, toggle, done }) {
  const search = h("input", { type: "search", class: "picker-search", maxLength: 40, autocomplete: "off",
    placeholder: "Find or make a label", "aria-label": "Find or make a label" });
  const list = h("div", { class: "picker-list", role: "group", "aria-label": "Labels" });
  const error = h("div", { class: "form-error", role: "alert" });
  const options = () => [...list.querySelectorAll("input, button")];
  const typed = () => search.value.trim();
  const draw = () => {
    const words = typed().toLowerCase();
    const labels = state.board.labels;
    const exact = labels.find((l) => l.name.toLowerCase() === words);
    const shown = words ? [exact, ...labels.filter((l) => l !== exact && l.name.toLowerCase().includes(words))].filter(Boolean) : labels;
    const on = list.contains(document.activeElement) ? document.activeElement : null;  // kept through the redraw
    list.replaceChildren(
      ...shown.map((l) => h("label", { class: "pick" },
        h("input", { type: "checkbox", checked: picked(l.id), "data-id": l.id, onchange: (e) => flip(l, e.target.checked) }),
        labelChip(l))),
      words && !exact ? h("button", { type: "button", class: "pick-new", text: `Make the label "${typed()}"`, onclick: () => make(typed()) }) : "",
      !labels.length && !words ? h("p", { class: "hint", text: "This board has no labels yet. Type a name to make one." }) : "");
    if (on) (on.dataset.id ? list.querySelector(`input[data-id="${CSS.escape(on.dataset.id)}"]`) : list.querySelector(".pick-new"))?.focus();
  };
  const flip = (label, on) => {
    error.textContent = "";
    toggle(label.id, on);
    announce(`${label.name} ${on ? "added" : "taken off"}.`);
  };
  const make = async (name) => {
    error.textContent = "";
    try {
      const label = await createLabel(name);
      search.value = "";
      if (label) flip(label, true);
      draw();
      search.focus();
    } catch (e) {
      error.textContent = e.message;
    }
  };
  search.addEventListener("input", (e) => {
    e.stopPropagation();  // finding a label isn't a change to the card
    draw();
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      options()[0]?.focus();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const first = list.querySelector("input");
      if (first) {
        first.click();
        search.value = "";
        draw();
      } else if (typed()) make(typed());
    } else if (e.key === "Escape") {
      e.preventDefault();  // close the picker, not the card
      e.stopPropagation();
      if (search.value) {
        search.value = "";
        draw();
      } else done();
    }
  });
  list.addEventListener("keydown", (e) => {
    const all = options();
    const at = all.indexOf(e.target);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const to = at + (e.key === "ArrowDown" ? 1 : -1);
      if (to < 0) search.focus();
      else all[Math.min(to, all.length - 1)]?.focus();
    } else if (e.key === "Enter" && e.target.type === "checkbox") {
      e.preventDefault();  // in the card form, Enter would otherwise send the form
      e.target.click();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      done();
    }
  });
  draw();
  const box = h("div", { class: "picker" }, search, list,
    h("p", { class: "hint", text: "Enter ticks or unticks the first match, or makes a new label. Up and Down go through the list. Esc closes it." }),
    error, h("div", {}, h("button", { type: "button", text: "Done", onclick: done })));
  return { box, focus: () => search.focus(), redraw: draw };
}

// The labels on a card with a button that opens the picker under them. Used by the card view and the card form.
function labelsPart({ id, picked, toggle, closed = () => {} }) {
  const button = h("button", { type: "button", id, "aria-expanded": "false" });
  const line = h("div", { class: "label-line" }, h("span"), button);
  const slot = h("div");
  let picker = null;
  const draw = () => {
    const ids = state.board.labels.filter((l) => picked(l.id)).map((l) => l.id);
    button.textContent = ids.length ? "Change labels" : "Add labels";
    line.firstChild.replaceWith(labelChips(ids) || h("span", { class: "hint", text: "No labels" }));  // the button stays, and keeps focus
  };
  const close = () => {
    picker?.box.remove();
    picker = null;
    button.setAttribute("aria-expanded", "false");
    button.focus();
    closed();
  };
  button.addEventListener("click", () => {
    if (picker) return close();
    picker = labelPicker({ picked, toggle: (lid, on) => { toggle(lid, on); draw(); }, done: close });
    slot.replaceChildren(picker.box);
    button.setAttribute("aria-expanded", "true");
    picker.focus();
  });
  draw();
  return { line, slot, draw, open: () => !!picker, redraw: () => { draw(); picker?.redraw(); } };
}

// In the card view: each tick saves straight away, as a change to the labels the server has, so labels
// changed in two tabs both stay.
export function labelsView(card) {
  const key = card.key;
  let on = new Set(card.labels);
  const error = h("div", { class: "form-error", role: "alert" });
  const save = (id, add) => send("PATCH", `/cards/${key}`, (board) => {
    const current = new Set(board?.cards.find((c) => c.key === key)?.labels ?? on);
    if (add) current.add(id);
    else current.delete(id);
    return { labels: [...current] };
  }, { retry: true }).then(() => { error.textContent = ""; }).catch((e) => {
    error.textContent = `Labels on ${key} weren't saved: ${e.message}`;
    sheet.sync?.();  // show what the server has
  });
  const part = labelsPart({
    id: "card-labels",
    picked: (id) => on.has(id),
    toggle: (id, add) => {
      if (add) on.add(id);
      else on.delete(id);
      save(id, add);
    },
    closed: () => sheet.sync?.(),  // whatever waited while the picker was open
  });
  return {
    node: h("div", { class: "sec labels-sec" }, h("h3", { text: "Labels" }), part.line, part.slot, error),
    busy: part.open,
    sync: (now) => {
      on = new Set(now.labels);
      part.redraw();
    },
  };
}

// In the card form: the ticks are kept with the form and sent when it is.
export function labelsField(start, changed) {
  const on = new Set(start);
  const part = labelsPart({
    id: "form-labels",
    picked: (id) => on.has(id),
    toggle: (id, add) => {
      if (add) on.add(id);
      else on.delete(id);
      changed();
    },
  });
  return {
    node: h("div", { class: "field" }, h("span", { class: "lbl", text: "Labels" }), part.line, part.slot),
    value: () => state.board.labels.filter((l) => on.has(l.id)).map((l) => l.id),
  };
}
