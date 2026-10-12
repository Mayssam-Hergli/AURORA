"""Write manuscript and hardening-report tables directly from the fresh result set."""

from __future__ import annotations

import json
import math

from ml import config


def number(value, digits=2):
    return f"{value:.{digits}f}" if value is not None and math.isfinite(value) else "--"


def interval(value, bounds, digits=2):
    return number(value, digits) + (
        " [" + ", ".join(number(x, digits) for x in bounds) + "]" if bounds else ""
    )


def table(header, rows, caption):
    columns = "l" + "r" * (len(header) - 1)
    body = [
        "\\begin{table}[ht]",
        "\\centering\\scriptsize",
        f"\\begin{{tabular}}{{{columns}}}",
        "\\toprule",
        " & ".join(header) + r" \\",
        r"\midrule",
    ]
    body += [" & ".join(map(str, row)) + r" \\" for row in rows]
    return "\n".join(body + [r"\bottomrule", r"\end{tabular}", f"\\caption{{{caption}}}", r"\end{table}"])


def write():
    ev = config.RESULTS / "eval"
    met = json.loads((ev / "metrics.json").read_text())
    runtime = json.loads((ev / "runtime_policy.json").read_text())
    robust = json.loads((ev / "robustness.json").read_text())
    latency = json.loads((config.RESULTS / "latency.json").read_text())
    parts = [
        "All tables in this revision are generated from the fresh hardened result JSON. "
        "Brackets denote 95\\% episode-bootstrap confidence intervals where available. "
        "The original dirty-revision manifest and historical prototype timings are excluded."
    ]
    labels = {
        "test_id": "Synthetic ID",
        "test_ood": "Synthetic OOD",
        "test_real": "Synthetic + noise",
        "test_hard": "Matched-origin",
    }
    rows = []
    for split, label in labels.items():
        v = met["splits"][split]["methods"]["ensemble"]
        rows.append(
            [
                label,
                interval(v["recall"], v.get("recall_ci")),
                interval(v["median_lead_s"], v.get("median_lead_ci"), 0),
                number(100 * v["realized_fpr"]),
            ]
        )
    parts.append(
        table(
            ["Split", "Recall", "Lead (s)", "FPR (\\%)"],
            rows,
            "Five-model ensemble; validation-selected thresholds. Lead is conditional on detection.",
        )
    )
    rows = []
    for key, label in [
        ("ensemble", "Ensemble"),
        ("single", "Single full"),
        ("no_eng", "No engineering"),
        ("unconstrained", "Unconstrained"),
        ("no_residual", "No residuals"),
        ("no_sparta", "No SPARTA"),
        ("no_coupling", "No coupling"),
        ("temporal", "Temporal only"),
        ("telemetry_only", "Telemetry only"),
        ("threshold", "Static limits"),
        ("trajectory", "Trajectory"),
        ("lstm_ae", "LSTM-AE"),
        ("gdn", "GDN-style"),
    ]:
        values = [met["splits"][split]["methods"][key] for split in labels]
        rows.append([label, *[number(x["recall"]) for x in values], number(values[0]["median_lead_s"], 0)])
    parts.append(
        table(
            ["Method", "ID", "OOD", "Noise", "Hard", "ID lead"],
            rows,
            "Recall by split and ID median lead in seconds. Ablations use seed 0; "
            "the ensemble uses five seeds, so ensemble-versus-ablation differences combine both changes.",
        )
    )
    fixed = met["splits"]["test_id"]["methods"]["ensemble@0.85"]
    parts.append(
        f"At the separate fixed 0.85 forecast operating point, ID recall is "
        f'{number(fixed["recall"])} and median detected lead is '
        f'{number(fixed["median_lead_s"],0)} seconds. These are not autonomous-policy outcomes. '
        "Component benefit should be assessed against the single full model as well as the ensemble; "
        "small differences on generated cases do not establish general causal attribution."
    )
    rows = []
    for mismatch in (False, True):
        rr = [r for r in runtime["rows"] if r["mismatch"] == mismatch]
        before = sum(len(r["baseline"]["failed_sats"]) for r in rr)
        after = sum(len(r["defended"]["failed_sats"]) for r in rr)
        prevented = sum(len(r["prevented"]) for r in rr)
        new = sum(len(r["new_failures"]) for r in rr)
        false = sum(
            any(a["action"] != "none" for a in r["defended"]["actions"])
            for r in rr
            if r["scenario"] == "clean"
        )
        rows.append(["Mismatch" if mismatch else "Nominal", before, after, prevented, new, false])
    parts.append(
        table(
            ["Plant", "Base F", "Def. F", "Prevented", "New F", "Clean acts"],
            rows,
            f'Actual-core paired campaign ({runtime["episodes"]} episodes). Failures are '
            "satellite-run crossings; clean acts counts clean episodes with any protective action. "
            "Small seed counts limit inference.",
        )
    )
    gated = sum(a.get("gated") is True for r in runtime["rows"] for a in r["defended"]["actions"])
    availability = sum(r["defended"]["mean_availability"] for r in runtime["rows"]) / len(runtime["rows"])
    parts.append(
        f"The runtime campaign records {gated} gated decision records, including no-action records. "
        f"Mean defended nominal-mode availability is {number(100*availability)}\\%. "
        "Complete transitions, action traces, gate states and per-run critical times are retained in "
        "\\texttt{runtime\\_policy.json}. Thermal mismatch results provide a limited sensitivity test; "
        "wheel and other model discrepancies remain untested."
    )
    rows = []
    for budget, v in robust["results"].items():
        ci = v.get("ensemble_ci", {})
        rows.append(
            [
                budget.split("=")[1],
                interval(v["recall"], ci.get("recall_ci")),
                interval(v["median_lead_s"], ci.get("median_lead_ci"), 0),
                number(v["mean_q"]),
            ]
        )
    parts.append(
        table(
            ["Budget (SD)", "Recall", "Lead (s)", "Mean Q"],
            rows,
            f'Iterative telemetry suppression on {robust["episodes"]} type-stratified satellite cases, '
            f'{robust["steps"]} steps. Confidence intervals resample episodes; '
            "the attack objective minimizes summed forecast probability.",
        )
    )
    measurement = next(x for x in latency["latency"] if x["threads"] == 1)
    parts.append(
        f"On this x86 workstation, ensemble inference alone takes "
        f'{number(measurement["median_ms"],1)} ms median and '
        f'{number(measurement["p95_ms"],1)} ms at the 95th percentile '
        f'({measurement["threads"]} thread). This excludes planning, is affected by host load, '
        "and does not establish the 150 ms inference-plus-planning target on an embedded ARM system."
    )
    narrative = "\n\n".join(parts)
    for name in [
        "docs/paper/CASSANDRA_paper_updated.tex",
        "docs/technical_report/CASSANDRA_ML_Technical_Report.tex",
    ]:
        path = config.ROOT / name
        text = path.read_text()
        begin, end = "% RESULTS-NARRATIVE-BEGIN", "% RESULTS-NARRATIVE-END"
        assert text.count(begin) == text.count(end) == 1
        path.write_text(
            text[: text.index(begin) + len(begin)] + "\n" + narrative + "\n" + text[text.index(end) :]
        )
    report = config.ROOT / "docs/HARDENING_REPORT.md"
    text = report.read_text()
    begin, end = "<!-- HARDENING-RESULTS:START -->", "<!-- HARDENING-RESULTS:END -->"
    markdown = ["| Split | Recall | Median lead (s) | Realized FPR (%) |", "|---|---:|---:|---:|"]
    for split, label in labels.items():
        v = met["splits"][split]["methods"]["ensemble"]
        markdown.append(
            f'| {label} | {number(v["recall"])} | {number(v["median_lead_s"],0)} | '
            f'{number(100*v["realized_fpr"])} |'
        )
    markdown += [
        "",
        "Runtime outcomes, ablations, PGD intervals and latency are tabulated in the updated paper. "
        "The complete measured records are frozen under `results/frozen/`.",
    ]
    assert text.count(begin) == text.count(end) == 1
    report.write_text(
        text[: text.index(begin) + len(begin)] + "\n" + "\n".join(markdown) + "\n" + text[text.index(end) :]
    )


if __name__ == "__main__":
    write()
