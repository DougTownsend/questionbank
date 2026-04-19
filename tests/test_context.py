from pathlib import Path

import pytest

from question_bank import context, paths


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
    # instructions now point Claude at the DB, not at a rendered snapshot
    assert "bank.sqlite" in content
    assert "sqlite3" in content


def test_refresh_writes_instructions(tmp_class):
    context.refresh(tmp_class)
    assert (paths.class_dir(tmp_class) / "CLAUDE.md").is_file()


def test_refresh_deletes_legacy_bank_context_file(tmp_class):
    stale = paths.class_dir(tmp_class) / "BANK_CONTEXT.md"
    stale.write_text("leftover from an earlier version", encoding="utf-8")
    assert stale.exists()
    context.refresh(tmp_class)
    assert not stale.exists()
