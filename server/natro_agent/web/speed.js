/* Speed: where his requests' time went, from his words to Natro's answer. */

import { $, bucketLabel, change, dayKey, DEVICE_WORDS, grouped, h, icon, percent, plural, secs, STAGE_GROUPS,
  state, tipRows, whenWords, whole } from "./common.js";
import { Lines, track } from "./charts.js";
import { loadStats } from "./load.js";

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

export { speed };
