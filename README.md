# nautilus-gitscm

A lightweight Git client integration for the **GNOME Nautilus** file manager,
inspired by RabbitVCS / Rabbit SCM Git.

## Features

### File status emblems

| Emblem | Meaning |
|--------|---------|
| 🟢 green checkmark | File is tracked by Git and up-to-date |
| 🔴 red cross | File is tracked by Git and has local changes (staged or unstaged) |
| ⚫ grey question mark | File is inside a Git repository but not tracked (untracked) |

Emblems are shown directly on the file/folder icons in Nautilus so you can
see the Git state of every item at a glance.

### Context menu Git actions (right-click)

| Action | When visible |
|--------|-------------|
| **Git Pull / Update** | Repository has at least one configured remote |
| **Git Commit…** | Selected files have staged, unstaged, or new changes |
| **Git Push** | Current branch is ahead of its upstream (unpushed commits exist) |

Each action opens a terminal window to show the command output.

---

## Requirements

| Package | Purpose |
|---------|---------|
| `nautilus-python` (Ubuntu/Debian) or `python-nautilus` (Fedora) | Nautilus Python extension bindings |
| `git` | Git CLI |
| `gnome-terminal` / `xterm` / `konsole` / … | Terminal for action output |

Install the bindings if missing:

```bash
# Ubuntu / Debian / Linux Mint
sudo apt install python3-nautilus

# Fedora / RHEL
sudo dnf install nautilus-python

# Arch Linux
sudo pacman -S python-nautilus
```

---

## Installation

```bash
git clone https://github.com/toherrmann/gitscm.git
cd gitscm
bash install.sh
```

Then restart Nautilus:

```bash
nautilus -q && nautilus &
```

> **Tip:** If emblems do not appear immediately, try logging out and back in
> so the GTK icon cache is fully refreshed.

### Debugging on Fedora / GNOME

The extension is quiet by default. To enable verbose diagnostics:

```bash
GITSCM_DEBUG=1 nautilus -q
GITSCM_DEBUG=1 nautilus
```

Then inspect logs:

```bash
journalctl --user -f | grep -Ei 'gitscm|nautilus'
```

This prints extension load information plus reasons why emblems or menu entries
are skipped.

> **Important:** `org.gnome.NautilusPreviewer` / `WebKit2` errors come from the
> Nautilus Previewer (GNOME Sushi) component, not from this extension.

### What `install.sh` does

1. Copies `nautilus-gitscm/nautilus_gitscm.py` →
   `~/.local/share/nautilus-python/extensions/`
2. Copies the SVG emblem icons →
   `~/.local/share/icons/hicolor/scalable/emblems/`
3. Runs `gtk-update-icon-cache` to register the new icons

---

## Uninstallation

```bash
bash uninstall.sh
nautilus -q && nautilus &
```

---

## Repository structure

```
nautilus-gitscm/
├── nautilus_gitscm.py          # Main Nautilus extension (Python 3)
└── icons/
    ├── emblem-gitscm-clean.svg      # Green checkmark
    ├── emblem-gitscm-modified.svg   # Red cross
    └── emblem-gitscm-untracked.svg  # Grey question mark
install.sh                      # User-local installation script
uninstall.sh                    # Removal script
README.md
```

---

## How it works

The extension registers two Nautilus interfaces:

* **`Nautilus.InfoProvider`** — called for every visible file/folder.  
  Runs `git rev-parse --show-toplevel` (cached per directory) to detect
  whether an item is inside a repository, then `git status --porcelain`
  to determine the emblem to attach.

* **`Nautilus.MenuProvider`** — called when the user right-clicks.  
  Checks repository state (`git remote`, `git status`, `git rev-list
  @{u}..HEAD`) to decide which menu entries to display, then opens a
  terminal window to execute the chosen Git command.

Git repository root lookups are cached in memory to avoid redundant
subprocess calls while browsing large directories.

---

## License

MIT
