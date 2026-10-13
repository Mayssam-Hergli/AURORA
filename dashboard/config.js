// Dashboard settings. Any of them can be overridden in the URL, e.g.
//   http://localhost:8080/?broker=ws://192.168.1.10:9001&theme=dark&follow=E
window.AURORA_DASH = {
  broker: "ws://" + (location.hostname || "localhost") + ":9001", // Mosquitto WebSocket listener
  theme: "light", // "light" (projector) or "dark"; same colour semantics
  follow: "", // pin the bottom strip to one satellite; empty = follow the satellite at risk
  fallback: "B", // the bottom strip shows this one while everything is nominal (B = the board)
  planP: 0.5, // decision threshold on the risk plot: from here the core compares responses (act.plan_p)
  calmP: 0.1, // below this P(F<60 s) a satellite is calm (act.calm_p)
  healBelowC: 30, // a contained satellite under this temperature is healing (satellites.heal_below_c)
  // fixed temperature axis = ambient - 5 .. limit + 5 (cassandra/config.yaml satellites)
  thermal: { twin: { ambient: 10, limit: 45 }, hw: { ambient: 25, limit: 60 } }, // twin buses run 8-25 C
  historyS: 900, // history kept per series: 15 min of message time
  windowS: 300, // seconds shown on the plots
  staleS: 5, // no telemetry for this long: the satellite is shown as silent
  timelineLines: 12, // lines shown in "What CASSANDRA is doing"
};
