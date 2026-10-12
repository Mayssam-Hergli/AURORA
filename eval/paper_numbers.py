"""Write every number quoted in the paper, the technical report and the README from the result files.

    python -m eval.paper_numbers            # rewrites the RESULTS-BEGIN/END and RESULTS:START/END blocks
    python -m eval.paper_numbers --check    # only lists the macros the documents use but the results lack

Sources (nothing is typed by hand): results/eval/metrics.json, closed_loop.json, robustness.json,
results/latency.json, results/datasets/*.json. A macro that a document uses but that the results do not
provide is defined as a red "n/a", so the document still compiles and the gap is visible.
"""

from __future__ import annotations

import argparse
import json
import math
import re

from ml import config

EV = config.RESULTS / "eval"
DOCS = [
    config.ROOT / "docs" / "paper" / "CASSANDRA_paper_updated.tex",
    config.ROOT / "docs" / "technical_report" / "CASSANDRA_ML_Technical_Report.tex",
]
README = config.ROOT / "README.md"

SPLITS = {"test_id": "ID", "test_ood": "OOD", "test_real": "Real", "test_hard": "Hard"}
METHODS = {
    "ensemble": "Ens",
    "single": "Single",
    "no_eng": "NoEng",
    "unconstrained": "Uncon",
    "no_residual": "NoRes",
    "threshold": "Thr",
    "trajectory": "Traj",
    "lstm_ae": "Ae",
    "gdn": "Gdn",
    "ensemble@0.85": "Act",
    "no_sparta": "NoSparta",
    "no_coupling": "NoCoupling",
    "temporal": "Temporal",
    "telemetry_only": "TelemetryOnly",
    "ensemble@10": "EnsHTen",
    "ensemble@30": "EnsHThirty",
    "ensemble@60": "EnsHSixty",
    "ensemble@120": "EnsHTwoMin",
}
DIGITS = dict(
    zip("0123456789", ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"])
)
HORIZON_NAME = {"10": "Ten", "30": "Thirty", "60": "Sixty", "120": "TwoMin"}


def _load(path):
    return json.loads(path.read_text()) if path.exists() else None


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _word(s: str) -> str:
    """A1 -> AOne: LaTeX macro names cannot contain digits."""
    return "".join(DIGITS.get(ch, ch) for ch in s).replace("_", "")


class Numbers(dict):
    def put(self, name, value, fmt="{:.2f}"):
        if _ok(value):
            self[name] = fmt.format(value)

    def lead(self, name, value):
        self.put(name, value, "{:.0f}")

    def lead_if_detected(self, name, v: dict):
        """A median lead is undefined when nothing was detected: write a dash, not 0."""
        if _ok(v.get("recall")) and v["recall"] == 0:
            self[name] = "--"
        else:
            self.lead(name, v.get("median_lead_s"))

    def pct(self, name, value, nd=0):
        self.put(name, None if not _ok(value) else 100 * value, f"{{:.{nd}f}}")


def collect() -> Numbers:
    N = Numbers()
    met = _load(EV / "metrics.json")
    if met:
        N.put("rTemp", met["temperature"])
        N.put("rEnsIDThresh", met["tau"].get("ensemble"))
        n_test, ens_leads, thr_leads = 0, [], []
        for split, sk in SPLITS.items():
            R = met["splits"].get(split)
            if not R:
                continue
            for m, mk in METHODS.items():
                v = R["methods"].get(m)
                if not v:
                    continue
                p = f"r{mk}{sk}"
                N.lead_if_detected(p + "Lead", v)
                N.put(p + "Rec", v["recall"])
                N.lead(p + "Mean", v["mean_lead_s"])
                N.lead(p + "Delay", v.get("median_delay_s"))
                if v.get("median_lead_ci"):
                    N.lead(p + "LeadLo", v["median_lead_ci"][0])
                    N.lead(p + "LeadHi", v["median_lead_ci"][1])
                if v.get("recall_ci"):
                    N.put(p + "RecLo", v["recall_ci"][0])
                    N.put(p + "RecHi", v["recall_ci"][1])
                N.put(p + "Auroc", v.get("auroc"), "{:.3f}")
                N.put(p + "Auprc", v.get("auprc"))
                N.pct(p + "Vfpr", v["realized_fpr"], 2)
                N.put(p + "Fah", v.get("false_alarms_per_sat_hour"))
                ep = v["episode"]
                N.put(p + "EpP", ep["reaches_F"]["precision"])
                N.put(p + "EpR", ep["reaches_F"]["recall"])
                N.put(p + "AttP", ep["attack"]["precision"])
                N.put(p + "AttR", ep["attack"]["recall"])
            ens = R["methods"]["ensemble"]
            N.put(f"rNfail{sk}", ens["failing"], "{:d}")
            ens_leads.append(ens["median_lead_s"])
            if "threshold" in R["methods"]:
                thr_leads.append(R["methods"]["threshold"]["median_lead_s"])
            for m in (
                "ensemble",
                "threshold",
                "trajectory",
                "lstm_ae",
                "gdn",
                "no_eng",
                "unconstrained",
                "no_residual",
            ):
                for g, v in R.get("by_scenario", {}).get(m, {}).items():
                    N.lead_if_detected(f"r{METHODS[m]}{sk}Scen{g}Lead", v)
                    N.put(f"r{METHODS[m]}{sk}Scen{g}Rec", v["recall"])
                for g, v in R.get("by_precursor", {}).get(m, {}).items():
                    N.lead_if_detected(f"r{METHODS[m]}{sk}Pre{g.capitalize()}Lead", v)
                    N.put(f"r{METHODS[m]}{sk}Pre{g.capitalize()}Rec", v["recall"])
            ens_b = R.get("by_scenario", {}).get("ensemble", {}).get("B")
            if ens_b and ens["failing"]:
                N.pct(f"rMaskShare{sk}", ens_b["failing"] / ens["failing"])
            for t, v in R.get("by_type", {}).items():
                N.lead_if_detected(f"rEns{sk}Type{_word(t)}Lead", v)
                N.put(f"rEns{sk}Type{_word(t)}Rec", v["recall"])
                N.put(f"rEns{sk}Type{_word(t)}N", v["failing"], "{:d}")
            for h, e in R["calibration"].items():
                N.put(f"rEce{sk}{HORIZON_NAME.get(h, h)}", e, "{:.3f}")
            N.put(f"rEce{sk}Cal", R["calibration"].get("60"), "{:.3f}")
            N.pct(f"rNext{sk}Top", R["next_sub"]["top1"])
            N.pct(f"rNext{sk}TopThree", R["next_sub"]["top3"])
            N.put(f"rMal{sk}Auroc", R["malicious"]["auroc"], "{:.3f}")
            N.pct(f"rMal{sk}Acc", R["malicious"]["accuracy"])
            N.pct(f"rQgate{sk}", R["uncertainty"]["frac_steps_q_above_gate"], 3)
            N.put(f"rQmed{sk}", R["uncertainty"]["median_q_benign"], "{:.3f}")
            n_test += len(_episodes(split))
        N.put("rNtestEp", n_test, "{:,d}")
        if ens_leads:
            N.lead("rEnsLeadMin", min(ens_leads))
            N.lead("rEnsLeadMax", max(ens_leads))
        if thr_leads:
            N.lead("rThrLeadMin", min(thr_leads))
            N.lead("rThrLeadMax", max(thr_leads))

    cl = _load(EV / "closed_loop.json")
    if cl:
        s = cl["summary"]
        N.put("rClEp", s["episodes"], "{:d}")
        N.put("rClAtt", sum(e["type"][0] in "ABC" for e in cl["episodes"]), "{:d}")
        N.put("rClNom", sum(e["type"][0] in "NF" for e in cl["episodes"]), "{:d}")
        N.put("rClFailWithout", s["failing_without"], "{:d}")
        N.put("rClAverted", s["prevented"], "{:d}")
        N.pct("rClAvertedPct", s["prevented_frac"])
        N.put("rClFailWith", s["failing_without"] - s["prevented"] + s["new_failures"], "{:d}")
        N.put("rClNewFail", s["new_failures"], "{:d}")
        N.pct("rClAvail", s["mean_availability"], 2)
        N.put("rClFalse", s["false_containments"], "{:d}")
        N.put("rClFalsePerOrbit", s["false_containments_per_orbit"])
        N.put("rClGated", s["gated"], "{:d}")
        acts = s["actions"]
        N.put("rClDecisions", sum(acts.values()), "{:d}")
        for a, k in (
            ("safe_mode", "rClSafe"),
            ("block_source", "rClBlock"),
            ("cut_heater", "rClIso"),
            ("quarantine_reroute", "rClReroute"),
        ):
            N.put(k, acts.get(a, 0), "{:d}")
        by_type: dict[str, list[int]] = {}
        for r in cl["episodes"]:
            fw, fwith = set(r["failing_without"]), set(r["failing_with"])
            t = by_type.setdefault(r["type"], [0, 0])
            t[0] += len(fw)
            t[1] += len(fw - fwith)
        for t, (fail, averted) in by_type.items():
            N.put(f"rClFail{_word(t)}", fail, "{:d}")
            N.put(f"rClAvert{_word(t)}", averted, "{:d}")
        N.put("rLatPlan", s["plan_ms_median"], "{:.0f}")
        plans = [x for r in cl["episodes"] for x in r["plan_ms"]]
        if plans:
            N.put("rLatPlanRatio", s["plan_ms_median"] / 150.0, "{:.1f}")
            N.put("rLatPlanMin", min(plans), "{:.0f}")
            N.put("rLatPlanMax", max(plans), "{:.0f}")
            N.put("rClPlans", len(plans), "{:d}")
        N.put("rClStepMs", s["step_ms_median"], "{:.0f}")
        none_by_type: dict[str, int] = {}
        for r in cl["episodes"]:
            none_by_type[r["type"]] = none_by_type.get(r["type"], 0) + len(r["plan_ms"]) - len(r["actions"])
        N.put("rClNoneTotal", sum(none_by_type.values()), "{:d}")
        for t, n in none_by_type.items():
            N.put(f"rClNone{_word(t)}", n, "{:d}")
        N.put("rLatPlanP", s["plan_ms_p95"], "{:.0f}")

    rob = _load(EV / "robustness.json")
    if rob:
        N.put("rRobN", rob["episodes"], "{:d}")
        for eps, k in (("0", "Zero"), ("0.0", "Zero"), ("0.25", "A"), ("0.5", "B"), ("1", "C"), ("1.0", "C")):
            v = rob["results"].get(f"eps={eps}")
            if v:
                N.put(f"rRob{k}Rec", v["recall"])
                N.lead(f"rRob{k}Lead", v["median_lead_s"])
                N.put(f"rRob{k}Q", v["mean_q"])
                N.put(f"rRob{k}ThrRec", v["threshold_recall"])
                N.lead(f"rRob{k}ThrLead", v["threshold_median_lead_s"])

    best = {}
    for log in sorted((config.RESULTS / "logs").glob("train_*.log")):
        m = re.search(r"done, best val ([0-9.]+)", log.read_text())
        if m:
            best[log.stem.replace("train_", "")] = float(m.group(1))
    full = [v for k, v in best.items() if k.startswith("full_")]
    if full:
        N.put("rValFullMin", min(full), "{:.3f}")
        N.put("rValFullMax", max(full), "{:.3f}")
    for k, name in (
        ("no_eng_s0", "rValNoEng"),
        ("unconstrained_s0", "rValUncon"),
        ("no_residual_s0", "rValNoRes"),
    ):
        N.put(name, best.get(k), "{:.3f}")

    fx = _load(EV / "fig_example.json")
    if fx:
        N["rFigEpisode"] = fx["episode"].replace("_", r"\_")
        N.put("rFigAlertDelay", fx["alert_t"] - fx["onset_t"], "{:.0f}")
        N.put("rFigFallBefore", fx["crit_t"] - fx["fall_t"], "{:.0f}")

    st = _load(EV / "stress.json")
    if st:
        N.put("rStressN", st["attack_episodes"], "{:d}")
        for variant, k in (("original", "Orig"), ("spoof_ground", "Spoof"), ("no_commands", "NoCmd")):
            v = st["variants"].get(variant)
            if v:
                N.put(f"rStress{k}Rec", v["recall"])
                N.lead(f"rStress{k}Lead", v["median_lead_s"])
                N.put(f"rStress{k}Fail", v["failing"], "{:d}")

    lat = _load(config.RESULTS / "latency.json")
    if lat:
        one = next((x for x in lat["latency"] if x["threads"] == 1), lat["latency"][0])
        N.put("rLatStep", one["median_ms"], "{:.1f}")
        N.put("rLatStepP", one["p95_ms"], "{:.1f}")
        four = next((x for x in lat["latency"] if x["threads"] == 4), None)
        if four:
            N.put("rLatStepFour", four["median_ms"], "{:.1f}")

    cu = _load(config.RESULTS / "datasets" / "cucd_audit.json")
    if cu:
        N.pct("rCucdShortcut", cu["shortcut_accuracy_time_split"]["memory_only"], 2)
        N.pct("rCucdTraffic", cu["shortcut_accuracy_time_split"]["traffic_only"], 2)
        lo = cu["leave_one_attack_out"]
        N.put("rCucdLoaoMin", min(lo.values()))
        N.put("rCucdLoaoMax", max(lo.values()))
        N.pct("rCucdDrift", cu["normal_score_drift"]["frac_test_normal_above_0.95"])
    sc = _load(config.RESULTS / "datasets" / "scad_profile.json")
    if sc:
        N.put("rScadRuns", sc["attack_runs"], "{:d}")
        N.pct("rScadReachF", sc["frac_reaching_F"], 1)
        N.put("rScadPointing", sc["pointing_dev_deg"], "{:g}")
        for sub, v in sc["subsystems"].items():
            k = "".join(w.capitalize() for w in sub.split("_"))
            N.pct(f"rScad{k}Frac", v["frac_affected"])
            N.put(f"rScad{k}Delay", v["median_delay_s"], "{:g}")
    return N


def _episodes(split):
    p = config.EPISODES / split / "index.csv"
    if not p.exists():
        return []
    return p.read_text().strip().splitlines()[1:]


# ----------------------------------------------------------------------------- writers
# the marker lines themselves: "% -------- RESULTS-BEGIN" (a comment that merely mentions the
# marker names, e.g. in a file header, must not match)
BEGIN = re.compile(r"^%\s*-{5,}\s*RESULTS-BEGIN\s*$", re.M)
END = re.compile(r"^%\s*-{5,}\s*RESULTS-END\s*$", re.M)
MACRO_USE = re.compile(r"\\(r[A-Z][A-Za-z]*)")
LATEX_BUILTINS = {"rVert", "rAngle"}  # \rVert is amsmath's norm bar, not a result


def used_macros(tex: str) -> set[str]:
    e = END.search(tex)
    return set(MACRO_USE.findall(tex[e.end() :] if e else tex)) - LATEX_BUILTINS


def write_tex(path, N: Numbers) -> list[str]:
    tex = path.read_text(encoding="utf-8")
    bs, es = list(BEGIN.finditer(tex)), list(END.finditer(tex))
    if len(bs) != 1 or len(es) != 1 or bs[0].start() > es[0].start():
        raise SystemExit(
            f"{path}: need exactly one RESULTS-BEGIN line before one RESULTS-END line "
            f"(found {len(bs)} / {len(es)}); nothing written"
        )
    b, e = bs[0], es[0]
    used = used_macros(tex)
    missing = sorted(u for u in used if u not in N)
    lines = ["% (generated by eval/paper_numbers.py from results/ -- do not edit by hand)"]
    lines += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(N.items())]
    if missing:
        lines.append("% used in the text but not produced by the results (shown in red):")
        lines += [f"\\newcommand{{\\{k}}}{{\\textcolor{{red}}{{n/a}}}}" for k in missing]
    tex = tex[: b.end()] + "\n" + "\n".join(lines) + "\n" + tex[e.start() :]
    path.write_text(tex, encoding="utf-8")
    return missing


