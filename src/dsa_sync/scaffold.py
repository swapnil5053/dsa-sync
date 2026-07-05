"""Filesystem scaffolding for a synced problem, plus the SyncTransaction rollback guard."""

from pathlib import Path

from rich.console import Console


class SyncTransaction:
    """Snapshots mutated files and tracks created directories so a failed sync can be undone.

    Used as a context manager: any exception raised inside the `with` block before
    mark_committed() is called triggers an automatic rollback (also covers Ctrl+C,
    since KeyboardInterrupt propagates through __exit__ like any other exception).
    """

    def __init__(self, console: Console | None = None) -> None:
        self._snapshots: dict[Path, bytes | None] = {}
        self._created_dirs: list[Path] = []
        self._committed = False
        self.console = console or Console()

    def __enter__(self) -> "SyncTransaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._committed and exc_type is not None:
            actions = self.rollback()
            if actions:
                self.console.print("[yellow]Sync rolled back:[/yellow]")
                for action in actions:
                    self.console.print(f"  - {action}")
        return False

    def snapshot(self, path: Path) -> None:
        """Record a file's current contents (or absence) before it is created/overwritten."""
        if path in self._snapshots:
            return
        self._snapshots[path] = path.read_bytes() if path.exists() else None

    def track_dir(self, path: Path) -> None:
        """Record a directory newly created during this sync, for removal on rollback."""
        self._created_dirs.append(path)

    def mark_committed(self) -> None:
        """Signal that the sync succeeded; __exit__ will no longer roll back."""
        self._committed = True

    def rollback(self) -> list[str]:
        """Undo all tracked changes: restore snapshotted files, remove created directories."""
        actions: list[str] = []
        for path, original in self._snapshots.items():
            if original is None:
                if path.exists():
                    path.unlink()
                    actions.append(f"removed {path}")
            else:
                path.write_bytes(original)
                actions.append(f"restored {path}")

        for path in reversed(self._created_dirs):
            try:
                path.rmdir()
                actions.append(f"removed directory {path}")
            except OSError:
                pass
        return actions


def write_text_file(transaction: SyncTransaction, path: Path, content: str) -> None:
    """Snapshot then write a text file, creating parent directories as needed."""
    transaction.snapshot(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def ensure_problem_folder(transaction: SyncTransaction, folder_path: Path) -> bool:
    """Create the problem folder if it doesn't exist. Returns True if newly created."""
    if folder_path.exists():
        return False
    folder_path.mkdir(parents=True)
    transaction.track_dir(folder_path)
    return True
