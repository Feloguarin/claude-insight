#!/usr/bin/env bash
set -e

# Claude Insight - One-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/Feloguarin/claude-insight/main/install.sh | bash

REPO="Feloguarin/claude-insight"
INSTALL_DIR="${HOME}/.local/claude-insight"
BIN_DIR="${HOME}/.local/bin"

echo "🔍 Claude Insight Installer"
echo "============================"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not found."
    echo "   Install Python 3.8+ and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.8"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ Python 3.8+ required. Found: $PYTHON_VERSION"
    exit 1
fi

echo "✅ Python $PYTHON_VERSION found"

# Check for pip
if ! python3 -m pip --version &> /dev/null 2>&1; then
    echo "📦 Installing pip..."
    python3 -m ensurepip --upgrade 2>/dev/null || {
        echo "❌ Failed to install pip. Please install manually."
        exit 1
    }
fi

echo "✅ pip found"

# Create directories
mkdir -p "$INSTALL_DIR" "$BIN_DIR"

# Download latest release or clone repo
echo "📥 Downloading Claude Insight..."
if command -v git &> /dev/null; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        cd "$INSTALL_DIR"
        git pull --quiet origin main
    else
        git clone --depth 1 --quiet "https://github.com/${REPO}.git" "$INSTALL_DIR"
    fi
else
    # Fallback: download tarball
    curl -fsSL "https://github.com/${REPO}/archive/refs/heads/main.tar.gz" | tar -xz -C /tmp
    rm -rf "$INSTALL_DIR"
    mv "/tmp/claude-insight-main" "$INSTALL_DIR"
fi

echo "✅ Downloaded to $INSTALL_DIR"

# Install the package
echo "🔧 Installing..."
cd "$INSTALL_DIR"
python3 -m pip install --user -e . 2>&1 | tail -20

# Create wrapper script
cat > "$BIN_DIR/claude-insight" << 'EOF'
#!/usr/bin/env bash
exec python3 -m claude_insight "$@"
EOF
chmod +x "$BIN_DIR/claude-insight"

# Add to PATH if needed
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
    SHELL_RC=""
    case "$SHELL" in
        */bash) SHELL_RC="$HOME/.bashrc" ;;
        */zsh)  SHELL_RC="$HOME/.zshrc" ;;
        */fish) SHELL_RC="$HOME/.config/fish/config.fish" ;;
    esac

    if [ -n "$SHELL_RC" ] && [ -f "$SHELL_RC" ]; then
        echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_RC"
        echo "📝 Added $BIN_DIR to PATH in $SHELL_RC"
        echo "   Run: source $SHELL_RC"
    fi

    export PATH="$BIN_DIR:$PATH"
fi

# Verify installation
if command -v claude-insight &> /dev/null; then
    echo ""
    echo "🎉 Claude Insight installed successfully!"
    echo ""
    echo "   Version: $(claude-insight --version 2>/dev/null || echo 'latest')"
    echo ""
    echo "🚀 Quick start:"
    echo "   claude-insight ~/.claude/projects/latest"
    echo "   claude-insight --help"
    echo ""
else
    echo "⚠️  Installation complete but command not found in PATH"
    echo "   Add this to your shell profile:"
    echo "   export PATH=\"$BIN_DIR:\$PATH\""
fi
