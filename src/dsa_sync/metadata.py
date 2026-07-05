"""Atomic read/write access to .dsa-sync/problems.json, the single source of truth."""

import json
from pathlib import Path

from .exceptions import MetadataError
from .models import Problem

METADATA_DIR_NAME = ".dsa-sync"
METADATA_FILE_NAME = "problems.json"


def metadata_path(repository_path: Path) -> Path:
    """Path to problems.json inside a repository."""
    return repository_path / METADATA_DIR_NAME / METADATA_FILE_NAME


def load_problems(repository_path: Path) -> list[Problem]:
    """Load all problems from problems.json. Returns an empty list if the file doesn't exist yet."""
    path = metadata_path(repository_path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"Could not read {path}: {exc}") from exc

    if not isinstance(raw, list):
        raise MetadataError(f"{path} is malformed: expected a JSON array.")

    try:
        return [Problem.from_dict(entry) for entry in raw]
    except (KeyError, TypeError) as exc:
        raise MetadataError(f"{path} contains a malformed entry: {exc}") from exc


def save_problems(repository_path: Path, problems: list[Problem]) -> None:
    """Write all problems to problems.json atomically (write to temp file, then rename)."""
    path = metadata_path(repository_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    ordered = sorted(problems, key=lambda p: p.number)
    payload = [p.to_dict() for p in ordered]

    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    tmp_path.replace(path)


def find_problem(problems: list[Problem], number: int) -> Problem | None:
    """Find a problem by number in a list, or None if absent."""
    for problem in problems:
        if problem.number == number:
            return problem
    return None


def upsert_problem(problems: list[Problem], problem: Problem) -> list[Problem]:
    """Insert or replace a problem by number, never duplicating an entry."""
    remaining = [p for p in problems if p.number != problem.number]
    remaining.append(problem)
    return remaining
