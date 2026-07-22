#!/usr/bin/env python3
"""
project_status.py — Scan and clean up git projects under PROJECT_FOLDER.

Env vars:
  PROJECT_FOLDER         Root directory to scan (default: $HOME/projects)
  PROJECT_FOLDER_MODEL   pi model for commit messages (default: bob/haiku-4.5)
  PROJECT_FOLDER_NOPUSH  Colon-separated dirs: pull only, local changes overwritten, no push
                         (recursive — any repo under these paths is affected)
  PROJECT_FOLDER_IGNORE  Colon-separated dirs: skip entirely, no operation at all
                         (recursive — any repo under these paths is skipped)
"""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_FOLDER = Path(os.environ.get("PROJECT_FOLDER", Path.home() / "projects"))
PROJECT_FOLDER_MODEL = os.environ.get("PROJECT_FOLDER_MODEL", "bob/haiku-4.5")


def _parse_path_list(env_var: str) -> list[Path]:
    """Parse a colon-separated list of paths from an env var, expanding ~ and making absolute."""
    raw = os.environ.get(env_var, "")
    if not raw.strip():
        return []
    paths = []
    for p in raw.split(":"):
        p = p.strip()
        if not p:
            continue
        expanded = Path(p).expanduser()
        # If relative, resolve against PROJECT_FOLDER
        if not expanded.is_absolute():
            expanded = PROJECT_FOLDER / expanded
        paths.append(expanded.resolve())
    return paths


NOPUSH_FOLDERS = _parse_path_list("PROJECT_FOLDER_NOPUSH")
IGNORE_FOLDERS = _parse_path_list("PROJECT_FOLDER_IGNORE")


def is_under(path: Path, folders: list[Path]) -> bool:
    """Return True if path is equal to or under any of the given folders."""
    try:
        resolved = path.resolve()
    except Exception:
        return False
    return any(
        resolved == f or resolved.is_relative_to(f)
        for f in folders
    )

PROTECTED_BRANCHES = {
    "main", "master", "uat", "develop", "tests", "staging",
    "dev", "production", "prod", "preprod", "release", "hotfix",
}

# Statuses
UP_TO_DATE              = "UP_TO_DATE"
BACKUPED                = "BACKUPED"
BACKUP_DIRTY            = "BACKUP_DIRTY"
REMOTE_DIVERGENT        = "REMOTE_DIVERGENT"
MISSING_GIT             = "MISSING_GIT"
MISSING_REMOTE_UP_TO_DATE = "MISSING_REMOTE_UP_TO_DATE"
MISSING_REMOTE_BACKUPED   = "MISSING_REMOTE_BACKUPED"

TODAY = date.today().strftime("%Y%m%d")

# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

GIT_FETCH_TIMEOUT = int(os.environ.get("GIT_FETCH_TIMEOUT", "15"))   # seconds
GIT_CMD_TIMEOUT   = int(os.environ.get("GIT_CMD_TIMEOUT",   "10"))   # seconds


def run(cmd: list[str], cwd: Path, check=False, timeout: int = GIT_CMD_TIMEOUT) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "", f"timed out after {timeout}s"
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git(args: list[str], cwd: Path, check=False, timeout: int = GIT_CMD_TIMEOUT) -> tuple[int, str, str]:
    return run(["git"] + args, cwd=cwd, check=check, timeout=timeout)

# ---------------------------------------------------------------------------
# Discovery: walk PROJECT_FOLDER, stop at .git boundaries
# ---------------------------------------------------------------------------

def discover(root: Path) -> tuple[list[Path], list[Path]]:
    """
    Returns (git_repos, non_git_dirs).

    git_repos: every directory containing a .git (recursion stops there).
    non_git_dirs: directories that:
      - are NOT under a git repo
      - have at least one sibling that IS a git repo (i.e. share a parent
        with at least one git repo) — so the MISSING_GIT ratio check is
        meaningful
      - contain no git repo inside themselves
    """
    git_repos: list[Path] = []
    # Map parent -> list of direct children that are NOT git repos
    # We'll build this during the walk, then filter afterwards.
    candidate_non_git: list[Path] = []   # dirs with no .git, no sub-git

    def _walk(path: Path) -> bool:
        """Returns True if this subtree contains at least one git repo."""
        # Skip entirely if under IGNORE_FOLDERS
        if is_under(path, IGNORE_FOLDERS):
            return False

        if (path / ".git").exists():
            git_repos.append(path)
            return True

        try:
            children = [
                c for c in path.iterdir()
                if c.is_dir() and not c.name.startswith(".")
            ]
        except PermissionError:
            return False

        has_git_descendant = False
        for child in children:
            if _walk(child):
                has_git_descendant = True

        if not has_git_descendant:
            # This dir has no git repo anywhere inside
            candidate_non_git.append(path)

        return has_git_descendant

    _walk(root)

    git_repos_set = set(git_repos)

    # Build parent -> git-repo children map to check sibling ratio
    git_parents: dict[Path, list[Path]] = {}
    for repo in git_repos:
        parent = repo.parent
        git_parents.setdefault(parent, []).append(repo)

    # Keep only candidates whose parent has at least one git-repo sibling
    non_git_dirs = [
        d for d in candidate_non_git
        if d.parent in git_parents
    ]

    return git_repos, non_git_dirs


