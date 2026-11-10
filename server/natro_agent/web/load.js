/* Loading what the pages show from /dashboard/api, and the menu's status. */

import { $, duration, fetched, get, h, longDay, query, setText, since, state, today } from "./common.js";
import { home } from "./home.js";
import { activity, renderYearTitle } from "./activity.js";
import { billing } from "./billing.js";
import { speed } from "./speed.js";
import { system } from "./system.js";

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

export { loadBill, loadLive, loadStats, loadYear };
