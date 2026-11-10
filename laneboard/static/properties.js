// Card properties on the page: how each type of value reads as text, how it shows on a card, and the control that
// edits it, used by the card view (saving in place) and the card form. The server checks every value and its message
// is the one shown; a control only turns what is typed or picked into the JSON the server reads.

import { latest, state } from "./state.js";
import { chip, h, offer, toast } from "./dom.js";
import { send } from "./api.js";
import { isWebLink } from "./util.js";

export const TYPE_TITLES = {
  text: "Text", number: "Number", choice: "Choice", multi: "Several choices", checkbox: "Checkbox", url: "Link", rating: "Rating",
};

const NUMBER = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
const PICK_DELAY = 500;  // choices and stars save this long after the last pick, so arrowing through them sends one change

export const sameValue = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const choice = (prop, id) => prop.choices.find((c) => c.id === id);
const inChoiceOrder = (prop, ids) => prop.choices.filter((c) => ids.has(c.id)).map((c) => c.id);

function host(url) {
  try { return new URL(url).host || url; } catch { return url; }
}

function stars(value, max) {
  return h("span", { class: "stars" },
    h("span", { "aria-hidden": "true", text: "★".repeat(value) + "☆".repeat(Math.max(0, max - value)) }),
    h("span", { class: "sr", text: `${value} out of ${max}` }));
}

// ---- reading a value ---------------------------------------------------------------------------

// A value as plain text, as the command line writes it: for search, for messages and for screen readers.
export function propText(prop, value) {
  if (value === undefined || value === null) return "";
  switch (prop.type) {
    case "number": return prop.unit ? `${value} ${prop.unit}` : String(value);
    case "choice": return choice(prop, value)?.name ?? "";
    case "multi": return value.map((id) => choice(prop, id)?.name).filter(Boolean).join(", ");
    case "checkbox": return value ? "yes" : "";
    case "rating": return `${value}/${prop.max}`;
    default: return String(value);
  }
}

// How a value shows on a card on the board, with the property's name so a bare number means something.
// The card is itself a link, so a web address shows as text here, never as a link inside it.
export function propFace(prop, value) {
  if (value === undefined || value === null || value === false) return null;
  const named = (...rest) => h("span", { class: "pf" }, h("span", { class: "pf-name", text: prop.name }), " ", ...rest);
  switch (prop.type) {
    case "choice": {
      const c = choice(prop, value);
      return c ? named(chip(c.name, c.color)) : null;
    }
    case "multi": {
      const picked = value.map((id) => choice(prop, id)).filter(Boolean);
      return picked.length ? named(...picked.map((c) => chip(c.name, c.color))) : null;
    }
    case "checkbox": return h("span", { class: "pf" }, h("span", { "aria-hidden": "true", text: "✓ " }), prop.name);
    case "url": return named(h("span", { class: "pf-link", text: host(value) }));
    case "rating": return named(stars(value, prop.max));
    default: return named(h("span", { class: "pf-text", text: propText(prop, value) }));
  }
}

// ---- controls ----------------------------------------------------------------------------------

// The control for one property: {el, read(), write(value), pending(), flush()}. `read` gives what the server should
// get (null means clear). `hooks.commit` is called when the person has finished an entry; without it (the card form)
// nothing is sent until the form is. `hooks.revert` puts back the saved value when Esc is pressed on a changed entry.
export function editor(prop, value, hooks = {}) {
  const make = EDITORS[prop.type] || EDITORS.text;
  return make(prop, value ?? null, hooks);
}

function lineInput(hooks, props) {
  const input = h("input", { id: hooks.id, autocomplete: "off", ...props });
  if (hooks.commit) {
    input.addEventListener("change", () => hooks.commit());
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        hooks.commit();
      } else if (e.key === "Escape" && hooks.revert && hooks.dirty?.()) {
        e.preventDefault();  // put the saved value back rather than closing the card
        e.stopPropagation();
        hooks.revert();
      }
    });
  }
  return input;
}

const idle = { pending: () => false, flush: () => {} };

function textEditor(prop, value, hooks) {
  const input = lineInput(hooks, { maxLength: 500, value: value ?? "" });
  return { el: input, focus: input, read: () => (input.value.trim() ? input.value : null), write: (v) => { input.value = v ?? ""; }, ...idle };
}