# ---------------------------------------------------------------------------
# pi commit message
# ---------------------------------------------------------------------------

def pi_available() -> bool:
    rc, _, _ = run(["which", "pi"], cwd=Path("/"))
    return rc == 0


def generate_commit_message(repo: Path) -> str:
    """Try pi for a commit message; fall back to 'Backup YYYY-mm-dd'."""
    fallback = f"Backup {date.today().isoformat()}"

    if not pi_available():
        return fallback

    # Use unstaged diff (before add -A) — or staged if already staged
    _, diff, _ = git(["diff", "--stat"], cwd=repo)
    _, diff_full, _ = git(["diff"], cwd=repo)
    if not diff_full.strip():
        # Maybe already staged
        _, diff, _ = git(["diff", "--cached", "--stat"], cwd=repo)
        _, diff_full, _ = git(["diff", "--cached"], cwd=repo)
    if not diff_full.strip():
        return fallback

    prompt = (
        "You are a commit message generator. Write a single concise conventional commit message "
        "(max 72 chars, no explanation, no markdown, no quotes) summarising these staged changes:\n\n"
        f"{diff}\n\n{diff_full[:3000]}"
    )

    rc, out, _ = run(
        ["pi", "--model", PROJECT_FOLDER_MODEL, "--print", prompt],
        cwd=repo
    )
    if rc == 0 and out.strip():
        # take only first line, strip quotes
        line = out.strip().splitlines()[0].strip().strip('"').strip("'")
        return line if line else fallback
    return fallback


# ---------------------------------------------------------------------------
# Core git analysis and actions
# ---------------------------------------------------------------------------

def get_current_branch(repo: Path) -> str:
    _, out, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    return out.strip()


def has_remote(repo: Path) -> bool:
    _, out, _ = git(["remote"], cwd=repo)
    return bool(out.strip())


def uncommitted_files(repo: Path) -> bool:
    _, out, _ = git(["status", "--porcelain"], cwd=repo)
    return bool(out.strip())


def remote_ahead_behind(repo: Path, branch: str) -> tuple[int, int]:
    """Returns (commits_behind_remote, commits_ahead_of_remote)."""
    # Set upstream tracking info reference
    _, upstream, _ = git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo
    )
    if not upstream:
        return 0, 0
    rc, behind, _ = git(
        ["rev-list", "--count", f"HEAD..{upstream}"],
        cwd=repo
    )
    rc2, ahead, _ = git(
        ["rev-list", "--count", f"{upstream}..HEAD"],
        cwd=repo
    )
    behind_n = int(behind) if rc == 0 and behind.isdigit() else 0
    ahead_n = int(ahead) if rc2 == 0 and ahead.isdigit() else 0
    return behind_n, ahead_n


def stage_and_commit(repo: Path, msg_or_callable):
    """Stage all, generate message if callable, then commit."""
    git(["add", "-A"], cwd=repo, check=True)
    message = msg_or_callable(repo) if callable(msg_or_callable) else msg_or_callable
    git(["commit", "-m", message], cwd=repo, check=True)


