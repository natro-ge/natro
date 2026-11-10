/* Activity: the year of days, and every request and event, newest first, filtered, with the live tail. */

import { $, capital, clock, compact, cost, dateShort, dayKey, dayWords, fetched, grouped, h, icon, keyDate,
  longDay, plural, query, requestCost, requestLine, save, saved, secs, setText, shiftKey, state, summary, today,
  whole } from "./common.js";
import { Heat, track } from "./charts.js";
import { loadYear } from "./load.js";

const KIND_GROUPS = [
  { key: "all", label: "All", kinds: null },
  { key: "requests", label: "Requests", kinds: ["request"] },
  { key: "devices", label: "Devices", kinds: ["device", "works_on", "chat", "keys", "queued", "queue_skipped", "app_update"] },
  { key: "jobs", label: "Jobs", kinds: ["job", "routine"] },
  { key: "reminders", label: "Reminders", kinds: ["reminder", "watcher", "message", "place", "card"] },
  { key: "files", label: "Files", kinds: ["file", "convert", "pdf"] },
  { key: "problems", label: "Problems", kinds: ["error", "model_error", "model_rest", "mcp_error", "hand_over", "cheaper_model", "stronger_model"] },
];
// Shown inside their request instead (dashboard/activity.py's attachments).
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

export { activity, renderYearTitle };
