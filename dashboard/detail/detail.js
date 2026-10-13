/* Subsystem detail page (/detail): every satellite's numbers, per-subsystem charts, link matrix and the
   telecommand log. Not used in the demo. Same data layer and subscriptions as the main screen. */
(function () {
  "use strict";
  const cfg = Object.assign({}, window.AURORA_DASH);
  for (const [k, v] of new URLSearchParams(location.search)) if (k in cfg) cfg[k] = typeof cfg[k] === "number" ? Number(v) : v;
  const { createStore, SATS, SUBSCRIPTIONS, modelLabel } = window.AuroraStore;
  const store = createStore({ historyS: cfg.historyS });
  const $ = (id) => document.getElementById(id);
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (x, d) => (typeof x === "number" && Number.isFinite(x) ? x.toFixed(d) : "–");
  const hhmmss = (t) => (typeof t === "number" ? new Date(t * 1000).toISOString().slice(11, 19) : "--:--:--");

  const client = mqtt.connect(cfg.broker, { clientId: "detail-" + Math.random().toString(16).slice(2, 8), reconnectPeriod: 2000, clean: true });
  const conn = (live) => {
    $("conn").textContent = live ? "live" : "reconnecting…";
    $("conn").className = live ? "conn" : "conn down";
  };
  client.on("connect", () => {
    client.subscribe(SUBSCRIPTIONS, { qos: 0 });
    conn(true);
  });
  for (const ev of ["reconnect", "offline", "close"]) client.on(ev, () => conn(false));
  client.on("message", (topic, payload) => store.handle(topic, payload));

  // ------------------------------------------------------------------ table
  function renderTable() {
    $("sat-rows").innerHTML = SATS.map((sat) => {
      const s = store.sats[sat];
      const tone = store.toneOf(sat, cfg.healBelowC);
      const th = s.fields.thermal || {};
      const eps = s.fields.eps;
      const ad = s.fields.adcs || {};
      const obc = s.fields.obc || {};
      const hw = s.src === "hw";
      const soc = eps ? `${fmt(eps.soc_pct, 0)} %` : hw ? "n/a (board has no battery)" : "–";
      const att = s.fields.adcs ? `${fmt(Math.abs(ad.att_err_deg), 1)}°` : hw ? "n/a (no attitude control)" : "–";
      const temp = typeof th.temp_c === "number" ? `${fmt(th.temp_c, 1)} °C${s.emulated.includes("temp_c") ? " (computed by firmware)" : ""}` : "–";
      const f = s.forecast;
      return `<tr><td><b>${sat}</b></td><td>${hw ? "physical board" : s.src ? "digital twin" : "–"}</td>` +
        `<td class="${tone ? "tone-" + tone : ""}">${esc(store.stateWord(sat, cfg.healBelowC))}</td><td class="num">${temp}</td>` +
        `<td class="num ${!eps && hw ? "na" : ""}">${soc}</td><td class="num ${!s.fields.adcs && hw ? "na" : ""}">${att}</td>` +
        `<td>${esc(String(obc.mode || "–").toLowerCase())}</td><td class="num">${fmt(obc.cmd_ok, 0)} / ${fmt(obc.cmd_rej, 0)}</td>` +
        `<td>${esc(obc.last_origin || "–")}</td><td class="num">${f && !f.warming_up ? Math.round(100 * f.p_fail["60"]) + " %" : "–"}</td>` +
        `<td>${esc(f ? modelLabel(f.model) : "–")}</td></tr>`;
    }).join("");
  }

  // ------------------------------------------------------------------ charts
  const GREYS = ["#1c2730", "#5b6872", "#8a96a0"];
  function chart(elm, field) {
    const series = SATS.map((s, i) => ({ label: s, stroke: GREYS[i % 3], width: 1.4, points: { show: false } }));
    const axis = { stroke: css("--ink-2"), grid: { stroke: css("--grid") }, ticks: { stroke: css("--grid") }, font: "13px Barlow, sans-serif" };
    const u = new uPlot({
      width: elm.clientWidth || 400, height: elm.clientHeight || 260, legend: { show: false },
      axes: [Object.assign({ values: (_, sp) => sp.map((t) => new Date(t * 1000).toISOString().slice(11, 19)), space: 90 }, axis), axis],
      series: [{}, ...series], scales: { x: { time: true } },
    }, [[], ...series.map(() => [])], elm);
    new ResizeObserver(() => elm.clientWidth && u.setSize({ width: elm.clientWidth, height: elm.clientHeight })).observe(elm);
    return () => {
      const data = uPlot.join(SATS.map((s) => {
        const r = store.sats[s].series[field];
        return [r.t.slice(-cfg.windowS), r.v.slice(-cfg.windowS)];
      }));
      u.setData(data);
    };
  }
  const charts = [chart($("c-temp"), "temp_c"), chart($("c-soc"), "soc_pct"), chart($("c-att"), "att_err_deg")];

  // ------------------------------------------------------------------ links, telecommands
  (function buildMatrix() {
    let html = "<thead><tr><th></th>" + SATS.map((s) => `<th>${s}</th>`).join("") + "</tr></thead><tbody>";
    for (const a of SATS) html += `<tr><th>${a}</th>` + SATS.map((b) => `<td id="lk-${a}${b}" class="${a === b ? "self" : ""}"></td>`).join("") + "</tr>";
    $("matrix").innerHTML = html + "</tbody>";
  })();
  function renderLinks() {
    for (const a of SATS) for (const b of SATS) if (a !== b) $(`lk-${a}${b}`).className = { up: "up", cut: "cut", none: "" }[store.linkStatus(a, b)];
  }
  function renderTcs() {
    $("tcs").innerHTML = store.tcs.slice(0, 30).map((c) => {
      const kind = String(c.origin).startsWith("isl:") ? "isl" : c.origin;
      return `<tr><td class="num">${hhmmss(c.t)}</td><td>${esc(c.sat)}</td><td class="${esc(kind)}">${esc(c.origin)}</td><td>${esc(c.cmd)} ${esc(JSON.stringify(c.args || {}))}</td></tr>`;
    }).join("");
  }

  setInterval(() => {
    $("clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";
    renderTable();
    charts.forEach((c) => c());
    renderLinks();
    renderTcs();
  }, 1000);
})();
