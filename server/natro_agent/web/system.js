/* System: the service, the VPS, the devices, and what's scheduled and running. */

import { $, bytes, capital, dateShort, dayKey, dayWords, DEVICE_WORDS, deviceIcon, duration, h, icon, item,
  listInto, lowerWhen, percent, setText, since, state, tag, whenWords, whole } from "./common.js";

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
          h("div", {}, h("small", { text: "Last request" }), h("b", { text: d.used ? whenWords(d.used) : "—" })),
          h("div", {}, h("small", { text: "Works on" }), h("b", { text: d.works_on ? DEVICE_WORDS[d.works_on] || d.works_on : "—" }))),
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

export { system };
