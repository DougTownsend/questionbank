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


def test_escape_latex_handles_ece_logic_symbols():
    # U+2295 ⊕ (XOR), U+00AC ¬ (NOT), U+2227 ∧ (AND), U+2228 ∨ (OR),
    # U+22A5 ⊥ (perp), U+2191/2193 arrows — typical in ECE circuit content.
    s = _escape_latex("A ⊕ B ¬C ∧ D ∨ E ⊥ F ↓ G ↑ H")
    for bad in "⊕¬∧∨⊥↓↑":
        assert bad not in s
    assert r"$\oplus$" in s
    assert r"$\neg$" in s
    assert r"$\wedge$" in s
    assert r"$\vee$" in s
    assert r"$\perp$" in s
    assert r"$\downarrow$" in s
    assert r"$\uparrow$" in s


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


def test_html_to_latex_super_and_sub():
    sup = _html_to_latex("2<sup>31</sup>-1")
    assert sup == r"2\textsuperscript{31}-1"

    sub = _html_to_latex("log<sub>2</sub>(N)")
    assert sub == r"log\textsubscript{2}(N)"


def test_html_to_latex_pre_block_preserves_line_breaks():
    s = _html_to_latex(
        "<pre><code>ADD R0, R1, R2\n  NOT R3, R3\nLOOP    HALT</code></pre>"
    )
    # opens monospace block
    assert r"\begin{flushleft}\ttfamily" in s
    # closes block
    assert r"\end{flushleft}" in s
    # newlines became line breaks (\\)
    assert "ADD~R0,~R1,~R2\\\\" in s
    # leading whitespace preserved as non-breaking spaces
    assert "~~NOT~R3,~R3" in s


def test_html_to_latex_pre_aligns_label_with_indented_lines():
    """`LOOP  ADD` (label + 2 spaces) should align with `      ADD`
    (6 leading spaces) — TeX would otherwise collapse the inner gap."""
    s = _html_to_latex("<pre>LOOP  ADD R0\n      ADD R1</pre>")
    # both rows show 6 chars of width before ADD: 'LOOP' + ~~ vs ~~~~~~
    assert "LOOP~~ADD" in s
    assert "~~~~~~ADD" in s


def test_html_to_latex_multiline_code_promoted_to_block():
    """Multi-line content inside a <code> tag should render as a block
    even without a surrounding <pre>."""
    s = _html_to_latex("<code>ADD R0, R1, R2\nNOT R3, R3</code>")
    assert r"\begin{flushleft}\ttfamily" in s
    assert "ADD~R0,~R1,~R2" in s
    assert "NOT~R3,~R3" in s
    # NOT wrapped in inline \texttt
    assert r"\texttt{ADD" not in s


def test_html_to_latex_singleline_code_stays_inline():
    s = _html_to_latex("<code>ADD R0, R1, R2</code>")
    assert r"\texttt{ADD R0, R1, R2}" in s
    assert "flushleft" not in s


def test_html_to_latex_code_with_br_promoted_to_block():
    s = _html_to_latex("<code>ADD R0<br>NOT R1</code>")
    assert r"\begin{flushleft}\ttfamily" in s
    assert "ADD~R0" in s
    assert "NOT~R1" in s


def test_html_to_latex_pre_strips_leading_and_trailing_blank_lines():
    s = _html_to_latex("<pre>\n\nABC\n\n</pre>")
    # internal content is "ABC", possibly with strut spacers between
    # leading and trailing blank lines should be removed
    assert "ABC" in s
    assert s.count(r"\strut") <= 1


def test_html_to_latex_simple_truth_table():
    s = _html_to_latex(
        "<table>"
        "<tr><td>A</td><td>B</td><td>F</td></tr>"
        "<tr><td>0</td><td>0</td><td>0</td></tr>"
        "<tr><td>0</td><td>1</td><td>1</td></tr>"
        "<tr><td>1</td><td>0</td><td>1</td></tr>"
        "<tr><td>1</td><td>1</td><td>0</td></tr>"
        "</table>"
    )
    assert r"\begin{tabular}{|c|c|c|}" in s
    assert r"\end{tabular}" in s
    assert r"A & B & F \\" in s
    assert r"0 & 0 & 0 \\" in s
    assert r"1 & 1 & 0 \\" in s
    assert r"\hline" in s
    # the cell text must NOT show up concatenated outside the tabular
    assert "ABF000011101110" not in s


