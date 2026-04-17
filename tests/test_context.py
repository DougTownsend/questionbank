from pathlib import Path

import pytest

from question_bank import context, db, importer, paths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_class(tmp_path, monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_ROOT", str(tmp_path))
    name = "TestClass"
    paths.ensure_class_dirs(name)
    return name


def test_write_instructions_creates_file(tmp_class):
    path = context.write_instructions(tmp_class)
    assert path.name == "CLAUDE.md"
    content = path.read_text()
    assert "AUTO-GENERATED" in content
    assert "Moodle XML" in content
    assert "multichoice" in content
    assert "truefalse" in content
    assert "numerical" in content
    assert "BANK_CONTEXT.md" in content  # cross-reference to the bank file


def test_write_bank_context_empty(tmp_class):
    path = context.write_bank_context(tmp_class)
    assert path.name == "BANK_CONTEXT.md"
    content = path.read_text()
    assert "Approved questions (0)" in content
    assert "Rejected questions (0)" in content


def test_bank_context_includes_approved_and_rejected(tmp_class):
    conn = db.connect(paths.class_db_path(tmp_class))
    importer.import_file(conn, FIXTURES / "sample_questions.xml")
    pending = db.list_questions(conn, status="pending")
    db.set_status(conn, pending[0]["id"], "approved")
    db.set_status(
        conn, pending[1]["id"], "rejected", rejection_note="too obvious, rewrite"
    )
    conn.close()

    path = context.write_bank_context(tmp_class)
    content = path.read_text()

    assert "Approved questions (1)" in content
    assert "Rejected questions (1)" in content
    assert "too obvious, rewrite" in content
    assert "[CORRECT]" in content
    assert "[INCORRECT]" in content
    assert "hits / (hits + misses)" in content or "write-through" in content.lower()


def test_export_all_writes_both_files(tmp_class):
    instructions_path, bank_path = context.export_all(tmp_class)
    assert instructions_path.exists()
    assert bank_path.exists()
    assert instructions_path.parent == bank_path.parent
