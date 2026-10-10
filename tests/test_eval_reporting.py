"""Reporting: false-alarm counting and the generated macro block of the paper and report."""

import numpy as np

from eval import metrics as M
from eval import paper_numbers as PN


def test_false_alarms_count_rising_edges():
    score = np.zeros((1, 3600, 2))
    score[0, 100:110, 0] = 1.0  # one alarm event of 10 s on satellite 0
    score[0, 500:501, 0] = 1.0  # a second event
    benign = np.array([[True, True]])
    assert M.false_alarms_per_hour(score, benign, 0.5) == 1.0  # 2 events / 2 satellite-hours


def test_macro_block_is_replaced_and_gaps_are_visible(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text(
        "\\documentclass{article}\n"
        "% header: numbers live between RESULTS-BEGIN and RESULTS-END\n"
        "% ------ RESULTS-BEGIN\n\\newcommand{\\rOld}{1}\n% ------ RESULTS-END\n"
        "\\begin{document}$\\lVert x \\rVert$ \\rEnsIDLead{} \\rMissing{}\\end{document}\n"
    )
    N = PN.Numbers()
    N.lead("rEnsIDLead", 42.4)
    missing = PN.write_tex(tex, N)
    out = tex.read_text()
    assert missing == ["rMissing"]  # \rVert is amsmath, never a result macro
    assert "\\newcommand{\\rEnsIDLead}{42}" in out
    assert "\\newcommand{\\rMissing}{\\textcolor{red}{n/a}}" in out
    assert "rOld" not in out


def test_block_is_replaced_not_duplicated_on_rerun(tmp_path):
    # regression: a header comment that mentions both marker names must not be taken for the block
    tex = tmp_path / "doc.tex"
    tex.write_text(
        "% see RESULTS-BEGIN and RESULTS-END below\n% ----- RESULTS-BEGIN\n% ----- RESULTS-END\n\\rTemp\n"
    )
    N = PN.Numbers()
    N.put("rTemp", 0.74)
    PN.write_tex(tex, N)
    PN.write_tex(tex, N)
    out = tex.read_text()
    assert out.count("\\newcommand{\\rTemp}") == 1
    assert out.count("% see RESULTS-BEGIN") == 1


def test_macro_names_have_no_digits():
    assert PN._word("A1") == "AOne" and PN._word("F5") == "FFive"
