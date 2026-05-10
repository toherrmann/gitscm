#!/usr/bin/env bash
# uninstall.sh — Remove the nautilus-gitscm Nautilus extension.
#
# Usage:
#   bash uninstall.sh

set -euo pipefail

EXTENSION_DIR="${HOME}/.local/share/nautilus-python/extensions"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/emblems"

echo "==> Uninstalling nautilus-gitscm..."

# Extension
if [ -f "${EXTENSION_DIR}/nautilus_gitscm.py" ]; then
    rm "${EXTENSION_DIR}/nautilus_gitscm.py"
    echo "    Removed → ${EXTENSION_DIR}/nautilus_gitscm.py"
fi

# Icons
for name in emblem-gitscm-clean emblem-gitscm-modified emblem-gitscm-untracked; do
    icon="${ICON_DIR}/${name}.svg"
    if [ -f "${icon}" ]; then
        rm "${icon}"
        echo "    Removed → ${icon}"
    fi
done

# Icon cache
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
    echo "    GTK icon cache updated."
fi

echo ""
echo "==> Uninstallation complete."
echo "    Restart Nautilus to apply: nautilus -q && nautilus &"