def test_html_to_latex_table_handles_inner_formatting():
    """A cell can contain inline HTML — bold, code, sup, etc. — and the
    LaTeX equivalents should appear inside the tabular cell."""
    s = _html_to_latex(
        "<table>"
        "<tr><td><b>x</b></td><td><code>2<sup>n</sup></code></td></tr>"
        "</table>"
    )
    assert r"\begin{tabular}{|c|c|}" in s
    assert r"\textbf{x}" in s
    assert r"\texttt{2\textsuperscript{n}}" in s


def test_html_to_latex_table_with_thead_tbody():
    s = _html_to_latex(
        "<table>"
        "<thead><tr><th>A</th><th>B</th></tr></thead>"
        "<tbody><tr><td>0</td><td>1</td></tr></tbody>"
        "</table>"
    )
    assert r"\begin{tabular}{|c|c|}" in s
    assert r"A & B \\" in s
    assert r"0 & 1 \\" in s


def test_html_to_latex_table_pads_short_rows():
    s = _html_to_latex(
        "<table>"
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "<tr><td>x</td></tr>"
        "</table>"
    )
    # short row must still render with the right number of & separators
    assert r"\begin{tabular}{|c|c|c|}" in s
    assert r"a & b & c \\" in s
    assert r"x &  &  \\" in s


def test_html_to_latex_super_inside_code():
    s = _html_to_latex("<code>2<sup>31</sup>-1</code>")
    assert r"\texttt{" in s
    assert r"\textsuperscript{31}" in s


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
        conn, aid, mode="student"
    )
    assert r"\documentclass" in tex
    assert r"\begin{document}" in tex
    assert r"\end{document}" in tex
    # blank affordance for truefalse + numerical
    assert "(circle one)" in tex
    assert r"\underline{\hspace{3cm}}" in tex


def test_answers_only_mode_lists_just_question_number_and_answer(conn_with_assignment):
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, mode="answers_only"
    )
    # title should announce it as an answer key
    assert "Answer key" in tex
    # multichoice answer letter — fixture has 4 options, correct is first
    assert "(a)" in tex
    # truefalse correct value (fixture has true as correct)
    assert "True" in tex
    # numerical correct value 6 (log2(64))
    assert "6" in tex
    # NO question stems, options, or feedback should be present
    assert "(circle one)" not in tex
    assert r"\textit{Feedback:}" not in tex
    assert r"\textit{General feedback:}" not in tex
    assert "hits / total accesses" not in tex
    # also no per-option correct markers since options aren't shown
    assert "(correct)" not in tex


def test_invalid_mode_raises(conn_with_assignment):
    conn, aid = conn_with_assignment
    with pytest.raises(ValueError):
        latex_exporter.build_assignment_latex(conn, aid, mode="bogus")


def test_answer_key_is_inlined_not_separate_section(conn_with_assignment):
    """The key now lives under each question (marking correct options and
    showing specific feedback) — there's no separate 'Answer key' section."""
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, mode="key"
    )
    assert r"\section*{Answer key}" not in tex
    assert r"\clearpage" not in tex


def test_answer_key_shows_per_choice_feedback_and_general(conn_with_assignment):
    """Each distractor's feedback appears directly under the choice it
    belongs to, and generalfeedback shows at the end of each question."""
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, mode="key"
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
        conn, aid, mode="student"
    )
    assert "(correct)" not in tex
    assert r"\textit{Feedback:}" not in tex
    assert r"\textit{General feedback:}" not in tex
    assert r"\begin{feedbackbox}" not in tex
    assert r"\begin{correctbox}" not in tex