function numberEditor(prop, value, hooks) {
  const input = lineInput(hooks, { class: "num-input", value: value ?? "", inputMode: prop.min !== null && prop.min >= 0 ? "decimal" : "text" });
  const read = () => {
    let typed = input.value.trim();
    if (prop.unit && typed.toLowerCase().endsWith(prop.unit.toLowerCase())) typed = typed.slice(0, -prop.unit.length).trim();
    if (!typed) return null;
    return NUMBER.test(typed) ? Number(typed) : typed;  // anything else goes as typed, and the server says what it wants
  };
  const el = h("span", { class: "num" }, input, prop.unit && h("span", { class: "unit", text: prop.unit }));
  return { el, focus: input, read, write: (v) => { input.value = v ?? ""; }, ...idle };
}

function urlEditor(prop, value, hooks) {
  const input = lineInput(hooks, { type: "url", maxLength: 500, value: value ?? "", placeholder: "https://" });
  const link = h("a", { target: "_blank", rel: "noopener noreferrer", class: "open-link" });
  const show = (v) => {
    const ok = typeof v === "string" && isWebLink(v);
    link.hidden = !ok;
    if (ok) {
      link.href = v;
      link.textContent = `Open ${host(v)}`;
    } else link.removeAttribute("href");
  };
  show(value);
  return {
    el: h("span", { class: "url-edit" }, input, link), focus: input,
    read: () => input.value.trim() || null,
    write: (v) => { input.value = v ?? ""; show(v); }, ...idle,
  };
}

function checkboxEditor(prop, value, hooks) {
  const box = h("input", { type: "checkbox", id: hooks.id, checked: value === true });
  if (hooks.commit) box.addEventListener("change", () => hooks.commit());
  return { el: box, focus: box, read: () => (box.checked ? true : null), write: (v) => { box.checked = v === true; }, ...idle };
}

// Choices, several choices and stars are groups of radio buttons or boxes. Picking saves a moment later, and at
// once when focus leaves the group.
function pickGroup(hooks, el) {
  let timer = null;
  const flush = () => {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    hooks.commit?.();
  };
  if (hooks.commit) {
    el.addEventListener("change", () => {
      clearTimeout(timer);
      timer = setTimeout(flush, PICK_DELAY);
    });
    el.addEventListener("focusout", (e) => { if (!el.contains(e.relatedTarget)) flush(); });
  }
  return { pending: () => timer !== null, flush };
}

function choiceEditor(prop, value, hooks) {
  const multi = prop.type === "multi";
  const name = hooks.id;
  const inputs = [];
  const option = (id, label) => {
    const input = h("input", { type: multi ? "checkbox" : "radio", name, value: id, class: "pick-input" });
    inputs.push(input);
    return h("label", { class: "opt" }, input, label);
  };
  const el = h("div", { class: "opts", id: hooks.id },
    !multi && option("", h("span", { class: "chip none", text: "None" })),
    prop.choices.map((c) => option(c.id, chip(c.name, c.color))));
  const write = (v) => {
    const on = new Set(multi ? v || [] : [v ?? ""]);
    for (const input of inputs) input.checked = on.has(input.value);
  };
  const read = () => {
    const on = new Set(inputs.filter((i) => i.checked && i.value).map((i) => i.value));
    if (!multi) return [...on][0] ?? null;
    const ids = inChoiceOrder(prop, on);
    return ids.length ? ids : null;
  };
  write(value);
  return { el, focus: inputs[0], read, write, group: true, ...pickGroup(hooks, el) };
}

function ratingEditor(prop, value, hooks) {
  const inputs = [];
  const labels = [];
  const said = h("span", { class: "rating-text", "aria-hidden": "true" });
  const clear = h("button", { type: "button", class: "link", text: "Clear" });
  const el = h("div", { class: "stars-edit", id: hooks.id });
  for (let n = 1; n <= prop.max; n++) {
    const input = h("input", { type: "radio", name: hooks.id, value: String(n), class: "pick-input", "aria-label": `${n} out of ${prop.max}` });
    inputs.push(input);
    labels.push(h("label", { class: "star" }, input, h("span", { "aria-hidden": "true", text: "★" })));
  }
  el.append(...labels, said, clear);
  const read = () => {
    const on = inputs.find((i) => i.checked);
    return on ? Number(on.value) : null;
  };
  const paint = () => {
    const v = read() ?? 0;
    labels.forEach((l, i) => l.classList.toggle("on", i < v));
    said.textContent = v ? `${v} of ${prop.max}` : "No rating";
    clear.hidden = !v;
  };
  const write = (v) => {
    for (const input of inputs) input.checked = Number(input.value) === v;
    paint();
  };
  el.addEventListener("change", paint);
  const group = pickGroup(hooks, el);
  clear.addEventListener("click", () => {
    write(null);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change"));
    group.flush();
    inputs[0].focus();
  });
  write(value);
  return { el, focus: inputs[0], read, write, group: true, ...group };
}

