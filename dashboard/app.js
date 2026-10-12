// AURORA dashboard (single page, MASTER_PLAN Phase 7 fallback): MQTT over WebSocket -> Store -> DOM.
// Subscribes only to telemetry, node states, links and the core's outputs. Never to gt/ or redteam/.
(function () {
  "use strict";
  const cfg = window.AURORA_DASHBOARD;
  const store = new window.AuroraStore(cfg);
  let focus = "B";
  const P = cfg.prefix;
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ tiles
  const tiles = {};
  cfg.sats.forEach((id) => {
    const el = document.createElement("div");
    el.className = "tile";
    el.innerHTML = `<span class="id">${id}</span><span class="badge emu">no data</span><span class="state"></span>
      <div class="vals"><span class="stname">nominal</span> · <span class="temp">–</span> °C · P60 <span class="pf">–</span></div>`;
    el.onclick = () => { focus = id; render(); };
    $("tiles").appendChild(el);
    tiles[id] = el;
  });

  function renderTiles() {
    cfg.sats.forEach((id) => {
      const s = store.sats[id], el = tiles[id], b = store.badge(s);
      el.className = `tile ${s.state}${id === focus ? " sel" : ""}`;
      const badge = el.querySelector(".badge");
      badge.className = `badge ${b.cls}`; badge.textContent = b.text;
      el.querySelector(".stname").textContent = s.state.replace("_", " ");
      el.querySelector(".temp").textContent = s.temp == null ? "–" : s.temp.toFixed(1);
      el.querySelector(".pf").textContent = s.pf == null ? "–" : s.pf.toFixed(2);
    });
  }

  // ------------------------------------------------------------------ charts (plain canvas)
  function chart(canvas, series, opts) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.height;
    if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.style.height = h + "px"; }
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    const tMax = store.latestT || Date.now() / 1000, tMin = tMax - (opts.window || 300);
    let lo = opts.min, hi = opts.max;
    if (lo === undefined || hi === undefined) {
      const vals = series.flatMap((s) => s.data.filter((d) => d[0] >= tMin).map((d) => d[1]));
      lo = vals.length ? Math.min(...vals) : 0; hi = vals.length ? Math.max(...vals) : 1;
      if (opts.limit !== undefined) { lo = Math.min(lo, opts.limit); hi = Math.max(hi, opts.limit); }
      const pad = (hi - lo) * 0.1 || 1; lo -= pad; hi += pad;
    }
    const X = (t) => ((t - tMin) / (tMax - tMin)) * (w - 40) + 34;
    const Y = (v) => h - 18 - ((v - lo) / (hi - lo)) * (h - 26);
    c.strokeStyle = "#24304f"; c.fillStyle = "#8a96b3"; c.font = "11px sans-serif"; c.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4, y = Y(v);
      c.beginPath(); c.moveTo(34, y); c.lineTo(w, y); c.stroke();
      c.fillText(v.toFixed(opts.digits ?? 1), 2, y + 4);
    }
    (opts.lines || []).forEach((l) => {
      c.strokeStyle = l.color; c.setLineDash([6, 4]); c.beginPath(); c.moveTo(34, Y(l.v)); c.lineTo(w, Y(l.v)); c.stroke();
      c.setLineDash([]); c.fillStyle = l.color; c.fillText(l.label, w - 80, Y(l.v) - 4);
    });
    if (opts.band) { // p_std band
      const [a, b] = opts.band;
      const A = a.filter((d) => d[0] >= tMin), B = b.filter((d) => d[0] >= tMin);
      if (A.length > 1 && A.length === B.length) {
        c.fillStyle = "rgba(147,197,253,0.25)"; c.beginPath();
        A.forEach((d, i) => (i ? c.lineTo(X(d[0]), Y(d[1])) : c.moveTo(X(d[0]), Y(d[1]))));
        for (let i = B.length - 1; i >= 0; i--) c.lineTo(X(B[i][0]), Y(B[i][1]));
        c.closePath(); c.fill();
      }
    }
    series.forEach((s) => {
      const d = s.data.filter((p) => p[0] >= tMin);
      c.strokeStyle = s.color; c.lineWidth = 2.5; c.beginPath();
      d.forEach((p, i) => (i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))));
      c.stroke();
    });
  }

  function renderFocus() {
    const s = store.sats[focus], b = store.badge(s);
    $("focus-title").textContent = `Satellite ${focus}`;
    $("focus-meta").innerHTML = `<span class="badge ${b.cls}">${b.text}</span> state <b>${s.state}</b> · mode ${s.mode || "–"} · heater ${s.heater == null ? "–" : s.heater ? "ON" : "off"}`
      + (s.nextSub ? ` · next ${s.nextSub}` : "") + (s.pMal != null ? ` · P(malicious) ${s.pMal.toFixed(2)}` : "");
    $("pf-model").textContent = s.model ? `${s.model}${s.pstd == null ? " · no ensemble band (single model)" : " · band ±1 std"}${s.warming ? " · warming up" : ""}` : "";
    chart($("chart-pf"), [{ data: s.series.pf, color: "#93c5fd" }], {
      min: 0, max: 1, digits: 2, band: s.series.pfLo.length ? [s.series.pfLo, s.series.pfHi] : null,
      lines: cfg.thresholds.map((th, i) => ({ v: th.p, label: th.label, color: i ? "#ef4444" : "#f59e0b" })),
    });
    const limit = s.src === "hw" ? 60 : 45;
    $("temp-note").textContent = `limit ${limit} °C` + (s.emulated.indexOf("temp_c") >= 0 ? " · emulated by firmware" : "");
    chart($("chart-temp"), [{ data: s.series.temp, color: "#f97316" }], { limit, lines: [{ v: limit, label: `T_max ${limit}`, color: "#ef4444" }] });
    chart($("chart-soc"), [{ data: s.series.soc, color: "#22c55e" }], { limit: 30, lines: [{ v: 30, label: "SOC_min 30", color: "#ef4444" }] });
  }

  // ------------------------------------------------------------------ side panels
  const esc = (x) => String(x).replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[ch]);
  const hhmmss = (t) => new Date(t * 1000).toTimeString().slice(0, 8);

  function renderAction() {
    const a = store.lastAction;
    if (!a) return;
    const gate = a.gated === true ? `<div class="gated">GATED: uncertainty too high, safe hold, ground alerted</div>`
      : a.gated === null ? `<div class="muted">uncertainty gate inactive (single model)</div>` : "";
    $("action").innerHTML = `<div class="act">${esc(a.action)} → ${esc(a.target)} <span class="muted">J=${a.J == null ? "–" : a.J.toFixed(2)}</span></div>
      <div class="alt">alternatives: ${(a.alternatives || []).map((x) => `${esc(x.action)} ${x.J == null ? "–" : x.J.toFixed(2)}`).join(" · ") || "none"}</div>${gate}
      <div class="muted" style="margin-top:4px">${esc(a.reason || "")}</div>`;
  }

  function renderEvents() {
    $("events").innerHTML = store.events.slice(0, 60).map((e) =>
      `<li class="${esc(e.level)}"><span class="t">${hhmmss(e.t)}</span>${esc(e.text)}${e.sparta ? ` <span class="muted">[${esc(e.sparta.join(", "))}]</span>` : ""}</li>`).join("");
  }

  function renderLinks() {
    const L = store.linkSummary();
    if (!L.total) return;
    $("links").innerHTML = `${L.feasible} of ${L.total} pairs feasible now · ${L.up} enabled` +
      (L.cut.length ? ` · <span class="cut">cut: ${L.cut.join(", ")}</span>` : " · no link cut");
  }

  function render() { renderTiles(); renderFocus(); renderAction(); renderEvents(); renderLinks(); }

  // ------------------------------------------------------------------ MQTT
  const client = window.mqtt.connect(cfg.url, { reconnectPeriod: 2000, connectTimeout: 5000, clean: true });
  const subs = [`${P}/sat/+/tm/+`, `${P}/sat/+/state`, `${P}/link/+`, `${P}/core/forecast/+`, `${P}/core/action`, `${P}/core/event`];
  client.on("connect", () => {
    $("conn").textContent = "live"; $("conn").className = "conn up";
    client.subscribe(subs, { qos: 0 });
  });
  client.on("reconnect", () => { $("conn").textContent = "reconnecting…"; $("conn").className = "conn down"; });
  client.on("close", () => { $("conn").textContent = "disconnected"; $("conn").className = "conn down"; });
  client.on("message", (topic, payload) => {
    try { store.handle(topic, JSON.parse(payload.toString())); } catch (e) { /* malformed: ignored */ }
  });
  setInterval(() => { $("clock").textContent = new Date().toTimeString().slice(0, 8); render(); }, 1000);
  window.addEventListener("resize", render);
  render();
})();
