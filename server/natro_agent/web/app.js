/* Natro's dashboard: Home, Activity, Billing, Speed and System, from /dashboard/api. Charts are built once and
   updated in place when new numbers come (no redraw, no dimming); hovering, focusing or tapping a bar, a point or a
   day says what's in it, and clicking it opens those requests in Activity. Times are his wall clock (the API's).
   One module per page (home.js, activity.js, billing.js, speed.js, system.js), with common.js, charts.js and
   load.js; this one routes between them and keeps the clock. */

import { $, $$, hideTip, longDay, money, phone, save, saved, setText, state, today, VIEWS } from "./common.js";
import { loadBill, loadLive, loadStats, loadYear } from "./load.js";
import { home } from "./home.js";
import { activity } from "./activity.js";
import { billing } from "./billing.js";
import { speed } from "./speed.js";
import { system } from "./system.js";

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
