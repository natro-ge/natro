/* Charts: built once, then updated in place. */

import { h, longDay, MONTHS, phone, pickable, plural, s, secs, SERIES, shiftKey, tipRows } from "./common.js";

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

export { Bars, Heat, Lines, track };
