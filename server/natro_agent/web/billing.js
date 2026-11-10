/* Billing: a month's bill, by day, model and command, against the budget, and its ledger. */

import { $, API, axisMoney, clock, compact, cost, dateShort, dayKey, deviceIcon, FAMILY, FAMILY_WORDS, fetched,
  foot, h, icon, longDay, meter, money, monthName, MONTHS, percent, plural, query, requestCost, SERIES_OF, setText,
  state, tipRows, whole } from "./common.js";
import { Lines } from "./charts.js";
import { loadBill } from "./load.js";

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

export { billing };
