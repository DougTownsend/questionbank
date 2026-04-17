import shutil
from pathlib import Path

import pytest

from question_bank import db, importer, paths

FIXTURES = Path(__file__).parent / "fixtures"


def test_import_creates_questions(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    report = importer.import_file(conn, FIXTURES / "sample_questions.xml")

    assert len(report.inserted) == 3
    assert len(report.skipped_duplicates) == 0
    assert report.unsupported == [("essay", "Explain cache coherence")]

    rows = db.list_questions(conn, status="pending")
    assert len(rows) == 3
    assert {r["moodle_type"] for r in rows} == {"multichoice", "truefalse", "numerical"}


def test_import_dedup_on_second_import(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    report2 = importer.import_file(conn, FIXTURES / "sample_questions.xml")
    assert len(report2.inserted) == 0
    assert len(report2.skipped_duplicates) == 3


def test_multichoice_answers_persisted(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    mc = [r for r in db.list_questions(conn) if r["moodle_type"] == "multichoice"][0]
    answers = db.get_answers(conn, mc["id"])
    assert len(answers) == 4
    correct = [a for a in answers if a["fraction"] == 100.0]
    assert len(correct) == 1


def test_numerical_tolerance_persisted(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    num = [r for r in db.list_questions(conn) if r["moodle_type"] == "numerical"][0]
    answers = db.get_answers(conn, num["id"])
    assert len(answers) == 1
    assert answers[0]["tolerance"] == 0.0


def test_set_status_rejected_stores_note(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    q = db.list_questions(conn, status="pending")[0]
    db.set_status(conn, q["id"], "rejected", rejection_note="too easy")
    assert db.latest_rejection_note(conn, q["id"]) == "too easy"
    counts = db.count_by_status(conn)
    assert counts["rejected"] == 1
    assert counts["pending"] == 2


def test_set_status_approved(tmp_path):
    conn = db.connect(tmp_path / "bank.sqlite")
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    q = db.list_questions(conn, status="pending")[0]
    db.set_status(conn, q["id"], "approved")
    counts = db.count_by_status(conn)
    assert counts["approved"] == 1


# ---------- cleanup_completed_import ----------


@pytest.fixture
def class_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    name = "TestClass"
    paths.ensure_class_dirs(name)
    imports_dir = paths.class_imports_dir(name)
    staged = imports_dir / "batch.xml"
    shutil.copy(FIXTURES / "sample_questions.xml", staged)
    conn = db.connect(paths.class_db_path(name))
    report = importer.import_file(conn, staged)
    return {
        "class_name": name,
        "conn": conn,
        "imports_dir": imports_dir,
        "staged": staged,
        "import_id": report.import_id,
    }


def test_cleanup_deletes_file_when_all_nonpending(class_env):
    conn = class_env["conn"]
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")
    deleted = importer.cleanup_completed_import(
        conn, class_env["class_name"], class_env["import_id"]
    )
    assert deleted == class_env["staged"].resolve()
    assert not class_env["staged"].exists()


def test_cleanup_accepts_mixed_approve_reject(class_env):
    conn = class_env["conn"]
    pending = db.list_questions(conn, status="pending")
    db.set_status(conn, pending[0]["id"], "approved")
    db.set_status(conn, pending[1]["id"], "rejected", rejection_note="no")
    db.set_status(conn, pending[2]["id"], "rejected", rejection_note="no")
    deleted = importer.cleanup_completed_import(
        conn, class_env["class_name"], class_env["import_id"]
    )
    assert deleted is not None
    assert not class_env["staged"].exists()


def test_cleanup_noop_while_pending_remain(class_env):
    conn = class_env["conn"]
    pending = db.list_questions(conn, status="pending")
    db.set_status(conn, pending[0]["id"], "approved")
    deleted = importer.cleanup_completed_import(
        conn, class_env["class_name"], class_env["import_id"]
    )
    assert deleted is None
    assert class_env["staged"].exists()


def test_cleanup_handles_missing_file(class_env):
    conn = class_env["conn"]
    class_env["staged"].unlink()
    for q in db.list_questions(conn, status="pending"):
        db.set_status(conn, q["id"], "approved")
    deleted = importer.cleanup_completed_import(
        conn, class_env["class_name"], class_env["import_id"]
    )
    assert deleted is None


def test_cleanup_handles_none_import_id(class_env):
    conn = class_env["conn"]
    deleted = importer.cleanup_completed_import(
        conn, class_env["class_name"], None
    )
    assert deleted is None


def test_cleanup_ignores_path_traversal_filename(tmp_path, monkeypatch):
    """Defense-in-depth: a malicious filename with .. shouldn't escape imports/."""
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    name = "TestClass"
    paths.ensure_class_dirs(name)
    conn = db.connect(paths.class_db_path(name))
    # import_file uses Path(path).name, so traversal is already stripped at ingestion.
    # Inject directly at the DB layer to simulate a hostile import row.
    import_id = db.create_import(conn, "../../etc/hosts", 0)
    db.insert_question(
        conn,
        moodle_type="truefalse",
        name="x",
        stem_html="<p>x</p>",
        stem_hash="h",
        raw_xml="<question type='truefalse'/>",
        raw_category=None,
        default_grade=1.0,
        penalty=1.0,
        general_feedback_html=None,
        source_import_id=import_id,
        answers=[{"text_html": "true", "fraction": 100, "feedback_html": None}],
    )
    q = db.list_questions(conn, status="pending")[0]
    db.set_status(conn, q["id"], "approved")
    deleted = importer.cleanup_completed_import(conn, name, import_id)
    assert deleted is None
