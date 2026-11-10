/* Home: the month's spending, the span's chart and numbers, what's on now and next, the latest requests. */

import { $, axisMoney, bucketLabel, capital, change, cost, dayKey, DEVICE_WORDS, deviceIcon, duration, fetched,
  foot, h, item, lowerWhen, meter, money, none, percent, plural, query, requestLine, secs, SERIES, SERIES_OF,
  setText, since, state, tipRows, whenWords, whole, worksOn } from "./common.js";
import { Bars } from "./charts.js";
import { loadStats } from "./load.js";

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
        [d.online ? `Connected since ${whenWords(d.since).replace("Today, ", "")}` : d.seen ? `Last seen ${whenWords(d.seen)}` : "Not connected",
          worksOn(d)].filter(Boolean).join(" · "),
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

function miniMeter(name, value, share) {
  const bar = h("span");
  bar.style.width = `${Math.min(1, share || 0) * 100}%`;
  return h("div", {}, h("span", {}, name, h("b", { text: value })), h("div", { class: "meter" }, bar));
}

export { home };
