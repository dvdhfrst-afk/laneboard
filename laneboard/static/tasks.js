// A card's own tasks in the card view: add, tick, reword, reorder and remove, each saved straight away. The list on
// screen runs ahead of the server while changes are on their way, and settles to what the server has once they land.

import { latest, state } from "./state.js";
import { announce, h, offer } from "./dom.js";
import { send } from "./api.js";

export function tasksView(card) {
  const key = card.key;
  const boardId = state.board.id;
  let model = card.tasks.map((t) => ({ ...t }));  // what's on screen
  let working = 0;  // task changes on their way
  let made = 0;     // numbers tasks still being added, which have no id yet
  const rows = new Map();
  const list = h("ul", { class: "task-list" });
  const tally = h("span", { class: "count" });
  const msg = h("div", { class: "form-error", role: "alert" });
  const adder = h("input", { id: "task-new", maxLength: 300, autocomplete: "off", placeholder: "Add a task", "aria-label": "Add a task" });

  const keyOf = (t) => t.id || `new-${t.made}`;
  const cardIn = (board) => board?.cards.find((c) => c.key === key);
  const server = () => (latest?.id === boardId ? cardIn(latest)?.tasks : null);
  const fail = (e) => { msg.textContent = `Not saved: ${e.message}`; };
  const run = (promise) => {
    working++;
    msg.textContent = "";
    return promise.finally(() => {
      working--;
      if (!working) settle();
    });
  };
  const settle = () => {
    const tasks = server();
    if (!tasks || !list.isConnected) return;
    model = tasks.map((t) => ({ ...t }));
    draw();
  };

  function taskRow(task) {
    const box = h("input", { type: "checkbox" });
    const text = h("input", { class: "task-text", maxLength: 300, autocomplete: "off", "aria-label": "Task", value: task.text });
    const up = h("button", { type: "button", class: "icon", text: "↑", "aria-label": "Move up", title: "Move up (Alt+Up)" });
    const down = h("button", { type: "button", class: "icon", text: "↓", "aria-label": "Move down", title: "Move down (Alt+Down)" });
    const remove = h("button", { type: "button", class: "icon danger", text: "×", "aria-label": "Remove task", title: "Remove" });
    const note = h("div", { class: "task-note", "aria-live": "polite" });
    const li = h("li", { class: "task" }, box, text, h("span", { class: "mini" }, up, down, remove), note);
    const r = { li, task, text, up, down, base: task.text, saved: task.text, sending: undefined };
    const theirs = () => server()?.find((t) => t.id === r.task.id)?.text;
    r.dirty = () => text.value !== r.saved;
    r.busy = () => r.dirty() || r.sending !== undefined;
    const conflict = () => {
      if (note.querySelector("button")) return;
      const other = theirs();
      if (other === undefined) {
        note.textContent = "This task was removed somewhere else.";
        return;
      }
      offer(note, `This task also changed somewhere else, to "${other}". Your text is still here.`,
        ["Keep mine", () => { r.base = theirs() ?? r.base; r.commit(); }],
        ["Use the other", () => { r.saved = r.base = theirs() ?? r.saved; text.value = r.saved; }]);
    };
    r.commit = () => {
      if (!r.task.id || !r.dirty() || note.querySelector("button")) return;
      const value = text.value;
      if (r.sending === value) return;
      const other = theirs();
      if (other !== undefined && other !== r.base) {
        conflict();
        return;
      }
      const base = r.base;
      const id = r.task.id;
      r.sending = value;
      note.textContent = "";
      run(send("PATCH", `/cards/${key}/tasks/${id}`, { text: value }, { retry: (fresh) => cardIn(fresh)?.tasks.find((t) => t.id === id)?.text === base })
        .then((data) => {
          const now = cardIn(data.board)?.tasks.find((t) => t.id === id)?.text ?? value;
          const unchanged = text.value === value;
          r.saved = r.base = now;
          if (unchanged) text.value = now;  // as the server keeps it
        })
        .catch((e) => {
          if (e.status === 412) conflict();
          else note.textContent = `Not saved: ${e.message}`;
        })
        .finally(() => { r.sending = undefined; }));
    };
    r.update = (t) => {
      r.task = t;
      const ready = !!t.id;
      li.dataset.id = t.id || "";
      text.id = ready ? `task-${t.id}` : "";
      box.setAttribute("aria-labelledby", text.id);
      box.checked = t.done;
      li.classList.toggle("done", t.done);
      box.disabled = remove.disabled = !ready;
      text.readOnly = !ready;
      if (!r.busy() && !note.querySelector("button")) {
        r.saved = r.base = t.text;
        text.value = t.text;
      }
    };
    const keys = (e) => {
      if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        move(r, e.key === "ArrowUp" ? -1 : 1);
        return true;
      }
      return false;
    };
    text.addEventListener("change", () => r.commit());
    text.addEventListener("input", () => { if (!note.querySelector("button")) note.textContent = ""; });
    text.addEventListener("keydown", (e) => {
      if (keys(e)) return;
      if (e.key === "Enter") {
        e.preventDefault();
        r.commit();
      } else if (e.key === "Escape" && r.dirty()) {
        e.preventDefault();  // put the saved text back rather than closing the card
        e.stopPropagation();
        text.value = r.saved;
        note.textContent = "";
      }
    });
    box.addEventListener("keydown", keys);
    box.addEventListener("change", () => tick(r, box.checked));
    up.addEventListener("click", () => move(r, -1, up));
    down.addEventListener("click", () => move(r, 1, down));
    remove.addEventListener("click", () => drop(r));
    r.update(task);
    return r;
  }

  const rowFor = (t) => {
    let r = rows.get(keyOf(t));
    if (!r) rows.set(keyOf(t), r = taskRow(t));
    else r.update(t);
    return r;
  };

  // Put the rows in the order of the model, reusing them so focus and half-typed text stay where they are.
  function draw() {
    const active = document.activeElement;
    const keep = active && list.contains(active) ? { el: active, start: active.selectionStart, end: active.selectionEnd } : null;
    const wanted = model.map(rowFor);
    const live = new Set(model.map(keyOf));
    for (const [k, r] of rows) {
      if (live.has(k)) continue;
      if (r.dirty()) msg.textContent = `A task you were changing was removed somewhere else. You had typed: ${r.text.value}`;
      r.li.remove();
      rows.delete(k);
    }
    wanted.forEach((r, i) => {
      if (list.children[i] !== r.li) list.insertBefore(r.li, list.children[i] || null);
      r.up.disabled = !r.task.id || i === 0;
      r.down.disabled = !r.task.id || i === wanted.length - 1;
    });
    if (keep && keep.el.isConnected && document.activeElement !== keep.el && !keep.el.disabled) {
      keep.el.focus({ preventScroll: true });
      if (typeof keep.start === "number") keep.el.setSelectionRange(keep.start, keep.end);
    }
    const done = model.filter((t) => t.done).length;
    tally.textContent = model.length ? `${done}/${model.length}` : "";
  }

  function tick(r, done) {
    const id = r.task.id;
    const t = model.find((x) => x.id === id);
    if (t) t.done = done;
    draw();
    run(send("PATCH", `/cards/${key}/tasks/${id}`, { done }, { retry: true }).catch(fail));
  }

  // Moved as a change to the order the server has, so a task added in another tab isn't lost.
  function move(r, delta, button) {
    const id = r.task.id;
    const at = model.findIndex((t) => t.id === id);
    const to = at + delta;
    if (!id || at < 0 || to < 0 || to >= model.length) return;
    r.commit();
    [model[at], model[to]] = [model[to], model[at]];
    draw();
    if (button?.disabled) r.text.focus();  // it reached the end of the list
    announce(`Moved to ${to + 1} of ${model.length}.`);
    run(send("PATCH", `/cards/${key}`, (board) => {
      const tasks = (cardIn(board)?.tasks ?? model).filter((t) => t.id).map(({ id: tid, text, done }) => ({ id: tid, text, done }));
      const i = tasks.findIndex((t) => t.id === id);
      const j = i + delta;
      if (i >= 0 && j >= 0 && j < tasks.length) [tasks[i], tasks[j]] = [tasks[j], tasks[i]];
      return { tasks };
    }, { retry: true }).catch(fail));
  }

  function drop(r) {
    const id = r.task.id;
    const at = model.findIndex((t) => t.id === id);
    if (!id || at < 0) return;
    model.splice(at, 1);
    const next = model[at] || model[at - 1];
    r.saved = r.text.value;  // nothing to keep from a task being removed
    draw();
    (next ? rows.get(keyOf(next))?.text : adder)?.focus();
    announce(`Removed the task ${r.task.text}.`);
    run(send("DELETE", `/cards/${key}/tasks/${id}`, undefined, { retry: true }).catch((e) => {
      if (e.status !== 404) fail(e);  // already gone is what was wanted
    }));
  }

  function add() {
    const value = adder.value;
    if (!value.trim()) return;
    const t = { id: null, made: ++made, text: value.trim(), done: false };
    model.push(t);
    adder.value = "";
    draw();
    run(send("POST", `/cards/${key}/tasks`, { text: value }, { retry: true })
      .then((data) => {
        const r = rows.get(keyOf(t));
        rows.delete(keyOf(t));
        t.id = data.task;
        if (r) {
          rows.set(t.id, r);
          r.update(t);
        }
        announce(`Added the task ${t.text}.`);
      })
      .catch((e) => {
        const i = model.indexOf(t);
        if (i >= 0) model.splice(i, 1);
        draw();
        if (!adder.value) adder.value = value;  // nothing typed is lost
        fail(e);
      }));
  }

  adder.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    } else if (e.key === "Escape" && adder.value) {
      e.preventDefault();
      e.stopPropagation();
      adder.value = "";
    }
  });
  draw();
  const node = h("div", { class: "sec tasks" },
    h("h3", {}, "Tasks ", tally),
    list,
    h("div", { class: "task-add" }, adder, h("button", { type: "button", text: "Add", onclick: () => { add(); adder.focus(); } })),
    h("p", { class: "hint", text: "Enter saves a task. Alt+Up and Alt+Down move the one you're on." }),
    msg);
  return {
    node,
    busy: () => working > 0 || !!adder.value.trim() || [...rows.values()].some((r) => r.busy()),
    sync: (now) => {
      if (working) return;  // settles when the last change lands
      model = now.tasks.map((t) => ({ ...t }));
      draw();
    },
  };
}
