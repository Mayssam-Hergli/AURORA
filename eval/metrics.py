"""Metrics of docs/CONTEXT.md Section 5.8.

Conventions (per satellite s of episode e; ticks are 1 s):
    benign timesteps  every tick of satellites in clean and fault episodes that never reach F
    tau               (1 - target_fpr) quantile of a method's scores on benign timesteps
    T_alert           first tick with score >= tau, counted from tick 0 (M3). If it falls before the
                      episode's first anomaly onset it is a false alarm (`pre_onset_alarm`, and
                      pre_onset_false_alarms below); detection and lead then use the first alert at
                      or after onset, so an early false alarm never earns lead time
    T_lead            T_critical - T_alert for satellites that reach F (responses disabled);
                      alerts at or after T_critical are misses
    detection delay   T_alert - onset of that satellite (negative = predictive warning, e.g. a
                      Scenario C neighbour flagged before the attack reaches it)
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score


def benign_mask(truth: dict, kinds: np.ndarray) -> np.ndarray:
    """[n, S] satellites whose every tick is benign."""
    benign_ep = np.isin(kinds, ["clean", "fault"])[:, None]
    return benign_ep & (truth["crit"] < 0)


def tau_at_fpr(score: np.ndarray, benign: np.ndarray, fpr: float) -> float:
    s = score.transpose(0, 2, 1)[benign]  # [k, T]
    s = s[np.isfinite(s)]
    return float(np.quantile(s, 1 - fpr, method="higher"))


def realized_fpr(score, benign, tau) -> float:
    s = score.transpose(0, 2, 1)[benign]
    return float((s >= tau).mean())


def false_alarms_per_hour(score, benign, tau, dt_s: float = 1.0) -> float:
    """Alarm events (rising edges of score >= tau) per benign satellite-hour."""
    a = score.transpose(0, 2, 1)[benign] >= tau  # [k, T]
    if a.size == 0:
        return np.nan
    events = a[:, 0].sum() + (a[:, 1:] & ~a[:, :-1]).sum()
    return float(events / (a.size * dt_s / 3600))


def alerts(score, truth, tau) -> list[dict]:
    rows = []
    n, T, S = score.shape
    for e in range(n):
        on = truth["onset"][e]
        if (on < 0).all():
            continue
        start = int(on[on >= 0].min())
        for s in range(S):
            tc = int(truth["crit"][e, s])
            above = score[e, :, s] >= tau
            t_alert_any = int(np.argmax(above)) if above.any() else -1
            hit = np.flatnonzero(above[start:])
            t_alert = start + int(hit[0]) if len(hit) else -1
            if tc >= 0:
                detected = 0 <= t_alert < tc
                rows.append(
                    dict(
                        episode=e,
                        sat=s,
                        failing=True,
                        t_crit=tc,
                        t_alert=t_alert,
                        t_alert_any=t_alert_any,
                        pre_onset_alarm=bool(0 <= t_alert_any < start),
                        detected=detected,
                        lead=tc - t_alert if detected else 0.0,
                        delay=(t_alert - int(on[s])) if detected and on[s] >= 0 else np.nan,
                        malicious=int(truth["malicious"][e, s]),
                    )
                )
    return rows


def pre_onset_false_alarms(score, truth, kinds, tau, dt_s: float = 1.0) -> dict:
    """M3: alarms in attack episodes before the first anomaly onset are false alarms (every satellite)."""
    events, sat_s, alarmed = 0, 0.0, 0
    for e in np.flatnonzero(np.asarray(kinds) == "attack"):
        on = truth["onset"][e]
        if (on < 0).all() or on[on >= 0].min() <= 0:
            continue
        a = score[e, : int(on[on >= 0].min())] >= tau  # [ticks before onset, S]
        events += int(a[0].sum() + (a[1:] & ~a[:-1]).sum())
        sat_s += a.size * dt_s
        alarmed += int(a.any(0).sum())
    return dict(
        pre_onset_alarm_events=events,
        pre_onset_sats_alarmed=alarmed,
        pre_onset_false_alarms_per_sat_hour=events / (sat_s / 3600) if sat_s else np.nan,
    )


def lead_summary(rows: list[dict]) -> dict:
    if not rows:
        return dict(
            failing=0,
            recall=np.nan,
            median_lead_s=np.nan,
            iqr_lead_s=[np.nan, np.nan],
            mean_lead_s=np.nan,
            median_delay_s=np.nan,
        )
    det = np.array([r["detected"] for r in rows])
    lead = np.array([r["lead"] for r in rows], float)
    delay = np.array([r["delay"] for r in rows], float)
    q = np.percentile(lead[det], [25, 75]) if det.any() else [np.nan, np.nan]
    return dict(
        failing=len(rows),
        recall=float(det.mean()),
        median_lead_s=float(np.median(lead[det])) if det.any() else 0.0,
        iqr_lead_s=[float(q[0]), float(q[1])],
        mean_lead_s=float(lead.mean()),
        median_delay_s=float(np.nanmedian(delay)) if np.isfinite(delay).any() else np.nan,
    )


def bootstrap(rows: list[dict], n_boot: int = 2000, seed: int = 0) -> dict:
    if not rows:
        return {}
    rng = np.random.default_rng(seed)
    by_ep: dict[int, list] = {}
    for r in rows:
        by_ep.setdefault(r["episode"], []).append(r)
    eps = list(by_ep)
    rec, med = [], []
    for _ in range(n_boot):
        rr = [r for e in rng.choice(eps, len(eps)) for r in by_ep[e]]
        det = np.array([r["detected"] for r in rr])
        rec.append(det.mean())
        med.append(np.median([r["lead"] for r in rr if r["detected"]]) if det.any() else 0.0)
    return dict(
        recall_ci=[float(x) for x in np.percentile(rec, [2.5, 97.5])],
        median_lead_ci=[float(x) for x in np.percentile(med, [2.5, 97.5])],
    )


def episode_pr(score, truth, kinds, tau) -> dict:
    """Episode level: predicted positive if any satellite alerts at any tick."""
    alert_ep = (score >= tau).any((1, 2))
    reach = (truth["crit"] >= 0).any(1)
    attack = kinds == "attack"
    out = {}
    for name, pos in (("reaches_F", reach), ("attack", attack)):
        tp, fp, fn = int((alert_ep & pos).sum()), int((alert_ep & ~pos).sum()), int((~alert_ep & pos).sum())
        out[name] = dict(precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1))
    return out


def next_sub_accuracy(probs: np.ndarray, targets: np.ndarray) -> dict:
    """probs [k, 6], targets [k] with -1 = undefined."""
    ok = targets >= 0
    if not ok.any():
        return dict(top1=np.nan, top3=np.nan, n=0)
    order = np.argsort(-probs[ok], -1)
    t = targets[ok]
    return dict(
        top1=float((order[:, 0] == t).mean()),
        top3=float((order[:, :3] == t[:, None]).any(1).mean()),
        n=int(ok.sum()),
    )


def malicious_auroc(p: np.ndarray, y: np.ndarray) -> dict:
    ok = y >= 0
    if ok.sum() < 2 or len(np.unique(y[ok])) < 2:
        return dict(auroc=np.nan, accuracy=np.nan, n=int(ok.sum()))
    return dict(
        auroc=float(roc_auc_score(y[ok], p[ok])),
        accuracy=float(((p[ok] > 0.5) == y[ok]).mean()),
        n=int(ok.sum()),
    )