const EDITORS = { text: textEditor, number: numberEditor, url: urlEditor, checkbox: checkboxEditor,
  choice: choiceEditor, multi: choiceEditor, rating: ratingEditor };

// A property with its name over its control, for the card view and the card form. Groups of buttons get a
// fieldset so the name is read with them.
export function propField(prop, ed, ...extra) {
  if (ed.group) return h("fieldset", { class: "prop-row" }, h("legend", { class: "lbl", text: prop.name }), ed.el, extra);
  return h("div", { class: "prop-row" }, h("label", { class: "lbl", for: ed.focus.id, text: prop.name }), ed.el, extra);
}

// ---- in the card view --------------------------------------------------------------------------

// The board's properties on one card, each saved on its own as soon as it's entered. A value that also changed
// somewhere else is never silently replaced: the person picks which one to keep.
export function propertiesView(card) {
  const rows = state.board.properties.map((prop) => propertyInPlace(state.board.id, card.key, prop, card.properties[prop.id] ?? null));
  return {
    node: rows.length ? h("div", { class: "sec props" }, h("h3", { text: "Properties" }), h("div", { class: "prop-grid" }, rows.map((r) => r.row))) : null,
    sync: (now) => rows.forEach((r) => r.sync(now)),
    busy: () => rows.some((r) => r.busy()),
    leave: () => rows.forEach((r) => r.leave()),
  };
}

function propertyInPlace(boardId, key, prop, initial) {
  let baseline = initial;  // what the card held when last seen, to tell whether someone else has changed it since
  let saved = initial;     // the value as last saved here, to tell whether there's anything new to save
  let sending = undefined; // the value on its way, so pressing Enter and then leaving the box sends it once
  const msg = h("div", { class: "prop-msg", "aria-live": "polite" });
  const valueIn = (board) => board?.cards.find((c) => c.key === key)?.properties[prop.id] ?? null;
  const other = () => (latest?.id === boardId ? valueIn(latest) : baseline);
  const dirty = () => !sameValue(ed.read(), saved);
  const waiting = () => !!msg.querySelector("button");
  const showConflict = () => {
    if (waiting()) return;  // clicking one of the buttons can land here again on its way
    const theirs = propText(prop, other());
    offer(msg, `${prop.name} also changed somewhere else${theirs ? `, to ${theirs}` : ", and was cleared"}. Your value is still here.`,
      ["Keep mine", () => { baseline = other(); save(); }],
      ["Use the other", () => { saved = baseline = other(); ed.write(saved); }]);
  };
  const save = () => {
    if (!dirty() || waiting()) return;
    const value = ed.read();
    if (sending !== undefined && sameValue(sending, value)) return;
    if (!sameValue(other(), baseline)) {
      showConflict();
      return;
    }
    const base = baseline;
    sending = value;
    msg.textContent = "Saving…";
    send("PATCH", `/cards/${key}`, { properties: { [prop.id]: value } }, { retry: (fresh) => sameValue(valueIn(fresh), base) })
      .then((data) => {
        const now = valueIn(data.board);
        const unchanged = sameValue(ed.read(), value);
        saved = baseline = now;
        if (unchanged) ed.write(now);  // as the server keeps it: rounded, trimmed
        if (msg.isConnected && !dirty()) msg.textContent = "Saved";
      })
      .catch((e) => {
        if (!msg.isConnected) toast(`${prop.name} on ${key} wasn't saved: ${e.message}`, { error: true });
        else if (e.status === 412) showConflict();
        else msg.textContent = `Not saved: ${e.message}`;
      })
      .finally(() => { sending = undefined; });
  };
  const ed = editor(prop, initial, {
    id: `prop-${prop.id}`, commit: save, dirty,
    revert: () => { ed.write(saved); msg.textContent = ""; },
  });
  ed.el.addEventListener("input", () => { if (!waiting()) msg.textContent = ""; });
  const busy = () => dirty() || ed.pending() || sending !== undefined;
  return {
    row: propField(prop, ed, msg),
    busy,
    sync: (now) => {
      const value = now.properties[prop.id] ?? null;
      if (busy() || waiting() || (sameValue(value, baseline) && sameValue(value, saved))) return;
      saved = baseline = value;
      ed.write(value);
      msg.textContent = "";
    },
    // Closing has nowhere to ask about a conflict, so a value that changed on both sides is left as the other one.
    leave: () => {
      ed.flush();
      if (!dirty()) return;
      if (sameValue(other(), baseline)) save();
      else toast(`Your ${prop.name} on ${key} wasn't saved: it also changed somewhere else.`, { error: true });
    },
  };
}
