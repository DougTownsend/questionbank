from question_bank import paths


def test_replaces_whitespace_with_underscore():
    assert paths.normalize_class_name("ECE 212") == "ECE_212"


def test_collapses_consecutive_whitespace():
    assert paths.normalize_class_name("CS   101  Spring") == "CS_101_Spring"


def test_strips_surrounding_whitespace():
    assert paths.normalize_class_name("  MATH 101  ") == "MATH_101"


def test_removes_filesystem_invalid_chars():
    assert paths.normalize_class_name("foo/bar:baz*") == "foobarbaz"


def test_preserves_underscore_and_hyphen():
    assert paths.normalize_class_name("ECE-212_Spring2026") == "ECE-212_Spring2026"


def test_empty_returns_empty():
    assert paths.normalize_class_name("") == ""
    assert paths.normalize_class_name("   ") == ""


def test_idempotent():
    n = paths.normalize_class_name("ECE 212")
    assert paths.normalize_class_name(n) == n


def test_tabs_and_mixed_whitespace():
    assert paths.normalize_class_name("ECE\t212 Fall") == "ECE_212_Fall"
