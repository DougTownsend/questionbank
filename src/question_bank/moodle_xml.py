from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from lxml import etree

SUPPORTED_TYPES = {"multichoice", "truefalse", "numerical"}


@dataclass
class ParsedAnswer:
    text_html: str
    fraction: float
    feedback_html: str | None = None
    tolerance: float | None = None


@dataclass
class ParsedQuestion:
    moodle_type: str
    multichoice_single: bool | None
    name: str | None
    stem_html: str
    raw_xml: str
    raw_category: str | None
    default_grade: float | None
    penalty: float | None
    general_feedback_html: str | None
    answers: list[ParsedAnswer] = field(default_factory=list)

    @property
    def stem_hash(self) -> str:
        return stem_hash_for(self.stem_html)


@dataclass
class ParseResult:
    questions: list[ParsedQuestion]
    unsupported: list[tuple[str, str]]  # (type, name)


class ParseError(Exception):
    pass


def parse_file(path: Path | str) -> ParseResult:
    with open(path, "rb") as f:
        return parse_bytes(f.read())


def parse_bytes(data: bytes) -> ParseResult:
    try:
        # strip_cdata=False keeps <![CDATA[...]]> sections intact so re-export
        # round-trips (Moodle rejects entity-escaped HTML in <text>).
        parser = etree.XMLParser(strip_cdata=False)
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as e:
        raise ParseError(f"invalid XML: {e}") from e
    if root.tag != "quiz":
        raise ParseError(f"expected root <quiz>, got <{root.tag}>")

    questions: list[ParsedQuestion] = []
    unsupported: list[tuple[str, str]] = []
    current_category: str | None = None

    for q in root.findall("question"):
        qtype = q.get("type") or ""
        if qtype == "category":
            current_category = _text(q.find("category/text"))
            continue
        name = _text(q.find("name/text"))
        if qtype not in SUPPORTED_TYPES:
            unsupported.append((qtype, name or ""))
            continue
        parsed = _parse_question(q, qtype, current_category)
        questions.append(parsed)

    return ParseResult(questions=questions, unsupported=unsupported)


def _parse_question(
    q: etree._Element, qtype: str, category: str | None
) -> ParsedQuestion:
    name = _text(q.find("name/text"))
    stem_html = _text(q.find("questiontext/text")) or ""
    general_feedback_html = _text(q.find("generalfeedback/text"))
    default_grade = _float(q.find("defaultgrade"))
    penalty = _float(q.find("penalty"))
    multichoice_single = (
        _bool(q.find("single"), default=True) if qtype == "multichoice" else None
    )

    answers: list[ParsedAnswer] = []
    for a in q.findall("answer"):
        text_html = _text(a.find("text")) or ""
        fraction = float(a.get("fraction") or "0")
        feedback_html = _text(a.find("feedback/text"))
        tolerance = _float(a.find("tolerance"))
        answers.append(
            ParsedAnswer(
                text_html=text_html,
                fraction=fraction,
                feedback_html=feedback_html,
                tolerance=tolerance,
            )
        )

    if qtype == "multichoice" and not multichoice_single:
        positive_total = sum(a.fraction for a in answers if a.fraction > 0)
        if abs(positive_total - 100.0) > 0.001:
            raise ParseError(
                "multi-answer multichoice fractions greater than zero "
                f"must total 100, got {positive_total:g}"
            )

    raw_xml = etree.tostring(q, encoding="unicode")

    return ParsedQuestion(
        moodle_type=qtype,
        multichoice_single=multichoice_single,
        name=name,
        stem_html=stem_html,
        raw_xml=raw_xml,
        raw_category=category,
        default_grade=default_grade,
        penalty=penalty,
        general_feedback_html=general_feedback_html,
        answers=answers,
    )


