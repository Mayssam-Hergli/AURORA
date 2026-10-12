// Dashboard settings. Any of them can be overridden in the URL, e.g.
//   http://localhost:8080/?broker=ws://192.168.1.10:9001&focus=E&act=0.85
window.AURORA_DASH = {
  broker: "ws://" + (location.hostname || "localhost") + ":9001", // Mosquitto WebSocket listener
  warn: 0.3, // flag line on the P(F) chart: the trajectory forecaster's nominal -> flagged (cassandra/config.yaml
  //           act.warn_p); twin satellites on the ensemble flag at tau from calibration.json (~0.015)
  act: 0.5, // plan line: from P(F<60 s) >= 0.50 the core scores the responses with J (act.plan_p); J picks the
  //          action (often cut_heater), so crossing it does not mean quarantine
  historyS: 900, // history kept per series: 15 min of message time
  windowS: 300, // seconds shown on the charts
  staleS: 5, // a tile says "no data" after this many seconds without telemetry
  autoFocus: true, // focus the satellite whose state most recently became flagged / quarantined
  focus: "B", // focused satellite at start (B is the hardware slot in the final demo)
};
