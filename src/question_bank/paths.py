from __future__ import annotations

import os
import re
from pathlib import Path

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')
_WHITESPACE = re.compile(r"\s+")


def normalize_class_name(name: str) -> str:
    name = name.strip()
    name = _WHITESPACE.sub("_", name)
    name = _INVALID_CHARS.sub("", name)
    name = name.strip("._")
    return name


def data_root() -> Path:
    override = os.environ.get("QUESTION_BANK_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "question_bank"


def classes_root() -> Path:
    return data_root() / "classes"


def class_dir(class_name: str) -> Path:
    return classes_root() / class_name


def class_db_path(class_name: str) -> Path:
    return class_dir(class_name) / "bank.sqlite"


def class_materials_dir(class_name: str) -> Path:
    return class_dir(class_name) / "materials"


def class_imports_dir(class_name: str) -> Path:
    return class_dir(class_name) / "imports"


def class_exports_dir(class_name: str) -> Path:
    return class_dir(class_name) / "exports"


def ensure_class_dirs(class_name: str) -> Path:
    d = class_dir(class_name)
    d.mkdir(parents=True, exist_ok=True)
    class_materials_dir(class_name).mkdir(exist_ok=True)
    class_imports_dir(class_name).mkdir(exist_ok=True)
    class_exports_dir(class_name).mkdir(exist_ok=True)
    return d


def list_classes() -> list[str]:
    root = classes_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "bank.sqlite").exists())
