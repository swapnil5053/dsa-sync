"""Interactive sync orchestration: prompt -> fetch -> scaffold -> readme -> metadata -> git."""

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt

from . import gitops, leetcode_api, metadata, naming, readmes
from .config import Config
from .exceptions import (
    GitCommandError,
    GitNotInstalledError,
    NotAGitRepoError,
    RepositoryNotFoundError,
    SyncAbortedError,
)
from .languages import normalize_language
from .models import Problem
from .scaffold import SyncTransaction, ensure_problem_folder, write_text_file

console = Console()

EOF_SENTINEL = "EOF"
DIFFICULTY_PROMPT_MAP = {"E": "Easy", "M": "Medium", "H": "Hard"}
ROOT_README_NAME = "README.md"


def _prompt_problem_number() -> int:
    while True:
        raw = Prompt.ask("Problem number")
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        console.print("[red]Enter a positive integer problem number.[/red]")


def _prompt_language(default: str) -> str:
    while True:
        raw = Prompt.ask("Language", default=default)
        normalized = normalize_language(raw)
        if normalized:
            return normalized
        console.print(
            f"[red]Unknown language '{raw}'.[/red] Try a full name or alias "
            "(cpp, py, js, ts, go, rs, kt, cs, rb)."
        )


def _prompt_difficulty() -> str:
    while True:
        raw = Prompt.ask("Difficulty [E/M/H]").strip().upper()
        if raw in DIFFICULTY_PROMPT_MAP:
            return DIFFICULTY_PROMPT_MAP[raw]
        console.print("[red]Enter E, M, or H.[/red]")


def _prompt_topics() -> list[str]:
    raw = Prompt.ask("Topics (comma-separated, optional)", default="")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _fetch_metadata(number: int) -> tuple[str, str, str, list[str]] | None:
    """Try the network path. Returns (title, slug, difficulty, topics), or None to go offline."""
    with console.status(f"Fetching problem {number} from LeetCode..."):
        cached = leetcode_api.get_cached_problem(number)
        if cached is None:
            return None
        details = leetcode_api.fetch_problem_details(number, cached.slug)
    if details is not None:
        return details.title, details.slug, details.difficulty, details.topics
    return cached.title, cached.slug, cached.difficulty, []


def _resolve_problem_metadata(number: int) -> tuple[str, str, str, list[str]]:
    """Fetch from LeetCode with user confirmation, falling back to fully offline manual entry."""
    fetched = _fetch_metadata(number)
    if fetched is not None:
        title, slug, difficulty, topics = fetched
        topic_str = ", ".join(topics) if topics else "no tags"
        console.print(f"  Fetched: {number}. {title} — {difficulty} — {topic_str}")
        if Confirm.ask("Correct?", default=True):
            return title, slug, difficulty, topics
    else:
        console.print("[yellow]Could not reach LeetCode. Falling back to manual entry.[/yellow]")

    console.print("Manual entry:")
    title = Prompt.ask("Title")
    difficulty = _prompt_difficulty()
    topics = _prompt_topics()
    slug = naming.title_to_hyphenated(title).lower()
    return title, slug, difficulty, topics


def _read_pasted_solution() -> str:
    console.print(f'(paste, finish with a line containing only "{EOF_SENTINEL}")')
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == EOF_SENTINEL:
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _read_file_solution() -> str:
    while True:
        raw_path = Prompt.ask("File path")
        path = Path(raw_path).expanduser()
        if not path.exists():
            console.print(f"[red]File not found: {path}[/red]")
            continue
        return path.read_text(encoding="utf-8")


def _prompt_solution() -> str:
    console.print("\nSolution:")
    console.print("  [1] Paste here  [2] File path  [3] Empty file")
    choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    if choice == "1":
        content = _read_pasted_solution()
    elif choice == "2":
        content = _read_file_solution()
    else:
        content = ""

    if not content.strip():
        if not Confirm.ask("Solution content is empty. Continue anyway?", default=False):
            raise SyncAbortedError("Cancelled: empty solution not confirmed.")
    return content


def _preflight(config: Config) -> Path:
    """Checks that must pass before any writes happen. Offers to fix recoverable issues."""
    repository_path = config.repository_path
    if not repository_path.exists():
        raise RepositoryNotFoundError(
            f"Repository path {repository_path} does not exist. "
            "Run 'dsa-sync config' to see the current setting, or edit config.yaml directly."
        )
    if not gitops.git_version_ok():
        raise GitNotInstalledError("git is not installed or not on PATH.")
    if not gitops.is_git_repo(repository_path):
        if Confirm.ask(f"{repository_path} is not a git repository. Run 'git init'?", default=True):
            gitops.init_repo(repository_path)
        else:
            raise NotAGitRepoError(f"{repository_path} is not a git repository.")

    leetcode_path = repository_path / config.leetcode_dir
    if not leetcode_path.exists():
        if Confirm.ask(f"{leetcode_path} does not exist. Create it?", default=True):
            leetcode_path.mkdir(parents=True)
        else:
            raise RepositoryNotFoundError(f"{leetcode_path} does not exist.")

    if not gitops.has_remote_origin(repository_path):
        console.print(
            "[yellow]Warning: no 'origin' remote configured. Will commit locally only.[/yellow]"
        )

    return repository_path


