#!/usr/bin/env python3
"""
nautilus-gitscm: Nautilus file manager extension for Git SCM integration.

Provides:
  - File status emblems
      green checkmark  → tracked and clean
      red cross        → tracked and modified / staged
      question mark    → inside a Git repo but untracked
  - Context menu Git actions (right-click)
      Git Pull / Update  → pull from remote (shown when a remote is configured)
      Git Commit…        → stage + commit selected paths (shown when there are changes)
      Git Push           → push to remote (shown when local commits are ahead of upstream)

Installation:
    Run install.sh from the repository root, then restart Nautilus:
        nautilus -q
"""

import os
import shlex
import subprocess
import threading
from urllib.parse import unquote, urlparse

from gi import require_version

require_version("Nautilus", "3.0")
require_version("GObject", "2.0")

from gi.repository import GObject, Nautilus  # noqa: E402

# ---------------------------------------------------------------------------
# Emblem identifiers
# ---------------------------------------------------------------------------

EMBLEM_CLEAN = "emblem-gitscm-clean"
EMBLEM_MODIFIED = "emblem-gitscm-modified"
EMBLEM_UNTRACKED = "emblem-gitscm-untracked"

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

# Simple in-memory cache: directory path → repo root (str) or None.
# Populated lazily; cleared only on extension reload (Nautilus restart).
_repo_cache: dict = {}
_cache_lock = threading.Lock()


