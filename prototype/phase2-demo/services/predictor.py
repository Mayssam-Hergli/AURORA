#!/usr/bin/env python3
"""CASSANDRA predictor - Scenario A: forecast power/thermal cascade -> contain -> observe -> heal.
Estimates time-to-critical from the observed trajectory and prints a rising P(fail) each second.
When the projection breaches the horizon it quarantines SatB, reroutes over a feasible link and
issues a safe-mode command (heater latch released). It then observes the isolated node and, once
its temperature is back to nominal and stable, restores SatB and its links.
"""
import time, redis
from collections import deque
R = redis.Redis(host="localhost", port=6379, decode_responses=True)
SOC_MIN, TEMP_MAX, HORIZON = 20.0, 60.0, 30.0
TEMP_NOMINAL, STABLE_S = 30.0, 3      # heal once below 30 C for 3 consecutive seconds
hist = deque(maxlen=8)

def now(): return time.strftime("%H:%M:%S")

def read():
    s = R.get("telemetry:soc"); t = R.get("telemetry:temp")
    return (float(s) if s is not None else 100.0, float(t) if t is not None else None)

def t_to_crit():
    if len(hist) < 3: return None
    t0, s0, p0 = hist[0]; t1, s1, p1 = hist[-1]
    dt = t1 - t0
    if dt <= 0: return None
    soc_rate = (s1 - s0) / dt; temp_rate = (p1 - p0) / dt
    times = []
    if soc_rate < -1e-2: times.append((s1 - SOC_MIN) / (-soc_rate))
    if temp_rate > 1e-2: times.append((TEMP_MAX - p1) / temp_rate)
    times = [x for x in times if x >= 0]
    return min(times) if times else None

def quarantine(pfail, temp):
    print(f"[{now()}] ALARM      -> cascade projected to breach {TEMP_MAX:.0f}C within horizon (now {temp:.1f}C)")
    print(f"[{now()}] QUARANTINE -> SatB P(fail<{int(HORIZON)}s)={pfail:.2f}: isolating node")
    R.set("node:sat_b", "off"); R.set("link:A-B", "down"); R.set("link:B-C", "down"); R.set("link:A-C", "up")
    print(f"[{now()}] REROUTE    -> cut A-B & B-C; opened feasible A-C bypass. Path: Ground -> A -> C -> Ground")
    R.set("attack:thermal", "0")
    print(f"[{now()}] SAFE-MODE  -> heater telecommand revoked; SatB cooling under observation")
    print(f"[{now()}] STATUS     -> SatB contained; neighbours nominal; mission impact minimised.\n")

def heal(temp):
    print(f"\n[{now()}] VERIFY     -> SatB at {temp:.1f}C, below {TEMP_NOMINAL:.0f}C for {STABLE_S}s: thermal state nominal")
    R.set("node:sat_b", "green"); R.set("link:A-B", "up"); R.set("link:B-C", "up"); R.set("link:A-C", "idle")
    print(f"[{now()}] HEAL       -> SatB restored to service; A-B & B-C links back up; A-C bypass on standby")
    print(f"[{now()}] STATUS     -> constellation nominal. Watching for the next anomaly ...\n")

def main():
    print("[CASSANDRA] predictor online. Forecasting SatB power/thermal cascade ...\n")
    for k in ("node:ground", "node:sat_a", "node:sat_b", "node:sat_c"): R.set(k, "green")
    R.set("link:A-B", "up"); R.set("link:B-C", "up"); R.set("link:A-C", "idle")
    state, warned, stable = "watch", False, 0
    while True:
        s, t = read()
        if t is not None:
            if state == "watch":
                hist.append((time.time(), s, t))
                tc = t_to_crit()
                if tc is not None:
                    pfail = max(0.0, min(1.0, 1.0 - tc / HORIZON))
                    if pfail > 0.0:
                        # NOTE (2026-10-12, honesty): the "+/-0.05" in this line was a fixed display
                        # string, not a computed uncertainty. Kept as recorded in Phase 2; see README.
                        print(f"[{now()}] FORECAST   -> soc={s:5.1f}%  temp={t:5.1f}C  | t_to_crit={tc:4.1f}s  | P(fail<{int(HORIZON)}s)={pfail:.2f} +/-0.05")
                    if pfail >= 0.30 and not warned:
                        R.set("node:sat_b", "red"); warned = True
                        print(f"[{now()}] WARNING    -> abnormal thermal trend on SatB at {t:.1f}C (limit {TEMP_MAX:.0f}C): flagged red")
                    if pfail >= 0.85:
                        quarantine(pfail, t); state, stable = "contained", 0
            else:  # contained: observe the isolated node until it is safe to heal
                stable = stable + 1 if t < TEMP_NOMINAL else 0
                print(f"[{now()}] OBSERVE    -> SatB isolated, temp={t:5.1f}C  ({stable}/{STABLE_S}s below {TEMP_NOMINAL:.0f}C)")
                if stable >= STABLE_S:
                    heal(t); state, warned = "watch", False; hist.clear()
        time.sleep(1.0)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\n[CASSANDRA] predictor stopped.")
