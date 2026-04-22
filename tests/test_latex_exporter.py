from pathlib import Path

import pytest

from question_bank import db, importer, latex_exporter, paths
from question_bank.latex_exporter import _escape_latex, _html_to_latex

FIXTURES = Path(__file__).parent / "fixtures"


# ---------- low-level helpers ----------


def test_escape_latex_handles_specials():
    s = _escape_latex("100% & $5 for _foo_ #1 ^two {a} ~ b \\ c")
    # backslash is the trickiest; it must not corrupt subsequently-inserted braces
    assert "\\textbackslash{}" in s
    assert r"\&" in s
    assert r"\%" in s
    assert r"\$" in s
    assert r"\#" in s
    assert r"\_" in s
    assert r"\{" in s
    assert r"\}" in s
    assert r"\textasciicircum{}" in s
    assert r"\textasciitilde{}" in s


def test_escape_latex_replaces_smart_unicode():
    s = _escape_latex("a — b – c “quoted” it’s")
    assert "---" in s          # em-dash
    assert "--" in s           # en-dash
    assert "``quoted''" in s
    assert "it's" in s


def test_escape_latex_handles_common_math_unicode():
    # U+2212 minus sign (not the ASCII hyphen) caused "Unicode character not
    # set up for use with LaTeX" errors before the map covered it.
    s = _escape_latex("N−1 steps, ±2 tolerance, 30° rotation, α × β ≥ 1")
    assert "−" not in s                     # raw minus gone
    assert "N-1" in s
    assert r"$\pm$" in s
    assert r"$^{\circ}$" in s
    assert r"$\alpha$" in s
    assert r"$\times$" in s
    assert r"$\geq$" in s


def test_html_to_latex_keeps_plain_text_safe():
    s = _html_to_latex("<p>100% profit</p>")
    assert r"100\% profit" in s


def test_html_to_latex_bold_and_italic():
    s = _html_to_latex("<p>A <b>bold</b> <i>italic</i> line.</p>")
    assert r"\textbf{bold}" in s
    assert r"\textit{italic}" in s


def test_html_to_latex_drops_unknown_tags_keeps_text():
    s = _html_to_latex("<p>pre <span class='x'>middle</span> post</p>")
    assert "pre middle post" in s


def test_html_to_latex_lists():
    s = _html_to_latex("<ul><li>one</li><li>two</li></ul>")
    assert r"\begin{itemize}" in s
    assert r"\item one" in s
    assert r"\end{itemize}" in s


# ---------- assignment rendering ----------


@pytest.fixture
def conn_with_assignment(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")
    aid = db.create_assignment(conn, "Quiz 1")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])
    return conn, aid


def test_student_latex_has_no_correct_indicators(conn_with_assignment):
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=False
    )
    assert r"\documentclass" in tex
    assert r"\begin{document}" in tex
    assert r"\end{document}" in tex
    assert "Answer key" not in tex
    # blank affordance for truefalse + numerical
    assert "(circle one)" in tex
    assert r"\underline{\hspace{3cm}}" in tex


def test_answer_key_is_inlined_not_separate_section(conn_with_assignment):
    """The key now lives under each question (marking correct options and
    showing specific feedback) — there's no separate 'Answer key' section."""
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=True
    )
    assert r"\section*{Answer key}" not in tex
    assert r"\clearpage" not in tex


def test_answer_key_shows_per_choice_feedback_and_general(conn_with_assignment):
    """Each distractor's feedback appears directly under the choice it
    belongs to, and generalfeedback shows at the end of each question."""
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=True
    )
    # per-choice feedback from the multichoice question in the fixture
    assert "That's the miss rate, not the hit rate." in tex
    assert "This ratio can exceed 1" in tex
    # the label that precedes per-option feedback
    assert r"\textit{Feedback:}" in tex
    # general feedback (multichoice: 'hits / total accesses')
    assert r"\textit{General feedback:}" in tex
    assert "hits / total accesses" in tex
    # correct option is marked explicitly
    assert "(correct)" in tex


def test_student_version_has_no_correct_markers_or_feedback(conn_with_assignment):
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=False
    )
    assert "(correct)" not in tex
    assert r"\textit{Feedback:}" not in tex
    assert r"\textit{General feedback:}" not in tex
    assert r"\begin{feedbackbox}" not in tex
    assert r"\begin{correctbox}" not in tex


def test_answer_key_uses_green_box_for_correct_and_gray_for_others(conn_with_assignment):
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=True
    )
    # the preamble defines both box environments
    assert r"\newtcolorbox{feedbackbox}" in tex
    assert r"\newtcolorbox{correctbox}" in tex
    # both envs actually get used
    assert r"\begin{correctbox}" in tex
    assert r"\begin{feedbackbox}" in tex
    # general feedback block uses the gray box
    assert tex.count(r"General feedback:") >= 1


def test_latex_points_render_per_question(conn_with_assignment):
    conn, aid = conn_with_assignment
    # default is 1.0 → "1 pt"
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=False
    )
    assert "[1 pt]" in tex

    # bump first question to 2.0 and rebuild
    qs = db.list_assignment_questions(conn, aid)
    db.set_assignment_question_points(conn, aid, qs[0]["id"], 2.0)
    tex = latex_exporter.build_assignment_latex(
        conn, aid, include_answer_key=False
    )
    assert "[2 pts]" in tex


def test_export_latex_to_file_blank_and_key(tmp_path, monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    cname = "X"
    paths.ensure_class_dirs(cname)
    conn = db.connect(paths.class_db_path(cname))
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")
    aid = db.create_assignment(conn, "Quiz 1")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])

    blank = latex_exporter.export_assignment_to_latex_file(
        conn, cname, aid, include_answer_key=False
    )
    keyed = latex_exporter.export_assignment_to_latex_file(
        conn, cname, aid, include_answer_key=True
    )
    assert blank.exists() and keyed.exists()
    assert blank != keyed
    assert blank.suffix == ".tex"
    assert "_key" in keyed.stem
    # content differs — the key version has inline feedback markers
    assert "Feedback:" not in blank.read_text()
    assert "Feedback:" in keyed.read_text()
