/* What every page shares: building nodes, icons, numbers, times and words, the state and fetching, the tooltip,
   and the pieces several pages show. */

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
// Where a connected device's requests work, when its switch points at the other device (works_on.py).
const worksOn = (d) => d.online && d.works_on && d.works_on !== d.name ? `works on the ${DEVICE_WORDS[d.works_on] || d.works_on}` : "";
// "gemini": the free tier that went on 2026-10-20, kept so earlier months still read (dashboard/logs.py's FAMILIES).
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
// Where a request's time went, in five parts (dashboard/speed.py's STAGES, put together).
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
    case "place": return { icon: "eye", title: `Place ${e.event || ""}`, detail: short(e.place || "") };
    case "card": return { icon: "file", title: `Card ${e.event || ""}`, detail: short(e.card || ""), meta: e.place || "" };
    case "app_update": return { icon: "phone", title: `Phone's app ${e.event || ""}`, detail: e.version ? `Version ${e.version}` : "" };
    case "file": return { icon: "file", title: `File ${e.event || ""}`, detail: `${e.name || e.id || ""}${e.to ? ` → ${e.to}` : ""}`, meta: e.size ? bytes(e.size) : "" };
    case "convert": return { icon: "file", title: "Converted", detail: `${e.name || ""} to ${e.to || ""}`, meta: e.seconds != null ? secs(e.seconds) : "" };
    case "pdf": return { icon: "file", title: "PDF", detail: short(e.action || e.name || "") };
    case "voice": return { icon: "voice", title: "Heard, not answered", detail: device, meta: e.voice_cost ? cost(e.voice_cost) : "" };
    case "warm": return { icon: "check", title: `${FAMILY_WORDS[FAMILY(e.model)] || e.model}'s cache kept warm`,
      detail: e.tokens?.cache_creation_input_tokens ? "written again" : "", meta: e.cost ? cost(e.cost) : "" };
    case "chat": return { icon: "message", title: `Chat ${e.event || ""}`, detail: device };
    case "works_on": return { icon: "plug", title: `${device} works on the ${DEVICE_WORDS[e.works_on] || e.works_on}`,
      detail: e.by && e.by !== e.device ? `asked on the ${DEVICE_WORDS[e.by] || e.by}` : "" };
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
  if (e.works_on) tags.push(tag(`On the ${DEVICE_WORDS[e.works_on] || e.works_on}`));
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

function bucketLabel(i, long) {
  const stats = state.stats, iso = stats.buckets[i];
  if (stats.unit === "hour") return long ? `${dayWords(dayKey(iso))}, ${clock(iso)}` : clock(iso);
  if (long) return longDay(dayKey(iso));
  if (stats.buckets.length > 8 || phone.matches) return String(+iso.slice(8, 10));
  return dayKey(iso) === today() ? "Today" : shortDay(dayKey(iso));
}

const capital = (text) => String(text || "").replace(/^./, (c) => c.toUpperCase());

export { $, $$, API, axisMoney, bucketLabel, bytes, capital, change, clock, compact, cost, dateShort, dayKey,
  dayWords, DEVICE_WORDS, deviceIcon, duration, FAMILY, FAMILY_WORDS, fetched, foot, get, grouped, h, hideTip,
  icon, item, keyDate, listInto, longDay, lowerWhen, meter, money, monthName, MONTHS, none, percent, phone,
  pickable, plural, query, requestCost, requestLine, s, save, saved, secs, SERIES, SERIES_OF, setText, shiftKey,
  since, STAGE_GROUPS, state, summary, tag, tipRows, today, VIEWS, whenWords, whole, worksOn };