def readme_block(N: Numbers) -> str:
    lines = [
        "Fresh hardened run; values generated from results/eval/metrics.json.",
        "",
        "| Split | Recall | Median lead (s) | Realized benign-step FPR (%) |",
        "|---|---:|---:|---:|",
    ]
    for split, label in (
        ("ID", "Synthetic ID"),
        ("OOD", "Synthetic OOD"),
        ("Real", "Synthetic + OPS-SAT noise/gaps"),
        ("Hard", "Matched-origin hard set"),
    ):
        p = "rEns" + split
        lines.append(
            f"| {label} | {N.get(p+'Rec', 'n/a')} | {N.get(p+'Lead', 'n/a')} | " f"{N.get(p+'Vfpr', 'n/a')} |"
        )
    lines += [
        "",
        "Thresholds are selected on validation; the 0.1% target is not a guarantee on test.",
        "The fixed 0.85 operating point is forecast evaluation, not the runtime policy.",
        "See docs/HARDENING_REPORT.md and results/FINAL_RUN_MANIFEST.json for paired runtime results, "
        "confidence intervals, provenance and limitations.",
    ]
    return "\n".join(lines)


def write_readme(N: Numbers):
    if not README.exists():
        return
    s = README.read_text(encoding="utf-8")
    a, b = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
    if a not in s or b not in s:
        return
    s = s[: s.index(a) + len(a)] + "\n" + readme_block(N) + "\n" + s[s.index(b) :]
    README.write_text(s, encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    N = collect()
    print(f"{len(N)} numbers from results/")
    for p in DOCS:
        if not p.exists():
            continue
        if a.check:
            miss = sorted(u for u in used_macros(p.read_text(encoding="utf-8")) if u not in N)
        else:
            miss = write_tex(p, N)
        print(
            f"{p.relative_to(config.ROOT)}: {len(miss)} macros without a result"
            + (f": {' '.join(miss)}" if miss else "")
        )
    if not a.check:
        write_readme(N)


if __name__ == "__main__":
    main()
