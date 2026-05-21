#!/usr/bin/env bash
# Install research-tree skill into ~/.claude/skills/ so /research-tree is
# discoverable across all Claude Code projects.
#
# Idempotent: re-running updates the symlink to point to the latest source.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${HOME}/.claude/skills/research-tree"

mkdir -p "${HOME}/.claude/skills"

if [ -L "$TARGET_DIR" ] || [ -e "$TARGET_DIR" ]; then
    rm -rf "$TARGET_DIR"
fi

ln -s "$REPO_ROOT/skills/research-tree" "$TARGET_DIR"

# Export the repo root so SKILL.md can resolve helpers.
ENV_HINT="export RESEARCH_TREE_REPO=\"$REPO_ROOT\""
SHELL_RC="${HOME}/.bashrc"
if [ -n "${ZSH_VERSION:-}" ] || [ -f "${HOME}/.zshrc" ]; then
    SHELL_RC="${HOME}/.zshrc"
fi

if ! grep -q "RESEARCH_TREE_REPO=" "$SHELL_RC" 2>/dev/null; then
    {
        echo ""
        echo "# Added by research-tree-explorer/scripts/install.sh"
        echo "$ENV_HINT"
    } >> "$SHELL_RC"
    echo "Added RESEARCH_TREE_REPO export to $SHELL_RC"
fi

echo "OK: research-tree skill installed."
echo "    symlink     : $TARGET_DIR -> $REPO_ROOT/skills/research-tree"
echo "    helpers     : $REPO_ROOT/scripts/"
echo ""
echo "Open a new Claude Code session and try:"
echo "    /research-tree init \"your research idea here\""
echo "    /research-tree autopilot"