def _text(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    # Claude sometimes writes <text><code>x</code></text> instead of wrapping
    # the HTML in CDATA. lxml then parses <code> as a real child element and
    # .text only holds the string BEFORE the first child, silently truncating
    # the answer. Serialize mixed content back into a single string.
    if len(el) > 0:
        parts: list[str] = [el.text or ""]
        for child in el:
            parts.append(etree.tostring(child, encoding="unicode"))
        return "".join(parts)
    return el.text


def _float(el: etree._Element | None) -> float | None:
    if el is None or el.text is None or el.text.strip() == "":
        return None
    try:
        return float(el.text)
    except ValueError:
        return None


def _bool(el: etree._Element | None, *, default: bool) -> bool:
    if el is None or el.text is None:
        return default
    return el.text.strip().lower() not in {"false", "0", "no"}


# ---------- stem hashing (for dedup) ----------


class _TextStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


def _strip_html(html: str) -> str:
    s = _TextStripper()
    s.feed(html)
    return "".join(s.chunks)


_WS_RE = re.compile(r"\s+")


def normalize_stem(stem_html: str) -> str:
    text = _strip_html(stem_html)
    text = text.replace("\u00a0", " ")
    text = _WS_RE.sub(" ", text).strip().lower()
    return text


def stem_hash_for(stem_html: str) -> str:
    return hashlib.sha256(normalize_stem(stem_html).encode("utf-8")).hexdigest()


# Moodle context-token prefix used in <category> (e.g. $course$, $module$, $system$).
# They're meaningful on import but noise for users browsing the bank.
_CONTEXT_PREFIX_RE = re.compile(r"^\$[A-Za-z_]+\$/+")


def format_category_for_display(raw: str | None) -> str:
    if not raw:
        return ""
    return _CONTEXT_PREFIX_RE.sub("", raw).strip("/")


# ---------- serializer (round-trips parse -> DB -> serialize) ----------


def serialize_question(
    *,
    moodle_type: str,
    name: str | None,
    stem_html: str,
    general_feedback_html: str | None,
    default_grade: float | None,
    penalty: float | None,
    answers: list[dict],
    multichoice_single: bool = True,
) -> str:
    """Build a `<question>` element fragment for the given fields.
    Used to refresh `raw_xml` after an in-app edit, so later exports reflect
    the current state."""
    q = etree.Element("question", type=moodle_type)

    name_el = etree.SubElement(q, "name")
    etree.SubElement(name_el, "text").text = name or ""

    qt = etree.SubElement(q, "questiontext", format="html")
    etree.SubElement(qt, "text").text = etree.CDATA(stem_html or "")

    gf = etree.SubElement(q, "generalfeedback", format="html")
    etree.SubElement(gf, "text").text = etree.CDATA(general_feedback_html or "")

    if default_grade is not None:
        etree.SubElement(q, "defaultgrade").text = _fmt_num(default_grade)
    if penalty is not None:
        etree.SubElement(q, "penalty").text = _fmt_num(penalty)
    etree.SubElement(q, "hidden").text = "0"

    if moodle_type == "multichoice":
        etree.SubElement(q, "single").text = (
            "true" if multichoice_single else "false"
        )
        etree.SubElement(q, "shuffleanswers").text = "true"
        etree.SubElement(q, "answernumbering").text = "abc"

    for a in answers:
        answer_fmt = (
            "html" if moodle_type == "multichoice" else "moodle_auto_format"
        )
        a_el = etree.SubElement(
            q, "answer", fraction=_fmt_num(float(a["fraction"])), format=answer_fmt
        )
        etree.SubElement(a_el, "text").text = etree.CDATA(a["text_html"] or "")
        if moodle_type == "numerical" and a.get("tolerance") is not None:
            etree.SubElement(a_el, "tolerance").text = _fmt_num(float(a["tolerance"]))
        fb_html = a.get("feedback_html") or ""
        fb_el = etree.SubElement(a_el, "feedback", format="html")
        etree.SubElement(fb_el, "text").text = etree.CDATA(fb_html)

    return etree.tostring(q, encoding="unicode", pretty_print=True)


def _fmt_num(x: float) -> str:
    # integer-valued floats become "100" not "100.0"; otherwise keep the float
    if x == int(x):
        return str(int(x))
    return f"{x:g}"