def process_repo(repo: Path) -> dict:
    """Analyse and act on a single git repo. Returns a result dict."""
    result = {
        "path": str(repo),
        "status": None,
        "active_branch": None,
        "backup_branch": None,
        "notes": [],
    }

    nopush = is_under(repo, NOPUSH_FOLDERS)
    current_branch = get_current_branch(repo)
    result["active_branch"] = current_branch
    remote = has_remote(repo)
    did_backup = False
    backup_branch_name = None

    # --- fetch if remote exists ---
    if remote:
        rc, _, err = git(["fetch", "--all", "--prune"], cwd=repo, timeout=GIT_FETCH_TIMEOUT)
        if rc != 0:
            result["notes"].append(f"fetch failed: {err}")

    # --- uncommitted changes? ---
    has_uncommitted = uncommitted_files(repo)

    if has_uncommitted:
        if current_branch in PROTECTED_BRANCHES:
            # Create backup branch and commit there
            backup_branch_name = f"backup-{TODAY}"
            # If backup branch already exists for today, append a counter
            _, branches_out, _ = git(["branch", "--list", f"{backup_branch_name}*"], cwd=repo)
            existing = [b.strip() for b in branches_out.splitlines() if b.strip()]
            if existing:
                backup_branch_name = f"backup-{TODAY}-{len(existing) + 1}"

            git(["checkout", "-b", backup_branch_name], cwd=repo, check=True)
            result["backup_branch"] = backup_branch_name

            stage_and_commit(repo, generate_commit_message)
            did_backup = True

            if remote and not nopush:
                rc, _, err = git(
                    ["push", "--set-upstream", "origin", backup_branch_name], cwd=repo
                )
                if rc != 0:
                    result["notes"].append(f"push backup failed: {err}")

            # Now pull/rebase the original protected branch
            git(["checkout", current_branch], cwd=repo, check=True)
            if remote:
                behind, _ = remote_ahead_behind(repo, current_branch)
                if behind > 0:
                    rc, _, err = git(["pull", "--rebase"], cwd=repo)
                    if rc != 0:
                        git(["rebase", "--abort"], cwd=repo)
                        result["notes"].append(f"rebase failed: {err}")
                        # Go back to backup branch
                        git(["checkout", backup_branch_name], cwd=repo)
                        result["status"] = BACKUP_DIRTY
                        result["active_branch"] = current_branch
                        result["backup_branch"] = backup_branch_name
                        return _apply_remote_status(result, remote)
                    else:
                        # Rebase backup branch on top of updated protected branch
                        git(["checkout", backup_branch_name], cwd=repo, check=True)
                        rc2, _, err2 = git(
                            ["rebase", current_branch], cwd=repo
                        )
                        if rc2 != 0:
                            git(["rebase", "--abort"], cwd=repo)
                            result["notes"].append(f"backup rebase on {current_branch} failed: {err2}")
                            result["status"] = BACKUP_DIRTY
                        else:
                            result["status"] = BACKUPED
                            if not nopush:
                                git(["push", "--force-with-lease", "origin", backup_branch_name], cwd=repo)
                    result["active_branch"] = current_branch
                    result["backup_branch"] = backup_branch_name
                else:
                    # Protected branch was already up to date
                    git(["checkout", backup_branch_name], cwd=repo)
                    result["status"] = BACKUPED
                    result["active_branch"] = current_branch
                    result["backup_branch"] = backup_branch_name
            else:
                # No remote: leave on backup branch
                git(["checkout", backup_branch_name], cwd=repo)
                result["status"] = BACKUPED
                result["active_branch"] = current_branch
                result["backup_branch"] = backup_branch_name

        else:
            # Non-protected branch: commit directly
            stage_and_commit(repo, generate_commit_message)
            did_backup = True

            if remote:
                # Try to pull --rebase first, then push
                behind, _ = remote_ahead_behind(repo, current_branch)
                if behind > 0:
                    rc, _, err = git(["pull", "--rebase"], cwd=repo)
                    if rc != 0:
                        git(["rebase", "--abort"], cwd=repo)
                        result["notes"].append(f"rebase failed after commit: {err}")
                        result["status"] = BACKUP_DIRTY
                        return _apply_remote_status(result, remote)
                if not nopush:
                    rc, _, err = git(["push", "--set-upstream", "origin", current_branch], cwd=repo)
                    if rc != 0:
                        result["notes"].append(f"push failed: {err}")

            result["status"] = BACKUPED

    else:
        # No uncommitted changes — check if remote is ahead
        if remote:
            behind, ahead = remote_ahead_behind(repo, current_branch)
            if behind > 0 and ahead > 0:
                # Divergent
                result["status"] = REMOTE_DIVERGENT
                result["notes"].append(f"local is {ahead} ahead, remote is {behind} ahead — cannot fast-forward")
                return _apply_remote_status(result, remote)
            elif behind > 0:
                rc, _, err = git(["pull", "--rebase"], cwd=repo)
                if rc != 0:
                    git(["rebase", "--abort"], cwd=repo)
                    result["status"] = REMOTE_DIVERGENT
                    result["notes"].append(f"pull --rebase failed: {err}")
                    return _apply_remote_status(result, remote)
                result["status"] = UP_TO_DATE
            else:
                result["status"] = UP_TO_DATE
        else:
            result["status"] = UP_TO_DATE

    return _apply_remote_status(result, remote)


def _apply_remote_status(result: dict, remote: bool) -> dict:
    """Remap BACKUPED/UP_TO_DATE to MISSING_REMOTE_* if no remote."""
    if not remote:
        if result["status"] == BACKUPED:
            result["status"] = MISSING_REMOTE_BACKUPED
        elif result["status"] == UP_TO_DATE:
            result["status"] = MISSING_REMOTE_UP_TO_DATE
    return result


# ---------------------------------------------------------------------------
# MISSING_GIT classification
# ---------------------------------------------------------------------------

