#!/usr/bin/env python3
"""Shuffle a question-bank LaTeX student copy and matching answers-only key.

Reads the two LaTeX files produced by `question-bank` for one assignment:
  - the student copy  (mode=student)        e.g. Homework_3.tex
  - the answers-only key (mode=answers_only) e.g. Homework_3_answers.tex

Randomly permutes the question order and, for each multichoice question,
the order of its (a)(b)(c)... options. Rewrites the answers-only key so
that:
  * its N-th entry still matches the N-th question in the shuffled student
    copy,
  * any letter answers point at the new positions of the correct options.

Standalone — uses only the Python standard library. Compile each output
with pdflatex exactly as you would the originals.

Usage:
    python3 shuffle_assignment.py \\
        --student Homework_3.tex \\
        --answers Homework_3_answers.tex \\
        [--out-student Homework_3_v2.tex] \\
        [--out-answers Homework_3_v2_answers.tex] \\
        [--seed 42]
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path


# ---------- regular expressions matching the exporter's output ----------

# A whole question block, exactly as the LaTeX exporter writes it:
#     \item\begin{qblock}
#     ...
#     \end{qblock}
QBLOCK_RE = re.compile(
    r"\\item\\begin\{qblock\}(.*?)\\end\{qblock\}",
    re.DOTALL,
)

# The (a)(b)(c) options enumerate within a multichoice question body.
MC_BLOCK_RE = re.compile(
    r"(\\begin\{enumerate\}\[label=\(\\alph\*\)[^\]]*\])(.*?)(\\end\{enumerate\})",
    re.DOTALL,
)

# A single letter answer reference like '(a)', '(c)'. Used in answer text.
LETTER_RE = re.compile(r"\(([a-z])\)")

# Any item in the answers-only enumerate.
ANSWER_ITEM_SPLIT_RE = re.compile(r"(?=\\item\b)")


# ---------- data ----------


@dataclass
class QBlock:
    body: str                       # content between \begin{qblock}..\end{qblock}
    mc_pre: str | None = None       # pre-options portion of body
    mc_open: str | None = None      # \begin{enumerate}[...] tag
    mc_options: list[str] | None = None  # each '\item ...' chunk
    mc_close: str | None = None     # \end{enumerate} tag
    mc_post: str | None = None      # post-options portion

    def is_multichoice(self) -> bool:
        return self.mc_options is not None

    def render(self) -> str:
        if not self.is_multichoice():
            inner = self.body
        else:
            inner = (
                self.mc_pre
                + self.mc_open
                + "".join(self.mc_options)
                + self.mc_close
                + self.mc_post
            )
        return f"\\item\\begin{{qblock}}{inner}\\end{{qblock}}"


# ---------- student-file parsing ----------


def parse_student_file(text: str) -> tuple[str, list[QBlock], str]:
    """Pull out every \\item\\begin{qblock}...\\end{qblock} block, plus the
    document prefix (preamble + \\begin{document} + \\begin{enumerate}) and
    suffix (\\end{enumerate} + \\end{document})."""
    matches = list(QBLOCK_RE.finditer(text))
    if not matches:
        raise SystemExit(
            "no \\item\\begin{qblock} blocks found in the student file — is "
            "this really a question-bank LaTeX export?"
        )

    prefix = text[: matches[0].start()]
    suffix = text[matches[-1].end() :]
    blocks: list[QBlock] = []
    for m in matches:
        body = m.group(1)
        mc = MC_BLOCK_RE.search(body)
        if mc:
            mc_pre = body[: mc.start()]
            mc_post = body[mc.end() :]
            inner = mc.group(2)
            # split inner content at every \item, keeping the \item with its
            # following text. The first chunk before any \item is whitespace
            # we keep with mc_pre.
            parts = ANSWER_ITEM_SPLIT_RE.split(inner)
            leading_ws = parts[0]
            options = [p for p in parts[1:] if p.strip()]
            blocks.append(
                QBlock(
                    body=body,
                    mc_pre=mc_pre,
                    mc_open=mc.group(1) + leading_ws,
                    mc_options=options,
                    mc_close=mc.group(3),
                    mc_post=mc_post,
                )
            )
        else:
            blocks.append(QBlock(body=body))
    return prefix, blocks, suffix


# ---------- answers-only-file parsing ----------


def parse_answer_file(text: str) -> tuple[str, list[str], str]:
    """Find the single enumerate environment in the answers-only file and
    return (prefix-up-to-and-including-\\begin{enumerate}, list-of-item-
    chunks, suffix-from-\\end{enumerate}-onward)."""
    enum_open = re.search(r"\\begin\{enumerate\}[^\n]*\n", text)
    enum_close = re.search(r"\\end\{enumerate\}", text)
    if not enum_open or not enum_close or enum_open.end() > enum_close.start():
        raise SystemExit(
            "answers-only file must contain a single \\begin{enumerate}..."
            "\\end{enumerate} block"
        )
    prefix = text[: enum_open.end()]
    inner = text[enum_open.end() : enum_close.start()]
    suffix = text[enum_close.start() :]
    parts = ANSWER_ITEM_SPLIT_RE.split(inner)
    items = [p.rstrip() + "\n" for p in parts[1:] if p.strip()]
    return prefix, items, suffix


# ---------- letter remapping ----------


def remap_answer_letters(answer: str, perm: list[int]) -> str:
    """`perm[new_index] = old_index` for the multichoice options. For every
    `(letter)` token in the answer, look up the option's old index, find
    where it ended up after the shuffle, and substitute the new letter."""
    inverse = [0] * len(perm)
    for new_idx, old_idx in enumerate(perm):
        inverse[old_idx] = new_idx

    def replace(m: re.Match) -> str:
        old_letter = m.group(1)
        old_idx = ord(old_letter) - ord("a")
        if 0 <= old_idx < len(perm):
            return f"({chr(ord('a') + inverse[old_idx])})"
        return m.group(0)

    return LETTER_RE.sub(replace, answer)


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--student", required=True, type=Path,
                        help="LaTeX student copy (mode=student)")
    parser.add_argument("--answers", required=True, type=Path,
                        help="LaTeX answers-only key (mode=answers_only)")
    parser.add_argument("--out-student", type=Path,
                        help="output path (default: <student>.shuffled.tex)")
    parser.add_argument("--out-answers", type=Path,
                        help="output path (default: <answers>.shuffled.tex)")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for reproducible shuffles")
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)

    student_text = args.student.read_text(encoding="utf-8")
    answers_text = args.answers.read_text(encoding="utf-8")

    s_prefix, qblocks, s_suffix = parse_student_file(student_text)
    a_prefix, answer_items, a_suffix = parse_answer_file(answers_text)

    if len(qblocks) != len(answer_items):
        raise SystemExit(
            f"question/answer count mismatch: {len(qblocks)} questions vs "
            f"{len(answer_items)} answers — files are not paired"
        )

    # Pair, shuffle the pairs, then for each multichoice block also shuffle
    # the options and remap the matching answer's letter references.
    paired = list(zip(qblocks, answer_items))
    rng.shuffle(paired)

    new_qblocks: list[QBlock] = []
    new_answers: list[str] = []
    for q, a in paired:
        if q.is_multichoice():
            n = len(q.mc_options)
            perm = list(range(n))
            rng.shuffle(perm)
            q.mc_options = [q.mc_options[perm[i]] for i in range(n)]
            a = remap_answer_letters(a, perm)
        new_qblocks.append(q)
        new_answers.append(a)

    student_out = s_prefix + "".join(b.render() for b in new_qblocks) + s_suffix
    answers_out = a_prefix + "".join(new_answers) + a_suffix

    out_student = args.out_student or args.student.with_suffix(".shuffled.tex")
    out_answers = args.out_answers or args.answers.with_suffix(".shuffled.tex")
    out_student.write_text(student_out, encoding="utf-8")
    out_answers.write_text(answers_out, encoding="utf-8")

    print(f"wrote {out_student}", file=sys.stderr)
    print(f"wrote {out_answers}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
