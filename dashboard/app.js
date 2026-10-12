/* AURORA dashboard: MQTT over WebSocket -> store.js -> views. Read-only: it never publishes.
   Subscribes to the topics in AuroraStore.SUBSCRIPTIONS (never gt/ or redteam/). */
(function () {
  "use strict";
  const cfg = Object.assign({}, window.AURORA_DASH);
  for (const [k, v] of new URLSearchParams(location.search)) {
    if (!(k in cfg)) continue;
    const cur = cfg[k];
    cfg[k] = typeof cur === "number" ? Number(v) : typeof cur === "boolean" ? !["0", "false", "no"].includes(v) : v;
  }
  const { createStore, SATS, SUBSCRIPTIONS } = window.AuroraStore;
  const store = createStore({ historyS: cfg.historyS });
  const $ = (id) => document.getElementById(id);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const PALETTE = ["#38bdf8", "#f97316", "#a78bfa", "#34d399", "#f472b6", "#facc15",
    "#60a5fa", "#fb7185", "#4ade80", "#c084fc", "#fbbf24", "#2dd4bf"];
  let focus = SATS.includes(cfg.focus) ? cfg.focus : "B";
  let view = "overview";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (x, d) => (typeof x === "number" && Number.isFinite(x) ? x.toFixed(d) : "--");
  const hhmmss = (t) => (typeof t === "number" ? new Date(t * 1000).toISOString().slice(11, 19) : "--:--:--");

  // ------------------------------------------------------------------ connection
  function setConn(cls, text) {
    $("conn").className = "pill " + cls;
    $("conn").textContent = text;
  }
  const client = mqtt.connect(cfg.broker, {
    clientId: "dashboard-" + Math.random().toString(16).slice(2, 8),
    reconnectPeriod: 2000,
    connectTimeout: 4000,
    keepalive: 10,
    clean: true,
  });
  client.on("connect", () => {
    client.subscribe(SUBSCRIPTIONS, { qos: 0 });
    setConn("pill-live", "live · " + cfg.broker.replace(/^wss?:\/\//, ""));
  });
  client.on("reconnect", () => setConn("pill-off", "reconnecting…"));
  client.on("offline", () => setConn("pill-down", "broker offline"));
  client.on("error", (e) => setConn("pill-down", "error: " + e.message));
  client.on("message", (topic, payload) => store.handle(topic, payload));

  // ------------------------------------------------------------------ charts (uPlot)
  function makeChart(el, series, opts = {}) {
    const axis = { stroke: css("--muted"), grid: { stroke: css("--line"), width: 1 }, ticks: { stroke: css("--line") } };
    const u = new uPlot(
      {
        width: el.clientWidth || 400,
        height: el.clientHeight || 200,
        legend: { show: !!opts.legend, live: false },
        cursor: { drag: { x: false, y: false } },
        scales: { x: { time: true }, y: opts.range ? { range: opts.range } : { auto: true } },
        axes: [axis, Object.assign({ size: 54 }, axis)],
        series: [{}, ...series],
        bands: opts.bands || [],
      },
      [[], ...series.map(() => [])],
      el,
    );
    new ResizeObserver(() => {
      if (el.clientWidth > 0) u.setSize({ width: el.clientWidth, height: Math.max(160, el.clientHeight) });
    }).observe(el);
    return u;
  }
  function windowed(ring) {
    const from = ring.t.length ? ring.t[ring.t.length - 1] - cfg.windowS : 0;
    let i = 0;
    while (i < ring.t.length && ring.t[i] < from) i++;
    return [ring.t.slice(i), ring.v.slice(i)];
  }
  const line = (label, stroke, extra = {}) => Object.assign({ label, stroke, width: 2, points: { show: false } }, extra);

  const charts = {
    p: makeChart($("chart-p"), [
      line("P(F<30 s)", css("--p30")),
      line("P(F<60 s)", css("--p60"), { width: 3 }),
      line("-σ", "transparent"),
      line("+σ", "transparent"),
      line("flag", css("--warn"), { dash: [8, 6], width: 1.5 }),
      line("act", css("--act"), { dash: [8, 6], width: 1.5 }),
    ], { range: [0, 1], bands: [{ series: [4, 3], fill: "rgba(56,189,248,0.18)" }] }),
    temp: makeChart($("chart-temp"), [line("temp_c", css("--temp"), { width: 2.5 })]),
    soc: makeChart($("chart-soc"), [line("soc_pct", css("--soc"), { width: 2.5 })]),
  };
  let allCharts = null; // built the first time the Satellites view is shown (needs a visible size)

  // ------------------------------------------------------------------ tiles
  const tiles = {};
  for (const sat of SATS) {
    const el = document.createElement("div");
    el.className = "tile st-unknown";
    el.tabIndex = 0;
    el.innerHTML = `<div class="tile-top"><span class="sat">${sat}</span><span class="badge" hidden></span></div>
      <div class="state-word">no state</div>
      <div class="tile-metrics"><span class="m-temp">-- °C</span><span class="m-soc">SOC --</span></div>
      <div class="tile-p">P60 --</div>`;
    el.addEventListener("click", () => setFocus(sat));
    el.addEventListener("keydown", (e) => e.key === "Enter" && setFocus(sat));
    $("tiles").appendChild(el);
    tiles[sat] = {
      el,
      badge: el.querySelector(".badge"),
      state: el.querySelector(".state-word"),
      temp: el.querySelector(".m-temp"),
      soc: el.querySelector(".m-soc"),
      p: el.querySelector(".tile-p"),
    };
  }
  function stateLabel(st) {
    return st === "unknown" ? "no state" : st.replace("_", " ");
  }
  function setBadge(el, sat) {
    const b = store.badgeOf(sat);
    el.hidden = !b;
    el.textContent = b || "";
    el.className = "badge" + (b === "HW" ? " hw" : "");
  }
  function renderTiles() {
    for (const sat of SATS) {
      const s = store.sats[sat];
      const t = tiles[sat];
      const st = store.stateOf(sat);
      t.el.className = `tile st-${st}` + (sat === focus ? " focused" : "") + (store.isStale(sat, cfg.staleS) ? " stale" : "");
      setBadge(t.badge, sat);
      t.state.textContent = stateLabel(st);
      const th = s.fields.thermal;
      const ep = s.fields.eps;
      t.temp.textContent = th ? `${fmt(th.temp_c, 1)} °C${s.emulated.includes("temp_c") ? "*" : ""}` : "-- °C";
      t.temp.title = s.emulated.includes("temp_c") ? "temperature emulated by the board firmware" : "";
      t.soc.textContent = ep ? `SOC ${fmt(ep.soc_pct, 0)} %` : "SOC --";
      const f = s.forecast;
      t.p.innerHTML = f ? `P60 ${fmt(f.p_fail["60"], 2)} <small>${esc(f.model)}</small>` : "P60 --";
    }
  }

  // ------------------------------------------------------------------ focus panel
  function setFocus(sat) {
    focus = sat;
    render();
  }
  function renderFocus() {
    const s = store.sats[focus];
    const st = store.stateOf(focus);
    $("focus-sat").textContent = focus;
    setBadge($("focus-badge"), focus);
    $("focus-state").textContent = stateLabel(st);
    $("focus-state").className = `state-word st-${st}`;
    $("focus-state").style.color = { nominal: css("--ok"), flagged: css("--alarm"), quarantined: css("--quar"), safe_hold: css("--hold") }[st] || css("--muted");
    $("focus-reason").textContent = s.reason || "";
    const f = s.forecast;
    $("focus-model").textContent = f ? `${f.model}${f.warming_up ? " · warming up" : ""}${f.q == null ? "" : " · Q " + fmt(f.q, 2)}` : "";

    const ser = s.series;
    const data = uPlot.join([windowed(ser.p30), [...windowed(ser.p60), windowed(ser.p60_lo)[1], windowed(ser.p60_hi)[1]]]);
    const xs = data[0];
    charts.p.setData([...data, xs.map(() => cfg.warn), xs.map(() => cfg.act)]);
    charts.temp.setData(windowed(ser.temp_c));
    charts.soc.setData(windowed(ser.soc_pct));
  }

  // ------------------------------------------------------------------ decision + events
  let lastEvent = null;
  function renderSide() {
    const a = store.action;
    if (a) {
      const gate = a.gated === true ? "held: Q above θ" : a.gated === false ? "passed" : "inactive (single model)";
      const alts = (a.alternatives || []).map((x) => `<tr><td>${esc(x.action)}</td><td>J ${fmt(x.J, 2)}</td></tr>`).join("");
      $("action").innerHTML = `<div class="verb">${esc(a.action)} ${esc(a.target)}</div>
        <dl><dt>J</dt><dd>${fmt(a.J, 2)}</dd><dt>gate</dt><dd>${gate}</dd><dt>Q</dt><dd>${fmt(a.q, 2)}</dd>
        <dt>time</dt><dd>${hhmmss(a.t)}</dd></dl>
        <div class="reason">${esc(a.reason)}</div>
        ${alts ? `<table class="alts"><tbody>${alts}</tbody></table>` : ""}`;
    }
    if (store.events.length && store.events[0] !== lastEvent) {
      lastEvent = store.events[0];
      $("events").innerHTML = store.events.slice(0, 60).map((e) => {
        const tags = (e.sparta || []).map((x) => `<span class="tag">${esc(x)}</span>`).join("");
        return `<li class="lvl-${esc(e.level)}"><time>${hhmmss(e.t)}</time>${esc(e.text)}${tags}</li>`;
      }).join("");
    }
  }

  // ------------------------------------------------------------------ links view
  const cells = {};
  (function buildMatrix() {
    let html = "<thead><tr><th></th>" + SATS.map((s) => `<th>${s}</th>`).join("") + "</tr></thead><tbody>";
    for (const a of SATS) {
      html += `<tr><th>${a}</th>` + SATS.map((b) => `<td id="lk-${a}${b}" class="${a === b ? "self" : "none"}"></td>`).join("") + "</tr>";
    }
    $("matrix").innerHTML = html + "</tbody>";
    for (const a of SATS) for (const b of SATS) if (a !== b) cells[a + b] = $(`lk-${a}${b}`);
  })();
  function renderLinks() {
    const n = { up: 0, cut: 0 };
    for (const a of SATS) {
      for (const b of SATS) {
        if (a === b) continue;
        const st = store.linkStatus(a, b);
        cells[a + b].className = st;
        if (a < b && st in n) n[st]++;
      }
    }
    $("link-counts").textContent = `${n.up} links up, ${n.cut} feasible but disabled (quarantine or reroute), ` +
      `${66 - n.up - n.cut} without line of sight or out of range.`;
  }

  // ------------------------------------------------------------------ satellites view
  function buildAllCharts() {
    const series = SATS.map((s, i) => line(s, PALETTE[i], { width: 1.8, spanGaps: true }));
    return {
      temp: makeChart($("chart-all-temp"), series, { legend: true }),
      soc: makeChart($("chart-all-soc"), series, { legend: true }),
      att: makeChart($("chart-all-att"), series, { legend: true }),
    };
  }
  function renderSatellites() {
    if (!allCharts) allCharts = buildAllCharts();
    for (const [key, field] of [["temp", "temp_c"], ["soc", "soc_pct"], ["att", "att_err_deg"]]) {
      allCharts[key].setData(uPlot.join(SATS.map((s) => windowed(store.sats[s].series[field]))));
    }
    $("tcs").innerHTML = store.tcs.slice(0, 25).map((c) => {
      const kind = String(c.origin).startsWith("isl:") ? "isl" : c.origin;
      return `<tr><td>${hhmmss(c.t)}</td><td>${esc(c.sat)}</td><td class="${esc(kind)}">${esc(c.origin)}</td>` +
        `<td>${esc(c.cmd)} ${esc(JSON.stringify(c.args || {}))}</td></tr>`;
    }).join("");
  }

  // ------------------------------------------------------------------ header, tabs, loop
  let lastCount = 0;
  function renderHeader() {
    $("rate").textContent = `${store.counts.messages - lastCount} msg/s`;
    lastCount = store.counts.messages;
    const latest = Math.max(...SATS.map((s) => store.sats[s].lastT ?? -Infinity));
    $("clock").textContent = Number.isFinite(latest) ? hhmmss(latest) + " UTC" : "--:--:--";
  }
  function autoFocus() {
    const hot = store.changed.filter((s) => ["flagged", "quarantined", "safe_hold"].includes(store.stateOf(s)));
    store.changed.length = 0;
    if (cfg.autoFocus && hot.length) focus = hot[hot.length - 1];
  }
  function render() {
    autoFocus();
    if (view === "overview") {
      renderTiles();
      renderFocus();
      renderSide();
    } else if (view === "links") {
      renderLinks();
    } else {
      renderSatellites();
    }
  }
  function show(name) {
    view = name;
    for (const b of document.querySelectorAll(".tab")) b.classList.toggle("active", b.dataset.view === name);
    for (const v of ["overview", "links", "satellites"]) $("view-" + v).hidden = v !== name;
    render();
  }
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => show(b.dataset.view));
  document.addEventListener("keydown", (e) => {
    const name = { 1: "overview", 2: "links", 3: "satellites" }[e.key];
    if (name && !e.ctrlKey && !e.altKey) show(name);
  });
  $("fullscreen").addEventListener("click", () => document.documentElement.requestFullscreen?.());
  setInterval(() => {
    renderHeader();
    render();
  }, 1000);
  render();
})();
