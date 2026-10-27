/* Natro's dashboard: Home, Activity, Billing, Speed and System, from /dashboard/api. Charts are built once and
   updated in place when new numbers come (no redraw, no dimming); hovering, focusing or tapping a bar, a point or a
   day says what's in it, and clicking it opens those requests in Activity. Times are his wall clock (the API's). */
(() => {
  "use strict";

  const API = "/dashboard/api";
  const VIEWS = ["home", "activity", "billing", "speed", "system"];
  const $ = (query, root = document) => root.querySelector(query);
  const phone = matchMedia("(max-width: 760px)");
  const $$ = (query, root = document) => [...root.querySelectorAll(query)];

  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  function s(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    return node;
  }

  const ICONS = {
    pc: "M4 5h16v10H4zM9 19h6M12 15v4",
    phone: "M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2",
    job: "M12 7v5l3 2M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z",
    device: "M5 6h14v12H5zM9 10h6",
    natro: "M7 18V6l10 12V7",
    server: "M4 4h16v7H4zM4 13h16v7H4zM8 7.5h.01M8 16.5h.01",
    bell: "M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15zM10 20a2 2 0 0 0 4 0",
    routine: "M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5",
    eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z",
    plug: "M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5",
    alert: "M12 4l9 16H3zM12 10v4M12 17v.5",
    file: "M7 3h7l4 4v14H7zM14 3v4h4",
    message: "M4 5h16v11H9l-5 4z",
    key: "M14 10a4 4 0 1 1-1.2-2.8L21 15v3h-3v-2h-2v-2h-2.2",
    voice: "M9 6a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21",
    check: "M5 12.5l4.5 4.5L19 7.5",
    cross: "M6 6l12 12M18 6L6 18",
    down: "M12 5v14M6 13l6 6 6-6",
    up: "M12 19V5M6 11l6-6 6 6",
  };
  const icon = (name, cls) => {
    const node = s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    if (cls) node.setAttribute("class", cls);
    node.append(s("path", { d: ICONS[name] || ICONS.device }));
    return node;
  };

  /* Numbers, times and words */

  const money = (v) => v == null ? "—" : `$${v.toFixed(2)}`;
  const cost = (v) => v == null ? "—" : v === 0 ? "$0" : Math.abs(v) < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
  const axisMoney = (v) => v === 0 ? "$0" : v < 1 ? `$${+v.toFixed(2)}` : `$${+v.toFixed(1)}`;
  const secs = (v) => v == null ? "—" : v < 10 ? `${v.toFixed(1)} s` : v < 60 ? `${Math.round(v)} s`
    : `${Math.floor(v / 60)} min ${Math.round(v % 60)} s`;
  const whole = (v) => v == null ? "—" : Math.round(v).toLocaleString("en-US");
  const compact = (v) => {
    if (v == null) return "—";
    const a = Math.abs(v);
    if (a >= 1e6) return `${+(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
    if (a >= 1e3) return `${+(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
    return `${Math.round(v)}`;
  };
  const percent = (v, digits = 0) => v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
  const bytes = (v) => {
    if (v == null) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
  };
  const duration = (seconds) => {
    if (seconds == null) return "—";
    const m = Math.floor(seconds / 60), hrs = Math.floor(m / 60), d = Math.floor(hrs / 24);
    if (d) return `${d} d ${hrs % 24} h`;
    if (hrs) return `${hrs} h ${m % 60} min`;
    if (m) return `${m} min`;
    return `${Math.max(0, Math.round(seconds))} s`;
  };
  const plural = (n, word) => `${whole(n)} ${word}${n === 1 ? "" : "s"}`;

  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"];
  const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  // A day as "2026-10-21", and its words. ISO times from the API are his wall clock, so their first ten characters
  // are his date and characters 11 to 16 his time; the browser's own time zone plays no part.
  const dayKey = (iso) => String(iso || "").slice(0, 10);
  const clock = (iso) => String(iso || "").slice(11, 16);
  const keyDate = (key) => new Date(`${key}T12:00:00Z`);
  const shiftKey = (key, days) => new Date(keyDate(key).getTime() + days * 864e5).toISOString().slice(0, 10);
  const today = () => state.live ? dayKey(state.live.now) : new Date().toISOString().slice(0, 10);
  const longDay = (key) => { const d = keyDate(key); return `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
  const shortDay = (key) => { const d = keyDate(key); return `${WEEKDAYS[d.getUTCDay()].slice(0, 3)} ${d.getUTCDate()}`; };
  const dateShort = (key) => { const d = keyDate(key); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()].slice(0, 3)}`; };
  const dayWords = (key) => {
    const t = today();
    if (key === t) return "Today";
    if (key === shiftKey(t, -1)) return "Yesterday";
    if (key === shiftKey(t, 1)) return "Tomorrow";
    const ahead = (keyDate(key) - keyDate(t)) / 864e5;
    if (ahead > 0 && ahead < 7) return WEEKDAYS[keyDate(key).getUTCDay()];
    return `${shortDay(key)} ${MONTHS[keyDate(key).getUTCMonth()].slice(0, 3)}`;
  };
  const whenWords = (iso) => iso ? `${dayWords(dayKey(iso))}, ${clock(iso)}` : "—";
  const lowerWhen = (iso) => whenWords(iso).replace(/^(Today|Tomorrow|Yesterday)/, (word) => word.toLowerCase());
  const since = (iso) => iso ? (Date.now() - new Date(iso)) / 1000 : null;
  const monthName = (key) => `${MONTHS[+key.slice(5, 7) - 1]} ${key.slice(0, 4)}`;

  const DEVICE_WORDS = { pc: "PC", phone: "Phone", job: "Background job" };
  const deviceIcon = (name) => ICONS[name] ? name : name === "job" ? "job" : "device";
  const FAMILY = (model) => {
    const name = String(model || "").toLowerCase();
    if (name.startsWith("none")) return "quick";
    return ["opus", "sonnet", "haiku", "gemini"].find((word) => name.includes(word)) || "other";
  };
  const FAMILY_WORDS = { sonnet: "Sonnet", haiku: "Haiku", opus: "Opus", gemini: "Gemini", quick: "No model", other: "Other",
    voice: "Voice", jobs: "Background jobs" };
  // Spending's series, in a fixed order and colour: a series keeps its colour whatever else is shown.
  const SERIES = [
    { key: "sonnet", cls: "s1", label: "Sonnet", from: ["sonnet"] },
    { key: "haiku", cls: "s2", label: "Haiku", from: ["haiku"] },
    { key: "voice", cls: "s3", label: "Voice", from: ["voice"] },
    { key: "opus", cls: "s4", label: "Opus", from: ["opus"] },
    { key: "other", cls: "s5", label: "Other", from: ["gemini", "jobs", "quick", "other"] },
  ];
  const SERIES_OF = Object.fromEntries(SERIES.flatMap((series) => series.from.map((source) => [source, series])));
  // Where a request's time went, in five parts (dashboard.py's STAGES, put together).
  const STAGE_GROUPS = [
    { key: "hearing", cls: "s1", label: "Hearing", from: ["speech", "translate"] },
    { key: "thinking", cls: "s2", label: "Thinking", from: ["first_token", "writing"] },
    { key: "tools", cls: "s3", label: "Tools", from: ["tools"] },
    { key: "speaking", cls: "s4", label: "Speaking", from: ["speaking"] },
    { key: "other", cls: "s5", label: "Other", from: ["other"] },
  ];
  const grouped = (stages) => STAGE_GROUPS.map((g) => ({ ...g, value: g.from.reduce((sum, k) => sum + (stages?.[k] || 0), 0) }));

  /* State, saved choices, fetching */

  const state = {
    view: null, params: new URLSearchParams(), range: "7d", reachable: null,
    live: null, stats: null, year: null, bill: null, billMonth: null,
    at: { live: 0, stats: 0, year: 0, bill: 0, latest: 0, tail: 0 },
  };
  function saved(key, fallback) {
    try { return localStorage.getItem(`natro.${key}`) ?? fallback; } catch { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem(`natro.${key}`, value); } catch { /* a private window: not kept */ }
  }
  async function get(path) {
    const response = await fetch(`${API}${path}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${response.status}`);
    return data;
  }
  // The same, or null if Natro can't be reached (the page keeps what it shows).
  const fetched = (path) => get(path).catch(() => null);
  const query = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")).toString();

  /* The tooltip. Mouse: it follows the pointer and a click goes to the requests. Touch: the first tap pins it, with
     a button to them. Keyboard: focus shows it, Enter goes. */

  const tip = $("#tip");
  let tipOwner = null, pinned = false, lastPointer = "mouse";
  addEventListener("pointerdown", (event) => {
    lastPointer = event.pointerType || "mouse";
    if (pinned && !tip.contains(event.target) && !(tipOwner && tipOwner.contains(event.target))) hideTip(true);
  }, true);
  addEventListener("scroll", () => hideTip(true), { passive: true });

  function showTip(owner, nodes, { x, y, pin = false } = {}) {
    tip.replaceChildren(...nodes.filter(Boolean));
    tip.hidden = false;
    tip.classList.toggle("pinned", pin);
    pinned = pin;
    tipOwner = owner;
    const box = tip.getBoundingClientRect(), r = owner.getBoundingClientRect();
    const px = x ?? r.left + r.width / 2, py = y ?? r.top;
    let left = px + 14, top = py - box.height - 10;
    if (left + box.width > innerWidth - 8) left = px - box.width - 14;
    if (left < 8) left = 8;
    if (top < 8) top = (y ?? r.bottom) + 14;
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }
  function hideTip(force) {
    if (pinned && !force) return;
    tip.hidden = true;
    pinned = false;
    tipOwner = null;
  }
  function tipRows(title, rows, { total, hint, go } = {}) {
    return [
      h("div", { class: "t", text: title }),
      ...rows.map((row) => h("div", { class: "r" }, row.cls ? h("i", { class: `key k-${row.cls}` }) : h("i"),
        h("span", { text: row.label }), h("b", { text: row.value }))),
      total ? h("div", { class: "r total" }, h("i"), h("span", { text: total.label }), h("b", { text: total.value })) : null,
      go ? h("a", { class: "go", href: go, text: "See these requests" }) : hint ? h("div", { class: "hint", text: hint }) : null,
    ];
  }
  // A bar, a point or a day: what's in it on hover or focus; its requests on click.
  function pickable(node, content, target) {
    const show = (event) => {
      if (pinned) return;
      showTip(node, content({ hint: target() ? "Click to see these requests" : null }),
        event && event.clientX != null ? { x: event.clientX, y: event.clientY } : {});
    };
    node.addEventListener("pointerenter", (event) => { if (event.pointerType !== "touch") show(event); });
    node.addEventListener("pointermove", (event) => { if (event.pointerType !== "touch") show(event); });
    node.addEventListener("pointerleave", () => hideTip());
    node.addEventListener("focus", () => show());
    node.addEventListener("blur", () => hideTip());
    node.addEventListener("click", () => {
      const go = target();
      if (lastPointer === "touch" && !(pinned && tipOwner === node)) {
        showTip(node, content({ go }), { pin: true });
        return;
      }
      hideTip(true);
      if (go) location.hash = go;
    });
  }

  /* Charts: built once, then updated in place */

  // A chart is one stop for Tab; the arrow keys move between its bars, points or days (the newest first).
  const ACROSS = { ArrowLeft: -1, ArrowRight: 1, Home: "first", End: "last" };
  const WEEKS = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1, Home: "first", End: "last" };
  function rove(root, items, keys) {
    root.addEventListener("keydown", (event) => {
      const list = items(), at = list.indexOf(document.activeElement), by = keys[event.key];
      if (at < 0 || by == null) return;
      event.preventDefault();
      const to = by === "first" ? 0 : by === "last" ? list.length - 1 : Math.max(0, Math.min(list.length - 1, at + by));
      list[at].tabIndex = -1;
      list[to].tabIndex = 0;
      list[to].focus();
    });
  }
  function firstStop(list) {
    list.forEach((node, i) => { node.tabIndex = i === list.length - 1 ? 0 : -1; });
  }

  function niceTicks(max, count = 4) {
    if (!(max > 0)) return [0, 1];
    const raw = max / count, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((value) => value >= raw);
    const ticks = [];
    for (let i = 0; i * step < max + step * 0.999 && ticks.length < 8; i++) ticks.push(+(i * step).toFixed(10));
    return ticks;
  }
  function gridLines(node, ticks, format) {
    const top = ticks[ticks.length - 1] || 1;
    node.replaceChildren(...ticks.map((value, i) => {
      const line = h("div", { class: i === 0 ? "gridline base" : "gridline" }, h("span", { text: format(value) }));
      line.style.bottom = `${(value / top) * 100}%`;
      return line;
    }));
    return top;
  }

  // Stacked columns: spending by model.
  class Bars {
    constructor(root, { format, axis, label, tip: tipFor, go }) {
      Object.assign(this, { root, format, axis, labelFor: label, tipFor, goFor: go, cols: [] });
      this.grid = h("div");
      this.colsNode = h("div", { class: "cols" });
      this.peak = h("span", { class: "peak" });
      this.plot = h("div", { class: "plot" }, this.grid, this.colsNode);
      this.x = h("div", { class: "xaxis", "aria-hidden": "true" });
      root.replaceChildren(this.plot, this.x);
      rove(root, () => this.cols.map((c) => c.col), ACROSS);
    }
    build(n) {
      this.cols = Array.from({ length: n }, (_, i) => {
        const segs = SERIES.map((series) => h("span", { class: `seg ${series.cls}`, hidden: true }));
        const stack = h("span", { class: "stack" }, ...segs);
        const col = h("button", { type: "button", class: "col" }, stack);
        pickable(col, (extra) => this.tipFor(i, extra), () => this.goFor(i));
        return { col, stack, segs };
      });
      this.colsNode.style.gridTemplateColumns = `repeat(${n}, minmax(0, 1fr))`;
      this.x.style.gridTemplateColumns = `repeat(${n}, minmax(0, 1fr))`;
      this.colsNode.replaceChildren(...this.cols.map((c) => c.col));
      firstStop(this.cols.map((c) => c.col));
    }
    update(n, values) {  // values: series key -> per bucket
      if (n !== this.cols.length) this.build(n);
      const totals = Array.from({ length: n }, (_, i) => SERIES.reduce((sum, series) => sum + (values[series.key]?.[i] || 0), 0));
      const max = Math.max(0, ...totals);
      const top = gridLines(this.grid, niceTicks(max), this.axis);
      const peak = totals.indexOf(max);
      this.cols.forEach(({ col, stack, segs }, i) => {
        stack.style.height = `${(totals[i] / top) * 100}%`;
        let last = null;
        SERIES.forEach((series, k) => {
          const value = values[series.key]?.[i] || 0;
          segs[k].hidden = value <= 0;
          segs[k].style.flexBasis = `${totals[i] ? (value / totals[i]) * 100 : 0}%`;
          segs[k].classList.remove("cap");
          if (value > 0) last = segs[k];
        });
        last?.classList.add("cap");
        col.setAttribute("aria-label", `${this.labelFor(i, true)}: ${this.format(totals[i])}`);
        if (i === peak && max > 0) {
          this.peak.textContent = this.format(max);
          this.peak.style.bottom = `calc(${(max / top) * 100}% + 6px)`;
          col.append(this.peak);
        }
      });
      if (!(max > 0)) this.peak.remove();
      const every = Math.ceil(n / (phone.matches ? 5 : 8));
      this.x.replaceChildren(...this.cols.map((_, i) => h("span", { text: i % every === 0 ? this.labelFor(i, false) : "" })));
    }
  }

  // Lines over columns: the month's running total, or reply times. Each column can be hovered, focused or clicked.
  class Lines {
    constructor(root, { series, axis, label, tip: tipFor, go, daily, everyLabel }) {
      Object.assign(this, { root, series, axis, labelFor: label, tipFor, goFor: go, everyLabel, n: 0, active: null });
      this.grid = h("div");
      this.refs = h("div");
      this.svg = s("svg", { class: "lines", viewBox: "0 0 1000 1000", preserveAspectRatio: "none", "aria-hidden": "true" });
      this.area = s("path", { class: "area" });
      this.paths = series.map((item) => s("path", { class: item.cls === "s2" ? "l2" : "l1" }));
      this.projection = s("path", { class: "l1 dash" });
      this.svg.append(this.area, ...this.paths, this.projection);
      this.cross = h("div", { class: "cross", hidden: true });
      this.dots = series.map((item) => h("span", { class: `pt ${item.cls}`, hidden: true }));
      this.end = h("span", { class: "endlabel", hidden: true });
      this.colsNode = h("div", { class: "cols" });
      this.plot = h("div", { class: "plot" }, this.grid, this.refs, this.svg, this.end, this.cross, ...this.dots, this.colsNode);
      this.x = h("div", { class: "xaxis", "aria-hidden": "true" });
      this.daily = daily ? h("div", { class: "days" }) : null;
      root.replaceChildren(this.plot, this.x, ...(daily ? [h("div", { class: "daily" }, h("div", { class: "label", text: daily }), this.daily)] : []));
      this.plot.addEventListener("pointerleave", () => this.mark(null));
      rove(root, () => this.cols, ACROSS);
    }
    build(n) {
      this.n = n;
      this.cols = Array.from({ length: n }, (_, i) => {
        const col = h("button", { type: "button", class: "col" });
        pickable(col, (extra) => this.tipFor(i, extra), () => this.goFor(i));
        col.addEventListener("pointerenter", () => this.mark(i));
        col.addEventListener("focus", () => this.mark(i));
        return col;
      });
      for (const node of [this.colsNode, this.x, this.daily].filter(Boolean)) node.style.gridTemplateColumns = `repeat(${n}, minmax(0, 1fr))`;
      this.colsNode.replaceChildren(...this.cols);
      firstStop(this.cols);
      if (this.daily) this.daily.replaceChildren(...this.cols.map(() => h("i")));
    }
    // data: {values: [per series, per column (null: none)], top?, refs: [{value, label, warn}], projection: [i, from, to, value],
    //        area: bool, daily: per column (null: to come), end: label for the projection's end}
    update(n, data) {
      if (n !== this.n) this.build(n);
      this.data = data;
      const all = [...data.values.flat(), ...(data.refs || []).map((r) => r.value), data.projection?.[3]].filter((v) => v != null);
      const top = gridLines(this.grid, niceTicks(Math.max(data.top || 0, ...all, 0)), this.axis);
      this.top = top;
      const x = (i) => ((i + 0.5) / n) * 1000, y = (v) => 1000 - (v / top) * 1000;
      const path = (values) => values.map((v, i) => v == null ? null : [x(i), y(v)])
        .reduce((d, p, i, list) => p ? `${d}${list[i - 1] ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}` : d, "");
      data.values.forEach((values, k) => this.paths[k].setAttribute("d", path(values)));
      if (data.area) {
        const values = data.values[0], last = values.reduce((at, v, i) => v != null ? i : at, -1);
        this.area.setAttribute("d", last >= 0 ? `${path(values)}L${x(last)},1000L${x(0)},1000Z` : "");
      } else this.area.setAttribute("d", "");
      if (data.projection) {
        const [from, value0, to, value1] = data.projection;
        this.projection.setAttribute("d", `M${x(from)},${y(value0)}L${x(to)},${y(value1)}`);
        this.end.hidden = false;
        this.end.textContent = data.end || "";
        this.end.style.bottom = `${(value1 / top) * 100}%`;
      } else {
        this.projection.setAttribute("d", "");
        this.end.hidden = true;
      }
      this.refs.replaceChildren(...(data.refs || []).map((ref) => {
        const line = h("div", { class: ref.warn ? "refline warn" : "refline" }, h("span", { text: ref.label }));
        line.style.bottom = `${(ref.value / top) * 100}%`;
        return line;
      }));
      const every = Math.ceil(n / (phone.matches ? 5 : 8));
      this.x.replaceChildren(...this.cols.map((_, i) => h("span", { text: this.everyLabel || i % every === 0 ? this.labelFor(i, false) : "" })));
      this.cols.forEach((col, i) => col.setAttribute("aria-label", this.labelFor(i, true)));
      if (this.daily) {
        const days = data.daily, max = Math.max(0, ...days.filter((v) => v != null));
        [...this.daily.children].forEach((bar, i) => {
          bar.classList.toggle("future", days[i] == null);
          bar.style.height = days[i] == null ? "" : `${max ? (days[i] / max) * 100 : 0}%`;
        });
      }
      this.mark(this.active);
    }
    mark(i) {
      this.active = i;
      const on = i != null && i < this.n;
      this.cross.hidden = !on;
      this.cols?.forEach((col, k) => col.classList.toggle("on", k === i));
      if (this.daily) [...this.daily.children].forEach((bar, k) => bar.classList.toggle("on", k === i));
      [...this.x.children].forEach((label, k) => label.classList.toggle("on", k === i));
      this.dots.forEach((dot, k) => {
        const value = on ? this.data.values[k]?.[i] : null;
        dot.hidden = value == null;
        if (value != null) {
          dot.style.left = `${((i + 0.5) / this.n) * 100}%`;
          dot.style.bottom = `${(value / this.top) * 100}%`;
        }
      });
      if (on) this.cross.style.left = `${((i + 0.5) / this.n) * 100}%`;
    }
  }

  // A year of days, in GitHub's order: a column a week, oldest on the left, Sunday at the top, months above.
  class Heat {
    constructor(root, { go }) {
      Object.assign(this, { root, goFor: go, cells: [], start: null });
      rove(root, () => this.cells, WEEKS);
    }
    build(year) {
      const n = year.counts.length, weeks = Math.ceil(n / 7);
      this.root.style.setProperty("--weeks", weeks);
      const nodes = [h("span", { class: "m" }), ...["", "Mon", "", "Wed", "", "Fri", ""].map((w) => h("span", { class: "w", text: w }))];
      let lastMonth = -1, lastLabel = -9;
      this.cells = [];
      for (let w = 0; w < weeks; w++) {
        const first = shiftKey(year.start, w * 7), month = +first.slice(5, 7);
        let label = "";
        if (month !== lastMonth) {
          // A month's name where its first full week starts; not the first column's if the next starts within 3 weeks.
          const next = +shiftKey(year.start, (w + 3) * 7).slice(5, 7);
          if (!(w === 0 && next !== month) && w - lastLabel >= 3) { label = MONTHS[month - 1].slice(0, 3); lastLabel = w; }
          lastMonth = month;
        }
        nodes.push(h("span", { class: "m", text: label }));
        for (let d = 0; d < 7 && w * 7 + d < n; d++) {
          const i = w * 7 + d;
          const cell = h("button", { type: "button" });
          pickable(cell, (extra) => this.tip(i, extra), () => this.goFor(this.key(i)));
          this.cells.push(cell);
          nodes.push(cell);
        }
      }
      this.root.replaceChildren(...nodes);
      firstStop(this.cells);
      this.start = year.start;
      const scroller = this.root.parentElement;  // on a narrow screen, start at this week, as GitHub does
      requestAnimationFrame(() => { scroller.scrollLeft = scroller.scrollWidth; });
    }
    key(i) { return shiftKey(this.start, i); }
    tip(i, extra) {
      const count = this.year.counts[i];
      return tipRows(`${count ? plural(count, "request") : "No requests"} on ${longDay(this.key(i))}`, [], extra);
    }
    update(year, selected) {
      if (year.start !== this.start || year.counts.length !== this.cells.length) this.build(year);
      this.year = year;
      const nonzero = year.counts.filter((c) => c > 0).sort((a, b) => a - b);
      const q = (share) => nonzero[Math.floor(share * (nonzero.length - 1))] || 0;
      const cuts = [q(0.25), q(0.5), q(0.75)];
      this.cells.forEach((cell, i) => {
        const count = year.counts[i];
        const level = !count ? 0 : count <= cuts[0] ? 1 : count <= cuts[1] ? 2 : count <= cuts[2] ? 3 : 4;
        cell.className = level ? `l${level}` : "";
        cell.setAttribute("aria-label", `${count ? plural(count, "request") : "No requests"} on ${longDay(this.key(i))}`);
        cell.setAttribute("aria-pressed", String(this.key(i) === selected));
      });
    }
  }

  // A row of parts as one bar: where a request's time went.
  function track(parts, total) {
    const sum = total || parts.reduce((a, p) => a + p.value, 0) || 1;
    return h("span", { class: "track" }, ...parts.filter((p) => p.value > 0).map((p) => {
      const share = p.value / sum;
      const node = h("i", { class: p.cls, title: `${p.label}: ${secs(p.value)}` });
      node.style.flex = `0 0 calc(${share * 100}% - 2px)`;
      return node;
    }));
  }

  /* Pieces used on several pages */

  function setText(id, text) { const node = document.getElementById(id); if (node) node.textContent = text; }
  function meter(id, share) {
    const node = document.getElementById(id);
    node.style.width = `${Math.min(1, Math.max(0, share || 0)) * 100}%`;
    node.classList.toggle("near", share >= 0.8 && share < 1);
    node.classList.toggle("over", share >= 1);
  }
  function foot(id, text, tone, arrow) {
    const node = document.getElementById(id);
    node.className = `foot${tone ? ` ${tone}` : ""}`;
    node.replaceChildren(...(arrow ? [icon(arrow)] : []), text);
  }
  function item(iconName, title, sub, right, dot) {
    return h("li", {}, h("span", { class: "ic" }, icon(iconName)),
      h("div", { class: "clip" }, h("b", {}, h("span", { text: title }), dot ? h("span", { class: `dot${dot === "off" ? " off" : ""}` }) : null),
        sub ? h("small", { text: sub }) : null),
      right || h("span"));
  }
  const none = (text) => h("li", { class: "none", text });
  function listInto(id, countId, items, render, empty) {
    $(`#${id}`).replaceChildren(...(items.length ? items.map(render) : [none(empty)]));
    if (countId) setText(countId, String(items.length));
  }
  const tag = (text, cls) => h("span", { class: `tag${cls ? ` ${cls}` : ""}`, text });
  const BEFORE = { "24h": "the day before", "7d": "the 7 days before", "30d": "the 30 days before", month: "the same days last month" };
  function change(now, before, { word, upIsGood }) {
    if (!now || !before) return null;
    const ratio = now / before - 1;
    if (Math.abs(ratio) < 0.02) return { text: `About the same as ${BEFORE[state.range]}`, tone: "" };
    const up = ratio > 0;
    return { text: `${Math.round(Math.abs(ratio) * 100)}% ${up ? word[0] : word[1]} than ${BEFORE[state.range]}`,
      tone: upIsGood == null ? "" : up === upIsGood ? "good" : "bad", arrow: upIsGood == null ? null : up ? "up" : "down" };
  }

  // A log entry in words: {icon, title, detail, meta, problem}.
  function summary(e) {
    const device = DEVICE_WORDS[e.device] || e.device || "";
    const short = (text, n = 140) => { const t = String(text || ""); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };
    switch (e.kind) {
      case "device": return { icon: "plug", title: `${device} ${e.event || ""}`, detail: e.tools?.length ? `${e.tools.length} tools` : "" };
      case "job": return { icon: "job", title: `Job ${e.event || ""}`, detail: short(e.task || e.title || ""),
        meta: [e.minutes != null ? `${e.minutes} min` : null, e.cost ? cost(e.cost) : null].filter(Boolean).join(" · ") };
      case "routine": return { icon: "routine", title: `Routine ${e.event || ""}`, detail: e.name || e.why || e.repeat || "" };
      case "reminder": return { icon: "bell", title: `Reminder ${e.event || ""}`, detail: short(e.text || ""), meta: e.spoken_on ? `on the ${DEVICE_WORDS[e.spoken_on] || e.spoken_on}` : "" };
      case "watcher": return { icon: "eye", title: `Watcher ${e.event || ""}`, detail: short(e.what || e.text || "") };
      case "message": return { icon: "message", title: `Message ${e.event || ""}`, detail: short(e.from || e.text || "") };
      case "file": return { icon: "file", title: `File ${e.event || ""}`, detail: `${e.name || e.id || ""}${e.to ? ` → ${e.to}` : ""}`, meta: e.size ? bytes(e.size) : "" };
      case "convert": return { icon: "file", title: "Converted", detail: `${e.name || ""} to ${e.to || ""}`, meta: e.seconds != null ? secs(e.seconds) : "" };
      case "pdf": return { icon: "file", title: "PDF", detail: short(e.action || e.name || "") };
      case "voice": return { icon: "voice", title: "Heard, not answered", detail: device, meta: e.voice_cost ? cost(e.voice_cost) : "" };
      case "warm": return { icon: "check", title: `${FAMILY_WORDS[FAMILY(e.model)] || e.model}'s cache kept warm`,
        detail: e.tokens?.cache_creation_input_tokens ? "written again" : "", meta: e.cost ? cost(e.cost) : "" };
      case "chat": return { icon: "message", title: `Chat ${e.event || ""}`, detail: device };
      case "keys": return { icon: "key", title: `Keys ${e.event || ""}`, detail: (e.keys || []).join(", ") };
      case "queued": return { icon: "job", title: `Kept for the ${device}`, detail: short(e.text) };
      case "queue_skipped": return { icon: "job", title: "Skipped what was kept", detail: short(e.text) };
      case "hand_over": return { icon: "alert", title: "Handed to Claude", detail: short(e.reason), problem: true };
      case "cheaper_model":
      case "stronger_model": return { icon: "alert", title: `${FAMILY_WORDS[FAMILY(e.model)] || e.model} → ${FAMILY_WORDS[FAMILY(e.switched_to)] || e.switched_to}`, detail: short(e.reason), problem: true };
      case "model_rest": return { icon: "alert", title: `${e.model} rests ${e.seconds} s`, detail: short(e.error, 120), problem: true };
      case "model_error": return { icon: "alert", title: `${e.model} failed`, detail: short(e.error, 160), problem: true };
      case "mcp_error": return { icon: "alert", title: "MCP server failed", detail: short(e.error || e.server, 160), problem: true };
      case "error": return { icon: "alert", title: `Error${e.step ? ` in ${e.step}` : ""}`, detail: short(e.error, 180), meta: device, problem: true };
      case "tool": return { icon: "check", title: e.tool, detail: e.declined ? "you said no" : e.ok === false ? "failed" : "", meta: device };
      default: return { icon: "device", title: e.kind || "Entry", detail: "" };
    }
  }

  // A request in a list: when, where, what was asked and answered, what it was and cost.
  function requestTags(row) {
    const e = row.entry;
    const tags = [];
    if (e.cancelled) return [tag("Cancelled", "no")];
    const family = FAMILY(e.model);
    tags.push(tag(FAMILY_WORDS[family] || family));
    if (row.command && !["Conversation", "Quick, no tools"].includes(row.command)) tags.push(tag(row.command));
    if ((row.tools || []).some((t) => t.declined)) tags.push(tag("You said no", "no"));
    return tags;
  }
  const requestCost = (e) => (e.cost || 0) + (e.voice_cost || 0);
  function requestLine(row, { withDay } = {}) {
    const e = row.entry;
    return [
      h("span", { class: "when", text: withDay && dayKey(row.time) !== today() ? `${dayWords(dayKey(row.time)).slice(0, 3)} ${clock(row.time)}` : clock(row.time) }),
      icon(deviceIcon(e.device), "dev"),
      h("span", { class: "what" }, h("b", { text: e.text || "(spoken)" }), h("span", { text: e.cancelled ? "Cancelled" : e.reply || "" })),
      h("span", { class: "meta" }, h("span", { class: "tags" }, ...requestTags(row)),
        h("span", { text: [cost(requestCost(e)), e.seconds != null ? secs(e.seconds) : null].filter(Boolean).join(" · ") })),
    ];
  }

  /* Home */

  const home = {
    enter() {
      if (!home.chart) {
        home.chart = new Bars($("#home-chart"), {
          format: cost, axis: axisMoney,
          label: (i, long) => bucketLabel(i, long),
          tip: (i, extra) => {
            const stats = state.stats, rows = SERIES.map((series) => ({ cls: series.cls, label: series.label, value: home.values[series.key]?.[i] || 0 }))
              .filter((row) => row.value > 0).map((row) => ({ ...row, value: cost(row.value) }));
            const total = SERIES.reduce((sum, series) => sum + (home.values[series.key]?.[i] || 0), 0);
            const requests = Object.values(stats.requests).reduce((sum, list) => sum + (list[i] || 0), 0);
            return tipRows(bucketLabel(i, true), [...rows, { label: "Requests", value: whole(requests) }], { total: { label: "Total", value: cost(total) }, ...extra });
          },
          go: (i) => `#activity?day=${dayKey(state.stats.buckets[i])}`,
        });
      }
      if (state.stats) home.renderStats();
      if (state.live) home.renderLive();
      if (Date.now() - state.at.stats > 20000) loadStats();
      home.latest();
    },
    renderStats() {
      const stats = state.stats;
      const values = {};
      for (const [source, list] of Object.entries(stats.spend)) {
        const series = SERIES_OF[source] || SERIES_OF.other;
        values[series.key] = (values[series.key] || list.map(() => 0)).map((v, i) => v + (list[i] || 0));
      }
      home.values = values;
      home.chart.update(stats.buckets.length, values);
      const shown = SERIES.filter((series) => (values[series.key] || []).some((v) => v > 0));
      $("#home-legend").replaceChildren(...shown.map((series) => h("span", {}, h("i", { class: `key k-${series.cls}` }), `${series.label} `,
        h("b", { text: cost(values[series.key].reduce((a, b) => a + b, 0)) }))),
        h("span", { class: "total" }, "Total ", h("b", { text: cost(stats.spent) })));

      setText("home-requests", whole(stats.request_count));
      const more = change(stats.request_count, stats.before.request_count, { word: ["more", "fewer"] });
      foot("home-requests-delta", more ? more.text : "", "", null);
      setText("home-typical", secs(stats.seconds.all_median));
      const faster = change(stats.seconds.all_median, stats.before.median_seconds, { word: ["slower", "faster"], upIsGood: false });
      foot("home-typical-delta", faster ? faster.text : "", faster?.tone, faster?.arrow);
      const switches = ["hand_over", "cheaper_model", "stronger_model"].reduce((a, k) => a + (stats.issues[k] || 0), 0);
      const errors = Object.values(stats.issues).reduce((a, b) => a + b, 0) - switches;
      setText("home-problems", whole(switches + errors));
      const sub = $("#home-problems-sub");
      const words = [switches ? `${whole(switches)} model switch${switches === 1 ? "" : "es"}` : null, errors ? plural(errors, "error") : null];
      sub.replaceChildren(h("span", {}, words.filter(Boolean).join(", ") || "None",
        switches + errors ? [" · ", h("a", { href: "#activity?kinds=problems", text: "See them" })] : []));
    },
    renderLive() {
      const live = state.live, month = live.month;
      setText("home-spent", money(month.spent));
      setText("home-budget", `of ${money(month.budget)}`);
      meter("home-meter", month.budget ? month.spent / month.budget : 0);
      const projected = month.projected;
      foot("home-track", month.spent >= month.budget ? "Over the budget" : projected != null ? `On track for ${money(projected)}` : "",
        month.spent >= month.budget ? "bad" : "");

      const host = live.host || {};
      const memory = host.memory ? 1 - (host.memory.available ?? host.memory.total) / host.memory.total : null;
      $("#home-now").replaceChildren(
        item("natro", "Natro", `Running · up ${duration(since(live.service.started))}`, null, "on"),
        ...live.devices.map((d) => item(deviceIcon(d.name), DEVICE_WORDS[d.name] || d.label,
          d.online ? `Connected since ${whenWords(d.since).replace("Today, ", "")}` : d.seen ? `Last seen ${whenWords(d.seen)}` : "Not connected",
          null, d.online ? "on" : "off")),
        item("server", "VPS", host.system || host.name || ""));
      $("#home-host").replaceChildren(
        miniMeter("VPS CPU", host.cpu != null ? percent(host.cpu) : "—", host.cpu),
        miniMeter("Memory", memory != null ? percent(memory) : "—", memory));

      const next = [
        ...live.jobs.filter((j) => j.state === "running").map((j) => ({ at: "", node: item("job", j.title, `Running for ${j.minutes} min`, h("span", { class: "chip good", text: "Running" })) })),
        ...live.reminders.filter((r) => r.next).map((r) => ({ at: r.next, node: item("bell", r.text, whenWords(r.next)) })),
        ...live.routines.filter((r) => r.next).map((r) => ({ at: r.next, node: item("routine", r.name || r.task, `${capital(r.repeat)} · next ${lowerWhen(r.next)}`) })),
      ].sort((a, b) => (a.at || "").localeCompare(b.at || "")).slice(0, 4);
      const watching = live.watchers.slice(0, Math.max(0, 5 - next.length)).map((w) => item("eye", capital(w.text), "Watching"));
      $("#home-next").replaceChildren(...(next.length + watching.length ? [...next.map((x) => x.node), ...watching] : [none("Nothing scheduled")]));
    },
    async latest(force) {
      if (!force && Date.now() - state.at.latest < 10000) return;
      state.at.latest = Date.now();
      let data = await fetched(`/logs?${query({ kinds: "request", range: "24h", limit: 6 })}`);
      if (data && data.entries.length < 3) data = await fetched(`/logs?${query({ kinds: "request", range: "7d", limit: 6 })}`);
      if (!data) return;
      $("#home-latest").replaceChildren(...(data.entries.length ? data.entries.map((row) =>
        h("li", {}, h("a", { href: `#activity?day=${dayKey(row.time)}&open=${row.id}` }, ...requestLine(row, { withDay: true })))) : [h("li", { class: "empty", text: "No requests yet." })]));
    },
  };
  function bucketLabel(i, long) {
    const stats = state.stats, iso = stats.buckets[i];
    if (stats.unit === "hour") return long ? `${dayWords(dayKey(iso))}, ${clock(iso)}` : clock(iso);
    if (long) return longDay(dayKey(iso));
    if (stats.buckets.length > 8 || phone.matches) return String(+iso.slice(8, 10));
    return dayKey(iso) === today() ? "Today" : shortDay(dayKey(iso));
  }
  function miniMeter(name, value, share) {
    const bar = h("span");
    bar.style.width = `${Math.min(1, share || 0) * 100}%`;
    return h("div", {}, h("span", {}, name, h("b", { text: value })), h("div", { class: "meter" }, bar));
  }
  const capital = (text) => String(text || "").replace(/^./, (c) => c.toUpperCase());

  /* Activity */

  const KIND_GROUPS = [
    { key: "all", label: "All", kinds: null },
    { key: "requests", label: "Requests", kinds: ["request"] },
    { key: "devices", label: "Devices", kinds: ["device", "chat", "keys", "queued", "queue_skipped"] },
    { key: "jobs", label: "Jobs", kinds: ["job", "routine"] },
    { key: "reminders", label: "Reminders", kinds: ["reminder", "watcher", "message"] },
    { key: "files", label: "Files", kinds: ["file", "convert", "pdf"] },
    { key: "problems", label: "Problems", kinds: ["error", "model_error", "model_rest", "mcp_error", "hand_over", "cheaper_model", "stronger_model"] },
  ];
  // Shown inside their request instead (dashboard.py's attachments).
  const NESTED = ["tool", "timing", "spoken"];

  const activity = {
    rows: [], group: "all", q: "", device: "", live: saved("live", "1") === "1", day: null, open: null, more: false, loading: false,
    enter(params) {
      if (!activity.heat) activity.heat = new Heat($("#heat"), { go: (key) => `#activity?day=${key}` });
      const day = params.get("day"), kinds = params.get("kinds");
      const changed = day !== activity.day || (kinds && kinds !== activity.group);
      activity.day = day;
      if (kinds && KIND_GROUPS.some((g) => g.key === kinds)) activity.group = kinds;
      if (params.get("open")) activity.open = activity.reveal = params.get("open");
      if (state.year) {
        activity.heat.update(state.year, activity.day);
        renderYearTitle();
      }
      if (Date.now() - state.at.year > 60000) loadYear();
      $("#log-live").setAttribute("aria-pressed", String(activity.live));
      $("#log-date").value = activity.day || "";
      $("#log-date").max = today();
      if (changed || !activity.rows.length) activity.load();
      else activity.render();
    },
    params(extra) {
      const group = KIND_GROUPS.find((g) => g.key === activity.group);
      return query({
        ...(activity.day ? { day: activity.day } : { range: "30d" }),
        kinds: group.kinds ? group.kinds.join(",") : NESTED.map((k) => `-${k}`).join(","),
        q: activity.q, device: activity.device, ...extra,
      });
    },
    async load() {
      activity.loading = true;
      const asked = activity.params();
      const data = await fetched(`/logs?${asked}`);
      activity.loading = false;
      if (asked !== activity.params()) return;  // the filters changed meanwhile
      if (!data) {
        $("#log-list").replaceChildren(h("div", { class: "empty", text: "Can't reach Natro." }));
        return;
      }
      activity.rows = data.entries;
      activity.more = data.more;
      activity.counts = data.kinds;
      state.at.tail = Date.now();
      activity.render();
    },
    async older() {
      const last = activity.rows[activity.rows.length - 1];
      if (!last) return;
      const data = await fetched(`/logs?${activity.params({ before: last.id })}`);
      if (!data) return;
      activity.rows.push(...data.entries);
      activity.more = data.more;
      activity.render();
    },
    async tail() {
      if (!activity.live || activity.loading || !activity.rows.length || (activity.day && activity.day !== today())) return;
      state.at.tail = Date.now();
      const data = await fetched(`/logs?${activity.params({ after: activity.rows[0].id })}`);
      if (data?.entries.length) {
        activity.rows.unshift(...data.entries);
        activity.counts = data.kinds;
        activity.render();
      }
    },
    render() {
      const counts = Object.fromEntries(activity.counts || []);
      const total = Object.entries(counts).filter(([k]) => !NESTED.includes(k)).reduce((a, [, v]) => a + v, 0);
      $("#log-kinds").replaceChildren(...KIND_GROUPS.map((g) => {
        const n = g.kinds ? g.kinds.reduce((a, k) => a + (counts[k] || 0), 0) : total;
        return h("button", { type: "button", "aria-pressed": String(g.key === activity.group), onclick: () => {
          activity.group = g.key;
          activity.load();
        } }, g.label, h("small", { text: compact(n) }));
      }));
      const banner = $("#log-day");
      banner.hidden = !activity.day;
      if (activity.day) {
        banner.replaceChildren(h("span", {}, "Showing ", h("b", { text: longDay(activity.day) })),
          h("button", { type: "button", class: "btn-soft", text: "All days", onclick: () => { location.hash = "#activity"; } }));
      }
      const list = $("#log-list");
      if (!activity.rows.length) {
        list.replaceChildren(h("div", { class: "empty", text: activity.q ? "Nothing matches." : "Nothing here yet." }));
      } else {
        const nodes = [];
        let current = null;
        for (const row of activity.rows) {
          const key = dayKey(row.time);
          if (key !== current) {
            current = key;
            const count = state.year ? yearCount(key) : null;
            nodes.push(h("div", { class: "day-head" }, h("b", { text: `${dayWords(key) === "Today" || dayWords(key) === "Yesterday" ? `${dayWords(key)}, ` : ""}${longDay(key)}` }),
              count != null ? h("span", { text: plural(count, "request") }) : null));
          }
          nodes.push(row.entry.kind === "request" ? activity.request(row) : activity.event(row));
        }
        list.replaceChildren(...nodes);
      }
      $("#log-more").hidden = !activity.more;
      if (activity.reveal && $(".req.open")) {  // opened from a link: bring it into view, once
        $(".req.open").scrollIntoView({ block: "center" });
        activity.reveal = null;
      }
    },
    request(row) {
      const open = row.id === activity.open;
      const button = h("button", { type: "button", "aria-expanded": String(open), onclick: () => {
        activity.open = open ? null : row.id;
        activity.render();
      } }, ...requestLine(row));
      return h("div", { class: open ? "req open" : "req" }, button, open ? activity.detail(row) : null);
    },
    detail(row) {
      const e = row.entry, tokens = e.tokens || {};
      const parts = row.stages ? grouped(row.stages) : null;
      const copy = h("button", { type: "button", class: "btn-soft", text: "Copy as JSON", onclick: async () => {
        copy.textContent = await copyText(JSON.stringify(e, null, 2)) ? "Copied" : "Can't copy here";
        setTimeout(() => { copy.textContent = "Copy as JSON"; }, 1400);
      } });
      const fresh = (tokens.input_tokens || 0) + (tokens.cache_creation_input_tokens || 0);
      return h("dl", { class: "detail" },
        h("dt", { text: "Request" }), h("dd", { text: e.text || "(spoken)" }),
        h("dt", { text: "Reply" }), h("dd", { text: e.cancelled ? "Cancelled" : e.reply || "—" }),
        h("dt", { text: "Model" }), h("dd", { text: e.model || "—" }),
        h("dt", { text: "Tools" }), h("dd", {}, ...((row.tools || []).length ? row.tools.map((t) => h("div", { class: "tool" },
          h("code", { text: t.tool }), t.args ? h("span", { class: "args", text: argsWords(t.args) }) : null,
          h("span", { class: t.declined || t.ok === false ? "ok no" : "ok", text: t.declined ? "you said no" : t.ok === false ? "failed" : "worked" }))) : ["None"])),
        h("dt", { text: "Time" }), h("dd", {}, parts ? [track(parts), h("div", { class: "times" },
          ...parts.filter((p) => p.value > 0).map((p) => h("span", {}, h("i", { class: `key k-${p.cls}` }), `${p.label} `, h("b", { text: secs(p.value) }))))]
          : secs(e.seconds)),
        h("dt", { text: "Cost" }), h("dd", { text: [cost(requestCost(e)), e.voice_cost ? `${cost(e.cost || 0)} model + ${cost(e.voice_cost)} voice` : null,
          fresh || tokens.cache_read_input_tokens ? `${compact(fresh + (tokens.cache_read_input_tokens || 0))} tokens in (${compact(tokens.cache_read_input_tokens || 0)} from the cache), ${compact(tokens.output_tokens || 0)} out` : null]
          .filter(Boolean).join(" · ") }),
        h("dt"), h("dd", {}, copy));
    },
    event(row) {
      const info = summary(row.entry);
      return h("div", { class: info.problem ? "event problem" : "event" },
        h("span", { class: "when", text: clock(row.time) }), icon(info.icon),
        h("span", { class: "clip" }, h("b", { text: capital(info.title) }), info.detail ? ` · ${info.detail}` : ""),
        h("span", { class: "meta", text: info.meta || "" }));
    },
  };
  // The clipboard API needs HTTPS (or localhost); the dashboard is plain HTTP inside the tailnet, so fall back.
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const area = h("textarea", { readonly: true, "aria-hidden": "true" });
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      const done = document.execCommand("copy");
      area.remove();
      return done;
    }
  }
  function argsWords(args) {
    if (!args || typeof args !== "object") return String(args || "");
    const text = Object.entries(args).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
    return text.length > 140 ? `${text.slice(0, 139)}…` : text;
  }
  function yearCount(key) {
    const year = state.year;
    const i = (keyDate(key) - keyDate(year.start)) / 864e5;
    return i >= 0 && i < year.counts.length ? year.counts[i] : null;
  }

  let typing;
  $("#log-q").addEventListener("input", (event) => {
    clearTimeout(typing);
    typing = setTimeout(() => { activity.q = event.target.value.trim(); activity.load(); }, 280);
  });
  $("#log-device").addEventListener("change", (event) => { activity.device = event.target.value; activity.load(); });
  $("#log-live").addEventListener("click", (event) => {
    activity.live = !activity.live;
    save("live", activity.live ? "1" : "0");
    event.currentTarget.setAttribute("aria-pressed", String(activity.live));
    if (activity.live) activity.tail();
  });
  $("#log-more").addEventListener("click", () => activity.older());
  $("#log-date").addEventListener("change", (event) => {
    location.hash = event.target.value ? `#activity?day=${event.target.value}` : "#activity";
  });

  /* Billing */

  const billing = {
    ledger: [], q: "", device: "", more: false,
    enter(params) {
      const month = params.get("month");
      if (!billing.chart) {
        billing.chart = new Lines($("#bill-chart"), {
          series: [{ cls: "s1" }], axis: axisMoney, daily: "Each day", everyLabel: true,
          label: (i, long) => long ? longDay(billing.key(i)) : i % 7 === 0 ? String(i + 1) : "",
          tip: (i, extra) => {
            const b = state.bill, future = b.current && i > b.today;
            if (future) return tipRows(longDay(billing.key(i)), [{ label: "To come", value: "" }]);
            return tipRows(longDay(billing.key(i)), [
              { label: "That day", value: cost(billing.daily[i]) },
              { label: "Month so far", value: money(billing.running[i]) },
              { label: "Requests", value: whole(billing.requests[i]) }], extra);
          },
          go: (i) => state.bill.current && i > state.bill.today ? null : `#activity?day=${billing.key(i)}`,
        });
      }
      const wanted = month || null;
      if (wanted !== state.billMonth || !state.bill) {
        state.billMonth = wanted;
        loadBill();
      } else {
        billing.render();
        billing.load();
        if (Date.now() - state.at.bill > 60000) loadBill();
      }
    },
    key(i) { return `${state.bill.month}-${String(i + 1).padStart(2, "0")}`; },
    render() {
      const b = state.bill, days = b.buckets.length;
      billing.days = days;
      const daily = Array.from({ length: days }, (_, i) => Object.values(b.spend).reduce((sum, list) => sum + (list[i] || 0), 0));
      const requests = Array.from({ length: days }, (_, i) => Object.values(b.requests).reduce((sum, list) => sum + (list[i] || 0), 0));
      const last = b.current ? b.today : days - 1;
      let total = 0;
      const running = daily.map((v, i) => i <= last ? (total += v) : null);
      Object.assign(billing, { daily, requests, running });
      const spent = b.spent, budget = b.budget, name = MONTHS[+b.month.slice(5) - 1];

      setText("month-name", monthName(b.month));
      const at = b.months.indexOf(b.month);
      $("#month-prev").disabled = at <= 0;
      $("#month-next").disabled = b.current || at < 0 || at >= b.months.length - 1;
      setText("subtitle", `Budget ${money(budget)} a month`);

      setText("bill-spent-label", `Spent in ${name}`);
      setText("bill-spent", money(spent));
      setText("bill-budget", `of ${money(budget)}`);
      meter("bill-meter", budget ? spent / budget : 0);
      foot("bill-left", spent > budget ? `${money(spent - budget)} over` : `${money(budget - spent)} left`, spent > budget ? "bad" : "");
      const elapsed = b.current ? Math.max(0.5, b.today + fractionOfDay()) : days;
      const projected = b.current ? (spent / elapsed) * days : null;
      $("#bill-key-track").hidden = !b.current;
      if (b.current) {
        setText("bill-track-label", "On track for");
        setText("bill-track", money(projected));
        const warnAt = 0.8 * budget;
        if (spent >= warnAt) foot("bill-warning", spent >= budget ? "Over the budget" : "Past the 80% warning", "bad", null);
        else if (projected >= warnAt) {
          const day = Math.min(days - 1, Math.ceil((warnAt - spent) / (spent / elapsed) + b.today));
          foot("bill-warning", `80% warning around ${dateShort(billing.key(day))}`, "", "up");
        } else foot("bill-warning", "Under the 80% warning", "good", null);
        setText("bill-chart-title", `${name} so far`);
      } else {
        const busiest = daily.indexOf(Math.max(...daily));
        setText("bill-track-label", "Busiest day");
        setText("bill-track", money(daily[busiest] || 0));
        foot("bill-warning", daily[busiest] ? longDay(billing.key(busiest)) : "", "", null);
        setText("bill-chart-title", name);
      }
      setText("bill-each", b.request_count ? cost(spent / b.request_count) : "—");
      setText("bill-count", plural(b.request_count, "request"));
      setText("bill-free", whole(b.quick));

      billing.chart.update(days, {
        values: [running], area: true, top: budget,
        refs: [{ value: budget, label: `Budget ${money(budget)}` }, { value: 0.8 * budget, label: `80% warning · ${money(0.8 * budget)}`, warn: true }],
        projection: b.current && last < days - 1 ? [last, running[last], days - 1, projected] : null,
        end: b.current ? money(projected) : "",
        daily: daily.map((v, i) => i <= last ? v : null),
      });

      billing.models();
      // As many commands as the card beside it has room for, the rest a click away: no empty space under By model.
      const room = Math.max(6, billing.modelRows + 2), commands = billing.allCommands ? b.commands : b.commands.slice(0, room);
      const all = $("#bill-commands-all");
      all.hidden = b.commands.length <= room;
      all.textContent = billing.allCommands ? "Show fewer" : `Show all ${b.commands.length}`;
      $("#bill-commands").replaceChildren(
        h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Command" }), h("th", { scope: "col", class: "num", text: "Times" }),
          h("th", { scope: "col", class: "num", text: "Each" }), h("th", { scope: "col", class: "num", text: "Cost" }))),
        h("tbody", {}, ...(commands.length ? commands.map((c) => h("tr", {}, h("th", { scope: "row", text: c.name }),
          h("td", { class: "num", text: whole(c.times) }), h("td", { class: "num", text: cost(c.times ? c.cost / c.times : 0) }),
          h("td", { class: "num cost", text: cost(c.cost) }))) : [h("tr", {}, h("td", { colspan: "4", class: "empty", text: "Nothing this month." }))])));
      $("#ledger-csv").href = `${API}/month.csv?month=${b.month}`;
      $("#ledger-csv").setAttribute("download", `natro-${b.month}.csv`);
    },
    models() {
      const b = state.bill, spent = b.spent || 1;
      const families = {};
      for (const m of b.models) {
        const f = families[m.family] || (families[m.family] = { key: m.family, requests: 0, cost: 0 });
        f.requests += m.requests;
        f.cost += m.cost;
      }
      for (const source of ["voice", "jobs"]) {
        const sum = (b.spend[source] || []).reduce((a, v) => a + v, 0);
        if (sum) families[source] = { key: source, requests: null, cost: sum };
      }
      const rows = Object.values(families).sort((a, b) => b.cost - a.cost || (b.requests || 0) - (a.requests || 0));
      billing.modelRows = rows.length;
      $("#bill-models").replaceChildren(
        h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Model" }), h("th", { scope: "col", class: "num", text: "Requests" }),
          h("th", { scope: "col", class: "share", text: "Share" }), h("th", { scope: "col", class: "num", text: "Cost" }))),
        h("tbody", {}, ...rows.map((row) => {
          const series = SERIES_OF[row.key] || SERIES_OF.other, bar = h("span");
          bar.style.width = `${Math.min(1, row.cost / spent) * 100}%`;
          return h("tr", {}, h("th", { scope: "row" }, h("i", { class: `key k-${series.cls}` }), FAMILY_WORDS[row.key] || row.key),
            h("td", { class: "num", text: row.requests == null ? "" : whole(row.requests) }),
            h("td", { class: "share" }, h("span", { class: "sharebar" }, h("span", { class: "meter" }, bar), h("small", { text: percent(row.cost / spent) }))),
            h("td", { class: "num cost", text: cost(row.cost) }));
        })));
      const t = b.token_totals || {};
      const tokensIn = (t.input_tokens || 0) + (t.cache_creation_input_tokens || 0) + (t.cache_read_input_tokens || 0);
      $("#bill-tokens").replaceChildren(
        h("div", {}, h("dt", { text: "Tokens in" }), h("dd", { text: compact(tokensIn) })),
        h("div", {}, h("dt", { text: "From the cache" }), h("dd", { text: percent(b.cache_share) })),
        h("div", {}, h("dt", { text: "Tokens out" }), h("dd", { text: compact(t.output_tokens || 0) })));
    },
    params(extra) {
      return query({ month: state.bill.month, kinds: "request", q: billing.q, device: billing.device, ...extra });
    },
    async load() {
      const asked = billing.params();
      const data = await fetched(`/logs?${asked}`);
      if (!data || asked !== billing.params()) return;
      billing.ledgerMonth = state.bill.month;
      billing.ledger = data.entries;
      billing.more = data.more;
      billing.renderLedger();
    },
    async older() {
      const last = billing.ledger[billing.ledger.length - 1];
      if (!last) return;
      const data = await fetched(`/logs?${billing.params({ before: last.id })}`);
      if (!data) return;
      billing.ledger.push(...data.entries);
      billing.more = data.more;
      billing.renderLedger();
    },
    renderLedger() {
      const nodes = [];
      let current = null;
      for (const row of billing.ledger) {
        const key = dayKey(row.time), e = row.entry;
        if (key !== current) {
          current = key;
          const i = +key.slice(8, 10) - 1;
          nodes.push(h("div", { class: "day-head" }, h("b", { text: longDay(key) }),
            h("span", { text: `${plural(billing.requests?.[i] || 0, "request")} · ${cost(billing.daily?.[i] || 0)}` })));
        }
        const family = FAMILY(e.model), series = SERIES_OF[family] || SERIES_OF.other, tokens = e.tokens || {};
        const tokensIn = (tokens.input_tokens || 0) + (tokens.cache_creation_input_tokens || 0) + (tokens.cache_read_input_tokens || 0);
        nodes.push(h("a", { class: "line", href: `#activity?day=${key}&open=${row.id}` },
          h("span", { class: "when", text: clock(row.time) }), icon(deviceIcon(e.device), "dev"),
          h("span", { class: "clip", text: e.text || "(spoken)" }),
          h("span", { class: "model" }, h("i", { class: `key k-${series.cls}` }), FAMILY_WORDS[family] || family),
          h("span", { class: "clip", text: row.command || "" }),
          h("span", { class: "num sub", text: tokensIn ? `${compact(tokensIn)} / ${compact(tokens.output_tokens || 0)}` : "–" }),
          h("span", { class: "num cost", text: cost(requestCost(e)) })));
      }
      $("#ledger").replaceChildren(...(nodes.length ? nodes : [h("div", { class: "empty", text: billing.q ? "Nothing matches." : "No requests this month." })]));
      $("#ledger-more").hidden = !billing.more;
    },
  };
  const fractionOfDay = () => { const iso = state.live?.now; return iso ? (+iso.slice(11, 13) + +iso.slice(14, 16) / 60) / 24 : 0.5; };
  $("#month-prev").addEventListener("click", () => step(-1));
  $("#month-next").addEventListener("click", () => step(1));
  function step(by) {
    const b = state.bill, at = b.months.indexOf(b.month), next = b.months[at + by];
    if (next) location.hash = `#billing?month=${next}`;
  }
  let ledgerTyping;
  $("#ledger-q").addEventListener("input", (event) => {
    clearTimeout(ledgerTyping);
    ledgerTyping = setTimeout(() => { billing.q = event.target.value.trim(); billing.load(); }, 280);
  });
  $("#ledger-device").addEventListener("change", (event) => { billing.device = event.target.value; billing.load(); });
  $("#ledger-more").addEventListener("click", () => billing.older());
  $("#bill-commands-all").addEventListener("click", () => {
    billing.allCommands = !billing.allCommands;
    billing.render();
  });

  /* Speed */

  const PART_WORDS = {
    recognize: "Recognising speech", georgian: "Trying again in Georgian", translate_first: "Translating, first word",
    translate: "Translating", wait: "Waiting its turn", first_token: "Model, first word", writing: "Model, writing",
    tools: "Tools", speaking: "Device starts speaking", other: "Other", waited: "In all",
  };
  const GROUP_WORDS = { spoken: "Spoken", typed: "Typed", quick: "No model" };
  const speed = {
    enter() {
      if (!speed.chart) {
        speed.chart = new Lines($("#speed-chart"), {
          series: [{ cls: "s1" }, { cls: "s2" }], axis: (v) => v === 0 ? "0" : `${+v.toFixed(1)} s`,
          label: (i, long) => bucketLabel(i, long),
          tip: (i, extra) => {
            const stats = state.stats, requests = Object.values(stats.requests).reduce((sum, list) => sum + (list[i] || 0), 0);
            return tipRows(bucketLabel(i, true), [
              { cls: "s1", label: "Typical", value: secs(stats.seconds.median[i]) },
              { cls: "s2", label: "Slowest 5%", value: secs(stats.seconds.p95[i]) },
              { label: "Requests", value: whole(requests) }], extra);
          },
          go: (i) => `#activity?day=${dayKey(state.stats.buckets[i])}`,
        });
        $("#speed-legend").replaceChildren(...STAGE_GROUPS.map((g) => h("span", {}, h("i", { class: `key k-${g.cls}` }), g.label)));
      }
      if (state.stats) speed.render();
      if (Date.now() - state.at.stats > 20000) loadStats();
    },
    render() {
      const stats = state.stats, timing = stats.timing, groups = Object.fromEntries(timing.groups.map((g) => [g.key, g]));
      const faster = change(stats.seconds.all_median, stats.before.median_seconds, { word: ["slower", "faster"], upIsGood: false });
      const tiles = [
        ["Typical reply", secs(stats.seconds.all_median), faster?.text || "", faster?.tone, faster?.arrow],
        ["Slowest 5%", secs(stats.seconds.all_p95), "One in twenty takes longer", "", null],
        ["Spoken", secs(groups.spoken?.median), groups.spoken ? `Typical, of ${plural(groups.spoken.count, "request")}` : "None in this range", "", null],
        ["Typed", secs(groups.typed?.median), groups.typed ? `Typical, of ${plural(groups.typed.count, "request")}` : "None in this range", "", null],
      ];
      $("#speed-tiles").replaceChildren(...tiles.map(([name, value, note, tone, arrow]) => h("article", { class: "card tile" },
        h("h2", { text: name }), h("div", { class: "figure" }, h("b", { text: value })),
        h("p", { class: `foot${tone ? ` ${tone}` : ""}` }, ...(arrow ? [icon(arrow)] : []), note))));

      speed.chart.update(stats.buckets.length, { values: [stats.seconds.median, stats.seconds.p95] });

      const first = timing.first_token;
      $("#speed-pairs").replaceChildren(...[
        ["Model's first word", secs(first.warm), first.warm_count ? `${plural(first.warm_count, "call")} with a warm cache` : ""],
        ["After a cold start", secs(first.cold), first.cold_count ? plural(first.cold_count, "call") : "None"],
        ["Speech tried again in Georgian", percent(timing.georgian_share), ""],
        ["PC starts speaking", secs(timing.speaking.pc), ""],
        ["Phone starts speaking", secs(timing.speaking.phone), ""],
      ].map(([name, value, note]) => h("div", {}, h("dt", { text: name }), h("dd", {}, value, note ? h("small", { text: note }) : null))));

      const longest = Math.max(1e-6, ...timing.groups.map((g) => Object.values(g.stages).reduce((a, b) => a + b, 0)));
      $("#speed-groups").replaceChildren(...(timing.groups.length ? timing.groups.map((g) => {
        const parts = grouped(g.stages), sum = parts.reduce((a, p) => a + p.value, 0);
        const bar = track(parts, sum);
        bar.style.width = `${(sum / longest) * 100}%`;
        return h("div", { class: "hbar" }, h("b", { text: GROUP_WORDS[g.key] || g.key }),
          h("div", {}, bar, h("div", { class: "times" }, ...parts.filter((p) => p.value > 0).map((p) =>
            h("span", { title: p.label }, h("i", { class: `key k-${p.cls}` }), h("span", { class: "sr-only", text: `${p.label} ` }), secs(p.value))))),
          h("span", { text: secs(sum) }));
      }) : [h("div", { class: "empty", text: "No timed requests in this range yet." })]));

      const parts = timing.parts.filter((p) => p.count);
      $("#speed-parts").replaceChildren(
        h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Part" }), h("th", { scope: "col", class: "num", text: "Requests" }),
          h("th", { scope: "col", class: "num", text: "Typical" }), h("th", { scope: "col", class: "num", text: "Slowest 5%" }))),
        h("tbody", {}, ...parts.map((p) => h("tr", {}, h("th", { scope: "row", text: PART_WORDS[p.key] || p.key }),
          h("td", { class: "num", text: whole(p.count) }), h("td", { class: "num", text: secs(p.median) }), h("td", { class: "num", text: secs(p.p95) })))));

      $("#speed-slowest").replaceChildren(...(timing.slowest.length ? timing.slowest.slice(0, 6).map((r) => {
        const parts = grouped(r.stages), bar = h("span", { class: "bar-mini" }, ...parts.filter((p) => p.value > 0).map((p) => {
          const node = h("i", { class: p.cls });
          node.style.flex = `${p.value} 1 0`;
          return node;
        }));
        const most = parts.reduce((a, p) => p.value > a.value ? p : a, parts[0]);
        return h("li", {}, h("a", { class: "slow", href: `#activity?day=${dayKey(r.time)}` },
          h("span", { class: "what" }, h("b", { text: r.text || "(spoken)" }), h("span", { text: `${whenWords(r.time)} · ${DEVICE_WORDS[r.device] || r.device} · mostly ${most.label.toLowerCase()}` })),
          h("b", { text: secs(r.waited) }), bar));
      }) : [h("li", { class: "empty", text: "No timed requests in this range yet." })]));
    },
  };

  /* System */

  const system = {
    enter() { if (state.live) system.render(); },
    render() {
      const live = state.live, service = live.service, host = live.host || {}, process = service.process || {};
      const chip = $("#sys-state");
      chip.className = state.reachable === false ? "chip bad" : "chip good";
      chip.replaceChildren(icon(state.reachable === false ? "cross" : "check"), state.reachable === false ? "Unreachable" : "Running");
      $("#sys-service").replaceChildren(...[
        ["Up for", duration(since(service.started))],
        ["Started", whenWords(service.started)],
        ["Version", service.version ? `${service.version.commit} · ${dateShort(dayKey(service.version.date))}` : "—"],
        ["Memory", process.memory ? `${bytes(process.memory)}${process.memory_max ? ` of ${bytes(process.memory_max)}` : ""}` : "—"],
        ["Natro's CPU", process.cpu != null ? percent(process.cpu, 1) : "—"],
        ["Voice", service.voice ? "On" : "Off"],
        ["Model", service.model || "—"],
      ].map(([name, value]) => h("div", {}, h("dt", { text: name }), h("dd", { text: value }))));

      setText("sys-host-name", [host.name, host.cores ? `${host.cores} cores` : null].filter(Boolean).join(" · "));
      const memoryUsed = host.memory ? host.memory.total - (host.memory.available ?? 0) : null;
      $("#sys-meters").replaceChildren(
        bigMeter("CPU", host.cpu != null ? percent(host.cpu) : "—", host.cpu),
        bigMeter("Memory", host.memory ? `${bytes(memoryUsed)} of ${bytes(host.memory.total)}` : "—", host.memory ? memoryUsed / host.memory.total : null),
        bigMeter("Disk", host.disk ? `${bytes(host.disk.used)} of ${bytes(host.disk.total)}` : "—", host.disk ? host.disk.used / host.disk.total : null));
      $("#sys-host").replaceChildren(...[
        ["System", host.system || "—"],
        ["Load", host.load ? host.load.map((v) => v.toFixed(2)).join(" · ") : "—"],
        ["Up", host.uptime ? duration(host.uptime) : "—"],
      ].map(([name, value]) => h("div", {}, h("dt", { text: name }), h("dd", { text: value }))));

      $("#sys-devices").replaceChildren(...live.devices.map((d) => h("div", { class: "device" },
        h("span", { class: "ic" }, icon(deviceIcon(d.name))),
        h("div", { class: "clip" },
          h("h3", {}, DEVICE_WORDS[d.name] || capital(d.label), h("span", { class: d.online ? "chip good" : "chip" }, h("span", { class: d.online ? "dot" : "dot off" }), d.online ? "Online" : "Offline")),
          h("div", { class: "facts" },
            h("div", {}, h("small", { text: d.online ? "Connected" : "Last seen" }), h("b", { text: d.online ? whenWords(d.since) : whenWords(d.seen) })),
            h("div", {}, h("small", { text: "Tools" }), h("b", { text: d.tools != null ? whole(d.tools) : "—" })),
            h("div", {}, h("small", { text: "Last request" }), h("b", { text: d.used ? whenWords(d.used) : "—" }))),
          d.abilities?.length ? h("div", { class: "abilities" }, ...d.abilities.map((a) => tag(a.replace("_", " ")))) : null))));

      listInto("sys-reminders", "sys-reminders-count", live.reminders, (r) => item("bell", r.text,
        [r.next ? whenWords(r.next) : "Not scheduled", r.repeat].filter(Boolean).join(" · "), r.on ? tag(DEVICE_WORDS[r.on] || r.on) : tag("All devices")), "No reminders");
      listInto("sys-routines", "sys-routines-count", live.routines, (r) => item("routine", r.name || r.task,
        `${capital(r.repeat)}${r.next ? ` · next ${lowerWhen(r.next)}` : ""}`, r.last?.at ? tag(`Ran ${dayWords(dayKey(r.last.at)).toLowerCase()}`) : null), "No routines");
      listInto("sys-jobs", "sys-jobs-count", live.jobs, (j) => item("job", j.title,
        j.state === "running" ? `${j.step || "Working"} · ${j.minutes} min` : `${whenWords(j.finished || j.started)} · ${j.minutes} min`,
        h("span", { class: j.state === "running" ? "chip good" : "chip", text: capital(j.state) })), "No background jobs");
      listInto("sys-watchers", "sys-watchers-count", live.watchers, (w) => item("eye", capital(w.text), w.once ? "Once" : "Every time",
        w.urgent ? tag("Urgent", "no") : tag("Watching", "yes")), "No watchers");
      const backups = live.backups;
      $("#sys-backups").replaceChildren(
        h("div", {}, h("dt", { text: "Last backup" }), h("dd", {}, backups ? whenWords(backups.last) : "None", backups ? h("small", { text: bytes(backups.size) }) : null)),
        h("div", {}, h("dt", { text: "Backups kept" }), h("dd", { text: backups ? whole(backups.count) : "0" })),
        h("div", {}, h("dt", { text: "Waiting for a device" }), h("dd", {}, whole(live.pending), h("small", { text: "Asked while it was offline" }))));
    },
  };
  function bigMeter(name, value, share) {
    const bar = h("span", { class: share >= 0.9 ? "over" : share >= 0.75 ? "near" : "" });
    bar.style.width = `${Math.min(1, share || 0) * 100}%`;
    return h("div", {}, h("span", {}, name, h("b", { text: value })), h("div", { class: "meter" }, bar));
  }

  /* Loading */

  async function loadLive() {
    state.at.live = Date.now();
    try {
      state.live = await get("/live");
      state.reachable = true;
    } catch {
      state.reachable = false;
    }
    renderState();
    if (!state.live) return;
    if (state.view === "home") home.renderLive();
    if (state.view === "system") system.render();
    if (state.view === "home") setText("subtitle", longDay(today()));
  }
  async function loadStats() {
    state.at.stats = Date.now();
    const range = state.range;
    const stats = await fetched(`/stats?range=${range}`);
    if (!stats || range !== state.range) return;
    state.stats = stats;
    if (state.view === "home") home.renderStats();
    if (state.view === "speed") speed.render();
  }
  async function loadYear() {
    state.at.year = Date.now();
    const year = await fetched("/year");
    if (!year) return;
    state.year = year;
    if (state.view === "activity") {
      activity.heat.update(year, activity.day);
      renderYearTitle();
      activity.render();
    }
  }
  function renderYearTitle() {
    const counts = state.year.counts, total = counts.reduce((a, b) => a + b, 0);
    let longest = 0, run = 0;
    for (const c of counts) { run = c ? run + 1 : 0; longest = Math.max(longest, run); }
    let current = 0;
    for (let i = counts.length - 1; i >= 0 && counts[i]; i--) current++;
    if (!counts[counts.length - 1]) for (let i = counts.length - 2; i >= 0 && counts[i]; i--) current++;  // today's may be yet to come
    const busiest = counts.indexOf(Math.max(...counts));
    setText("year-title", `${plural(total, "request")} in the last year`);
    setText("year-stats", total ? `Busiest day ${dateShort(shiftKey(state.year.start, busiest))} (${whole(counts[busiest])}) · Longest streak ${plural(longest, "day")} · Current streak ${plural(current, "day")}` : "");
  }
  async function loadBill() {
    state.at.bill = Date.now();
    const month = state.billMonth;
    const bill = await fetched(`/month?${query({ month })}`);
    if (!bill || month !== state.billMonth) return;
    state.bill = bill;
    if (state.view === "billing") {
      billing.render();
      if (billing.ledgerMonth !== bill.month) billing.load();  // a refresh leaves the pages already loaded alone
    }
  }

  function renderState() {
    const node = $("#state"), pill = $("#online-pill"), live = state.live, down = state.reachable === false;
    $(".dot", node).className = down ? "dot down" : live ? "dot pulse" : "dot";
    $(".state-text", node).textContent = down ? "Can't reach Natro" : live ? "Natro is online" : "Connecting…";
    $(".state-sub", node).textContent = !down && live
      ? `Up ${duration(since(live.service.started))}${live.service.version ? ` · ${live.service.version.commit}` : ""}` : "";
    pill.className = down ? "pill-online down" : "pill-online";
    pill.replaceChildren(h("span", { class: "dot" }), down ? "Offline" : "Online");
  }

  /* Views, the range, the clock */

  const TITLES = {
    home: ["Home", () => state.live ? longDay(today()) : ""],
    activity: ["Activity", () => "Every request and event, newest first"],
    billing: ["Billing", () => state.bill ? `Budget ${money(state.bill.budget)} a month` : ""],
    speed: ["Speed", () => "From your words to Natro's answer"],
    system: ["System", () => "Live, every 5 seconds"],
  };
  const PAGES = { home, activity, billing, speed, system };

  function route() {
    const [name, search] = location.hash.slice(1).split("?");
    const view = VIEWS.includes(name) ? name : "home";
    const changed = view !== state.view;
    state.view = view;
    state.params = new URLSearchParams(search || "");
    for (const page of VIEWS) $(`#view-${page}`).hidden = page !== view;
    $$(".nav a, .tabs a").forEach((link) => {
      if (link.dataset.view === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    setText("title", TITLES[view][0]);
    setText("subtitle", TITLES[view][1]());
    document.title = `${TITLES[view][0]} · Natro Dashboard`;
    $("#range").hidden = !["home", "speed"].includes(view);
    $("#month-nav").hidden = view !== "billing";
    hideTip(true);
    if (changed) scrollTo(0, 0);
    PAGES[view].enter(state.params);
    if (Date.now() - state.at.live > 3000) loadLive();
  }

  function setRange(range) {
    state.range = range;
    save("range", range);
    $$("#range button").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.range === range)));
  }
  $("#range").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-range]");
    if (!button || button.dataset.range === state.range) return;
    setRange(button.dataset.range);
    loadStats();
  });
  $("#range").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const buttons = $$("#range button"), at = buttons.findIndex((b) => b.dataset.range === state.range);
    const next = buttons[(at + (event.key === "ArrowLeft" ? -1 : 1) + buttons.length) % buttons.length];
    next.focus();
    next.click();
  });

  addEventListener("hashchange", route);
  phone.addEventListener("change", route);  // fewer labels on a phone's charts, more on a wider screen
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tick(true);
  });

  // One clock for every refresh, and nothing while the page is hidden: live state every 5 s where it's shown (15 s
  // elsewhere, for the menu's status), charts every minute, the log's tail every 3 s, the year every 5 minutes.
  function tick(now) {
    if (document.visibilityState !== "visible") return;
    const t = Date.now(), view = state.view, live = view === "home" || view === "system" ? 5000 : 15000;
    if (now || t - state.at.live >= live) loadLive();
    if ((view === "home" || view === "speed") && (now || t - state.at.stats >= 60000)) loadStats();
    if (view === "home") home.latest(now);
    if (view === "activity") {
      if (now || t - state.at.tail >= 3000) activity.tail();
      if (t - state.at.year >= 300000) loadYear();
    }
    if (view === "billing" && state.bill?.current && (now || t - state.at.bill >= 60000)) loadBill();
  }
  setInterval(tick, 1000);

  setRange(["24h", "7d", "30d", "month"].includes(saved("range", "7d")) ? saved("range", "7d") : "7d");
  route();
  loadYear();
})();
