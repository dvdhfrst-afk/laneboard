// Building the page: the h() helper, form fields, card text, copying, toasts, the banner and the side sheet.

import { START_COMMAND, parseHash, setHash, sheet, state } from "./state.js";
import { isWebLink } from "./util.js";

export const $ = (sel, root = document) => root.querySelector(sel);

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k.startsWith("data-") || k.startsWith("aria-") || k === "for" || !(k in el)) el.setAttribute(k, v === true ? "" : v);
    else el[k] = v;
  }
  for (const c of children.flat(Infinity)) {
    if (c !== undefined && c !== null && c !== false) el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

export function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl", text: label }), control,
    hint && h("span", { class: "hint", text: hint }));
}

export function groupTag(g) {
  return h("span", { class: "grp" }, h("span", { class: `dot c-${g.color}` }), g.name);
}

// A colored chip with its name in it, for labels and property choices. The name is always there: color alone
// doesn't say anything to everyone.
export function chip(name, color, extra = "") {
  return h("span", { class: `chip c-${color}${extra ? " " + extra : ""}` }, h("span", { class: `dot c-${color}`, "aria-hidden": "true" }), name);
}

export function groupLink(g) {
  return g.link && isWebLink(g.link) ? h("a", { href: g.link, target: "_blank", rel: "noopener noreferrer" }, groupTag(g)) : groupTag(g);
}

export function announce(message) {
  const live = $("#live");
  live.textContent = "";
  requestAnimationFrame(() => { live.textContent = message; });
}

// The clipboard API can be refused (embedded browsers, missing permission); the older
// select-and-copy route covers those.
function copyBySelection(text) {
  const dlg = $("#sheet");
  const box = h("textarea", { value: text, readOnly: true, class: "offscreen", "aria-hidden": "true" });
  (dlg.open ? dlg : document.body).append(box);  // outside an open modal sheet, nothing can be selected
  box.select();
  try { return document.execCommand("copy"); } catch { return false; } finally { box.remove(); }
}

export async function copyText(text) {
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    ok = copyBySelection(text);
  }
  if (ok) toast("Copied");
  else toast("Couldn't copy. Select the text and press Ctrl+C.", { error: true });
}

// Text from cards: `backticks` become code with a Copy button, and web addresses become links.
// Never parsed as HTML.
export function rich(text) {
  const parts = String(text).split("`");
  if (parts.length % 2 === 0) parts.push(parts.splice(-2).join("`"));
  const out = [];
  parts.forEach((part, i) => {
    if (i % 2) {
      return out.push(h("code", { text: part }), h("button", {
        type: "button", class: "copy", text: "Copy", "aria-label": `Copy ${part}`, onclick: () => copyText(part) }));
    }
    part.split(/(https?:\/\/[^\s<>"'`)\]]+)/).forEach((bit, j) => {
      if (j % 2) {
        const end = bit.match(/[.,;:!?]+$/)?.[0] || "";  // the period ending a sentence isn't part of the address
        const url = end ? bit.slice(0, -end.length) : bit;
        out.push(h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: url }));
        if (end) out.push(end);
      } else if (bit) out.push(bit);
    });
  });
  return out;
}

let toastTimer = null;
export function toast(message, { error = false, action = null } = {}) {
  const el = $("#toast");
  const dlg = $("#sheet");
  (dlg.open ? dlg : document.body).append(el);  // a modal sheet covers everything outside it
  el.className = error ? "toast error" : "toast";
  el.replaceChildren(...[h("span", { text: message }),
    action && h("button", { type: "button", text: action.label, onclick: () => { el.hidden = true; action.run(); } }),
    h("button", { type: "button", "aria-label": "Dismiss", text: "×", onclick: () => { el.hidden = true; } })].filter(Boolean));
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 9000 : action ? 7000 : 4000);
}

// What the banner offers when the boards file has a problem (the Backups sheet), set by main.js, which can import it.
let bannerAction = null;
export const setBannerAction = (action) => { bannerAction = action; };

export function renderBanner() {
  const el = $("#banner");
  const message = state.offline
    ? `Can't reach Laneboard, so changes can't be saved right now. If it has stopped, start it again with: ${START_COMMAND}`
    : state.problem || state.versionNote;
  el.hidden = !message;
  el.textContent = message || "";
  if (state.problem && !state.offline && bannerAction) {
    el.append(" ", h("button", { type: "button", text: bannerAction.label, onclick: bannerAction.run }));
  }
  el.className = state.offline || state.problem ? "banner bad" : "banner";
}

// ---- side sheet -------------------------------------------------------------------------------

export function showSheet(mode, key, content, { onClose = null, unsaved = null, retry = null, sync = null } = {}) {
  const dlg = $("#sheet");
  sheet.onClose?.();
  Object.assign(sheet, { mode, key, dirty: false, onClose, unsaved, retry, sync });
  $("#sheet-body").replaceChildren(content);
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
}

export function closeSheet(force = false) {
  const dlg = $("#sheet");
  if (!dlg.open) return;
  if (!force && sheet.dirty) {
    askDiscard();
    return;
  }
  finishSheet();
  dlg.close();
}

