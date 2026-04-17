from pathlib import Path

import pytest
from lxml import etree

from question_bank import db, exporter, importer, moodle_xml

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn_with_approved(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")
    return conn


def test_create_list_rename_delete(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    assert db.get_assignment(conn, aid)["name"] == "Quiz 1"

    rows = db.list_assignments(conn)
    assert len(rows) == 1
    assert rows[0]["n_questions"] == 0

    db.rename_assignment(conn, aid, "Midterm")
    assert db.get_assignment(conn, aid)["name"] == "Midterm"

    db.delete_assignment(conn, aid)
    assert db.get_assignment(conn, aid) is None


def test_add_and_remove_questions_ordering(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    qs = db.list_questions(conn, status="approved")
    assert len(qs) == 3

    for q in qs:
        assert db.add_question_to_assignment(conn, aid, q["id"]) is True
    assert db.add_question_to_assignment(conn, aid, qs[0]["id"]) is False  # dupe

    picked = db.list_assignment_questions(conn, aid)
    assert [p["id"] for p in picked] == [q["id"] for q in qs]

    db.remove_question_from_assignment(conn, aid, qs[1]["id"])
    picked = db.list_assignment_questions(conn, aid)
    assert [p["id"] for p in picked] == [qs[0]["id"], qs[2]["id"]]
    assert [p["aq_ord"] for p in picked] == [0, 1]  # compacted


def test_delete_assignment_cascades(conn_with_approved):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    qs = db.list_questions(conn, status="approved")
    db.add_question_to_assignment(conn, aid, qs[0]["id"])
    db.delete_assignment(conn, aid)
    rows = list(conn.execute("SELECT * FROM assignment_questions"))
    assert rows == []


def test_export_xml_is_valid_and_has_all_questions(conn_with_approved, tmp_path):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])

    xml = exporter.build_assignment_xml(conn, aid)
    assert xml.startswith("<?xml")

    root = etree.fromstring(xml.encode("utf-8"))
    assert root.tag == "quiz"
    types = [q.get("type") for q in root.findall("question")]
    assert "multichoice" in types
    assert "truefalse" in types
    assert "numerical" in types
    assert "category" in types


def test_export_round_trip_parses_with_own_parser(conn_with_approved, tmp_path):
    conn = conn_with_approved
    aid = db.create_assignment(conn, "Quiz 1")
    for q in db.list_questions(conn, status="approved"):
        db.add_question_to_assignment(conn, aid, q["id"])

    xml = exporter.build_assignment_xml(conn, aid)
    result = moodle_xml.parse_bytes(xml.encode("utf-8"))
    assert len(result.questions) == 3
    assert {q.moodle_type for q in result.questions} == {
        "multichoice",
        "truefalse",
        "numerical",
    }
    for q in result.questions:
        assert q.raw_category == "$course$/ECE212/Chapter 3/Caches"


def test_export_to_file_writes_under_class_exports(
    conn_with_approved, tmp_path, monkeypatch
):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    from question_bank import paths

    class_name = "TestClass"
    paths.ensure_class_dirs(class_name)
    # re-connect via the class path so the DB lives where exporter expects
    class_db = db.connect(paths.class_db_path(class_name))
    importer.import_file(class_db, FIXTURES / "sample_questions.xml")
    for q in db.list_questions(class_db, status="pending"):
        db.set_status(class_db, q["id"], "approved")
    aid = db.create_assignment(class_db, "Quiz 3 — Caches")
    for q in db.list_questions(class_db, status="approved"):
        db.add_question_to_assignment(class_db, aid, q["id"])

    out_path = exporter.export_assignment_to_file(class_db, class_name, aid)
    assert out_path.exists()
    assert out_path.parent == paths.class_exports_dir(class_name)
    assert out_path.suffix == ".xml"
    # filename should have no spaces
    assert " " not in out_path.name