def run_sync(config: Config) -> None:
    """Run the full interactive sync flow for one problem."""
    from . import __version__

    console.print(f"[bold]dsa-sync v{__version__}[/bold] — syncing to {config.repository_path}\n")
    repository_path = _preflight(config)

    number = _prompt_problem_number()
    title, slug, difficulty, topics = _resolve_problem_metadata(number)

    existing_problems = metadata.load_problems(repository_path)
    existing = metadata.find_problem(existing_problems, number)

    if existing is not None:
        console.print(f"[yellow]Problem {number} already exists ({existing.folder}).[/yellow]")
        action = Prompt.ask("Overwrite or skip? [o/s]", choices=["o", "s"], default="o")
        if action == "s":
            console.print("Skipped.")
            return
        default_language = existing.language
        added_at = existing.added_at
    else:
        default_language = config.default_language
        added_at = datetime.now().isoformat(timespec="seconds")

    language = _prompt_language(default_language)
    content = _prompt_solution()

    problem = Problem(
        number=number,
        title=title,
        slug=slug,
        difficulty=difficulty,
        language=language,
        topics=topics,
        added_at=added_at,
    )

    with SyncTransaction(console=console) as txn:
        leetcode_path = repository_path / config.leetcode_dir
        folder_path = leetcode_path / problem.folder

        console.print(f"Creating {config.leetcode_dir}/{problem.folder}/", end="  ")
        ensure_problem_folder(txn, folder_path)
        console.print("[green]done[/green]")

        solution_path = folder_path / problem.solution_filename
        console.print(f"Writing {problem.solution_filename}", end="  ")
        write_text_file(txn, solution_path, content)
        console.print("[green]done[/green]")

        problem_readme_path = folder_path / "README.md"
        console.print("Writing README.md", end="  ")
        write_text_file(txn, problem_readme_path, readmes.render_problem_readme(problem))
        console.print("[green]done[/green]")

        updated_problems = metadata.upsert_problem(existing_problems, problem)

        meta_path = metadata.metadata_path(repository_path)
        console.print(
            f"Updating {metadata.METADATA_DIR_NAME}/{metadata.METADATA_FILE_NAME}", end="  "
        )
        txn.snapshot(meta_path)
        metadata.save_problems(repository_path, updated_problems)
        console.print("[green]done[/green]")

        root_readme_path = repository_path / ROOT_README_NAME
        console.print("Regenerating root README.md", end="  ")
        txn.snapshot(root_readme_path)
        root_readme_content = readmes.render_root_readme(
            updated_problems,
            config.leetcode_dir,
            config.readme.recently_solved_count,
            config.readme.date_format,
        )
        root_readme_path.write_text(root_readme_content, encoding="utf-8", newline="\n")
        console.print("[green]done[/green]")

        touched_paths = [
            str(folder_path.relative_to(repository_path)),
            str(meta_path.relative_to(repository_path)),
            str(root_readme_path.relative_to(repository_path)),
        ]

        status_output = gitops.status_porcelain(repository_path, touched_paths)
        if not status_output.strip():
            console.print("Nothing changed since last sync; skipping commit.")
            txn.mark_committed()
            console.print(f"\n[bold green]Total problems: {len(updated_problems)}[/bold green]")
            return

        console.print("git add", end="  ")
        gitops.add(repository_path, touched_paths)
        console.print("[green]done[/green]")

        commit_message = f"{config.git.commit_prefix} {number}: {title}"
        console.print(f'git commit "{commit_message}"', end="  ")
        try:
            gitops.commit(repository_path, commit_message)
        except GitCommandError:
            gitops.reset_paths(repository_path, touched_paths)
            raise
        console.print("[green]done[/green]")
        txn.mark_committed()

        if config.git.auto_push:
            branch = gitops.current_branch(repository_path)
            console.print("git push", end="  ")
            success, message = gitops.push(repository_path, branch)
            if success:
                console.print("[green]done[/green]")
            else:
                console.print("[yellow]failed[/yellow]")
                console.print(f"[yellow]Push failed: {message}[/yellow]")
                console.print(
                    "[yellow]The commit was kept locally. "
                    "Run 'git pull --rebase' then 'git push' when ready.[/yellow]"
                )

    console.print(f"\n[bold green]Synced. Total problems: {len(updated_problems)}[/bold green]")