// Runs when the sheet closes, whether through closeSheet or the browser (Escape).
function finishSheet() {
  if (!sheet.mode) return;
  const backTo = sheet.key;
  sheet.onClose?.();
  Object.assign(sheet, { mode: null, key: null, dirty: false, onClose: null, unsaved: null, retry: null, sync: null });
  $("#sheet-body").replaceChildren();
  document.body.append($("#toast"));
  if (state.board && parseHash().key) setHash(state.board.id);
  // The card that was clicked has usually been redrawn since, so the browser can't return to it.
  if (backTo) {
    state.focusKey = backTo;
    requestAnimationFrame(() => $(`.card[data-key="${CSS.escape(backTo)}"]`)?.focus());
  }
}

export function bindSheet() {
  const dlg = $("#sheet");
  let downOutside = false;
  const outside = (e) => {
    const r = dlg.getBoundingClientRect();
    return e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom);
  };
  dlg.addEventListener("pointerdown", (e) => { downOutside = outside(e); });
  dlg.addEventListener("click", (e) => {
    if (downOutside && outside(e)) closeSheet();
    downOutside = false;
  });
  dlg.addEventListener("cancel", (e) => {
    if (sheet.dirty) {
      e.preventDefault();
      askDiscard();
    }
  });
  dlg.addEventListener("close", () => {
    if (!dlg.open) finishSheet();  // the sheet may have been reopened before this event arrived
  });
}

export function askDiscard(then = () => closeSheet(true)) {
  const body = $("#sheet-body");
  body.querySelector(".confirm")?.remove();
  const keep = h("button", { type: "button", text: "Keep editing", onclick: () => box.remove() });
  const box = h("div", { class: "confirm", role: "alert" },
    h("span", { text: "Discard your changes?" }),
    h("button", { type: "button", class: "danger solid", text: "Discard", onclick: () => { sheet.dirty = false; then(); } }),
    keep);
  const actions = body.querySelector(".form-actions");
  if (actions) actions.after(box);
  else body.prepend(box);
  keep.focus();
  box.scrollIntoView({ block: "nearest" });
}

export function confirmBox(anchor, message, label, run) {
  $("#sheet-body").querySelector(".confirm")?.remove();
  const err = h("span", { class: "form-error" });
  const keep = h("button", { type: "button", text: "Keep it", onclick: () => box.remove() });
  const go = h("button", {
    type: "button", class: "danger solid", text: label,
    onclick: async () => {
      go.disabled = true;
      try {
        await run();
        box.remove();
      } catch (e) {
        err.textContent = e.message;
        go.disabled = false;
      }
    },
  });
  const box = h("div", { class: "confirm", role: "alert" }, h("span", { text: message }), go, keep, err);
  anchor.after(box);
  keep.focus();
}

export function sheetHead(title, key, ...buttons) {
  return h("div", { class: "sheet-head" },
    h("div", { class: "sheet-title" }, key && h("div", { class: "key", text: key }), h("h2", { id: "sheet-title", text: title })),
    h("div", { class: "actions" }, buttons, h("button", { type: "button", text: "Close", onclick: () => closeSheet() })));
}

// A choice the person has to make, like which of two versions to keep: a message and a button for each way.
// While it shows, `box` holds buttons, which is how callers tell it's waiting for an answer.
export function offer(box, message, ...choices) {
  box.replaceChildren(h("span", { text: message }), ...choices.map(([label, run]) => h("button", {
    type: "button", text: label, onclick: () => { box.replaceChildren(); run(); } })));
}

// Tabs over panels: [{id, label, panel}]. Left and Right go between tabs, as tabs usually do.
export function tabs(list, { onSelect = null } = {}) {
  const buttons = list.map((t, i) => h("button", {
    type: "button", role: "tab", id: `tab-${t.id}`, "aria-controls": `panel-${t.id}`, "aria-selected": String(i === 0),
    tabIndex: i === 0 ? 0 : -1, text: t.label, onclick: () => select(t.id) }));
  list.forEach((t, i) => {
    t.panel.id = `panel-${t.id}`;
    t.panel.hidden = i > 0;
    t.panel.setAttribute("role", "tabpanel");
    t.panel.setAttribute("aria-labelledby", `tab-${t.id}`);
  });
  const select = (id, focus = false) => {
    list.forEach((t, i) => {
      const on = t.id === id;
      t.panel.hidden = !on;
      buttons[i].setAttribute("aria-selected", String(on));
      buttons[i].tabIndex = on ? 0 : -1;
      if (on && focus) buttons[i].focus();
    });
    onSelect?.(id);
  };
  const bar = h("div", { class: "tabs", role: "tablist" }, buttons);
  bar.addEventListener("keydown", (e) => {
    const at = buttons.indexOf(document.activeElement);
    if (at < 0) return;
    const to = e.key === "ArrowRight" ? at + 1 : e.key === "ArrowLeft" ? at - 1 : e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : null;
    if (to === null) return;
    e.preventDefault();
    select(list[(to + buttons.length) % buttons.length].id, true);
  });
  return { bar, select };
}
