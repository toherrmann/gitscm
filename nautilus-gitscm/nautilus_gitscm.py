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
      Git Commit History → show commit log in a GUI window for one selected tracked file
      Git Push           → push to remote (shown when local commits are ahead of upstream)

Installation:
    Run install.sh from the repository root, then restart Nautilus:
        nautilus -q
"""

import os
import shlex
import subprocess
import sys
import threading
import logging
from urllib.parse import unquote, urlparse

from gi import require_version

require_version("GObject", "2.0")


def _is_env_enabled(value):
    if value is None:
        return False
    return str(value).lower() in {"1", "true", "yes", "on", "y"}


_DEBUG_ENABLED = _is_env_enabled(os.environ.get("GITSCM_DEBUG", "0"))

_logger = logging.getLogger("nautilus-gitscm")
if not _logger.handlers:
    _handler = logging.StreamHandler(stream=sys.stderr)
    _handler.setFormatter(logging.Formatter("gitscm: %(message)s"))
    _logger.addHandler(_handler)
_logger.setLevel(logging.DEBUG if _DEBUG_ENABLED else logging.WARNING)


def _debug(msg, *args):
    if _DEBUG_ENABLED:
        _logger.debug(msg, *args)


_nautilus_version = None
for _candidate in ("4.0", "3.0"):
    try:
        require_version("Nautilus", _candidate)
        _nautilus_version = _candidate
        break
    except ValueError:
        continue

if _nautilus_version is None:
    raise ImportError(
        "Nautilus GI binding not found (tried versions 4.0 and 3.0). "
        "Ensure nautilus-python is installed."
    )

from gi.repository import GObject, Nautilus  # noqa: E402

_debug(
    "Loaded Nautilus extension (Nautilus %s, GITSCM_DEBUG=%s)",
    _nautilus_version,
    int(_DEBUG_ENABLED),
)

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
    _debug("Running git command in %s: git %s", cwd, " ".join(args))
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
        if result.returncode != 0:
            _debug(
                "Git command failed (%s): %s",
                result.returncode,
                result.stderr.strip(),
            )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        _debug("Git command timed out in %s: git %s", cwd, " ".join(args))
        return -1, ""
    except (FileNotFoundError, OSError) as exc:
        _logger.warning("Cannot execute git in %s: %s", cwd, exc)
        return -1, ""


def _get_repo_root(path):
    """Return the repository root for *path*, or None if not in a Git repo."""
    dir_path = path if os.path.isdir(path) else os.path.dirname(path)
    if not dir_path:
        return None

    with _cache_lock:
        if dir_path in _repo_cache:
            _debug("Repo root cache hit for %s", dir_path)
            return _repo_cache[dir_path]

    code, output = _run_git(["rev-parse", "--show-toplevel"], dir_path)
    root = output if code == 0 else None
    _debug("Resolved repo root for %s -> %s", dir_path, root)

    with _cache_lock:
        _repo_cache[dir_path] = root
    return root


def _get_local_path(file_info):
    """Return a normalized local filesystem path for a Nautilus file object."""
    if file_info.get_uri_scheme() != "file":
        return None

    location = file_info.get_location()
    if location:
        path = location.get_path()
        if path:
            return os.path.normpath(path)

    uri = file_info.get_uri()
    if not uri:
        return None
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    return os.path.normpath(path) if path else None


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
# GUI helper – commit history window
# ---------------------------------------------------------------------------

def _show_commit_history_window(title, text):
    """Open a standalone GTK window displaying *text* as scrollable commit history."""
    try:
        from gi.repository import Gtk
    except ImportError:
        _logger.warning("GTK not available; cannot show commit history window")
        return

    win = Gtk.Window()
    win.set_title(title)
    win.set_default_size(800, 600)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

    text_view = Gtk.TextView()
    text_view.set_editable(False)
    text_view.set_cursor_visible(False)
    text_view.set_monospace(True)
    text_view.get_buffer().set_text(text)

    # Support GTK 3 (.add) and GTK 4 (.set_child)
    if hasattr(scrolled, "set_child"):
        scrolled.set_child(text_view)
        win.set_child(scrolled)
    else:
        scrolled.add(text_view)
        win.add(scrolled)

    # Support GTK 3 (.show_all) and GTK 4 (.present)
    if hasattr(win, "show_all"):
        win.show_all()
    else:
        win.present()


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
            _debug("Opened terminal command with: %s", args[0])
            return
        except FileNotFoundError:
            continue
        except OSError as exc:
            _logger.warning("Failed launching terminal %s: %s", args[0], exc)
    _logger.warning("No supported terminal emulator found for Git action output.")


# ---------------------------------------------------------------------------
# Extension class
# ---------------------------------------------------------------------------

class GitSCMExtension(GObject.GObject, Nautilus.InfoProvider, Nautilus.MenuProvider):
    """
    Nautilus extension that adds Git status emblems and a Git context menu.
    """

    def __init__(self):
        super().__init__()
        _debug("GitSCMExtension initialized")

    # ------------------------------------------------------------------ #
    # Nautilus.InfoProvider                                                #
    # ------------------------------------------------------------------ #

    def update_file_info(self, file):
        """Add a Git status emblem to *file* (called once per visible item)."""
        try:
            path = _get_local_path(file)
            if not path:
                return

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
                _debug("Adding emblem %s to %s", emblem, path)
                file.add_emblem(emblem)
        except Exception:
            _logger.exception("update_file_info failed")

    # ------------------------------------------------------------------ #
    # Nautilus.MenuProvider                                                #
    # ------------------------------------------------------------------ #

    def get_file_items(self, window, files):
        """Return context menu items for the selected *files*."""
        try:
            return self._build_menu_items(files)
        except Exception:
            _logger.exception("get_file_items failed")
            return []

    def get_background_items(self, window, file):
        """Return context menu items when right-clicking a directory background."""
        try:
            return self._build_menu_items([file])
        except Exception:
            _logger.exception("get_background_items failed")
            return []

    # ------------------------------------------------------------------ #
    # Internal: menu building                                              #
    # ------------------------------------------------------------------ #

    def _build_menu_items(self, files):
        if not files:
            return []

        paths = []
        repo_root = None

        for f in files:
            path = _get_local_path(f)
            if not path:
                continue
            root = _get_repo_root(path)
            if root:
                if repo_root is None:
                    repo_root = root
                if root != repo_root:
                    _debug("Skipping path from different repository: %s", path)
                    continue
                paths.append(path)

        if not repo_root or not paths:
            _debug("No menu items: repo_root=%s, paths=%d", repo_root, len(paths))
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

        # ---- Git Commit History -------------------------------------
        history_path = self._get_history_path(repo_root, paths)
        if history_path:
            item = Nautilus.MenuItem(
                name="GitSCM::CommitHistory",
                label="Git Commit History",
                tip="Show commit history for the selected tracked file",
            )
            item.connect(
                "activate",
                lambda _m, rr=repo_root, hp=history_path: self._action_commit_history(
                    rr, hp
                ),
            )
            items.append(item)

        return items

    # ------------------------------------------------------------------ #
    # Condition helpers                                                     #
    # ------------------------------------------------------------------ #

    def _has_remote(self, repo_root):
        """Return True when the repository has at least one configured remote."""
        code, output = _run_git(["remote"], repo_root)
        has_remote = code == 0 and bool(output.strip())
        _debug("Repository %s has remote: %s", repo_root, has_remote)
        return has_remote

    def _has_committable_changes(self, repo_root, paths):
        """
        Return True when there are staged or unstaged tracked changes,
        or untracked files within *paths* that can be added and committed.
        """
        rel_paths = [os.path.relpath(p, repo_root) for p in paths]
        code, output = _run_git(
            ["status", "--porcelain", "--"] + rel_paths, repo_root
        )
        has_changes = code == 0 and bool(output.strip())
        _debug("Repository %s has committable changes: %s", repo_root, has_changes)
        return has_changes

    def _is_ahead_of_remote(self, repo_root):
        """Return True when the current branch has commits not yet pushed."""
        code, output = _run_git(
            ["rev-list", "--count", "@{u}..HEAD"], repo_root
        )
        if code != 0:
            return False
        try:
            ahead = int(output) > 0
            _debug("Repository %s ahead of upstream: %s", repo_root, ahead)
            return ahead
        except ValueError:
            return False

    def _get_history_path(self, repo_root, paths):
        """
        Return the selected tracked file path for history view, or None.

        History is intentionally scoped to exactly one selected local file.
        """
        if len(paths) != 1:
            return None

        path = paths[0]
        if not os.path.isfile(path):
            return None

        rel = os.path.relpath(path, repo_root)
        code, _ = _run_git(["ls-files", "--error-unmatch", "--", rel], repo_root)
        if code == 0:
            return path
        _debug("History menu hidden: %s is not tracked by git", path)
        return None

    # ------------------------------------------------------------------ #
    # Action handlers                                                       #
    # ------------------------------------------------------------------ #

    def _action_pull(self, repo_root):
        _debug("Action selected: pull in %s", repo_root)
        cmd = (
            f"cd {shlex.quote(repo_root)} && "
            "git pull; "
            "echo; read -rp 'Press Enter to close\u2026'"
        )
        _open_in_terminal(cmd)

    def _action_commit(self, repo_root, paths):
        rel_paths = [shlex.quote(os.path.relpath(p, repo_root)) for p in paths]
        rel = " ".join(rel_paths)
        _debug("Action selected: commit in %s for %d paths", repo_root, len(paths))
        cmd = (
            f"cd {shlex.quote(repo_root)} && "
            f"git add -- {rel} && "
            "git commit; "
            "echo; read -rp 'Press Enter to close\u2026'"
        )
        _open_in_terminal(cmd)

    def _action_push(self, repo_root):
        _debug("Action selected: push in %s", repo_root)
        cmd = (
            f"cd {shlex.quote(repo_root)} && "
            "git push; "
            "echo; read -rp 'Press Enter to close\u2026'"
        )
        _open_in_terminal(cmd)

    def _action_commit_history(self, repo_root, path):
        rel = os.path.relpath(path, repo_root)
        _debug("Action selected: commit history in %s for %s", repo_root, path)
        rc, output = _run_git(
            ["log", "--follow", "--decorate", "--date=short", "--stat", "--", rel],
            repo_root,
            timeout=30,
        )
        if rc != 0:
            text = f"git log failed for:\n{path}\n\n(Exit code {rc})"
        elif not output:
            text = f"No commit history found for:\n{path}"
        else:
            text = output
        title = f"Git Commit History \u2014 {os.path.basename(path)}"
        _show_commit_history_window(title, text)
