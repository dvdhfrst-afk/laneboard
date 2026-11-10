// The Labels, Properties and Card face tabs of Board settings. Each gives the panel to show and the part of the
// board change it adds when the settings are saved; nothing is sent from here on its own.

import { COLORS } from "./state.js";
import { plural } from "./util.js";
import { $, chip, confirmBox, field, h } from "./dom.js";
import { TYPE_TITLES } from "./properties.js";

const RATING_TOPS = [3, 4, 5, 6, 7, 8, 9, 10];
const DECIMALS = [0, 1, 2, 3, 4];
const NUMBER = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function colorPick(value, cls) {
  const pick = h("select", { class: cls }, COLORS.map((c) => h("option", { value: c, text: c[0].toUpperCase() + c.slice(1) })));
  pick.value = value;
  return pick;
}

function nudges(row, changed, what) {
  return [
    h("button", { type: "button", class: "icon", title: "Move up", "aria-label": `Move ${what} up`, text: "↑",
      onclick: () => { const prev = sibling(row, -1); if (prev) { prev.before(row); changed(); } } }),
    h("button", { type: "button", class: "icon", title: "Move down", "aria-label": `Move ${what} down`, text: "↓",
      onclick: () => { const next = sibling(row, 1); if (next) { next.after(row); changed(); } } }),
  ];
}

// The next row of the same kind, skipping a confirm box between them.
function sibling(row, dir) {
  let n = dir < 0 ? row.previousElementSibling : row.nextElementSibling;
  while (n && n.className !== row.className) n = dir < 0 ? n.previousElementSibling : n.nextElementSibling;
  return n;
}

const rowsOf = (box, cls) => [...box.children].filter((r) => r.classList.contains(cls));

// A number typed in a settings box: empty is null, and anything that isn't a number goes as typed so the
// server's message says what it wants.
function typedNumber(text) {
  const t = text.trim();
  if (!t) return null;
  return NUMBER.test(t) ? Number(t) : t;
}

// Remove a row, asking first when cards would lose something.
function removeRow(row, n, message, changed) {
  if (!n) {
    row.remove();
    changed();
    return;
  }
  confirmBox(row, message, "Remove", () => {
    row.remove();
    changed();
  });
}

// ---- labels ------------------------------------------------------------------------------------

export function labelsTab(board, changed) {
  const counts = new Map();
  for (const card of board.cards) for (const id of card.labels) counts.set(id, (counts.get(id) || 0) + 1);
  const rows = h("div", { class: "list-rows" });
  const add = (label = { id: "", name: "", color: COLORS[rowsOf(rows, "label-row").length % COLORS.length] }) => {
    const n = counts.get(label.id) || 0;
    const name = h("input", { class: "l-name", maxLength: 40, value: label.name, placeholder: "Bug" });
    const color = colorPick(label.color, "l-color");
    const preview = h("span", { class: "l-preview", "aria-hidden": "true" });
    const paint = () => preview.replaceChildren(chip(name.value.trim() || "Label", color.value, "label"));
    name.addEventListener("input", paint);
    color.addEventListener("change", paint);
    paint();
    const row = h("div", { class: "label-row", "data-id": label.id });
    row.append(field("Label", name), field("Color", color), preview,
      h("div", { class: "mini" }, nudges(row, changed, "label"), h("button", {
        type: "button", class: "danger", text: "Remove", title: n ? `On ${plural(n, "card")}` : null,
        onclick: () => removeRow(row, n, `${label.name} is on ${plural(n, "card")}. Removing it takes it off ${n === 1 ? "that card" : "them"} when you save.`, changed),
      })));
    rows.append(row);
    return row;
  };
  board.labels.forEach((label) => add(label));
  const value = () => rowsOf(rows, "label-row").map((r) => ({ id: r.dataset.id || undefined, name: $(".l-name", r).value, color: $(".l-color", r).value }));
  const panel = h("div", { class: "form" },
    h("p", { class: "hint", text: "Labels sort cards across groups and columns: Bug, Waiting, Quick. A card can have several. Top to bottom here is the order they show in." }),
    rows,
    h("div", {}, h("button", { type: "button", text: "Add label", onclick: () => { $(".l-name", add()).focus(); changed(); } })));
  return {
    panel,
    changes: () => (same(value(), board.labels.map((l) => ({ id: l.id, name: l.name, color: l.color }))) ? {} : { labels: value() }),
  };
}

// ---- properties --------------------------------------------------------------------------------

const DESCRIBE = {
  text: "One line of text on each card.",
  number: "A number, with a unit if you like: 3 h, 250 g.",
  choice: "One of a list of choices.",
  multi: "Any of a list of choices.",
  checkbox: "A box to tick.",
  url: "A web address, starting with http:// or https://.",
  rating: "Stars, from one up to the highest rating.",
};

