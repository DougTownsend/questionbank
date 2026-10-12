from __future__ import annotations

import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

from . import db, paths
from .exporter import _safe_filename

_LATEX_PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{textcomp}
\usepackage[margin=0.75in]{geometry}
\usepackage{enumitem}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{parskip}
\usepackage{needspace}
\usepackage[most]{tcolorbox}
\setlength{\parskip}{0.3em}
\setlength{\parindent}{0pt}
\linespread{0.95}
\definecolor{feedbackgray}{gray}{0.93}
\definecolor{feedbackgreen}{HTML}{DFF5DD}
\newtcolorbox{feedbackbox}{
  enhanced,
  colback=feedbackgray, colframe=feedbackgray,
  boxrule=0pt, arc=1.5pt,
  left=4pt, right=4pt, top=1.5pt, bottom=1.5pt,
  before skip=1pt, after skip=2pt,
  breakable,
}
\newtcolorbox{correctbox}{
  enhanced,
  colback=feedbackgreen, colframe=feedbackgreen,
  boxrule=0pt, arc=1.5pt,
  left=4pt, right=4pt, top=1.5pt, bottom=1.5pt,
  before skip=1pt, after skip=2pt,
  breakable,
}
% Question wrapper: each question body goes in a minipage so the stem, its
% choices, and their feedback stay together on one page (never split mid-
% question). `\Needspace*` beforehand bumps us to the next page if there
% isn't enough room left.
\newenvironment{qblock}[1][10]{%
  \Needspace*{#1\baselineskip}%
  \begin{minipage}[t]{\dimexpr\linewidth-0.5em}%
}{%
  \end{minipage}%
}
"""


VALID_ASSIGNMENT_MODES = ("student", "key", "answers_only")


def build_assignment_latex(
    conn: sqlite3.Connection,
    assignment_id: int,
    *,
    mode: str,
) -> str:
    """Render an assignment to LaTeX. ``mode`` selects the variant:

    - ``"student"``     — questions only, blank affordances, no feedback.
    - ``"key"``         — full detailed key (correct option marked,
      per-choice feedback, general feedback) inlined under each question.
    - ``"answers_only"`` — compact grading sheet: just the question number
      and the correct answer (letter / value / True/False)."""
    if mode not in VALID_ASSIGNMENT_MODES:
        raise ValueError(
            f"mode must be one of {VALID_ASSIGNMENT_MODES!r}, got {mode!r}"
        )
    assignment = db.get_assignment(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"assignment {assignment_id} not found")
    questions = db.list_assignment_questions(conn, assignment_id)

    if mode == "answers_only":
        return _build_answers_only_latex(conn, assignment, questions)

    include_answer_key = mode == "key"
    lines: list[str] = [_LATEX_PREAMBLE]
    lines.append(f"\\title{{{_escape_latex(assignment['name'])}}}")
    lines.append(r"\author{}")
    lines.append(r"\date{}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    lines.append("")

    lines.append(r"\begin{enumerate}[leftmargin=*, itemsep=0.8em]")
    for q in questions:
        pts = q["aq_point_value"] if q["aq_point_value"] is not None else 1.0
        lines.append(
            _render_question_body(
                conn, q, include_answer_key, pts_label=_fmt_points(pts)
            )
        )
    lines.append(r"\end{enumerate}")
    lines.append("")

    lines.append(r"\end{document}")
    lines.append("")
    return "\n".join(lines)


def _build_answers_only_latex(
    conn: sqlite3.Connection, assignment, questions
) -> str:
    """Compact grading sheet: '1. (c)  [1 pt]', '2. True', '3. 6 ± 0.1', ..."""
    lines: list[str] = [_LATEX_PREAMBLE]
    title = f"Answer key — {assignment['name']}"
    lines.append(f"\\title{{{_escape_latex(title)}}}")
    lines.append(r"\author{}")
    lines.append(r"\date{}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    lines.append("")
    lines.append(r"\begin{enumerate}[leftmargin=*, itemsep=0.2em]")
    for q in questions:
        ans = _render_answer_summary(conn, q)
        pts = q["aq_point_value"] if q["aq_point_value"] is not None else 1.0
        lines.append(
            f"  \\item {ans} \\hfill {{\\small {_fmt_points(pts)}}}"
        )
    lines.append(r"\end{enumerate}")
    lines.append("")
    lines.append(r"\end{document}")
    lines.append("")
    return "\n".join(lines)


def _render_answer_summary(conn: sqlite3.Connection, q) -> str:
    qtype = q["moodle_type"]
    answers = db.get_answers(conn, q["id"])
    if qtype == "multichoice":
        threshold = 0 if q["multichoice_single"] == 0 else 99.999
        letters = [
            f"({chr(ord('a') + i)})"
            for i, a in enumerate(answers)
            if (a["fraction"] or 0) > threshold
        ]
        return ", ".join(letters) or "(no correct answer marked)"
    if qtype == "truefalse":
        correct = next(
            (a for a in answers if (a["fraction"] or 0) >= 99.999), None
        )
        if correct is None:
            return "?"
        return (
            "True"
            if (correct["text_html"] or "").strip().lower() == "true"
            else "False"
        )
    if qtype == "numerical":
        bits = []
        for a in answers:
            if (a["fraction"] or 0) >= 99.999:
                val = (a["text_html"] or "").strip()
                tol = a["tolerance"]
                if tol:
                    bits.append(
                        f"{_escape_latex(val)} $\\pm$ {_escape_latex(str(tol))}"
                    )
                else:
                    bits.append(_escape_latex(val))
        return " or ".join(bits) or "?"
    return "?"


def build_bank_latex(
    conn: sqlite3.Connection, class_name: str, *, include_feedback: bool
) -> str:
    """Dump every approved question in a class to a single LaTeX document,
    grouped by category. If include_feedback, each question shows its
    correct option, per-choice feedback, and general feedback (same layout
    as the assignment 'with answer key' export). Otherwise it's a blank
    reference: stem + options, no correctness markers."""
    from .moodle_xml import format_category_for_display

    questions = db.list_questions(conn, status="approved")
    grouped: dict[str, list] = {}
    for q in questions:
        cat = format_category_for_display(q["raw_category"]) or "Uncategorized"
        grouped.setdefault(cat, []).append(q)

    title = f"{class_name} — Question bank"
    if include_feedback:
        title += " (with answer key)"

    lines: list[str] = [_LATEX_PREAMBLE]
    lines.append(f"\\title{{{_escape_latex(title)}}}")
    lines.append(r"\author{}")
    lines.append(r"\date{}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    lines.append("")

    for cat in sorted(grouped.keys()):
        lines.append(f"\\section*{{{_escape_latex(cat)}}}")
        lines.append(r"\begin{enumerate}[leftmargin=*, itemsep=0.8em]")
        for q in grouped[cat]:
            lines.append(_render_question_body(conn, q, include_feedback))
        lines.append(r"\end{enumerate}")
        lines.append("")

    lines.append(r"\end{document}")
    lines.append("")
    return "\n".join(lines)


def export_bank_to_latex_file(
    conn: sqlite3.Connection, class_name: str, *, include_feedback: bool
) -> Path:
    tex = build_bank_latex(conn, class_name, include_feedback=include_feedback)
    out_dir = paths.class_exports_dir(class_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_filename(f"{class_name}_bank")
    suffix = "_key" if include_feedback else ""
    path = out_dir / f"{base}{suffix}.tex"
    path.write_text(tex, encoding="utf-8")
    return path


_MODE_FILE_SUFFIX = {
    "student": "",
    "key": "_key",
    "answers_only": "_answers",
}


def export_assignment_to_latex_file(
    conn: sqlite3.Connection,
    class_name: str,
    assignment_id: int,
    *,
    mode: str,
) -> Path:
    a = db.get_assignment(conn, assignment_id)
    if a is None:
        raise ValueError(f"assignment {assignment_id} not found")
    tex = build_assignment_latex(conn, assignment_id, mode=mode)
    out_dir = paths.class_exports_dir(class_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_filename(a["name"])
    suffix = _MODE_FILE_SUFFIX[mode]
    path = out_dir / f"{base}{suffix}.tex"
    path.write_text(tex, encoding="utf-8")
    return path


# ---------- per-question rendering ----------


def _render_question_body(
    conn: sqlite3.Connection,
    q: sqlite3.Row,
    include_answer_key: bool,
    pts_label: str | None = None,
) -> str:
    """Render one question. If `include_answer_key` is False, output a blank
    student-facing version (options, true/false toggle, numerical blank). If
    True, mark the correct option(s), show each option's specific feedback
    underneath it, and append general feedback at the end of the question.

    If `pts_label` is given it's shown as a bold tag before the stem (used
    by assignment exports). Bank exports pass None so the point tag is
    omitted entirely."""
    qtype = q["moodle_type"]
    stem = _html_to_latex(q["stem_html"] or "")
    answers = db.get_answers(conn, q["id"])

    lead = f"\\textbf{{[{pts_label}]}} " if pts_label else ""
    parts: list[str] = [
        r"  \item\begin{qblock}",
        f"  {lead}{stem}",
    ]

    if qtype == "multichoice":
        multiple = q["multichoice_single"] == 0
        if multiple:
            parts.append(r"  \emph{Select all that apply.}")
        parts.extend(
            _render_mc_options(
                answers, include_answer_key, multiple=multiple
            )
        )
    elif qtype == "truefalse":
        parts.extend(_render_tf_options(answers, include_answer_key))
    elif qtype == "numerical":
        parts.extend(_render_numerical_options(answers, include_answer_key))
    else:
        parts.append(f"  \\hfill \\emph{{[unsupported type: {qtype}]}}")

    if include_answer_key and q["general_feedback_html"]:
        gf = _html_to_latex(q["general_feedback_html"]).strip()
        if gf:
            parts.append(r"  \begin{feedbackbox}")
            parts.append(f"  \\textit{{General feedback:}} {gf}")
            parts.append(r"  \end{feedbackbox}")

    parts.append(r"  \end{qblock}")
    return "\n".join(parts)


def _render_mc_options(
    answers: list, include_answer_key: bool, *, multiple: bool
) -> list[str]:
    if not include_answer_key:
        out = [r"  \begin{enumerate}[label=(\alph*), leftmargin=2em]"]
        for a in answers:
            out.append(f"    \\item {_html_to_latex(a['text_html'] or '')}")
        out.append(r"  \end{enumerate}")
        return out

    out = [r"  \begin{enumerate}[label=(\alph*), leftmargin=2em, itemsep=0.25em]"]
    for a in answers:
        fraction = a["fraction"] or 0
        is_correct = fraction > 0 if multiple else fraction >= 99.999
        text = _html_to_latex(a["text_html"] or "")
        display = f"\\textbf{{{text} (correct)}}" if is_correct else text
        fb = _html_to_latex(a["feedback_html"] or "").strip()
        out.append(f"    \\item {display}")
        if fb:
            env = "correctbox" if is_correct else "feedbackbox"
            out.append(f"    \\begin{{{env}}}")
            out.append(f"    \\textit{{Feedback:}} {fb}")
            out.append(f"    \\end{{{env}}}")
    out.append(r"  \end{enumerate}")
    return out


def _render_tf_options(answers: list, include_answer_key: bool) -> list[str]:
    if not include_answer_key:
        return [
            r"  \hfill \textbf{(circle one)} \quad True \quad / \quad False"
        ]
    out = [r"  \begin{itemize}[leftmargin=2em, itemsep=0.25em, label={}]"]
    for a in answers:
        is_correct = (a["fraction"] or 0) >= 99.999
        opt = (a["text_html"] or "").strip().capitalize() or "?"
        display = f"\\textbf{{{opt} (correct)}}" if is_correct else opt
        fb = _html_to_latex(a["feedback_html"] or "").strip()
        out.append(f"    \\item {display}")
        if fb:
            env = "correctbox" if is_correct else "feedbackbox"
            out.append(f"    \\begin{{{env}}}")
            out.append(f"    \\textit{{Feedback:}} {fb}")
            out.append(f"    \\end{{{env}}}")
    out.append(r"  \end{itemize}")
    return out


def _render_numerical_options(
    answers: list, include_answer_key: bool
) -> list[str]:
    if not include_answer_key:
        return [r"  \hfill answer: \underline{\hspace{3cm}}"]
    correct_bits = []
    for a in answers:
        if (a["fraction"] or 0) >= 99.999:
            val = (a["text_html"] or "").strip()
            tol = a["tolerance"]
            if tol:
                correct_bits.append(
                    f"{_escape_latex(val)} $\\pm$ {_escape_latex(str(tol))}"
                )
            else:
                correct_bits.append(_escape_latex(val))
    label = " or ".join(correct_bits) or "?"
    out = [f"  \\hfill \\textbf{{Accepted answer: {label}}}"]
    # any per-value feedback (rare for numerical but the schema allows it)
    fb_rows: list[str] = []
    for a in answers:
        fb = _html_to_latex(a["feedback_html"] or "").strip()
        if not fb:
            continue
        val = (a["text_html"] or "").strip()
        is_correct = (a["fraction"] or 0) >= 99.999
        env = "correctbox" if is_correct else "feedbackbox"
        val_tex = f"\\texttt{{{_escape_latex(val)}}}"
        if is_correct:
            val_tex = f"\\textbf{{{val_tex} (correct)}}"
        fb_rows.append(f"  \\begin{{{env}}}")
        fb_rows.append(f"  {val_tex} --- \\textit{{Feedback:}} {fb}")
        fb_rows.append(f"  \\end{{{env}}}")
    if fb_rows:
        out.extend(fb_rows)
    return out


# ---------- HTML → LaTeX ----------


_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
}

_UNICODE_MAP = {
    "–": "--",
    "—": "---",
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "…": r"\ldots{}",
    " ": "~",
    "−": "-",
    "×": r"$\times$",
    "÷": r"$\div$",
    "±": r"$\pm$",
    "≤": r"$\leq$",
    "≥": r"$\geq$",
    "≠": r"$\neq$",
    "≈": r"$\approx$",
    "≡": r"$\equiv$",
    "∞": r"$\infty$",
    "·": r"$\cdot$",
    "•": r"$\bullet$",
    "°": r"$^{\circ}$",
    "′": "'",
    "″": "''",
    "√": r"$\sqrt{\,}$",
    "∑": r"$\sum$",
    "∏": r"$\prod$",
    "∫": r"$\int$",
    "∂": r"$\partial$",
    "∇": r"$\nabla$",
    "←": r"$\leftarrow$",
    "→": r"$\rightarrow$",
    "↔": r"$\leftrightarrow$",
    "⇐": r"$\Leftarrow$",
    "⇒": r"$\Rightarrow$",
    "⇔": r"$\Leftrightarrow$",
    "α": r"$\alpha$",
    "β": r"$\beta$",
    "γ": r"$\gamma$",
    "δ": r"$\delta$",
    "ε": r"$\epsilon$",
    "ζ": r"$\zeta$",
    "η": r"$\eta$",
    "θ": r"$\theta$",
    "κ": r"$\kappa$",
    "λ": r"$\lambda$",
    "μ": r"$\mu$",
    "ν": r"$\nu$",
    "ξ": r"$\xi$",
    "π": r"$\pi$",
    "ρ": r"$\rho$",
    "σ": r"$\sigma$",
    "τ": r"$\tau$",
    "φ": r"$\phi$",
    "χ": r"$\chi$",
    "ψ": r"$\psi$",
    "ω": r"$\omega$",
    "Δ": r"$\Delta$",
    "Θ": r"$\Theta$",
    "Λ": r"$\Lambda$",
    "Ξ": r"$\Xi$",
    "Π": r"$\Pi$",
    "Σ": r"$\Sigma$",
    "Φ": r"$\Phi$",
    "Ψ": r"$\Psi$",
    "Ω": r"$\Omega$",
    "²": r"$^{2}$",
    "³": r"$^{3}$",
    "¼": r"$\frac{1}{4}$",
    "½": r"$\frac{1}{2}$",
    "¾": r"$\frac{3}{4}$",
    "©": r"\copyright{}",
    "®": r"\textregistered{}",
    "™": r"\texttrademark{}",
    # logic / circuit operators
    "⊕": r"$\oplus$",
    "⊖": r"$\ominus$",
    "⊗": r"$\otimes$",
    "⊙": r"$\odot$",
    "⊘": r"$\oslash$",
    "¬": r"$\neg$",
    "∧": r"$\wedge$",
    "∨": r"$\vee$",
    "⊻": r"$\veebar$",
    # sets / logic
    "∀": r"$\forall$",
    "∃": r"$\exists$",
    "∈": r"$\in$",
    "∉": r"$\notin$",
    "⊆": r"$\subseteq$",
    "⊇": r"$\supseteq$",
    "⊂": r"$\subset$",
    "⊃": r"$\supset$",
    "∪": r"$\cup$",
    "∩": r"$\cap$",
    "∅": r"$\emptyset$",
    # relations / misc math
    "∴": r"$\therefore$",
    "∵": r"$\because$",
    "⊥": r"$\perp$",
    "⊤": r"$\top$",
    "∥": r"$\parallel$",
    "∣": r"$\mid$",
    "≪": r"$\ll$",
    "≫": r"$\gg$",
    "∓": r"$\mp$",
    "↑": r"$\uparrow$",
    "↓": r"$\downarrow$",
    "↦": r"$\mapsto$",
}


def _escape_latex(s: str) -> str:
    out_chars: list[str] = []
    for c in s:
        if c in _UNICODE_MAP:
            out_chars.append(_UNICODE_MAP[c])
        elif c in _ESCAPE_MAP:
            out_chars.append(_ESCAPE_MAP[c])
        else:
            out_chars.append(c)
    return "".join(out_chars)


class _HtmlToLatex(HTMLParser):
    """Translate a small subset of HTML into LaTeX. Unknown tags are dropped
    (their text content is kept). Text content is LaTeX-escaped as it streams
    in, so only the LaTeX commands we emit for tags carry raw backslashes."""

    _WRAPPERS = {
        "b": ("\\textbf{", "}"),
        "strong": ("\\textbf{", "}"),
        "i": ("\\textit{", "}"),
        "em": ("\\textit{", "}"),
        "code": ("\\texttt{", "}"),
        "tt": ("\\texttt{", "}"),
        "u": ("\\underline{", "}"),
        "sup": ("\\textsuperscript{", "}"),
        "sub": ("\\textsubscript{", "}"),
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.pre_depth = 0
        # Tables: when self.in_table is True, we're collecting cells.
        # `cell_parts` is None between cells (whitespace text is dropped) and
        # a list while we're inside a <td>/<th> (LaTeX written there instead
        # of self.parts).
        self.in_table = False
        self.table_rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.cell_parts: list[str] | None = None

    def _emit(self, s: str) -> None:
        if self.cell_parts is not None:
            self.cell_parts.append(s)
        else:
            self.parts.append(s)

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if tag == "pre":
            self.pre_depth += 1
            self._emit("\n\\begin{flushleft}\\ttfamily\n")
            return
        if self.pre_depth > 0:
            return
        if tag == "table":
            self.in_table = True
            self.table_rows = []
            return
        if self.in_table:
            if tag == "tr":
                self.current_row = []
                return
            if tag in ("td", "th"):
                # divert subsequent emit() calls into this cell's buffer
                self.cell_parts = []
                return
            if tag in ("thead", "tbody", "tfoot", "caption", "colgroup", "col"):
                # transparent wrappers — children still get processed
                return
            # other inline tags inside a cell fall through to normal handling
            # so <code>/<b>/<sup> inside <td> render correctly
        if tag in self._WRAPPERS:
            self._emit(self._WRAPPERS[tag][0])
        elif tag == "br":
            self._emit("\\\\\n")
        elif tag == "p":
            pass
        elif tag == "ul":
            self._emit("\n\\begin{itemize}\n")
        elif tag == "ol":
            self._emit("\n\\begin{enumerate}\n")
        elif tag == "li":
            self._emit("\\item ")
        # unknown tags: ignore

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            self._emit("\n\\end{flushleft}\n")
            return
        if self.pre_depth > 0:
            return
        if self.in_table:
            if tag == "table":
                self.in_table = False
                self.parts.append(_render_table_latex(self.table_rows))
                self.table_rows = []
                self.current_row = None
                self.cell_parts = None
                return
            if tag == "tr":
                if self.current_row is not None and self.current_row:
                    self.table_rows.append(self.current_row)
                self.current_row = None
                return
            if tag in ("td", "th"):
                if self.cell_parts is not None and self.current_row is not None:
                    self.current_row.append("".join(self.cell_parts).strip())
                self.cell_parts = None
                return
            if tag in ("thead", "tbody", "tfoot", "caption", "colgroup", "col"):
                return
        if tag in self._WRAPPERS:
            self._emit(self._WRAPPERS[tag][1])
        elif tag == "p":
            self._emit("\n\n")
        elif tag == "ul":
            self._emit("\n\\end{itemize}\n")
        elif tag == "ol":
            self._emit("\n\\end{enumerate}\n")

    def handle_startendtag(self, tag, attrs) -> None:
        # handles <br/>
        self.handle_starttag(tag, attrs)

    def handle_data(self, data) -> None:
        if self.pre_depth > 0:
            self._emit(_pre_to_latex(data))
            return
        if self.in_table and self.cell_parts is None:
            # whitespace between table tags — drop it
            return
        self._emit(_escape_latex(data))


def _render_table_latex(rows: list[list[str]]) -> str:
    """Render a list of cell-rows (each cell's content is already
    LaTeX-escaped) as a centered, fully-bordered tabular. Empty tables
    produce nothing."""
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    col_spec = "|" + ("c|" * n_cols)
    out: list[str] = ["\n\\begin{center}", f"\\begin{{tabular}}{{{col_spec}}}", r"\hline"]
    for row in rows:
        cells = list(row) + [""] * (n_cols - len(row))
        out.append(" & ".join(cells) + r" \\")
        out.append(r"\hline")
    out.append(r"\end{tabular}")
    out.append("\\end{center}\n")
    return "\n".join(out)


def _pre_to_latex(data: str) -> str:
    """Render <pre>-block text inside a flushleft/ttfamily environment:
    every newline becomes a forced LaTeX line break; ALL spaces become
    non-breaking ~ so columns align identically to the HTML source (TeX
    otherwise collapses runs of spaces, breaking label/operand alignment
    in code blocks)."""
    raw_lines = data.split("\n")
    out_lines: list[str] = []
    for line in raw_lines:
        line = line.replace("\t", "    ")
        esc = _escape_latex(line)
        esc = esc.replace(" ", "~")
        if not esc.strip("~"):
            esc = "\\strut"
        out_lines.append(esc)
    while out_lines and out_lines[0] == "\\strut":
        out_lines.pop(0)
    while out_lines and out_lines[-1] == "\\strut":
        out_lines.pop()
    return "\\\\\n".join(out_lines)


_CODE_BLOCK_RE = re.compile(r"<code>(.*?)</code>", re.DOTALL | re.IGNORECASE)
_BR_INSIDE_CODE_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _promote_multiline_code_to_pre(html: str) -> str:
    """If a <code> block contains real newlines or <br> tags, convert it
    into a <pre> block so the LaTeX renderer treats it as multi-line. Single
    line <code> stays inline. This catches multi-line code that Claude wrote
    in an answer choice without a surrounding <pre>."""
    def replace(m: re.Match) -> str:
        inner = m.group(1)
        has_newline = "\n" in inner
        has_br = bool(_BR_INSIDE_CODE_RE.search(inner))
        if not (has_newline or has_br):
            return m.group(0)
        if has_br:
            inner = _BR_INSIDE_CODE_RE.sub("\n", inner)
        return f"<pre>{inner}</pre>"
    return _CODE_BLOCK_RE.sub(replace, html)


_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def _html_to_latex(html: str) -> str:
    html = _promote_multiline_code_to_pre(html or "")
    parser = _HtmlToLatex()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = _MULTI_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _fmt_points(points: float) -> str:
    rounded = round(points, 2)
    if rounded == int(rounded):
        n = int(rounded)
        return f"{n} pt" if n == 1 else f"{n} pts"
    return f"{rounded:g} pts"
