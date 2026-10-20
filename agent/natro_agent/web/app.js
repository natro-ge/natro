"use strict";
/* Natro's dashboard. It reads /dashboard/api/* and draws everything with the DOM and SVG: no libraries, and
   nothing loaded from anywhere but Natro. Whatever came from the log goes onto the page as text
   (textContent), never as HTML. */

(() => {
  const API = "/dashboard/api";
  const $ = (selector, root = document) => root.querySelector(selector);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");

  const SPEND = [["sonnet", "Sonnet"], ["haiku", "Haiku"], ["opus", "Opus"], ["gemini", "Gemini"], ["voice", "Voice"],
    ["jobs", "Background jobs"], ["quick", "Quick answers"], ["other", "Other"]];
  const DEVICES = [["pc", "PC"], ["phone", "Phone"], ["job", "Background jobs"], ["other", "Tests & other"]];
  const TOKENS = [["cache_read_input_tokens", "Cache read"], ["cache_creation_input_tokens", "Cache write"],
    ["input_tokens", "Input"], ["output_tokens", "Output"]];
  const ISSUES = { hand_over: "Handed to Claude", cheaper_model: "Cheaper model", stronger_model: "Stronger model",
    model_error: "Model errors", model_rest: "Models resting", error: "Errors" };
  const PROBLEM_KINDS = new Set(["error", "model_error"]);
  const BEFORE_WORDS = { "24h": "the 24 h before", "7d": "the 7 days before", "30d": "the 30 days before" };
  const DEVICE_WORDS = { pc: "PC", phone: "Phone", job: "Background job", chat: "Chat", test: "Test", tester: "Test" };
  const VIEWS = ["overview", "usage", "system", "logs"];

  const state = {
    view: "overview",
    range: remember("range") || "7d",
    stats: null,
    live: null,
    reachable: null,
    animate: true,
    lastLive: 0,
    lastStats: 0,
    logs: { entries: [], kinds: new Set(), counts: [], device: "", q: "", live: true, more: false, matched: 0,
      open: new Set(), fresh: new Set(), loading: false, lastTail: 0 },
  };

  /* Small helpers */

  function remember(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(`natro-dashboard-${key}`);
      localStorage.setItem(`natro-dashboard-${key}`, value);
    } catch { /* private window: nothing remembered */ }
    return null;
  }

  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props || {})) {
      if (value == null || value === false) continue;
      if (name === "class") node.className = value;
      else if (name === "text") node.textContent = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  function s(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value != null && value !== "") node.setAttribute(name, value);
    }
    return node;
  }

  function icon(paths) {
    const svg = s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    for (const d of paths) svg.append(s("path", { d }));
    return svg;
  }

  const ICONS = {
    pc: ["M4 5h16v10H4z", "M9 19h6", "M12 15v4"],
    phone: ["M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M11 18h2"],
    device: ["M5 6h14v12H5z", "M9 10h6"],
    job: ["M12 7v5l3 2", "M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z"],
    bell: ["M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z", "M10 20a2 2 0 0 0 4 0"],
    routine: ["M4 12a8 8 0 0 1 14-5.3L20 9", "M20 4v5h-5", "M20 12a8 8 0 0 1-14 5.3L4 15", "M4 20v-5h5"],
    eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    cross: ["M6 6l12 12", "M18 6L6 18"],
    alert: ["M12 4l9 16H3z", "M12 10v4", "M12 17v.5"],
    off: ["M8 12h8"],
  };

  /* Numbers and times */

  const money = (v) => v == null ? "—" : v === 0 ? "$0" : Math.abs(v) < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
  const moneyAxis = (v) => v === 0 ? "$0" : v < 0.1 ? `$${+v.toFixed(3)}` : v < 10 ? `$${+v.toFixed(2)}` : `$${Math.round(v)}`;
  const whole = (v) => v == null ? "—" : Math.round(v).toLocaleString("en-US");
  const compact = (v) => {
    if (v == null) return "—";
    const a = Math.abs(v);
    if (a >= 1e9) return `${+(v / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
    if (a >= 1e6) return `${+(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
    if (a >= 1e3) return `${+(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
    return `${Math.round(v)}`;
  };
  const secs = (v) => v == null ? "—" : v < 10 ? `${v.toFixed(1)} s` : v < 60 ? `${Math.round(v)} s`
    : `${Math.floor(v / 60)} m ${Math.round(v % 60)} s`;
  const secsAxis = (v) => v === 0 ? "0" : `${+v.toFixed(1)} s`;
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
    if (hrs) return `${hrs} h ${m % 60} m`;
    if (m) return `${m} min`;
    return `${Math.max(0, Math.round(seconds))} s`;
  };
  const date = (iso) => iso ? new Date(iso) : null;
  const hhmm = (d) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const hhmmss = (d) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const dayWords = (d) => {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const that = new Date(d); that.setHours(0, 0, 0, 0);
    const days = Math.round((that - today) / 864e5);
    if (days === 0) return "Today";
    if (days === 1) return "Tomorrow";
    if (days === -1) return "Yesterday";
    if (days > 1 && days < 7) return d.toLocaleDateString("en-GB", { weekday: "long" });
    return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  };
  const ago = (iso) => {
    if (!iso) return "—";
    const seconds = (Date.now() - new Date(iso)) / 1000;
    if (seconds < 45) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
  };
  const whenWords = (iso) => {
    const d = date(iso);
    return d ? `${dayWords(d)}, ${hhmm(d)}` : "—";
  };

  function bucketLabel(iso, unit, long) {
    const d = new Date(iso);
    if (unit === "hour") {
      return long ? `${dayWords(d)}, ${hhmm(d)}–${hhmm(new Date(d.getTime() + 36e5))}` : hhmm(d);
    }
    if (long) return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    return state.range === "7d" ? d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric" })
      : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  }

  function setNumber(node, value, format) {
    const from = node._value;
    node._value = value;
    cancelAnimationFrame(node._frame);
    if (value == null || from == null || reduced.matches || from === value) {
      node.textContent = format(value);
      return;
    }
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 750), eased = 1 - (1 - t) ** 3;
      node.textContent = format(from + (value - from) * eased);
      if (t < 1) node._frame = requestAnimationFrame(step);
    };
    node._frame = requestAnimationFrame(step);
  }

  /* Tooltip: one for the page. Values lead, names follow. */

  const tip = $("#tip");

  function showTip(x, y, title, rows) {
    tip.replaceChildren(h("div", { class: "tip-title", text: title }), ...rows.map((row) =>
      h("div", { class: `tip-row${row.total ? " total" : ""}` },
        row.key ? h("i", { class: `key ${row.shape || "line"}`, "data-key": row.key }) : null,
        h("b", { text: row.value }), h("span", { text: row.label || "" }))));
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    let left = x + 16;
    if (left + box.width > innerWidth - 8) left = x - box.width - 16;
    let top = y - box.height / 2;
    top = Math.max(8, Math.min(innerHeight - box.height - 8, top));
    tip.style.transform = `translate(${Math.max(8, left)}px, ${top}px)`;
  }

  function hideTip() { tip.hidden = true; }

  /* Charts. Each is drawn for its card's width, again when that changes. */

  const drawn = new Map(); // chart node -> draw(animate)
  const resizer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const width = Math.round(entry.contentRect.width);
      if (width && width !== entry.target._width) {
        const first = !entry.target._width;
        entry.target._width = width;
        drawn.get(entry.target)?.(first && state.animate);
      }
    }
  });

  function mount(node, draw, animate) {
    drawn.set(node, draw);
    if (!node._observed) {
      resizer.observe(node);
      node._observed = true;
    }
    node._width = node.clientWidth;
    if (node._width) draw(animate);
  }

  function niceTicks(max, count = 4) {
    if (!(max > 0)) return [0, 1];
    const raw = max / count, magnitude = 10 ** Math.floor(Math.log10(raw)), norm = raw / magnitude;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * magnitude;
    const ticks = [];
    for (let v = 0; v < max + step * 0.999; v += step) ticks.push(+v.toFixed(10));
    if (ticks.length < 2) ticks.push(step);
    return ticks;
  }

  function label(x, y, text, anchor = "middle", cls) {
    const node = s("text", { x, y, "text-anchor": anchor, class: cls });
    node.textContent = text;
    return node;
  }

  const roundTop = (x, y, w, hgt, r) =>
    `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
  const rectPath = (x, y, w, hgt) => `M${x},${y}h${w}v${hgt}h${-w}Z`;

  function frame(node, n, max, axisFormat, rightPad = 0) {
    const width = node.clientWidth, plotH = +node.dataset.height || 220, top = 18, bottom = 26;
    const ticks = niceTicks(max), yMax = ticks[ticks.length - 1];
    const tickLabels = ticks.map(axisFormat);
    const left = Math.max(...tickLabels.map((t) => t.length)) * 6.4 + 12;
    const plotW = Math.max(10, width - left - rightPad), band = plotW / n;
    const y = (v) => top + plotH - (v / yMax) * plotH;
    const svg = s("svg", { viewBox: `0 0 ${width} ${top + plotH + bottom}`, height: top + plotH + bottom });
    ticks.forEach((tick, k) => {
      const ty = Math.round(y(tick)) + 0.5;
      svg.append(s("line", { class: k ? "grid-line" : "base-line", x1: left, x2: left + plotW, y1: ty, y2: ty }));
      svg.append(label(left - 8, ty + 3.5, tickLabels[k], "end"));
    });
    return { svg, width, plotH, plotW, top, bottom, left, band, y, height: top + plotH + bottom };
  }

  function xLabels(f, buckets, unit) {
    const n = buckets.length, every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(f.plotW / 56))));
    for (let i = n - 1; i >= 0; i -= every) {
      const cx = f.left + f.band * (i + 0.5);
      const text = bucketLabel(buckets[i], unit);
      const half = text.length * 3.2;
      const anchor = cx - half < f.left - 4 ? "start" : cx + half > f.left + f.plotW + 4 ? "end" : "middle";
      const x = anchor === "start" ? f.left + f.band * i : anchor === "end" ? f.left + f.band * (i + 1) : cx;
      f.svg.append(label(x, f.height - 7, text, anchor));
    }
  }

  function legendItems(series, shape) {
    return series.map((item) => h("span", {}, h("i", { class: `key ${shape}`, "data-key": item.key }), item.label));
  }

  function keyboard(node, count, activate, clear) {
    node.tabIndex = 0;
    node.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Escape") { node._active = null; clear(); return; }
      const now = node._active ?? count - 1;
      const next = event.key === "Home" ? 0 : event.key === "End" ? count - 1
        : Math.max(0, Math.min(count - 1, now + (event.key === "ArrowLeft" ? -1 : 1)));
      node._active = next;
      activate(next, null);
    };
    node.onfocus = () => { if (node.matches(":focus-visible")) { node._active = node._active ?? count - 1; activate(node._active, null); } };
    node.onblur = () => { node._active = null; clear(); };
  }

  /* Stacked columns: one column per hour or day, a segment per series. */

  function columns(card, { buckets, unit, series, format, axis, totalWord }) {
    const node = $(".chart", card), legend = $(".legend", card);
    const shown = series.filter((item) => item.values.some((v) => v > 0));
    const totals = buckets.map((_, i) => shown.reduce((sum, item) => sum + (item.values[i] || 0), 0));
    const grand = totals.reduce((a, b) => a + b, 0);
    legend.replaceChildren(...(shown.length > 1 ? legendItems(shown, "rect") : []),
      h("span", { class: "total" }, `${totalWord} `, h("b", { text: format(grand) })));
    table(card, ["", ...shown.map((item) => item.label), ...(shown.length > 1 ? ["Total"] : [])],
      buckets.map((bucket, i) => [bucketLabel(bucket, unit, true), ...shown.map((item) => format(item.values[i] || 0)),
        ...(shown.length > 1 ? [format(totals[i])] : [])]));

    mount(node, (animate) => {
      node.replaceChildren();
      if (!grand) {
        node.append(h("div", { class: "empty", text: "Nothing in this range yet." }));
        return;
      }
      const n = buckets.length, f = frame(node, n, Math.max(...totals), axis);
      const barW = Math.max(2, Math.min(24, f.band * 0.62));
      const groups = [];
      for (let i = 0; i < n; i++) {
        const cx = f.left + f.band * (i + 0.5), x = cx - barW / 2;
        const group = s("g", { class: "col" });
        group.append(s("rect", { class: "band-hover", x: f.left + f.band * i + 1, y: f.top - 6, width: Math.max(1, f.band - 2),
          height: f.plotH + 6, rx: 6 }));
        const bars = s("g", { class: animate && !reduced.matches ? "grow" : "" });
        if (animate) bars.style.animationDelay = `${Math.round(i * Math.min(28, 420 / n))}ms`;
        const present = shown.filter((item) => (item.values[i] || 0) > 0);
        let base = 0;
        present.forEach((item, k) => {
          const value = item.values[i];
          let bottom = f.y(base) - (k > 0 ? 1 : 0), top = f.y(base + value) + (k < present.length - 1 ? 1 : 0);
          base += value;
          if (bottom - top < 1.5) top = bottom - 1.5;
          const r = Math.min(4, barW / 2, bottom - top);
          bars.append(s("path", { class: `bar mark-${item.key}`,
            d: k === present.length - 1 ? roundTop(x, top, barW, bottom - top, r) : rectPath(x, top, barW, bottom - top) }));
        });
        group.append(bars);
        const hit = s("rect", { class: "hit", x: f.left + f.band * i, y: 0, width: f.band, height: f.height });
        hit.addEventListener("pointerenter", (event) => activate(i, event));
        hit.addEventListener("pointermove", (event) => activate(i, event));
        group.append(hit);
        f.svg.append(group);
        groups.push(group);
      }
      const peak = totals.indexOf(Math.max(...totals));
      f.svg.append(label(f.left + f.band * (peak + 0.5), f.y(totals[peak]) - 7, format(totals[peak]), "middle", "end-label"));
      xLabels(f, buckets, unit);
      f.svg.setAttribute("role", "img");
      f.svg.setAttribute("aria-label", `${$("h2", card).textContent}: ${format(grand)} in all; the table view lists each value.`);
      f.svg.addEventListener("pointerleave", clear);
      node.append(f.svg);

      function activate(i, event) {
        groups.forEach((group, k) => {
          group.classList.toggle("active", k === i);
          group.classList.toggle("dim", k !== i);
        });
        const rows = shown.map((item) => ({ key: item.key, shape: "rect", value: format(item.values[i] || 0), label: item.label }));
        if (shown.length > 1) rows.push({ value: format(totals[i]), label: "Total", total: true });
        const box = groups[i].getBoundingClientRect();
        showTip(event ? event.clientX : box.left + box.width / 2, event ? event.clientY : box.top + box.height / 2,
          bucketLabel(buckets[i], unit, true), rows);
      }

      function clear() {
        groups.forEach((group) => group.classList.remove("active", "dim"));
        hideTip();
      }
      keyboard(node, n, activate, clear);
    }, state.animate);
  }

  /* Lines, with a crosshair that finds the hour or day. */

  function lines(card, { buckets, unit, series, format, axis }) {
    const node = $(".chart", card), legend = $(".legend", card);
    const shown = series.filter((item) => item.values.some((v) => v != null));
    legend.replaceChildren(...(shown.length > 1 ? legendItems(shown, "line") : []));
    table(card, ["", ...shown.map((item) => item.label)],
      buckets.map((bucket, i) => [bucketLabel(bucket, unit, true), ...shown.map((item) => format(item.values[i]))]));

    mount(node, (animate) => {
      node.replaceChildren();
      const values = shown.flatMap((item) => item.values.filter((v) => v != null));
      if (!values.length) {
        node.append(h("div", { class: "empty", text: "No requests in this range yet." }));
        return;
      }
      const n = buckets.length, f = frame(node, n, Math.max(...values), axis, 54);
      const xOf = (i) => f.left + f.band * (i + 0.5);
      const crosshair = s("line", { class: "crosshair", y1: f.top - 4, y2: f.top + f.plotH });
      f.svg.append(crosshair);
      const ends = [];
      const paths = [];
      for (const item of shown) {
        let d = "", single = [];
        item.values.forEach((v, i) => {
          if (v == null) return;
          const previous = i > 0 ? item.values[i - 1] : null;
          d += `${previous == null ? "M" : "L"}${xOf(i).toFixed(1)},${f.y(v).toFixed(1)}`;
          const next = i < n - 1 ? item.values[i + 1] : null;
          if (previous == null && next == null) single.push(i);
        });
        if (shown.length === 1) {
          const first = item.values.findIndex((v) => v != null), last = item.values.length - 1 - [...item.values].reverse().findIndex((v) => v != null);
          f.svg.append(s("path", { class: `area mark-${item.key}`,
            d: `${d}L${xOf(last).toFixed(1)},${f.y(0)}L${xOf(first).toFixed(1)},${f.y(0)}Z` }));
        }
        const path = s("path", { class: `line mark-${item.key}`, d });
        f.svg.append(path);
        paths.push(path);
        for (const i of single) f.svg.append(s("circle", { class: `dot-mark mark-${item.key}`, cx: xOf(i), cy: f.y(item.values[i]), r: 3 }));
        const lastIndex = item.values.length - 1 - [...item.values].reverse().findIndex((v) => v != null);
        const lastValue = item.values[lastIndex];
        f.svg.append(s("circle", { class: `dot-mark mark-${item.key}`, cx: xOf(lastIndex), cy: f.y(lastValue), r: 4 }));
        ends.push({ item, x: xOf(lastIndex), y: f.y(lastValue), value: lastValue });
      }
      const apart = ends.every((a, k) => ends.every((b, j) => j <= k || Math.abs(a.y - b.y) >= 14));
      if (apart) for (const end of ends) f.svg.append(label(end.x + 9, end.y + 4, format(end.value), "start", "end-label"));
      const hoverDots = shown.map((item) => {
        const dot = s("circle", { class: `dot-mark hover-dot mark-${item.key}`, r: 4.5, cx: -20, cy: -20 });
        f.svg.append(dot);
        return dot;
      });
      for (let i = 0; i < n; i++) {
        const hit = s("rect", { class: "hit", x: f.left + f.band * i, y: 0, width: f.band, height: f.height });
        hit.addEventListener("pointerenter", (event) => activate(i, event));
        hit.addEventListener("pointermove", (event) => activate(i, event));
        f.svg.append(hit);
      }
      xLabels(f, buckets, unit);
      f.svg.setAttribute("role", "img");
      f.svg.setAttribute("aria-label", `${$("h2", card).textContent}; the table view lists each value.`);
      f.svg.addEventListener("pointerleave", clear);
      node.append(f.svg);
      if (animate && !reduced.matches) {
        for (const path of paths) {
          path.style.setProperty("--len", Math.ceil(path.getTotalLength()));
          path.classList.add("draw");
        }
      }

      function activate(i, event) {
        node.classList.add("hovering");
        crosshair.setAttribute("x1", xOf(i));
        crosshair.setAttribute("x2", xOf(i));
        shown.forEach((item, k) => {
          const v = item.values[i];
          hoverDots[k].setAttribute("cx", v == null ? -20 : xOf(i));
          hoverDots[k].setAttribute("cy", v == null ? -20 : f.y(v));
        });
        const box = node.getBoundingClientRect();
        showTip(event ? event.clientX : box.left + (xOf(i) / f.width) * box.width, event ? event.clientY : box.top + box.height / 2,
          bucketLabel(buckets[i], unit, true),
          shown.map((item) => ({ key: item.key, value: format(item.values[i]), label: item.label })));
      }

      function clear() {
        node.classList.remove("hovering");
        hideTip();
      }
      keyboard(node, n, activate, clear);
    }, state.animate);
  }

  function spark(node, values) {
    mount(node, () => {
      node.replaceChildren();
      const points = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
      if (points.length < 2) return;
      const width = node.clientWidth, height = 34;
      const max = Math.max(...points.map(([, v]) => v)), min = Math.min(0, ...points.map(([, v]) => v));
      const x = (i) => 3 + (i / (values.length - 1)) * (width - 6);
      const y = (v) => height - 4 - ((v - min) / (max - min || 1)) * (height - 9);
      const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, "aria-hidden": "true" });
      const line = points.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
      svg.append(s("polygon", { class: "spark-area", points: `${x(points[0][0])},${height} ${line} ${x(points.at(-1)[0])},${height}` }));
      svg.append(s("polyline", { class: "spark-line", points: line }));
      const [li, lv] = points.at(-1);
      svg.append(s("circle", { class: "spark-dot", cx: x(li), cy: y(lv), r: 3 }));
      node.append(svg);
    }, false);
  }

  /* Table views: every chart's numbers, without hovering. */

  function table(card, head, rows) {
    let view = $(".table-view", card);
    if (!view) {
      view = h("div", { class: "table-view table-wrap" });
      card.append(view);
    }
    view.replaceChildren(h("table", { class: "data" },
      h("thead", {}, h("tr", {}, head.map((cell, k) => h("th", { class: k ? "num" : null, text: cell })))),
      h("tbody", {}, rows.map((row) => h("tr", {}, row.map((cell, k) => h("td", { class: k ? "num" : null, text: cell })))))));
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".table-toggle");
    if (!button) return;
    const card = button.closest(".card");
    const on = card.classList.toggle("as-table");
    button.setAttribute("aria-pressed", on);
    button.textContent = on ? "Chart" : "Table";
  });

  /* Overview */

  const KPIS = [
    { id: "spent", label: "Spent", format: money, upIsGood: false },
    { id: "requests", label: "Requests", format: whole, upIsGood: null },
    { id: "latency", label: "Median response", format: secs, upIsGood: false },
    { id: "tools", label: "Tool calls", format: whole, upIsGood: null },
  ];

  function buildKpis() {
    $("#kpis").replaceChildren(...KPIS.map((kpi) => h("article", { class: "card kpi", id: `kpi-${kpi.id}` },
      h("span", { class: "label", text: kpi.label }), h("span", { class: "value", text: "—" }),
      h("span", { class: "delta" }), h("div", { class: "spark" }))));
  }

  function change(now, before, upIsGood) {
    if (now == null || before == null) return null;
    if (before === 0) return now === 0 ? { text: "Same as", cls: "" } : { text: "Up from none in", cls: "" };
    const ratio = (now - before) / before;
    if (Math.abs(ratio) < 0.005) return { text: "Same as", cls: "" };
    const cls = upIsGood == null ? "" : (ratio > 0) === upIsGood ? "good" : "bad";
    return { text: `${ratio > 0 ? "▲" : "▼"} ${Math.abs(ratio * 100).toFixed(Math.abs(ratio) < 0.1 ? 1 : 0)}%`, cls };
  }

  function beforeWords(stats) {
    return BEFORE_WORDS[stats.range] || `the ${stats.buckets.length} days before`;
  }

  function renderKpis(stats) {
    const before = stats.before;
    const spendSeries = stats.buckets.map((_, i) => Object.values(stats.spend).reduce((sum, values) => sum + values[i], 0));
    const requestSeries = stats.buckets.map((_, i) => Object.values(stats.requests).reduce((sum, values) => sum + values[i], 0));
    const values = {
      spent: [stats.spent, before.spent, spendSeries],
      requests: [stats.request_count, before.request_count, requestSeries],
      latency: [stats.seconds.all_median, before.median_seconds, stats.seconds.median],
      tools: [stats.tool_calls, before.tool_calls, stats.tool_buckets],
    };
    for (const kpi of KPIS) {
      const tile = $(`#kpi-${kpi.id}`), [now, then, series] = values[kpi.id];
      setNumber($(".value", tile), now, kpi.format);
      const delta = $(".delta", tile), moved = change(now, then, kpi.upIsGood);
      if (kpi.id === "tools") {
        $(".label", tile).textContent = stats.tool_calls
          ? `${kpi.label} · ${percent(1 - stats.tool_failed / stats.tool_calls)} worked` : kpi.label;
      }
      delta.replaceChildren(...(moved ? [h("b", { class: moved.cls, text: moved.text }),
        ` ${moved.text.includes("%") ? "vs" : ""} ${beforeWords(stats)}`.replace("  ", " ")] : []));
      spark($(".spark", tile), series);
    }
  }

  function renderHero(live) {
    const month = live.month, share = month.budget ? month.spent / month.budget : 0;
    setNumber($("#hero-spent"), month.spent, money);
    $("#hero-budget").textContent = `of the $${month.budget} budget`;
    $("#month-name").textContent = new Date(live.now).toLocaleDateString("en-GB", { month: "long" });
    const meter = $("#hero-meter");
    meter.classList.toggle("warning", share >= 0.8 && share < 1);
    meter.classList.toggle("critical", share >= 1);
    $("span", meter).style.width = `${Math.min(100, share * 100)}%`;
    let mark = $(".mark", meter);
    if (month.projected != null && month.budget) {
      if (!mark) meter.append(mark = h("i", { class: "mark", title: "projected by month end" }));
      mark.style.left = `calc(${Math.min(100, (month.projected / month.budget) * 100)}% - 1px)`;
    } else if (mark) mark.remove();
    $("#hero-projected").textContent = money(month.projected);
    const left = month.budget - month.spent;
    $("#hero-left").textContent = left >= 0 ? money(left) : `over by ${money(-left)}`;
    $("#hero-days").textContent = Math.max(0, Math.ceil(month.days - month.elapsed_days));
  }

  function renderNow(live) {
    const rows = [];
    const online = live.devices.filter((device) => device.online);
    rows.push(nowRow("pc", online.length ? `${online.length} device${online.length > 1 ? "s" : ""} online` : "No device online",
      h("div", { class: "device-pills" }, live.devices.filter((d) => d.online || DEVICE_WORDS[d.name])
        .slice(0, 4).map((d) => statusChip(d.online, `${cap(d.label)}${d.online ? "" : ` · ${ago(d.seen)}`}`)))));
    const running = live.jobs.filter((job) => job.state === "running");
    rows.push(nowRow("job", running.length ? `${running.length} background job${running.length > 1 ? "s" : ""} running`
      : "No background job running", running[0] ? (running[0].step || running[0].title || running[0].task) : lastJobWords(live.jobs)));
    const nextReminder = live.reminders.find((item) => item.next);
    rows.push(nowRow("bell", nextReminder ? `Next reminder: ${whenWords(nextReminder.next)}` : "No reminder coming",
      nextReminder ? nextReminder.text : `${live.reminders.length} set`));
    const nextRoutine = live.routines.filter((item) => item.next).sort((a, b) => a.next.localeCompare(b.next))[0];
    rows.push(nowRow("routine", nextRoutine ? `Next routine: ${whenWords(nextRoutine.next)}` : "No routine coming",
      nextRoutine ? nextRoutine.name : `${live.routines.length} set up`));
    rows.push(nowRow("eye", `${live.watchers.length} watcher${live.watchers.length === 1 ? "" : "s"}`,
      live.watchers.map((item) => item.text).join(" · ") || "Nothing watched"));
    $("#now").replaceChildren(...rows);
    $("#now-sub").textContent = `as of ${hhmmss(new Date(live.now))}`;
  }

  function lastJobWords(jobs) {
    const last = jobs[0];
    return last ? `Last: ${last.title || last.task} (${last.state}, ${ago(last.finished || last.started)})` : "None today";
  }

  function nowRow(kind, title, detail) {
    return h("div", { class: "now-row" }, h("span", { class: "now-icon" }, icon(ICONS[kind])),
      h("div", { class: "what" }, h("b", { text: title }), typeof detail === "string" ? h("span", { text: detail }) : detail));
  }

  function statusChip(on, text) {
    return h("span", { class: `chip ${on ? "good" : ""}` }, icon(on ? ICONS.check : ICONS.off), text);
  }

  function issueChip(kind) {
    return h("span", { class: `chip ${PROBLEM_KINDS.has(kind) ? "critical" : kind === "model_rest" ? "warning" : ""}`,
      text: ISSUES[kind] || kind });
  }

  function issueFeed(list, items, wide) {
    if (!items.length) {
      list.replaceChildren(h("li", {}, h("span", { class: "chip good", text: "All quiet" }),
        h("span", { class: "text", text: "No model switches or problems in this range." })));
      return;
    }
    list.replaceChildren(...items.map((item) => h("li", {}, issueChip(item.kind), h("span", { class: "text", text: item.text }),
      h("span", { class: "when", text: `${dayWords(new Date(item.time))}, ${hhmm(new Date(item.time))}` }))));
  }

  function seriesFrom(map, order) {
    const known = new Set(order.map(([key]) => key));
    const extra = Object.keys(map).filter((key) => !known.has(key));
    return [...order, ...extra.map((key) => [key, key])].filter(([key]) => map[key])
      .map(([key, name]) => ({ key, label: name, values: map[key] }));
  }

  function renderOverview() {
    const { stats, live } = state;
    if (live) {
      renderHero(live);
      renderNow(live);
    }
    if (!stats) return;
    renderKpis(stats);
    columns($("#card-spend"), { buckets: stats.buckets, unit: stats.unit, series: seriesFrom(stats.spend, SPEND),
      format: money, axis: moneyAxis, totalWord: "In all" });
    columns($("#card-requests"), { buckets: stats.buckets, unit: stats.unit, series: seriesFrom(stats.requests, DEVICES),
      format: whole, axis: compact, totalWord: "In all" });
    issueFeed($("#issues-short"), stats.recent_issues.slice(0, 6));
  }

  /* Usage */

  function renderUsage() {
    const stats = state.stats;
    if (!stats) return;
    columns($("#card-tokens"), { buckets: stats.buckets, unit: stats.unit, series: seriesFrom(stats.tokens, TOKENS),
      format: compact, axis: compact, totalWord: "In all" });
    setNumber($("#cache-share"), stats.cache_share, (v) => percent(v));
    $("span", $("#cache-meter")).style.width = `${(stats.cache_share || 0) * 100}%`;
    const totals = stats.token_totals;
    $("#token-totals").replaceChildren(...TOKENS.flatMap(([key, name]) => [h("dt", { text: name }),
      h("dd", { text: whole(totals[key] || 0) })]), h("dt", { text: "Total" }),
    h("dd", {}, h("b", { text: whole(Object.values(totals).reduce((a, b) => a + b, 0)) })));

    lines($("#card-latency"), { buckets: stats.buckets, unit: stats.unit, format: secs, axis: secsAxis,
      series: [{ key: "median", label: "Median", values: stats.seconds.median },
        { key: "p95", label: "Slowest 5%", values: stats.seconds.p95 }] });
    heat(stats.week);
    models(stats.models);
    tools(stats);
    $("#issues-sub").textContent = `${Object.values(stats.issues).reduce((a, b) => a + b, 0)} in this range`;
    $("#issue-counts").replaceChildren(...Object.keys(ISSUES).map((kind) => h("div", { class: "issue-count" },
      h("b", { text: whole(stats.issues[kind] || 0) }), h("span", { text: ISSUES[kind] }))));
    issueFeed($("#issues"), stats.recent_issues, true);
  }

  function heat(week) {
    const node = $("#heat"), card = $("#card-week");
    const max = Math.max(1, ...week.flat());
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const cells = [];
    week.forEach((hours, d) => {
      cells.push(h("span", { class: "day", text: days[d] }));
      hours.forEach((count, hour) => {
        const level = count ? Math.max(1, Math.ceil((count / max) * 5)) : 0;
        const cell = h("span", { class: `cell l${level}`, role: "img", "aria-label": `${days[d]} ${hour}:00, ${count} requests` });
        cell.addEventListener("pointerenter", (event) => showTip(event.clientX, event.clientY,
          `${days[d]}, ${String(hour).padStart(2, "0")}:00–${String((hour + 1) % 24).padStart(2, "0")}:00`,
          [{ value: whole(count), label: count === 1 ? "request" : "requests" }]));
        cell.addEventListener("pointerleave", hideTip);
        cells.push(cell);
      });
    });
    cells.push(h("span"));
    for (let hour = 0; hour < 24; hour++) cells.push(h("span", { class: "hour", text: hour % 6 === 0 ? String(hour).padStart(2, "0") : "" }));
    node.replaceChildren(...cells);
    let scale = $(".heat-scale", card);
    if (!scale) card.insertBefore(scale = h("div", { class: "heat-scale" }), node.nextSibling);
    scale.replaceChildren("Fewer", ...[0, 1, 2, 3, 4, 5].map((l) => h("span", { class: `cell l${l}` })), "More");
    table(card, ["", "Requests", "Busiest hour", ...Array.from({ length: 24 }, (_, hour) => `${hour}:00`)],
      week.map((hours, d) => {
        const top = Math.max(...hours);
        return [days[d], whole(hours.reduce((a, b) => a + b, 0)), top ? `${hours.indexOf(top)}:00` : "—", ...hours.map(whole)];
      }));
  }

  function models(list) {
    const most = Math.max(1, ...list.map((m) => m.requests));
    $("#models").replaceChildren(
      h("thead", {}, h("tr", {}, h("th", { text: "Model" }), h("th", { class: "num", text: "Requests" }), h("th", { text: "" }),
        h("th", { class: "num", text: "Cost" }), h("th", { class: "num", text: "Median" }), h("th", { class: "num", text: "Tokens" }))),
      h("tbody", {}, list.length ? list.map((m) => {
        const share = h("span", { class: `share mark-${m.family}` }, h("i"));
        share.firstChild.style.width = `${(m.requests / most) * 100}%`;
        return h("tr", {}, h("td", {}, h("span", { class: "model-name" }, h("i", { class: "key rect", "data-key": m.family }),
          h("span", { class: "mono", text: m.model }))), h("td", { class: "num", text: whole(m.requests) }), h("td", {}, share),
        h("td", { class: "num", text: money(m.cost) }), h("td", { class: "num", text: secs(m.median_seconds) }),
        h("td", { class: "num", text: compact(m.tokens) }));
      }) : h("tr", {}, h("td", { colspan: 6, class: "muted", text: "No requests in this range yet." }))));
  }

  function tools(stats) {
    const node = $("#tools"), most = Math.max(1, ...stats.tools.map((t) => t.calls));
    $("#tools-sub").textContent = stats.tool_calls
      ? `${whole(stats.tool_calls)} calls to ${stats.tool_count} tools · ${percent(1 - stats.tool_failed / stats.tool_calls)} worked` : "";
    if (!stats.tools.length) {
      node.replaceChildren(h("div", { class: "empty", text: "No tool calls in this range yet." }));
      return;
    }
    node.replaceChildren(...stats.tools.map((tool, k) => {
      const worked = tool.calls - tool.failed - tool.declined;
      const parts = [["ok", worked], ["failed", tool.failed], ["declined", tool.declined]].filter(([, n]) => n > 0);
      const track = h("span", { class: "track" }, parts.map(([key]) => h("i", { class: `key`, "data-key": key })));
      const row = h("div", { class: "hbar", tabindex: 0 }, h("span", { class: "name", text: tool.tool, title: tool.tool }), track,
        h("span", { class: "n" }, whole(tool.calls), tool.failed ? h("small", { text: `${tool.failed}✕` }) : null));
      const tell = (x, y) => showTip(x, y, tool.tool, [{ key: "ok", shape: "rect", value: whole(worked), label: "worked" },
        { key: "failed", shape: "rect", value: whole(tool.failed), label: "failed" },
        { key: "declined", shape: "rect", value: whole(tool.declined), label: "you said no" }]);
      row.addEventListener("pointermove", (event) => tell(event.clientX, event.clientY));
      row.addEventListener("pointerleave", hideTip);
      row.addEventListener("focus", () => { const box = row.getBoundingClientRect(); tell(box.right - 60, box.top + box.height / 2); });
      row.addEventListener("blur", hideTip);
      requestAnimationFrame(() => setTimeout(() => {
        [...track.children].forEach((bar, i) => { bar.style.width = `${(parts[i][1] / most) * 100}%`; });
      }, state.animate ? k * 35 : 0));
      return row;
    }));
  }

  /* System */

  function renderSystem() {
    const live = state.live;
    if (!live) return;
    const service = live.service, process = service.process || {};
    const up = (Date.now() - new Date(service.started)) / 1000;
    const stateChip = $("#service-state");
    stateChip.className = `chip ${state.reachable ? "good" : "critical"}`;
    stateChip.replaceChildren(icon(state.reachable ? ICONS.check : ICONS.cross), state.reachable ? "Running" : "Unreachable");
    const version = service.version;
    pairs($("#service"), [
      ["Up for", `${duration(up)}`, `since ${whenWords(service.started)}`],
      ["Version", version ? h("span", {}, h("span", { class: "mono", text: version.commit }), ` · ${whenWords(version.date)}`) : "—"],
      ["Last change", version ? clip(version.subject, 140) : "—"],
      ["Main model", service.model ? h("span", { class: "mono", text: service.model }) : "—"],
      ["Voice", service.voice ? "On" : "Off (no speech here)"],
      ["Python", service.python],
    ]);
    $("#service-meters").replaceChildren(
      meterBox("Natro's memory", process.memory, process.memory_max, bytes),
      meterBox("Natro's CPU", process.cpu, 1, (v) => percent(v, 1), process.cpu == null ? "measured from the next look" : "of one core"));
    const host = live.host;
    $("#host-name").textContent = `${host.name} · ${host.system}`;
    const memoryUsed = host.memory ? host.memory.total - (host.memory.available ?? 0) : null;
    $("#host-meters").replaceChildren(
      meterBox("CPU", host.cpu, 1, (v) => percent(v, 0), `${host.cores} cores`),
      meterBox("Memory", memoryUsed, host.memory?.total, bytes),
      meterBox("Disk", host.disk.used, host.disk.total, bytes));
    pairs($("#host"), [
      ["Load", host.load ? `${host.load.map((v) => v.toFixed(2)).join(" · ")}` : "—", host.load ? `1, 5 and 15 min, on ${host.cores} cores` : ""],
      ["Up for", host.uptime != null ? duration(host.uptime) : "—"],
    ]);
    renderDevices(live.devices);
    renderReminders(live.reminders);
    renderRoutines(live.routines);
    renderJobs(live.jobs);
    renderWatchers(live.watchers);
    const backup = live.backups;
    const old = backup && (Date.now() - new Date(backup.last)) / 36e5 > 26;
    pairs($("#backups"), [
      ["Last backup", backup ? h("span", {}, `${ago(backup.last)} · ${bytes(backup.size)} `,
        old ? h("span", { class: "chip warning", text: "Overdue" }) : null) : "None found"],
      ["Copies kept", backup ? `${backup.count}` : "—"],
      ["Waiting for a device", `${live.pending} request${live.pending === 1 ? "" : "s"}`],
    ]);
  }

  function cap(text) {
    return text ? text[0].toUpperCase() + text.slice(1) : text;
  }

  function clip(text, length) {
    return text && text.length > length ? `${text.slice(0, length - 1).trimEnd()}…` : text;
  }

  function pairs(node, rows) {
    node.replaceChildren(...rows.flatMap(([name, value, note]) => [h("dt", { text: name }),
      h("dd", {}, value, note ? h("small", { text: ` ${note}` }) : null)]));
  }

  function meterBox(name, value, max, format, note) {
    const share = value != null && max ? value / max : null;
    const meter = h("div", { class: `meter${share >= 0.9 ? " critical" : share >= 0.75 ? " warning" : ""}` }, h("span"));
    requestAnimationFrame(() => { $("span", meter).style.width = `${Math.min(100, (share || 0) * 100)}%`; });
    return h("div", { class: "meter-box" },
      h("div", { class: "top-line" }, h("span", { text: name }), h("b", { text: format(value) })),
      meter, h("small", { text: note || (max && max !== 1 ? `of ${format(max)}${share != null ? ` · ${percent(share)}` : ""}` : "") }));
  }

  function renderDevices(devices) {
    $("#devices").replaceChildren(...devices.map((device) => {
      const kind = ICONS[device.name] ? device.name : "device";
      return h("div", { class: `device${device.online ? " online" : ""}` },
        h("div", { class: "device-top" }, h("span", { class: "device-icon" }, icon(ICONS[kind])),
          h("div", {}, h("b", { text: cap(device.label) }), h("span", { text: device.name })),
          statusChip(device.online, device.online ? "Online" : "Offline")),
        h("dl", { class: "pairs" },
          h("dt", { text: device.online ? "Connected" : "Last seen" }),
          h("dd", { text: device.online ? ago(device.since) : ago(device.seen) }),
          h("dt", { text: "Last request" }), h("dd", { text: device.used ? ago(device.used) : "not since Natro started" }),
          device.online ? [h("dt", { text: "Its tools" }), h("dd", { text: `${device.tools}` })] : null),
        device.abilities.length ? h("div", { class: "abilities" }, device.abilities.map((a) => h("span", { class: "chip", text: a }))) : null);
    }));
  }

  function whenBox(iso, fallback) {
    const d = date(iso);
    return h("span", { class: "when-box" }, h("b", { text: d ? hhmm(d) : fallback }), h("span", { text: d ? dayWords(d) : "" }));
  }

  function listInto(node, countNode, items, render, emptyText) {
    countNode.textContent = items.length;
    node.replaceChildren(...(items.length ? items.map(render) : [h("li", { class: "none", text: emptyText })]));
  }

  function renderReminders(items) {
    listInto($("#reminders"), $("#reminders-count"), items, (item) => h("li", {},
      whenBox(item.next, item.on ? "Back" : "—"),
      h("div", { class: "body" }, h("b", { text: item.text }),
        h("div", { class: "chips" }, item.repeat ? h("span", { class: "chip accent", text: item.repeat }) : null,
          item.on ? h("span", { class: "chip", text: `when the ${DEVICE_WORDS[item.on] || item.on} is back` }) : null))),
    "No reminders set.");
  }

  const RUN_CHIPS = { done: "good", quiet: "", failed: "critical", cancelled: "", skipped: "warning" };

  function renderRoutines(items) {
    listInto($("#routines"), $("#routines-count"), items, (item) => h("li", {},
      whenBox(item.next, "—"),
      h("div", { class: "body" }, h("b", { text: item.name }), h("span", { text: item.task }),
        h("div", { class: "chips" }, h("span", { class: "chip accent", text: item.repeat }),
          item.quiet ? h("span", { class: "chip", text: "quiet" }) : null,
          item.last ? h("span", { class: `chip ${RUN_CHIPS[item.last.state] ?? ""}`, title: item.last.result || "",
            text: `last: ${item.last.state}, ${ago(item.last.at)}` }) : null))),
    "No routines set up. Ask Natro for one: “every weekday at 8, brief me”.");
  }

  const JOB_CHIPS = { running: "accent", done: "good", failed: "critical", cancelled: "" };

  function renderJobs(items) {
    listInto($("#jobs"), $("#jobs-count"), items, (job) => h("li", {},
      h("span", { class: "when-box" }, h("b", { text: hhmm(new Date(job.started)) }), h("span", { text: dayWords(new Date(job.started)) })),
      h("div", { class: "body" }, h("b", { text: job.title || clip(job.task, 120) }),
        h("span", { text: job.state === "running" ? (job.step || "Working…") : clip(job.result, 200) }),
        h("div", { class: "chips" }, h("span", { class: `chip ${JOB_CHIPS[job.state] ?? ""}`, text: job.state }),
          h("span", { class: "chip", text: `${job.minutes} min` }), job.routine ? h("span", { class: "chip", text: "routine" }) : null))),
    "No background jobs since Natro started.");
  }

  function renderWatchers(items) {
    listInto($("#watchers"), $("#watchers-count"), items, (item) => h("li", {},
      h("span", { class: "now-icon" }, icon(ICONS.eye)),
      h("div", { class: "body" }, h("b", { text: item.text }),
        h("div", { class: "chips" }, h("span", { class: "chip", text: item.kind }),
          item.urgent ? h("span", { class: "chip warning", text: "said out loud" }) : null,
          item.once ? h("span", { class: "chip", text: "once" }) : null))),
    "Nothing watched.");
  }

  /* Logs */

  const LOG = state.logs;

  function logParams(extra) {
    const params = new URLSearchParams({ range: state.range });
    if (LOG.kinds.size) params.set("kinds", [...LOG.kinds].join(","));
    if (LOG.device) params.set("device", LOG.device);
    if (LOG.q.trim()) params.set("q", LOG.q.trim());
    for (const [key, value] of Object.entries(extra || {})) params.set(key, value);
    return params.toString();
  }

  async function loadLogs() {
    LOG.loading = true;
    $("#log-list").classList.add("stale");
    try {
      const data = await get(`/logs?${logParams()}`);
      LOG.entries = data.entries;
      LOG.more = data.more;
      LOG.matched = data.matched;
      LOG.counts = data.kinds;
      LOG.fresh.clear();
      renderLogs();
    } catch (error) {
      $("#log-meta").textContent = `Couldn't load the log: ${error.message}`;
    } finally {
      LOG.loading = false;
      $("#log-list").classList.remove("stale");
    }
  }

  async function tailLogs() {
    if (LOG.loading || !LOG.entries.length && LOG.matched) return;
    LOG.lastTail = Date.now();
    const newest = LOG.entries[0]?.id;
    try {
      const data = await get(`/logs?${logParams(newest ? { after: newest } : {})}`);
      if (!data.entries.length) return;
      for (const entry of data.entries) LOG.fresh.add(entry.id);
      LOG.entries = [...data.entries, ...LOG.entries];
      LOG.matched += data.entries.length;
      const seen = new Map(LOG.counts);
      for (const entry of data.entries) seen.set(entry.entry.kind, (seen.get(entry.entry.kind) || 0) + 1);
      LOG.counts = [...seen].sort((a, b) => b[1] - a[1]);
      renderLogs();
    } catch { /* the status pill says Natro can't be reached */ }
  }

  async function olderLogs() {
    const oldest = LOG.entries.at(-1)?.id;
    if (!oldest) return;
    const button = $("#log-more");
    button.disabled = true;
    try {
      const data = await get(`/logs?${logParams({ before: oldest })}`);
      LOG.entries = [...LOG.entries, ...data.entries];
      LOG.more = data.more;
      renderLogs();
    } finally {
      button.disabled = false;
    }
  }

  function renderLogChips() {
    const node = $("#log-kinds");
    const all = h("button", { type: "button", "aria-pressed": String(!LOG.kinds.size), onclick: () => { LOG.kinds.clear(); loadLogs(); } }, "All");
    node.replaceChildren(all, ...LOG.counts.map(([kind, count]) => h("button", {
      type: "button", "aria-pressed": String(LOG.kinds.has(kind)),
      onclick: () => {
        if (LOG.kinds.has(kind)) LOG.kinds.delete(kind); else LOG.kinds.add(kind);
        loadLogs();
      },
    }, kind, h("small", { text: whole(count) }))));
  }

  function renderLogs() {
    renderLogChips();
    const list = $("#log-list");
    const items = [];
    let day = "";
    for (const row of LOG.entries) {
      const at = new Date(row.time), thisDay = at.toDateString();
      if (thisDay !== day) {
        day = thisDay;
        items.push(h("li", { class: "log-day" }, h("span", { text: dayWords(at) })));
      }
      items.push(logRow(row, at));
    }
    if (!items.length) items.push(h("li", { class: "empty", text: "No log entries match." }));
    list.replaceChildren(...items);
    LOG.fresh.clear();
    $("#log-more").hidden = !LOG.more;
    const shown = LOG.entries.length;
    $("#log-meta").textContent = shown ? `Showing ${whole(shown)} of ${whole(Math.max(LOG.matched, shown))} entries, newest first`
      + (LOG.live ? " · live" : "") : "";
  }

  function logRow(row, at) {
    const entry = row.entry, said = summary(entry);
    const open = LOG.open.has(row.id);
    const item = h("li", { class: `log-row${open ? " open" : ""}${LOG.fresh.has(row.id) ? " fresh" : ""}` });
    const button = h("button", { type: "button", "aria-expanded": String(open) },
      h("span", { class: "t", text: hhmmss(at) }),
      h("span", { class: `k chip kind-${entry.kind}`, text: entry.kind || "?" }),
      h("span", { class: "s" }, said.main, said.dim ? h("em", { text: said.dim }) : null),
      h("span", { class: "m", text: said.meta || "" }));
    const detail = h("div", { class: "log-detail" }, h("div", {}, open ? detailOf(entry) : null));
    button.addEventListener("click", () => {
      const now = item.classList.toggle("open");
      button.setAttribute("aria-expanded", String(now));
      if (now) {
        LOG.open.add(row.id);
        if (!detail.firstChild.firstChild) detail.firstChild.append(detailOf(entry));
      } else LOG.open.delete(row.id);
    });
    item.append(button, detail);
    return item;
  }

  function detailOf(entry) {
    const fields = Object.entries(entry).filter(([key]) => key !== "time" && key !== "kind");
    const raw = JSON.stringify(entry, null, 2);
    const pre = h("pre", { text: raw });
    const copy = h("button", { type: "button", class: "ghost", text: "Copy JSON" });
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(raw);
      } catch {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand("copy");
        selection.removeAllRanges();
      }
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy JSON"; }, 1400);
    });
    return h("div", { class: "inner" },
      h("dl", { class: "pairs" }, fields.flatMap(([key, value]) => [h("dt", { text: key }),
        h("dd", { class: typeof value === "object" && value !== null ? "mono" : null,
          text: typeof value === "object" && value !== null ? JSON.stringify(value) : String(value) })])),
      h("details", {}, h("summary", { class: "muted", text: "Raw entry" }), pre),
      h("div", { class: "actions" }, copy));
  }

  function short(text, length = 140) {
    const words = String(text ?? "").replace(/\s+/g, " ").trim();
    return words.length > length ? `${words.slice(0, length - 1)}…` : words;
  }

  function argsWords(args) {
    if (!args || typeof args !== "object") return "";
    return short(Object.entries(args).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join(", "), 90);
  }

  function summary(e) {
    const device = DEVICE_WORDS[e.device] || e.device;
    switch (e.kind) {
      case "request":
        return { main: short(e.text, 160) || "(spoken)", dim: e.cancelled ? "  · cancelled" : e.reply ? `  → ${short(e.reply, 200)}` : "",
          meta: [device, money((e.cost || 0) + (e.voice_cost || 0)), e.seconds != null ? secs(e.seconds) : null].filter(Boolean).join(" · ") };
      case "tool":
        return { main: e.tool, dim: `  ${argsWords(e.args)}${e.declined ? "  · you said no" : e.ok === false ? "  · failed" : ""}`, meta: device };
      case "device":
        return { main: `${device} ${e.event}`, dim: e.tools?.length ? `  · ${e.tools.length} tools` : "", meta: "" };
      case "job":
        return { main: `Job ${e.id} ${e.event}`, dim: e.task ? `  ${short(e.task, 160)}` : "",
          meta: [e.minutes != null ? `${e.minutes} min` : null, e.cost ? money(e.cost) : null].filter(Boolean).join(" · ") };
      case "routine":
        return { main: `Routine ${e.name || e.id} ${e.event}`, dim: e.why ? `  ${e.why}` : e.repeat ? `  ${e.repeat}` : "", meta: "" };
      case "reminder":
        return { main: `Reminder ${e.event}`, dim: e.text ? `  ${short(e.text)}` : e.minutes ? `  ${e.minutes} min` : "", meta: e.spoken_on || "" };
      case "watcher":
        return { main: `Watcher ${e.event}`, dim: e.what ? `  ${short(e.what)}` : "", meta: "" };
      case "file":
        return { main: `File ${e.event}: ${e.name || e.id}`, dim: e.to ? `  → ${e.to}` : "", meta: e.size ? bytes(e.size) : "" };
      case "convert":
        return { main: `Converted ${e.name} to ${e.to}`, dim: "", meta: [e.size ? bytes(e.size) : null, e.seconds != null ? secs(e.seconds) : null].filter(Boolean).join(" · ") };
      case "voice":
        return { main: "Voice", dim: "  heard, not answered", meta: money(e.voice_cost) };
      case "hand_over":
        return { main: "Handed to Claude", dim: `  ${e.reason || ""}`, meta: "" };
      case "cheaper_model":
      case "stronger_model":
        return { main: `${e.model} → ${e.switched_to}`, dim: `  ${e.reason || ""}`, meta: "" };
      case "model_rest":
        return { main: `${e.model} rests ${e.seconds} s`, dim: `  after ${e.failures} failure${e.failures === 1 ? "" : "s"}: ${short(e.error, 120)}`, meta: "" };
      case "model_error":
        return { main: `${e.model} failed`, dim: `  ${short(e.error, 160)}${e.switched_to ? ` → ${e.switched_to}` : ""}`, meta: "" };
      case "error":
        return { main: `Error${e.step ? ` in ${e.step}` : ""}`, dim: `  ${short(e.error, 180)}`, meta: device || "" };
      case "keys":
        return { main: `Keys ${e.event}`, dim: e.keys ? `  ${e.keys.join(", ")}` : "", meta: device };
      case "chat":
        return { main: `Chat ${e.event}`, dim: e.bytes ? `  ${bytes(e.bytes)}` : "", meta: device };
      case "queued":
        return { main: `Kept for the ${device}`, dim: `  ${short(e.text)}`, meta: "" };
      default: {
        const rest = Object.entries(e).filter(([key]) => !["time", "kind"].includes(key))
          .map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join(", ");
        return { main: short(rest, 200), dim: "", meta: "" };
      }
    }
  }

  let typing;
  $("#log-q").addEventListener("input", (event) => {
    clearTimeout(typing);
    typing = setTimeout(() => { LOG.q = event.target.value; loadLogs(); }, 280);
  });
  $("#log-device").addEventListener("change", (event) => { LOG.device = event.target.value; loadLogs(); });
  $("#log-live").addEventListener("click", (event) => {
    LOG.live = !LOG.live;
    event.currentTarget.classList.toggle("on", LOG.live);
    event.currentTarget.setAttribute("aria-pressed", String(LOG.live));
    renderLogs();
  });
  $("#log-more").addEventListener("click", olderLogs);

  /* Fetching */

  async function get(path) {
    const response = await fetch(`${API}${path}`, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
    return data;
  }

  async function refreshLive() {
    state.lastLive = Date.now();
    try {
      state.live = await get("/live");
      state.reachable = true;
    } catch {
      state.reachable = false;
    }
    renderStatus();
    if (!state.live) return;
    if (state.view === "overview") {
      renderHero(state.live);
      renderNow(state.live);
    }
    if (state.view === "system") renderSystem();
  }

  async function refreshStats(animate) {
    state.lastStats = Date.now();
    document.querySelectorAll(".chart, .kpis, .hero").forEach((node) => node.classList.add("stale"));
    try {
      state.stats = await get(`/stats?range=${state.range}`);
      state.animate = animate;
      if (state.view === "overview") renderOverview();
      if (state.view === "usage") renderUsage();
      state.animate = false;
      stamp();
    } catch { /* kept as it was, dimmed until it loads */
    } finally {
      document.querySelectorAll(".stale").forEach((node) => { if (node.id !== "log-list") node.classList.remove("stale"); });
    }
  }

  function stamp() {
    state.updated = new Date();
    if (state.view !== "system") $("#updated").textContent = `Updated ${hhmmss(state.updated)}`;
  }

  function renderStatus() {
    const node = $("#status"), text = $(".status-text", node);
    node.classList.toggle("online", state.reachable === true);
    node.classList.toggle("down", state.reachable === false);
    if (state.reachable && state.live) {
      text.textContent = `Online · up ${duration((Date.now() - new Date(state.live.service.started)) / 1000)}`;
    } else if (state.reachable === false) {
      text.textContent = "Can't reach Natro";
    }
  }

  /* Views, tabs and the range */

  function placePill(container, active, pill) {
    if (!active) return;
    const first = !pill.style.width;  // where it starts: no slide in from the left
    if (first) pill.classList.add("instant");
    pill.style.width = `${active.offsetWidth}px`;
    pill.style.transform = `translateX(${active.offsetLeft}px)`;
    if (first) requestAnimationFrame(() => requestAnimationFrame(() => pill.classList.remove("instant")));
  }

  function show(view) {
    if (!VIEWS.includes(view)) view = "overview";
    state.view = view;
    for (const name of VIEWS) $(`#view-${name}`).hidden = name !== view;
    document.querySelectorAll(".tabs a").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.view === view)));
    placePill($(".tabs"), $(`.tabs a[data-view="${view}"]`), $(".tab-pill"));
    $("#range").hidden = view === "system";
    $("#updated").textContent = view === "system" ? "Live: every 5 seconds"
      : state.updated ? `Updated ${hhmmss(state.updated)}` : "";
    hideTip();
    state.animate = true;
    if (view === "overview") renderOverview();
    if (view === "usage") renderUsage();
    if (view === "system") renderSystem();
    state.animate = false;
    if (view === "logs") loadLogs();
    if ((view === "overview" || view === "usage") && Date.now() - state.lastStats > 20000) refreshStats(true);
    if (Date.now() - state.lastLive > 3000) refreshLive();
  }

  function setRange(range) {
    state.range = range;
    remember("range", range);
    document.querySelectorAll("#range button").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.range === range)));
    placePill($("#range"), $(`#range button[data-range="${range}"]`), $(".seg-pill"));
  }

  $("#range").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-range]");
    if (!button || button.dataset.range === state.range) return;
    setRange(button.dataset.range);
    refreshStats(true);
    if (state.view === "logs") loadLogs();
  });
  $("#range").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const buttons = [...document.querySelectorAll("#range button")];
    const now = buttons.findIndex((button) => button.dataset.range === state.range);
    const next = buttons[(now + (event.key === "ArrowLeft" ? -1 : 1) + buttons.length) % buttons.length];
    next.focus();
    next.click();
  });

  addEventListener("hashchange", () => show(location.hash.slice(1)));
  addEventListener("resize", () => {
    placePill($(".tabs"), $(`.tabs a[data-view="${state.view}"]`), $(".tab-pill"));
    placePill($("#range"), $(`#range button[data-range="${state.range}"]`), $(".seg-pill"));
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshLive();
      if (state.view === "overview" || state.view === "usage") refreshStats(false);
      if (state.view === "logs" && LOG.live) tailLogs();
    }
  });
  addEventListener("scroll", hideTip, { passive: true });

  // One clock for every refresh: live state every 5 s (15 s off the pages that show it), charts every minute,
  // the log's tail every 3 s. Nothing while the page is hidden.
  setInterval(() => {
    if (document.visibilityState !== "visible") return;
    const now = Date.now(), liveEvery = state.view === "system" || state.view === "overview" ? 5000 : 15000;
    if (now - state.lastLive >= liveEvery) refreshLive();
    if ((state.view === "overview" || state.view === "usage") && now - state.lastStats >= 60000) refreshStats(false);
    if (state.view === "logs" && LOG.live && now - LOG.lastTail >= 3000) tailLogs();
    renderStatus();
  }, 1000);

  buildKpis();
  setRange(["24h", "7d", "30d", "month"].includes(state.range) ? state.range : "7d");
  show(location.hash.slice(1) || "overview");
  if (state.view !== "overview" && state.view !== "usage") refreshStats(false);
})();
