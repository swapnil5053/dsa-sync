"""All git interaction, via subprocess calls to the system `git` executable."""

import subprocess
from pathlib import Path

from .exceptions import GitCommandError, GitNotInstalledError

DEFAULT_REMOTE = "origin"


def _run(repository_path: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command with cwd=repository_path, raising specific exceptions on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise GitNotInstalledError("git is not installed or not on PATH.") from exc

    if check and result.returncode != 0:
        raise GitCommandError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def git_version_ok() -> bool:
    """Whether `git --version` runs successfully."""
    try:
        result = _run(Path.cwd(), ["--version"], check=False)
    except GitNotInstalledError:
        return False
    return result.returncode == 0


def is_git_repo(repository_path: Path) -> bool:
    """Whether repository_path is inside a git working tree."""
    if not repository_path.exists():
        return False
    result = _run(repository_path, ["rev-parse", "--is-inside-work-tree"], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def init_repo(repository_path: Path) -> None:
    """Run `git init` in repository_path."""
    repository_path.mkdir(parents=True, exist_ok=True)
    _run(repository_path, ["init"])


def has_remote_origin(repository_path: Path) -> bool:
    """Whether a remote named 'origin' is configured."""
    result = _run(repository_path, ["remote"], check=False)
    if result.returncode != 0:
        return False
    return DEFAULT_REMOTE in result.stdout.split()


def current_branch(repository_path: Path) -> str:
    """The currently checked-out branch name."""
    result = _run(repository_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def status_porcelain(repository_path: Path, paths: list[str]) -> str:
    """`git status --porcelain` output scoped to the given paths."""
    result = _run(repository_path, ["status", "--porcelain", "--", *paths])
    return result.stdout


def add(repository_path: Path, paths: list[str]) -> None:
    """Stage only the given paths. Never stages the whole working tree."""
    if not paths:
        return
    _run(repository_path, ["add", "--", *paths])


def reset_paths(repository_path: Path, paths: list[str]) -> None:
    """Unstage the given paths (index only, working tree untouched). Best-effort."""
    if not paths:
        return
    _run(repository_path, ["reset", "--", *paths], check=False)


def commit(repository_path: Path, message: str) -> None:
    """Create a commit with the given message."""
    _run(repository_path, ["commit", "-m", message])


def push(repository_path: Path, branch: str) -> tuple[bool, str]:
    """Push the given branch to origin. Returns (success, message)."""
    result = _run(repository_path, ["push", DEFAULT_REMOTE, branch], check=False)
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, (result.stderr.strip() or result.stdout.strip())