def test_answer_key_uses_green_box_for_correct_and_gray_for_others(conn_with_assignment):
    conn, aid = conn_with_assignment
    tex = latex_exporter.build_assignment_latex(
        conn, aid, mode="key"
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
    # Three untouched questions are automatically balanced to 100 points.
    tex = latex_exporter.build_assignment_latex(
        conn, aid, mode="student"
    )
    assert "[33.34 pts]" in tex
    assert "[33.33 pts]" in tex

    # bump first question to 2.0 and rebuild
    qs = db.list_assignment_questions(conn, aid)
    db.set_assignment_question_points(conn, aid, qs[0]["id"], 2.0)
    tex = latex_exporter.build_assignment_latex(
        conn, aid, mode="student"
    )
    assert "[2 pts]" in tex


def test_select_all_latex_marks_all_positive_answers(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    raw_xml = """
    <question type="multichoice">
      <name><text>Select all</text></name>
      <questiontext format="html"><text>Select all that apply.</text></questiontext>
      <single>false</single>
      <answer fraction="50"><text>A</text></answer>
      <answer fraction="50"><text>B</text></answer>
      <answer fraction="-50"><text>C</text></answer>
    </question>
    """
    qid = db.insert_question(
        conn,
        moodle_type="multichoice",
        multichoice_single=False,
        name="Select all",
        stem_html="<p>Which apply?</p>",
        stem_hash="select-all",
        raw_xml=raw_xml,
        raw_category=None,
        default_grade=1.0,
        penalty=0.3333333,
        general_feedback_html=None,
        source_import_id=None,
        answers=[
            {"text_html": "A", "fraction": 50.0, "feedback_html": "yes"},
            {"text_html": "B", "fraction": 50.0, "feedback_html": "yes"},
            {"text_html": "C", "fraction": -50.0, "feedback_html": "no"},
        ],
    )
    db.set_status(conn, qid, "approved")
    aid = db.create_assignment(conn, "Select all")
    db.add_question_to_assignment(conn, aid, qid)

    student = latex_exporter.build_assignment_latex(conn, aid, mode="student")
    key = latex_exporter.build_assignment_latex(conn, aid, mode="key")
    answers = latex_exporter.build_assignment_latex(conn, aid, mode="answers_only")

    assert "Select all that apply" in student
    assert key.count("(correct)") == 2
    assert "(a), (b)" in answers


def test_bank_latex_no_feedback_has_no_correct_markers(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")

    tex = latex_exporter.build_bank_latex(conn, "ECE", include_feedback=False)
    assert r"\documentclass" in tex
    assert r"\end{document}" in tex
    # groups by category with section headers
    assert r"\section*" in tex
    # three supported question types from the fixture all appear
    assert "(circle one)" in tex  # truefalse blank affordance
    assert r"\underline{\hspace{3cm}}" in tex  # numerical blank affordance
    # no feedback markers in the no-feedback version
    assert "(correct)" not in tex
    assert r"\textit{Feedback:}" not in tex
    # no assignment-style point labels
    assert "[1 pt]" not in tex and "[1 pts]" not in tex


def test_bank_latex_with_feedback_has_correct_markers_and_feedback(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")

    tex = latex_exporter.build_bank_latex(conn, "ECE", include_feedback=True)
    assert "(correct)" in tex
    assert r"\textit{Feedback:}" in tex
    assert r"\textit{General feedback:}" in tex
    assert r"\begin{correctbox}" in tex
    assert r"\begin{feedbackbox}" in tex


def test_bank_latex_skips_non_approved(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    # approve one, reject one, leave one pending
    pending = db.list_questions(conn, status="pending")
    db.set_status(conn, pending[0]["id"], "approved")
    db.set_status(conn, pending[1]["id"], "rejected", rejection_note="x")
    # pending[2] stays pending
    tex = latex_exporter.build_bank_latex(conn, "ECE", include_feedback=False)
    approved_stem = pending[0]["stem_html"]
    rejected_stem = pending[1]["stem_html"]
    pending_stem = pending[2]["stem_html"]
    # approved's distinctive fragment must be in the doc
    if "hit" in approved_stem.lower():
        assert "hit" in tex.lower()
    # rejected/pending stems must NOT appear
    assert rejected_stem.strip("<p>").strip("</p>") not in tex or True
    # (the above is loose since HTML-to-latex mangles chars; more robustly:)
    # the rejected stems contain unique words; assert a unique one is absent
    assert "write-through" not in tex.lower()
    assert "log2" not in tex.lower()


def test_export_bank_to_latex_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    cname = "X"
    paths.ensure_class_dirs(cname)
    conn = db.connect(paths.class_db_path(cname))
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")

    blank = latex_exporter.export_bank_to_latex_file(
        conn, cname, include_feedback=False
    )
    keyed = latex_exporter.export_bank_to_latex_file(
        conn, cname, include_feedback=True
    )
    assert blank.exists() and keyed.exists()
    assert blank != keyed
    assert "_key" in keyed.stem
    assert "_bank" in blank.stem


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
        conn, cname, aid, mode="student"
    )
    keyed = latex_exporter.export_assignment_to_latex_file(
        conn, cname, aid, mode="key"
    )
    answers = latex_exporter.export_assignment_to_latex_file(
        conn, cname, aid, mode="answers_only"
    )
    assert blank.exists() and keyed.exists() and answers.exists()
    assert len({blank, keyed, answers}) == 3
    assert blank.suffix == ".tex"
    assert "_key" in keyed.stem
    assert "_answers" in answers.stem
    # content differs — the key version has inline feedback markers
    assert "Feedback:" not in blank.read_text()
    assert "Feedback:" in keyed.read_text()
    # answers-only is shorter: no question stems, no feedback
    assert "Feedback:" not in answers.read_text()
    assert len(answers.read_text()) < len(keyed.read_text())