// The choices of a choice or several-choices property. `uses(id)` counts the cards that have a choice picked.
function choicesEditor(prop, uses, changed) {
  const rows = h("div", { class: "list-rows" });
  const add = (c = { id: "", name: "", color: COLORS[rowsOf(rows, "choice-row").length % COLORS.length] }) => {
    const n = c.id ? uses(c.id) : 0;
    const row = h("div", { class: "choice-row", "data-id": c.id });
    const name = h("input", { class: "ch-name", maxLength: 40, value: c.name, "aria-label": "Choice", placeholder: "Choice" });
    row.append(name, colorPick(c.color, "ch-color"),
      h("div", { class: "mini" }, nudges(row, changed, "choice"), h("button", {
        type: "button", class: "danger icon", text: "×", title: "Remove", "aria-label": "Remove choice",
        onclick: () => removeRow(row, n, `${c.name} is picked on ${plural(n, "card")}. Removing it clears it there when you save.`, changed),
      })));
    rows.append(row);
    return row;
  };
  (prop?.choices || []).forEach((c) => add(c));
  if (!rowsOf(rows, "choice-row").length) add();
  return {
    el: h("div", { class: "field" }, h("span", { class: "lbl", text: "Choices" }), rows,
      h("div", {}, h("button", { type: "button", text: "Add choice", onclick: () => { $(".ch-name", add()).focus(); changed(); } }))),
    value: () => ({ choices: rowsOf(rows, "choice-row").map((r) => ({ id: r.dataset.id || undefined, name: $(".ch-name", r).value, color: $(".ch-color", r).value })) }),
  };
}

function numberOptions(prop) {
  const low = h("input", { class: "n-min", inputMode: "decimal", value: prop?.min ?? "", placeholder: "None" });
  const high = h("input", { class: "n-max", inputMode: "decimal", value: prop?.max ?? "", placeholder: "None" });
  const places = h("select", { class: "n-decimals" }, DECIMALS.map((d) => h("option", { value: String(d), text: String(d) })));
  places.value = String(prop?.decimals ?? 0);
  const unit = h("input", { class: "n-unit", maxLength: 10, value: prop?.unit ?? "", placeholder: "h" });
  return {
    el: h("div", { class: "num-options" }, field("Smallest", low), field("Largest", high), field("Decimal places", places), field("Unit", unit)),
    value: () => ({ min: typedNumber(low.value), max: typedNumber(high.value), decimals: Number(places.value), unit: unit.value }),
  };
}

function ratingOptions(prop) {
  const top = h("select", { class: "r-max" }, RATING_TOPS.map((n) => h("option", { value: String(n), text: String(n) })));
  top.value = String(prop?.max ?? 5);
  return { el: field("Highest rating", top), value: () => ({ max: Number(top.value) }) };
}

// What a definition looks like when sent, so an unchanged one can be told from a changed one.
function sent(prop) {
  const out = { id: prop.id, name: prop.name, type: prop.type };
  if (prop.type === "number") Object.assign(out, { min: prop.min, max: prop.max, decimals: prop.decimals, unit: prop.unit });
  if (prop.type === "choice" || prop.type === "multi") out.choices = prop.choices.map((c) => ({ id: c.id, name: c.name, color: c.color }));
  if (prop.type === "rating") out.max = prop.max;
  return out;
}