def classify_missing_git(
    git_repos: set[Path],
    non_git_dirs: list[Path],
) -> list[dict]:
    """
    For each non-git dir: if >= 1/3 of sibling dirs are git repos
    and it contains no sub-git repos, mark as MISSING_GIT.
    """
    missing = []
    non_git_set = set(non_git_dirs)

    for d in non_git_dirs:
        # Check no sub-git inside
        has_sub_git = any(r.is_relative_to(d) for r in git_repos)
        if has_sub_git:
            continue

        parent = d.parent
        siblings = [c for c in parent.iterdir() if c.is_dir() and not c.name.startswith(".")]
        if not siblings:
            continue

        git_siblings = sum(1 for s in siblings if s in git_repos)
        ratio = git_siblings / len(siblings)

        if ratio >= 1 / 3:
            missing.append({
                "path": str(d),
                "status": MISSING_GIT,
                "active_branch": None,
                "backup_branch": None,
                "notes": [f"{git_siblings}/{len(siblings)} siblings are git repos"],
            })

    return missing


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    UP_TO_DATE:               "\033[32m",   # green
    BACKUPED:                 "\033[36m",   # cyan
    BACKUP_DIRTY:             "\033[33m",   # yellow
    REMOTE_DIVERGENT:         "\033[33m",   # yellow
    MISSING_GIT:              "\033[35m",   # magenta
    MISSING_REMOTE_UP_TO_DATE:"\033[34m",   # blue
    MISSING_REMOTE_BACKUPED:  "\033[34m",   # blue
}
RESET = "\033[0m"
BOLD  = "\033[1m"


def fmt_status(status: str) -> str:
    color = STATUS_COLORS.get(status, "")
    return f"{color}{BOLD}{status}{RESET}"


def print_results(results: list[dict]):
    # Sort by status then path
    order = [
        BACKUP_DIRTY, REMOTE_DIVERGENT, MISSING_GIT,
        MISSING_REMOTE_BACKUPED, MISSING_REMOTE_UP_TO_DATE,
        BACKUPED, UP_TO_DATE,
    ]
    order_map = {s: i for i, s in enumerate(order)}
    results.sort(key=lambda r: (order_map.get(r["status"], 99), r["path"]))

    col_path   = max(len(r["path"]) for r in results) + 2
    col_status = max(len(r["status"]) for r in results) + 2

    header = f"{'PATH':<{col_path}}  {'STATUS':<{col_status}}  {'BRANCH':<20}  {'BACKUP BRANCH':<25}  NOTES"
    print()
    print(BOLD + header + RESET)
    print("-" * len(header))

    for r in results:
        path_rel = r["path"].replace(str(PROJECT_FOLDER) + "/", "")
        branch   = r["active_branch"] or ""
        backup   = r["backup_branch"] or ""
        notes    = "; ".join(r["notes"])
        status   = fmt_status(r["status"])
        # Pad status manually (fmt adds ANSI codes that confuse ljust)
        status_pad = " " * max(0, col_status - len(r["status"]))
        print(f"{path_rel:<{col_path}}  {status}{status_pad}  {branch:<20}  {backup:<25}  {notes}")

    print()
    # Summary counts
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    print(BOLD + "Summary:" + RESET)
    for s in order:
        if counts[s]:
            print(f"  {fmt_status(s)}: {counts[s]}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not PROJECT_FOLDER.exists():
        print(f"ERROR: PROJECT_FOLDER does not exist: {PROJECT_FOLDER}", file=sys.stderr)
        sys.exit(1)

    print(f"{BOLD}Scanning {PROJECT_FOLDER} ...{RESET}")

    git_repos_list, non_git_dirs_list = discover(PROJECT_FOLDER)
    git_repos_set = set(git_repos_list)

    print(f"Found {len(git_repos_list)} git repos, {len(non_git_dirs_list)} non-git dirs\n")

    results: list[dict] = []

    for i, repo in enumerate(git_repos_list, 1):
        rel = str(repo).replace(str(PROJECT_FOLDER) + "/", "")
        print(f"[{i}/{len(git_repos_list)}] Processing {rel} ...", end=" ", flush=True)
        try:
            r = process_repo(repo)
        except Exception as e:
            r = {
                "path": str(repo),
                "status": "ERROR",
                "active_branch": None,
                "backup_branch": None,
                "notes": [str(e)],
            }
        print(r["status"])
        results.append(r)

    # Classify MISSING_GIT
    missing = classify_missing_git(
        git_repos=git_repos_set,
        non_git_dirs=non_git_dirs_list,
    )
    results.extend(missing)

    if not results:
        print("Nothing found.")
        return

    print_results(results)


if __name__ == "__main__":
    main()
