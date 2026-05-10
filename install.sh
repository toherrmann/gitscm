#!/usr/bin/env bash
# install.sh — Install the nautilus-gitscm Nautilus extension for the current user.
#
# Usage:
#   bash install.sh
#
# What it does:
#   1. Copies the Python extension to ~/.local/share/nautilus-python/extensions/
#   2. Copies the SVG emblems to ~/.local/share/icons/hicolor/scalable/emblems/
#   3. Refreshes the GTK icon cache
#   4. Prints instructions to restart Nautilus

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/nautilus-gitscm"

EXTENSION_DIR="${HOME}/.local/share/nautilus-python/extensions"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/emblems"

echo "==> Installing nautilus-gitscm..."

if ! command -v python3 &>/dev/null; then
    echo "WARNING: python3 not found in PATH."
fi

if ! command -v git &>/dev/null; then
    echo "WARNING: git not found in PATH."
fi

if command -v rpm &>/dev/null && ! rpm -q nautilus-python &>/dev/null; then
    echo "WARNING: Fedora/RHEL package 'nautilus-python' is not installed."
    echo "         Without it, Nautilus will not load this extension."
    echo "         Install with: sudo dnf install nautilus-python"
    echo ""
fi

# 1. Extension
mkdir -p "${EXTENSION_DIR}"
cp "${SOURCE_DIR}/nautilus_gitscm.py" "${EXTENSION_DIR}/"
echo "    Extension installed → ${EXTENSION_DIR}/nautilus_gitscm.py"

# 2. Icons / emblems
mkdir -p "${ICON_DIR}"
for svg in "${SOURCE_DIR}/icons/"*.svg; do
    cp "${svg}" "${ICON_DIR}/"
    echo "    Icon installed      → ${ICON_DIR}/$(basename "${svg}")"
done

# 3. Icon cache
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
    echo "    GTK icon cache updated."
fi

echo ""
echo "==> Installation complete."
echo ""
echo "Restart Nautilus to activate the extension:"
echo "    nautilus -q && nautilus &"
echo ""
echo "Debug logs (optional):"
echo "    GITSCM_DEBUG=1 nautilus -q"
echo "    GITSCM_DEBUG=1 nautilus"
echo "    journalctl --user -f | grep -Ei 'gitscm|nautilus'"
echo ""
echo "Note: Nautilus Previewer / WebKit2 errors are external to this extension."
echo ""
echo "Tip: If emblems do not appear after restart, log out and back in."
