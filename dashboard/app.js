/* AURORA ground segment: MQTT over WebSocket -> store.js -> one screen. Read-only: it never publishes.
   Subscribes to the topics in AuroraStore.SUBSCRIPTIONS (never gt/ or redteam/). */
(function () {
  "use strict";
  const cfg = Object.assign({}, window.AURORA_DASH);
  for (const [k, v] of new URLSearchParams(location.search)) {
    if (!(k in cfg)) continue;
    const cur = cfg[k];
    cfg[k] = typeof cur === "number" ? Number(v) : typeof cur === "boolean" ? !["0", "false", "no"].includes(v) : v;
  }
  const { createStore, SATS, SUBSCRIPTIONS, ACTION_LABEL, modelLabel } = window.AuroraStore;
  const store = createStore({ historyS: cfg.historyS });
  const $ = (id) => document.getElementById(id);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const hhmmss = (t) => (typeof t === "number" ? new Date(t * 1000).toISOString().slice(11, 19) : "--:--:--");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ connection
  function setConn(live) {
    $("conn").textContent = live ? "live" : "reconnecting…";
    $("conn").className = live ? "conn" : "conn down";
  }
  const client = mqtt.connect(cfg.broker, {
    clientId: "dashboard-" + Math.random().toString(16).slice(2, 8),
    reconnectPeriod: 2000,
    connectTimeout: 4000,
    keepalive: 10,
    clean: true,
  });
  let wasDown = false;
  client.on("connect", () => {
    if (wasDown) store.resetSession(); // broker restarted (make stage-reset): start a clean session
    wasDown = false;
    client.subscribe(SUBSCRIPTIONS, { qos: 0 });
    setConn(true);
  });
  client.on("offline", () => (wasDown = true));
  client.on("close", () => (wasDown = true));
  client.on("reconnect", () => setConn(false));
  client.on("offline", () => setConn(false));
  client.on("close", () => setConn(false));
  client.on("message", (topic, payload) => store.handle(topic, payload));

  // ------------------------------------------------------------------ constellation (SVG)
  const NS = "http://www.w3.org/2000/svg";
  const sky = $("sky");
  const C = { x: 500, y: 380 }; // centre of the view
  const R = 345; // orbit radius on screen
  const INC = (53 * Math.PI) / 180; // inclination (ml/configs/synthetic.yaml)
  const RAAN = [0, 120, 240]; // plane i: 120 deg apart
  const el = (tag, attrs = {}, parent = sky) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    parent.appendChild(e);
    return e;
  };
  /** Top view (from above the North Pole) of a satellite at argument of latitude u in plane p. */
  function project(plane, phaseDeg) {
    const u = (phaseDeg * Math.PI) / 180;
    const x = R * Math.cos(u);
    const y = R * Math.cos(INC) * Math.sin(u);
    const O = (RAAN[plane - 1] * Math.PI) / 180;
    return { x: C.x + x * Math.cos(O) - y * Math.sin(O), y: C.y - (x * Math.sin(O) + y * Math.cos(O)) };
  }
  el("circle", { cx: C.x, cy: C.y, r: 56, class: "earth" });
  for (const O of RAAN) {
    el("ellipse", { cx: C.x, cy: C.y, rx: R, ry: R * Math.cos(INC), class: "ring", transform: `rotate(${-O} ${C.x} ${C.y})` });
  }
  const relayLayer = el("g", { class: "relay" }); // attack paths of the active incident (C1), under the links
  const linkLayer = el("g", { class: "links" }); // so a cut link stays visible (red dashed) over its attack path
  const relayEls = {};
  const satLayer = el("g", { class: "sats" });
  const linkEls = {}; // "A-B" -> <line>
  const drawn = new Set(); // reroute draw-ins already played
  const t0Page = Date.now();
  const satEls = {};
  for (const sat of SATS) {
    const g = el("g", { "data-sat": sat }, satLayer);
    const halo = el("circle", { r: 40, class: "halo", visibility: "hidden" }, g);
    const circle = el("circle", { r: 15, class: "mk-sim" }, g);
    const square = el("rect", { width: 28, height: 28, class: "mk-hw", visibility: "hidden" }, g);
    const label = el("text", { class: "sat-label", "text-anchor": "middle", "dominant-baseline": "central" }, g);
    label.textContent = sat;
    const temp = el("text", { class: "sat-temp num", "text-anchor": "middle", "dominant-baseline": "central" }, g);
    satEls[sat] = { g, halo, circle, square, label, temp };
  }

  function renderSky(pos) {
    // text in screen pixels whatever the diagram's scale: letters >= 20 px, temperatures 22 px
    const vb = sky.viewBox.baseVal;
    const scale = Math.min(sky.clientWidth / vb.width, sky.clientHeight / vb.height) || 1;
    sky.style.setProperty("--letter", `${(22 / scale).toFixed(1)}px`);
    sky.style.setProperty("--letter-hot", `${(28 / scale).toFixed(1)}px`);
    sky.style.setProperty("--temp", `${(22 / scale).toFixed(1)}px`);
    // links
    const seen = new Set();
    for (const [key, l] of Object.entries(store.links)) {
      if (!l.feasible) continue;
      const a = pos[l.a];
      const b = pos[l.b];
      if (!a || !b) continue;
      seen.add(key);
      const pa = project(a.plane, a.phase_deg);
      const pb = project(b.plane, b.phase_deg);
      let line = linkEls[key];
      if (!line) line = linkEls[key] = el("line", { "data-link": key }, linkLayer);
      line.setAttribute("x1", pa.x.toFixed(1));
      line.setAttribute("y1", pa.y.toFixed(1));
      line.setAttribute("x2", pb.x.toFixed(1));
      line.setAttribute("y2", pb.y.toFixed(1));
      const rerouted = l.enabled && store.reroutes.some((r) => r.key === key && store.latestT - r.t < 60);
      let cls = l.enabled ? (rerouted ? "ln-new" : "ln-up") : "ln-cut";
      if (rerouted && !drawn.has(key) && Date.now() - t0Page > 3000) {
        drawn.add(key);
        if (!reduceMotion) {
          const len = Math.hypot(pb.x - pa.x, pb.y - pa.y);
          line.style.setProperty("--len", len.toFixed(0));
          line.style.strokeDasharray = `${len.toFixed(0)}`;
          cls += " draw";
          line.addEventListener("animationend", () => {
            line.style.strokeDasharray = "";
            line.classList.remove("draw");
          }, { once: true });
        }
      }
      if (line.classList.contains("draw")) cls += " draw";
      line.setAttribute("class", cls);
    }
    for (const [key, line] of Object.entries(linkEls)) {
      if (!seen.has(key)) {
        line.remove();
        delete linkEls[key];
      }
    }
    // the active incident's attack paths: source -> each victim, on top of the links
    const inc = store.activeIncident();
    const paths = new Set(inc && inc.source ? inc.victims.map((v) => `${inc.source}>${v}`) : []);
    for (const key of paths) {
      const [a, b] = key.split(">");
      const pa = project(pos[a].plane, pos[a].phase_deg);
      const pb = project(pos[b].plane, pos[b].phase_deg);
      let line = relayEls[key];
      if (!line) line = relayEls[key] = el("line", { class: "ln-relay" }, relayLayer);
      line.setAttribute("x1", pa.x.toFixed(1));
      line.setAttribute("y1", pa.y.toFixed(1));
      line.setAttribute("x2", pb.x.toFixed(1));
      line.setAttribute("y2", pb.y.toFixed(1));
    }
    for (const [key, line] of Object.entries(relayEls)) {
      if (!paths.has(key)) {
        line.remove();
        delete relayEls[key];
      }
    }
    $("lg-relay").hidden = paths.size === 0;
    // satellites
    for (const sat of SATS) {
      const s = store.sats[sat];
      const p = project(pos[sat].plane, pos[sat].phase_deg);
      const e = satEls[sat];
      const tone = store.toneOf(sat, cfg.healBelowC);
      const silent = s.lastRx !== null && store.isStale(sat, cfg.staleS);
      e.g.setAttribute("class", (tone ? `tone-${tone}` : "") + (silent ? " silent" : ""));
      const hw = s.src === "hw";
      e.circle.setAttribute("cx", p.x);
      e.circle.setAttribute("cy", p.y);
      e.circle.setAttribute("visibility", hw ? "hidden" : "visible");
      e.square.setAttribute("x", p.x - 14);
      e.square.setAttribute("y", p.y - 14);
      e.square.setAttribute("visibility", hw ? "visible" : "hidden");
      e.halo.setAttribute("cx", p.x);
      e.halo.setAttribute("cy", p.y);
      e.halo.setAttribute("visibility", tone ? "visible" : "hidden");
      // label outward from the centre so it does not sit on the rings' crossing
      const dx = p.x - C.x;
      const dy = p.y - C.y;
      const d = Math.hypot(dx, dy) || 1;
      const off = tone ? 50 : 34; // an abnormal marker is bigger
      let lx = p.x + (dx / d) * off;
      let ly = p.y + (dy / d) * off;
      const half = (tone ? 28 : 22) / scale / 2 + 4;
      if (ly - half < vb.y || ly + half > vb.y + vb.height) { // would be clipped: beside the marker instead
        lx = p.x + (dx >= 0 ? off : -off);
        ly = p.y;
      }
      e.label.setAttribute("x", lx.toFixed(1));
      e.label.setAttribute("y", ly.toFixed(1));
      const th = s.fields.thermal;
      if (tone && th && typeof th.temp_c === "number") {
        e.temp.textContent = `${th.temp_c.toFixed(1)} °C`;
        const gap = 30 / scale; // one screen line beyond the letter, whatever the diagram's scale
        const out = ly + (dy >= 0 ? gap : -gap); // outward, like the letter
        if (out > vb.y + 12 && out < vb.y + vb.height - 8) {
          e.temp.setAttribute("x", (lx + (dx / d) * 4).toFixed(1));
          e.temp.setAttribute("y", out.toFixed(1));
          e.temp.setAttribute("text-anchor", "middle");
        } else { // at the top or bottom edge: beside the letter, never over the marker
          const side = dx >= 0 ? 1 : -1;
          e.temp.setAttribute("x", (lx + side * 0.9 * gap).toFixed(1));
          e.temp.setAttribute("y", ly.toFixed(1));
          e.temp.setAttribute("text-anchor", side > 0 ? "start" : "end");
        }
        e.temp.setAttribute("visibility", "visible");
      } else {
        e.temp.setAttribute("visibility", "hidden");
      }
    }
    // legend: a colour key appears only while that colour is on screen (nominal stays colourless)
    const tones = new Set(SATS.map((sat) => store.toneOf(sat, cfg.healBelowC)));
    for (const t of ["caution", "warning", "safe"]) $("lg-" + t).hidden = !tones.has(t);
    $("lg-cut").hidden = !Object.values(store.links).some((l) => l.feasible && !l.enabled);
    // the attacked satellite on top
    for (const sat of SATS) if (store.toneOf(sat, cfg.healBelowC)) satLayer.appendChild(satEls[sat].g);
  }

  // ------------------------------------------------------------------ what CASSANDRA is doing: the attack chain
  const KIND_WORD = {
    command: "Command", effect: "Effect", deception: "Deception", predicted: "Forecast", action: "Response", healed: "Healed",
  };
  const KIND_TONE = { command: "caution", effect: "caution", deception: "caution", predicted: "", action: "warning", healed: "safe" };
  const STATUS_WORD = { open: "in progress", contained: "contained", closed: "closed, all healed" };
  let lastChainKey = "";
  function renderChain() {
    const inc = store.activeIncident();
    const key = inc ? `${inc.incident_id}|${inc.updated_t}|${inc.steps.length}` : "none";
    if (key === lastChainKey) return;
    lastChainKey = key;
    if (!inc) {
      $("chain").innerHTML = `<p class="empty">Watching 12 satellites. No anomalies.</p>`;
      return;
    }
    const who = inc.source
      ? `<div class="fan"><span class="fan-src">${esc(inc.source)}</span><span class="fan-arrow">relayed to →</span>` +
        inc.victims.map((v) => `<span class="fan-victim">${esc(v)}</span>`).join("") + `</div>`
      : "";
    const head = `<div class="chain-head"><span>Attack chain${inc.sats.length ? " on " + esc(inc.sats.join(", ")) : ""}</span>` +
      `<span class="chain-status st-${esc(inc.status)}">${esc(STATUS_WORD[inc.status] || inc.status)}</span></div>`;
    const html = (max) => window.AuroraStore.chainView(inc.steps, max).map(({ step: st, collapsed }) => {
      if (collapsed) return `<li class="step collapsed"><time></time><div class="step-body"><span class="step-kind">… ${collapsed} more step${collapsed > 1 ? "s" : ""} (all in /detail)</span></div></li>`;
      const tags = (st.sparta || []).map((t) => `<span class="tag">${esc(t.id)}${t.tactic ? " · " + esc(t.tactic) : ""}</span>`).join("");
      const tone = KIND_TONE[st.kind] || "";
      const text = String(st.text).replace(/^Forecast:\s*/, "");
      return `<li class="step ${st.observed ? "obs" : "pred"}${tone ? " tone-" + tone : ""}">` +
        `<time class="num">${hhmmss(st.t)}</time>` +
        `<div class="step-body"><div class="step-line"><span class="step-kind">${esc(KIND_WORD[st.kind] || st.kind)}` +
        `${st.observed ? "" : " · predicted, not observed"}</span>${tags}</div>` +
        `<span class="step-text">${esc(text)}</span></div></li>`;
    }).join("");
    // as many rows as fit (the first step always stays; the middle collapses); never clip a step
    for (let max = cfg.chainRows || 7; max >= 3; max--) {
      $("chain").innerHTML = head + who + `<ol class="steps">${html(max)}</ol>`;
      const list = $("chain").querySelector(".steps");
      if (list.scrollHeight <= list.clientHeight + 1) break;
    }
  }

  // the flat timeline (kept for the detail page and as a fallback)
  let lastTimelineKey = "";
  function renderTimeline() {
    if (!$("timeline")) return;
    const lines = store.timeline.slice(0, cfg.timelineLines);
    const key = lines.map((l) => l.t + l.text).join("|") + "|" + Math.floor((store.latestT || 0) / 30);
    if (key === lastTimelineKey) return;
    lastTimelineKey = key;
    if (!lines.length) {
      $("timeline").innerHTML = `<li class="empty">Watching 12 satellites. No anomalies.</li>`;
      return;
    }
    const now = store.latestT || 0;
    $("timeline").innerHTML = lines
      .map((l) => {
        const old = now - l.t > 180 ? " old" : "";
        const tone = l.tone && !old ? ` tone-${l.tone}` : "";
        return `<li class="${tone}${old}"><time class="num">${hhmmss(l.t)}</time><span>${esc(l.text)}</span></li>`;
      })
      .join("");
  }

  function renderOptions() {
    const a = store.action;
    $("options").hidden = !a;
    if (!a) return;
    const opts = [{ action: a.action, J: a.J, chosen: true }, ...(a.alternatives || []).map((x) => ({ ...x, chosen: false }))]
      .filter((o) => typeof o.J === "number")
      .sort((x, y) => x.J - y.J);
    const max = Math.max(...opts.map((o) => o.J), 1e-6);
    const label = ACTION_LABEL[a.action] || a.action;
    $("options-for").textContent = `Chosen for ${a.target} at ${hhmmss(a.t)}: ${label}${a.gated ? " (held by the uncertainty gate)" : ""}`;
    $("bars").innerHTML = opts
      .map((o) => `<li class="${o.chosen ? "chosen" : ""}"><span>${esc(ACTION_LABEL[o.action] || o.action)}</span>` +
        `<span class="bar-track"><span class="bar" style="display:block;width:${Math.max(2, (100 * o.J) / max).toFixed(1)}%"></span></span>` +
        `<span class="j num">${o.J.toFixed(2)}</span></li>`)
      .join("");
  }

  // ------------------------------------------------------------------ satellite at risk
  let follow = cfg.fallback;
  let ranges = { temp: [15, 50] };
  function makePlot(elm, series, yRange, bands = []) {
    const axis = () => ({
      stroke: css("--ink-2"),
      grid: { stroke: css("--grid"), width: 1 },
      ticks: { stroke: css("--grid") },
      font: "15px Barlow, system-ui, sans-serif",
    });
    const utc = (splits) => splits.map((t) => new Date(t * 1000).toISOString().slice(11, 19));
    const u = new uPlot(
      {
        width: elm.clientWidth || 500,
        height: elm.clientHeight || 200,
        legend: { show: false },
        cursor: { show: false },
        tzDate: (ts) => uPlot.tzDate(new Date(ts * 1e3), "Etc/UTC"),
        scales: { x: { time: true }, y: { range: yRange } },
        axes: [
          Object.assign(axis(), { values: (u, splits) => utc(splits), space: 110, grid: { show: false } }),
          Object.assign({ size: 52 }, axis()),
        ],
        series: [{}, ...series],
        bands,
      },
      [[], ...series.map(() => [])],
      elm,
    );
    new ResizeObserver(() => {
      if (elm.clientWidth > 0) u.setSize({ width: elm.clientWidth, height: Math.max(120, elm.clientHeight) });
    }).observe(elm);
    return u;
  }
  const line = (stroke, extra = {}) => Object.assign({ stroke, width: 2.5, points: { show: false }, spanGaps: false }, extra);
  const plots = {
    temp: makePlot($("plot-temp"), [line(css("--ink")), line(css("--warning"), { width: 2 })], () => ranges.temp),
    risk: makePlot(
      $("plot-risk"),
      [line("transparent"), line("transparent"), line(css("--ink")), line(css("--ink-2"), { width: 1.5, dash: [8, 6] })],
      () => [0, 1],
      [{ series: [2, 1], fill: css("--grid") + "aa" }],
    ),
  };
  // both plots share one time window ending at the latest message time, so their axes line up
  function windowed(ring, from) {
    let i = 0;
    while (i < ring.t.length && ring.t[i] < from) i++;
    return [ring.t.slice(i), ring.v.slice(i)];
  }

  function chooseFollow() {
    if (cfg.follow && SATS.includes(cfg.follow)) return cfg.follow;
    const tone = (x) => store.toneOf(x, cfg.healBelowC);
    if (tone(follow) === "caution") return follow; // stay on a satellite while it is still at risk
    // a satellite at risk and not yet contained comes first: in C1 the heating victim, not the held source
    const p60 = (x) => {
      const f = store.sats[x].forecast;
      return f && !f.warming_up && typeof f.p_fail["60"] === "number" ? f.p_fail["60"] : 0;
    };
    const atRisk = SATS.filter((x) => tone(x) === "caution").sort((a, b) => p60(b) - p60(a));
    if (atRisk.length) return atRisk[0];
    if (tone(follow)) return follow; // stay on a satellite while it is abnormal
    return store.atRisk(cfg.fallback, cfg.calmP);
  }

  function renderRisk() {
    follow = chooseFollow();
    const s = store.sats[follow];
    const tone = store.toneOf(follow, cfg.healBelowC);
    $("risk-sat").textContent = follow;
    $("who-label").textContent = tone ? "Satellite at risk" : "Watching";
    $("risk-state").textContent = store.stateWord(follow, cfg.healBelowC);
    $("risk-state").className = "risk-state" + (tone ? ` tone-${tone}` : "");
    const f = s.forecast;
    $("risk-model").textContent = f ? modelLabel(f.model) + (f.warming_up ? ", warming up" : "") : "";
    const left = tone === "caution" ? store.etaLeft(follow) : null;
    $("countdown").hidden = left === null;
    if (left !== null) {
      const what = window.AuroraStore.limitName(s.eta && s.eta.channel);
      $("countdown").innerHTML = `<small>${what} in</small><br>${Math.round(left)} s`;
    }

    const th = s.src === "hw" ? cfg.thermal.hw : cfg.thermal.twin;
    ranges.temp = [th.ambient - 5, th.limit + 5];
    // shared window: 2 min for the board (real time), 5 min of message time for the twins; until that much
    // history exists it starts at the first sample, so the lines fill the plot from the first seconds
    const now = store.latestT;
    const span = s.src === "hw" ? cfg.windowHwS || cfg.windowS : cfg.windowS;
    const first = s.series.temp_c.t.length ? s.series.temp_c.t[0] : null;
    const from = now === null ? 0 : Math.max(now - span, first === null ? now - span : Math.min(first, now - 10));
    const [tt, tv] = windowed(s.series.temp_c, from);
    // the window's two edges as extra x points (no data there), so the limit line runs edge to edge
    const edge = (xs) => (now === null ? xs : [from, ...xs, now]);
    const gap = (ys) => (now === null ? ys : [null, ...ys, null]);
    plots.temp.setData([edge(tt), gap(tv), edge(tt).map(() => th.limit)]);
    plots.temp.setScale("y", { min: ranges.temp[0], max: ranges.temp[1] });

    // the board decides on P(F<30 s) >= 0.85, the twins on P(F<60 s) >= 0.5: plot what decides
    const board = s.src === "hw" && cfg.board;
    $("risk-caption").textContent = `Failure risk, next ${board ? cfg.board.horizonS : 60} s`;
    const [pt, pv] = windowed(board && cfg.board.horizonS === 30 ? s.series.p30 : s.series.p60, from);
    const lo = board ? pt.map(() => null) : windowed(s.series.p60_lo, from)[1];
    const hi = board ? pt.map(() => null) : windowed(s.series.p60_hi, from)[1];
    const hasBand = lo.some((x) => x !== null);
    const plan = board ? cfg.board.planP : cfg.planP;
    $("risk-threshold").textContent = board
      ? `acts at ${plan.toFixed(2)}`
      : `acts at ${plan.toFixed(2)} · alerts at τ = ${(f && f.model.startsWith("tgn") ? cfg.tau : cfg.warnP).toFixed(3)}`;
    const none = pt.map(() => null);
    plots.risk.setData([edge(pt), gap(hasBand ? lo : none), gap(hasBand ? hi : none), gap(pv), edge(pt).map(() => plan)]);
    if (now !== null) for (const u of [plots.temp, plots.risk]) u.setScale("x", { min: from, max: now });
  }

  // ------------------------------------------------------------------ header, footer, loop
  function renderHeader() {
    // every time on screen is message (simulation) time in UTC; the header says so when it is not real time
    const t = store.latestT;
    if (t === null) {
      $("clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";
    } else {
      const sim = Math.abs(t - Date.now() / 1000) > 5;
      $("clock").textContent = hhmmss(t) + (sim ? " UTC, simulation time" : " UTC");
    }
  }
  function renderFooter() {
    const hw = SATS.some((sat) => store.sats[sat].src === "hw");
    $("honesty").textContent = hw
      ? "Hollow circles are simulated by the digital twin. B is a physical board; its temperature is computed by its firmware. Forecasts are model outputs, not measurements."
      : "All satellites are simulated by the digital twin (hollow circles). Forecasts are model outputs, not measurements.";
  }
  function render() {
    renderHeader();
    renderSky(store.positions());
    renderChain();
    renderOptions();
    renderRisk();
    renderFooter();
  }
  window.__aurora = { store, cfg, plots }; // for screenshot scripts and debugging (set before rendering)
  setInterval(render, 1000);
  render();
})();