def _run_git(args, cwd, timeout=5):
    """Run *git args* in *cwd* and return (returncode, stdout_str)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, ""


def _get_repo_root(path):
    """Return the repository root for *path*, or None if not in a Git repo."""
    dir_path = path if os.path.isdir(path) else os.path.dirname(path)
    with _cache_lock:
        if dir_path in _repo_cache:
            return _repo_cache[dir_path]

    code, output = _run_git(["rev-parse", "--show-toplevel"], dir_path)
    root = output if code == 0 else None

    with _cache_lock:
        _repo_cache[dir_path] = root
    return root


def _get_path_status(repo_root, path):
    """
    Return the Git status of *path* relative to *repo_root*.

    Returns one of:
        'clean'     – tracked by Git, no local changes
        'modified'  – tracked by Git, has staged or unstaged changes
        'untracked' – not tracked by Git, but inside the repository tree
        None        – path is not part of any Git repository
    """
    rel = os.path.relpath(path, repo_root)
    code, output = _run_git(["status", "--porcelain", "-u", "--", rel], repo_root)
    if code != 0:
        return None

    if not output:
        # No output → either clean-tracked or completely unknown.
        # Use ls-files to distinguish.
        lf_code, _ = _run_git(["ls-files", "--error-unmatch", "--", rel], repo_root)
        return "clean" if lf_code == 0 else None

    lines = [ln for ln in output.splitlines() if ln.strip()]
    has_tracked_changes = any(len(ln) >= 2 and ln[:2] != "??" for ln in lines)
    has_untracked = any(len(ln) >= 2 and ln[:2] == "??" for ln in lines)

    if has_tracked_changes:
        return "modified"
    if has_untracked:
        return "untracked"
    return None


# ---------------------------------------------------------------------------
# Terminal helper
# ---------------------------------------------------------------------------

def _open_in_terminal(cmd):
    """
    Open a new terminal window and run *cmd* (a shell command string).

    Tries common GNOME/KDE/XFCE terminals in order.
    """
    candidates = [
        ["gnome-terminal", "--", "bash", "-c", cmd],
        ["xterm", "-e", "bash", "-c", cmd],
        ["konsole", "--noclose", "-e", "bash", "-c", cmd],
        ["xfce4-terminal", "--hold", "-e", "bash", "-c", cmd],
        ["mate-terminal", "--", "bash", "-c", cmd],
        ["tilix", "--", "bash", "-c", cmd],
    ]
    for args in candidates:
        try:
            subprocess.Popen(args, start_new_session=True)
            return
        except FileNotFoundError:
            continue


# ---------------------------------------------------------------------------
# Extension class
# ---------------------------------------------------------------------------

class GitSCMExtension(GObject.GObject, Nautilus.InfoProvider, Nautilus.MenuProvider):
    """
    Nautilus extension that adds Git status emblems and a Git context menu.
    """

    # ------------------------------------------------------------------ #
    # Nautilus.InfoProvider                                                #
    # ------------------------------------------------------------------ #

    def update_file_info(self, file):
        """Add a Git status emblem to *file* (called once per visible item)."""
        if file.get_uri_scheme() != "file":
            return

        path = unquote(urlparse(file.get_uri()).path)
        repo_root = _get_repo_root(path)
        if repo_root is None:
            return

        status = _get_path_status(repo_root, path)
        emblem = {
            "clean": EMBLEM_CLEAN,
            "modified": EMBLEM_MODIFIED,
            "untracked": EMBLEM_UNTRACKED,
        }.get(status)

        if emblem:
            file.add_emblem(emblem)

    # ------------------------------------------------------------------ #
    # Nautilus.MenuProvider                                                #
    # ------------------------------------------------------------------ #

    def get_file_items(self, window, files):
        """Return context menu items for the selected *files*."""
        return self._build_menu_items(files)

    def get_background_items(self, window, file):
        """Return context menu items when right-clicking a directory background."""
        return self._build_menu_items([file])

    # ------------------------------------------------------------------ #
    # Internal: menu building                                              #
    # ------------------------------------------------------------------ #

    def _build_menu_items(self, files):
        paths = []
        repo_root = None

        for f in files:
            if f.get_uri_scheme() != "file":
                continue
            path = unquote(urlparse(f.get_uri()).path)
            root = _get_repo_root(path)
            if root:
                if repo_root is None:
                    repo_root = root
                paths.append(path)

        if not repo_root or not paths:
            return []

        items = []

        # ---- Git Pull / Update ----------------------------------------
        if self._has_remote(repo_root):
            item = Nautilus.MenuItem(
                name="GitSCM::Pull",
                label="Git Pull / Update",
                tip="Pull the latest changes from the configured remote",
            )
            item.connect(
                "activate",
                lambda _m, rr=repo_root: self._action_pull(rr),
            )
            items.append(item)

        # ---- Git Commit -----------------------------------------------
        if self._has_committable_changes(repo_root, paths):
            item = Nautilus.MenuItem(
                name="GitSCM::Commit",
                label="Git Commit\u2026",
                tip="Stage and commit the selected files",
            )
            item.connect(
                "activate",
                lambda _m, rr=repo_root, pp=list(paths): self._action_commit(rr, pp),
            )
            items.append(item)

        # ---- Git Push ------------------------------------------------
        if self._is_ahead_of_remote(repo_root):
            item = Nautilus.MenuItem(
                name="GitSCM::Push",
                label="Git Push",
                tip="Push local commits to the remote",
            )
            item.connect(
                "activate",
                lambda _m, rr=repo_root: self._action_push(rr),
            )
            items.append(item)

        return items

    # ------------------------------------------------------------------ #
    # Condition helpers                                                     #
    # ------------------------------------------------------------------ #

    def _has_remote(self, repo_root):
        """Return True when the repository has at least one configured remote."""
        code, output = _run_git(["remote"], repo_root)
        return code == 0 and bool(output.strip())

    def _has_committable_changes(self, repo_root, paths):
        """
        Return True when there are staged or unstaged tracked changes,
        or untracked files within *paths* that can be added and committed.
        """
        rel_paths = [os.path.relpath(p, repo_root) for p in paths]
        code, output = _run_git(
            ["status", "--porcelain", "--"] + rel_paths, repo_root
        )
        return code == 0 and bool(output.strip())

    def _is_ahead_of_remote(self, repo_root):
        """Return True when the current branch has commits not yet pushed."""
        code, output = _run_git(
            ["rev-list", "--count", "@{u}..HEAD"], repo_root
        )
        if code != 0:
            return False
        try:
            return int(output) > 0
        except ValueError:
            return False

    # ------------------------------------------------------------------ #
    # Action handlers                                                       #
    # ------------------------------------------------------------------ #

    def _action_pull(self, repo_root):
        cmd = (
            f"cd {shlex.quote(repo_root)} && "
            "git pull; "
            "echo; read -rp 'Press Enter to close\u2026'"
        )
        _open_in_terminal(cmd)

    def _action_commit(self, repo_root, paths):
        rel = " ".join(
            shlex.quote(os.path.relpath(p, repo_root)) for p in paths
        )
        cmd = (
            f"cd {shlex.quote(repo_root)} && "
            f"git add -- {rel} && "
            "git commit; "
            "echo; read -rp 'Press Enter to close\u2026'"
        )
        _open_in_terminal(cmd)

    def _action_push(self, repo_root):
        cmd = (
            f"cd {shlex.quote(repo_root)} && "
            "git push; "
            "echo; read -rp 'Press Enter to close\u2026'"
        )
        _open_in_terminal(cmd)
