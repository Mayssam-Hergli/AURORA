// Dashboard settings. Any of them can be overridden in the URL, e.g.
//   http://localhost:8080/?broker=ws://192.168.1.10:9001&focus=E&act=0.85
window.AURORA_DASH = {
  broker: "ws://" + (location.hostname || "localhost") + ":9001", // Mosquitto WebSocket listener
  warn: 0.3, // flag threshold drawn on the P(F) chart (core: nominal -> flagged)
  act: 0.85, // act threshold drawn on the P(F) chart (core: flagged -> quarantined)
  historyS: 900, // history kept per series: 15 min of message time
  windowS: 300, // seconds shown on the charts
  staleS: 5, // a tile says "no data" after this many seconds without telemetry
  autoFocus: true, // focus the satellite whose state most recently became flagged / quarantined
  focus: "B", // focused satellite at start (B is the hardware slot in the final demo)
};