export function propertiesTab(board, changed) {
  const holding = (prop, test) => board.cards.filter((c) => prop && c.properties[prop.id] !== undefined && test(c.properties[prop.id])).length;
  const list = h("div", { class: "list-rows" });
  const items = [];
  const add = (prop = null) => {
    const item = { id: prop?.id || "" };
    const name = h("input", { class: "p-name", maxLength: 40, value: prop?.name ?? "", placeholder: "Points" });
    const type = h("select", { class: "p-type" }, Object.entries(TYPE_TITLES).map(([value, text]) => h("option", { value, text })));
    type.value = prop?.type ?? "text";
    const about = h("p", { class: "hint" });
    const options = h("div", { class: "p-options" });
    const made = {};  // option editors made so far, kept so switching type and back loses nothing typed
    const uses = (cid) => holding(prop, (v) => (Array.isArray(v) ? v.includes(cid) : v === cid));
    const editorFor = (kind) => {
      if (kind === "number") return made.number ||= numberOptions(prop?.type === "number" ? prop : null);
      if (kind === "rating") return made.rating ||= ratingOptions(prop?.type === "rating" ? prop : null);
      if (kind === "choice" || kind === "multi") return made.choices ||= choicesEditor(prop?.choices ? prop : null, uses, changed);
      return null;
    };
    const showType = () => {
      about.textContent = DESCRIBE[type.value];
      const ed = editorFor(type.value);
      options.replaceChildren(...(ed ? [ed.el] : []));
    };
    type.addEventListener("change", showType);
    showType();
    const n = holding(prop, () => true);
    const row = h("div", { class: "list-row prop-def", "data-id": item.id },
      h("div", { class: "prop-def-top" },
        field("Property", name), field("Type", type),
        h("div", { class: "mini" })),
      about, options);
    $(".mini", row).append(...nudges(row, changed, "property"), h("button", {
      type: "button", class: "danger", text: "Remove",
      onclick: () => removeRow(row, n, `${prop?.name} has a value on ${plural(n, "card")}. Removing it clears ${n === 1 ? "it" : "them"} when you save.`, () => {
        items.splice(items.indexOf(item), 1);
        changed();
      }),
    }));
    Object.assign(item, {
      row,
      name: () => name.value.trim(),
      value: () => ({ id: item.id || undefined, name: name.value, type: type.value, ...(editorFor(type.value)?.value() || {}) }),
    });
    items.push(item);
    list.append(row);
    return item;
  };
  board.properties.forEach((p) => add(p));
  const ordered = () => rowsOf(list, "prop-def").map((r) => items.find((i) => i.row === r)).filter(Boolean);
  const panel = h("div", { class: "form" },
    h("p", { class: "hint", text: "Properties are values every card can hold, like points, a priority or a link. Changing the type keeps the values that fit; the board says when some wouldn't." }),
    list,
    h("div", {}, h("button", { type: "button", text: "Add property", onclick: () => { $(".p-name", add().row).focus(); changed(); } })));
  return {
    panel,
    items: ordered,
    changes: () => {
      const now = ordered().map((i) => i.value());
      return same(now, board.properties.map(sent)) ? {} : { properties: now };
    },
  };
}

// ---- card face ---------------------------------------------------------------------------------

// What shows on each card on the board, and in what order. Properties added in the Properties tab show
// here too, and can be placed before they're saved.
export function faceTab(board, props, changed) {
  const parts = { group: board.groupLabel, labels: "Labels", field: "The first field", progress: "Checklist and task progress", blocked: "Blocked" };
  const list = h("div", { class: "list-rows face-list" });
  const entries = [];
  const entry = (ref, shown) => {
    const e = { ref, shown };
    const box = h("input", { type: "checkbox", checked: shown, onchange: () => { e.shown = box.checked; changed(); } });
    e.label = h("span");
    e.row = h("div", { class: "face-row" }, h("label", { class: "face-pick" }, box, e.label), h("div", { class: "mini" }));
    $(".mini", e.row).append(...nudges(e.row, changed, "it"));
    entries.push(e);
    return e;
  };
  const title = (e) => (typeof e.ref === "string" ? parts[e.ref] : e.ref.name() || "New property");
  const byId = new Map(props.items().map((i) => [i.id, i]));
  for (const part of board.settings.cardFace) {
    const ref = parts[part] ? part : byId.get(part);
    if (ref) entry(ref, true);
  }
  for (const part of Object.keys(parts)) if (!board.settings.cardFace.includes(part)) entry(part, false);
  // Bring the list up to date with the Properties tab: new ones at the end, removed ones gone, names as typed.
  const refresh = () => {
    const live = props.items();
    for (const e of [...entries]) {
      if (typeof e.ref !== "string" && !live.includes(e.ref)) {
        entries.splice(entries.indexOf(e), 1);
        e.row.remove();
      }
    }
    for (const item of live) if (!entries.some((e) => e.ref === item)) entry(item, false);
    for (const e of entries) {
      e.label.textContent = title(e);
      if (!e.row.isConnected) list.append(e.row);
    }
  };
  refresh();
  const inOrder = () => [...list.children].map((r) => entries.find((e) => e.row === r)).filter(Boolean);
  const panel = h("div", { class: "form" },
    h("p", { class: "hint", text: "Tick what each card shows on the board under its title, and put it in order. A property shows only on cards that have a value for it." }),
    list);
  return {
    panel,
    refresh,
    // The card face to send: parts and property ids, and {name} for a property that isn't saved yet, which has
    // no id until it is. `changed` says whether it differs from what the board has.
    face: () => {
      refresh();
      const face = inOrder().filter((e) => e.shown).map((e) => (typeof e.ref === "string" ? e.ref : e.ref.id || { name: e.ref.name() }));
      return { face, changed: !same(face, board.settings.cardFace) };
    },
  };
}
