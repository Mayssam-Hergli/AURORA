// Dashboard settings. The broker's WebSocket listener is on 9001 (ground/mosquitto/mosquitto.conf).
window.AURORA_DASHBOARD = {
  url: "ws://" + (location.hostname || "localhost") + ":9001",
  prefix: "aurora/v1",
  sats: "ABCDEFGHIJKL".split(""),
  historySeconds: 900, // ring buffer per series (15 min), no database
  // threshold lines on the P(F) chart: the core's decision levels (cassandra/config.yaml act.*)
  thresholds: [
    { p: 0.30, label: "flag 0.30" },
    { p: 0.50, label: "plan 0.50" },
  ],
  maxEvents: 200,
};
