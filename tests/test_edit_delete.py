from pathlib import Path

import pytest
from lxml import etree

from question_bank import db, importer, moodle_xml

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def populated(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    return conn


# ---------- delete_question ----------


def test_delete_question_removes_row(populated):
    q = db.list_questions(populated)[0]
    db.delete_question(populated, q["id"])
    assert db.get_question(populated, q["id"]) is None


def test_delete_question_cascades_to_answers(populated):
    q = db.list_questions(populated)[0]
    assert db.get_answers(populated, q["id"])
    db.delete_question(populated, q["id"])
    assert db.get_answers(populated, q["id"]) == []


def test_delete_question_cascades_to_rejection_notes(populated):
    q = db.list_questions(populated)[0]
    db.set_status(populated, q["id"], "rejected", rejection_note="bad")
    assert db.latest_rejection_note(populated, q["id"]) == "bad"
    db.delete_question(populated, q["id"])
    assert db.latest_rejection_note(populated, q["id"]) is None


def test_delete_question_cascades_from_assignments(populated):
    q = db.list_questions(populated)[0]
    db.set_status(populated, q["id"], "approved")
    aid = db.create_assignment(populated, "Q1")
    db.add_question_to_assignment(populated, aid, q["id"])
    db.delete_question(populated, q["id"])
    picked = db.list_assignment_questions(populated, aid)
    assert picked == []


def test_assignments_containing_question_reports_memberships(populated):
    q = db.list_questions(populated)[0]
    db.set_status(populated, q["id"], "approved")
    a1 = db.create_assignment(populated, "Quiz 1")
    a2 = db.create_assignment(populated, "Quiz 2")
    db.add_question_to_assignment(populated, a1, q["id"])
    db.add_question_to_assignment(populated, a2, q["id"])
    rows = db.assignments_containing_question(populated, q["id"])
    names = {r["name"] for r in rows}
    assert names == {"Quiz 1", "Quiz 2"}


# ---------- update_question ----------


def test_update_question_updates_fields_and_answers(populated):
    mc = [q for q in db.list_questions(populated) if q["moodle_type"] == "multichoice"][0]
    new_stem = "<p>Updated stem</p>"
    new_hash = moodle_xml.stem_hash_for(new_stem)
    new_xml = moodle_xml.serialize_question(
        moodle_type="multichoice",
        name="New name",
        stem_html=new_stem,
        general_feedback_html="<p>gf</p>",
        default_grade=mc["default_grade"],
        penalty=mc["penalty"],
        answers=[
            {"text_html": "only answer", "fraction": 100.0, "feedback_html": "yes", "tolerance": None},
        ],
    )
    db.update_question(
        populated,
        mc["id"],
        name="New name",
        stem_html=new_stem,
        stem_hash=new_hash,
        raw_xml=new_xml,
        raw_category="$course$/New/Cat",
        general_feedback_html="<p>gf</p>",
        answers=[
            {"text_html": "only answer", "fraction": 100.0, "feedback_html": "yes", "tolerance": None},
        ],
    )
    refreshed = db.get_question(populated, mc["id"])
    assert refreshed["name"] == "New name"
    assert refreshed["stem_html"] == new_stem
    assert refreshed["stem_hash"] == new_hash
    assert refreshed["raw_category"] == "$course$/New/Cat"
    answers = db.get_answers(populated, mc["id"])
    assert len(answers) == 1
    assert answers[0]["text_html"] == "only answer"
    assert answers[0]["fraction"] == 100.0


# ---------- serialize_question ----------


def test_serialize_multichoice_parses_back():
    xml = moodle_xml.serialize_question(
        moodle_type="multichoice",
        name="Q1",
        stem_html="<p>What is 2+2?</p>",
        general_feedback_html=None,
        default_grade=1.0,
        penalty=0.3333333,
        answers=[
            {"text_html": "4", "fraction": 100.0, "feedback_html": "correct"},
            {"text_html": "5", "fraction": 0.0, "feedback_html": "no"},
        ],
    )
    wrapped = f"<quiz>{xml}</quiz>".encode("utf-8")
    result = moodle_xml.parse_bytes(wrapped)
    assert len(result.questions) == 1
    q = result.questions[0]
    assert q.moodle_type == "multichoice"
    assert q.name == "Q1"
    assert len(q.answers) == 2
    assert q.answers[0].text_html == "4"
    assert q.answers[0].fraction == 100.0
    assert q.answers[0].feedback_html == "correct"


def test_serialize_numerical_preserves_tolerance():
    xml = moodle_xml.serialize_question(
        moodle_type="numerical",
        name="N1",
        stem_html="<p>log2(64)?</p>",
        general_feedback_html=None,
        default_grade=1.0,
        penalty=0.3333333,
        answers=[
            {"text_html": "6", "fraction": 100.0, "feedback_html": "ok", "tolerance": 0.0},
        ],
    )
    result = moodle_xml.parse_bytes(f"<quiz>{xml}</quiz>".encode("utf-8"))
    q = result.questions[0]
    assert q.moodle_type == "numerical"
    assert q.answers[0].tolerance == 0.0


def test_serialize_truefalse_round_trip():
    xml = moodle_xml.serialize_question(
        moodle_type="truefalse",
        name="T1",
        stem_html="<p>X is true.</p>",
        general_feedback_html=None,
        default_grade=1.0,
        penalty=1.0,
        answers=[
            {"text_html": "true", "fraction": 100.0, "feedback_html": "yes"},
            {"text_html": "false", "fraction": 0.0, "feedback_html": "no"},
        ],
    )
    result = moodle_xml.parse_bytes(f"<quiz>{xml}</quiz>".encode("utf-8"))
    q = result.questions[0]
    assert q.moodle_type == "truefalse"
    assert [a.text_html for a in q.answers] == ["true", "false"]


def test_serialize_then_export_in_assignment(populated, tmp_path, monkeypatch):
    """Edit a question, then build an assignment XML — make sure the edited
    version is what gets exported."""
    from question_bank import exporter, paths
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))

    mc = [q for q in db.list_questions(populated) if q["moodle_type"] == "multichoice"][0]
    db.set_status(populated, mc["id"], "approved")

    new_stem = "<p>EDITED STEM</p>"
    new_xml = moodle_xml.serialize_question(
        moodle_type="multichoice",
        name="edited",
        stem_html=new_stem,
        general_feedback_html=None,
        default_grade=mc["default_grade"],
        penalty=mc["penalty"],
        answers=[
            {"text_html": "a", "fraction": 100.0, "feedback_html": "yes"},
            {"text_html": "b", "fraction": 0.0, "feedback_html": "no"},
        ],
    )
    db.update_question(
        populated,
        mc["id"],
        name="edited",
        stem_html=new_stem,
        stem_hash=moodle_xml.stem_hash_for(new_stem),
        raw_xml=new_xml,
        raw_category=mc["raw_category"],
        general_feedback_html=None,
        answers=[
            {"text_html": "a", "fraction": 100.0, "feedback_html": "yes"},
            {"text_html": "b", "fraction": 0.0, "feedback_html": "no"},
        ],
    )

    aid = db.create_assignment(populated, "Q")
    db.add_question_to_assignment(populated, aid, mc["id"])
    assignment_xml = exporter.build_assignment_xml(populated, aid)
    assert "EDITED STEM" in assignment_xml
    # parses cleanly
    etree.fromstring(assignment_xml.encode("utf-8"))
