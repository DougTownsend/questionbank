from __future__ import annotations

import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

from . import db, paths
from .exporter import _safe_filename

_LATEX_PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{textcomp}
\usepackage[margin=1in]{geometry}
\usepackage{enumitem}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{parskip}
\usepackage[most]{tcolorbox}
\definecolor{feedbackgray}{gray}{0.93}
\definecolor{feedbackgreen}{HTML}{DFF5DD}
\newtcolorbox{feedbackbox}{
  enhanced,
  colback=feedbackgray, colframe=feedbackgray,
  boxrule=0pt, arc=1.5pt,
  left=5pt, right=5pt, top=3pt, bottom=3pt,
  before skip=2pt, after skip=4pt,
  breakable,
}
\newtcolorbox{correctbox}{
  enhanced,
  colback=feedbackgreen, colframe=feedbackgreen,
  boxrule=0pt, arc=1.5pt,
  left=5pt, right=5pt, top=3pt, bottom=3pt,
  before skip=2pt, after skip=4pt,
  breakable,
}
"""


def build_assignment_latex(
    conn: sqlite3.Connection,
    assignment_id: int,
    *,
    include_answer_key: bool,
) -> str:
    assignment = db.get_assignment(conn, assignment_id)
    if assignment is None:
        raise ValueError(f"assignment {assignment_id} not found")
    questions = db.list_assignment_questions(conn, assignment_id)

    lines: list[str] = [_LATEX_PREAMBLE]
    lines.append(f"\\title{{{_escape_latex(assignment['name'])}}}")
    lines.append(r"\author{}")
    lines.append(r"\date{}")
    lines.append("")
    lines.append(r"\begin{document}")
    lines.append(r"\maketitle")
    lines.append("")

    lines.append(r"\begin{enumerate}[leftmargin=*, itemsep=2em]")
    for q in questions:
        lines.append(_render_question_body(conn, q, include_answer_key))
    lines.append(r"\end{enumerate}")
    lines.append("")

    lines.append(r"\end{document}")
    lines.append("")
    return "\n".join(lines)


def export_assignment_to_latex_file(
    conn: sqlite3.Connection,
    class_name: str,
    assignment_id: int,
    *,
    include_answer_key: bool,
) -> Path:
    a = db.get_assignment(conn, assignment_id)
    if a is None:
        raise ValueError(f"assignment {assignment_id} not found")
    tex = build_assignment_latex(
        conn, assignment_id, include_answer_key=include_answer_key
    )
    out_dir = paths.class_exports_dir(class_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_filename(a["name"])
    suffix = "_key" if include_answer_key else ""
    path = out_dir / f"{base}{suffix}.tex"
    path.write_text(tex, encoding="utf-8")
    return path


# ---------- per-question rendering ----------


def _render_question_body(
    conn: sqlite3.Connection, q: sqlite3.Row, include_answer_key: bool
) -> str:
    """Render one question. If `include_answer_key` is False, output a blank
    student-facing version (options, true/false toggle, numerical blank). If
    True, mark the correct option(s), show each option's specific feedback
    underneath it, and append general feedback at the end of the question."""
    points = q["aq_point_value"] if q["aq_point_value"] is not None else 1.0
    pts_label = _fmt_points(points)
    qtype = q["moodle_type"]
    stem = _html_to_latex(q["stem_html"] or "")
    answers = db.get_answers(conn, q["id"])

    parts: list[str] = [f"  \\item \\textbf{{[{pts_label}]}} {stem}"]

    if qtype == "multichoice":
        parts.extend(_render_mc_options(answers, include_answer_key))
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

    return "\n".join(parts)


def _render_mc_options(answers: list, include_answer_key: bool) -> list[str]:
    if not include_answer_key:
        out = [r"  \begin{enumerate}[label=(\alph*), leftmargin=2em]"]
        for a in answers:
            out.append(f"    \\item {_html_to_latex(a['text_html'] or '')}")
        out.append(r"  \end{enumerate}")
        return out

    out = [r"  \begin{enumerate}[label=(\alph*), leftmargin=2em, itemsep=0.5em]"]
    for a in answers:
        is_correct = (a["fraction"] or 0) >= 99.999
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
    out = [r"  \begin{itemize}[leftmargin=2em, itemsep=0.5em, label={}]"]
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

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if tag in self._WRAPPERS:
            self.parts.append(self._WRAPPERS[tag][0])
        elif tag == "br":
            self.parts.append("\\\\\n")
        elif tag == "p":
            pass
        elif tag == "ul":
            self.parts.append("\n\\begin{itemize}\n")
        elif tag == "ol":
            self.parts.append("\n\\begin{enumerate}\n")
        elif tag == "li":
            self.parts.append("\\item ")
        # unknown tags: ignore

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag in self._WRAPPERS:
            self.parts.append(self._WRAPPERS[tag][1])
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag == "ul":
            self.parts.append("\n\\end{itemize}\n")
        elif tag == "ol":
            self.parts.append("\n\\end{enumerate}\n")

    def handle_startendtag(self, tag, attrs) -> None:
        # handles <br/>
        self.handle_starttag(tag, attrs)

    def handle_data(self, data) -> None:
        self.parts.append(_escape_latex(data))


_MULTI_BLANK_LINES = re.compile(r"\n{3,}")


def _html_to_latex(html: str) -> str:
    parser = _HtmlToLatex()
    parser.feed(html or "")
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
