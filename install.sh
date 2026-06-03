#!/usr/bin/env bash
# Tome Speeder one-line installer for Linux and macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/AlfiePRIME/TomeSpeeder/main/install.sh | bash
#
# What it does:
#   1. Installs uv (Astral) if not already present.
#   2. Installs ffmpeg via the system package manager if not already present.
#   3. Downloads tome_speeder.py to ~/.local/share/tome-speeder/.
#   4. Renders the app icon to ~/.local/share/tome-speeder/icon.png.
#   5. Creates a launcher (Linux: .desktop entry; macOS: a clickable .command
#      in ~/Applications).

set -euo pipefail

REPO="AlfiePRIME/TomeSpeeder"
BRANCH="main"
SCRIPT_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/tome_speeder.py"
INSTALL_DIR="${HOME}/.local/share/tome-speeder"
SCRIPT_PATH="${INSTALL_DIR}/tome_speeder.py"
ICON_PATH="${INSTALL_DIR}/icon.png"

c_b='\033[1m'; c_g='\033[32m'; c_y='\033[33m'; c_r='\033[31m'; c_0='\033[0m'
say()  { printf "${c_b}::${c_0} %s\n" "$*"; }
ok()   { printf "${c_g}✓${c_0} %s\n" "$*"; }
warn() { printf "${c_y}!${c_0} %s\n" "$*" >&2; }
die()  { printf "${c_r}✗${c_0} %s\n" "$*" >&2; exit 1; }

case "$(uname -s)" in
    Linux)  PLATFORM=linux ;;
    Darwin) PLATFORM=macos ;;
    *) die "Unsupported OS: $(uname -s). Use install.ps1 on Windows." ;;
esac
say "Detected platform: ${PLATFORM}"

# 1. uv ---------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
    ok "uv already installed ($(uv --version))"
else
    say "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv's installer drops it in ~/.local/bin; add to PATH for this session.
    export PATH="${HOME}/.local/bin:${PATH}"
    command -v uv >/dev/null 2>&1 || die "uv install completed but uv is not on PATH. Open a new shell and re-run."
    ok "uv installed"
fi

# 2. ffmpeg -----------------------------------------------------------
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    ok "ffmpeg already installed"
else
    say "Installing ffmpeg..."
    if [[ "$PLATFORM" == "macos" ]]; then
        if command -v brew >/dev/null 2>&1; then
            brew install ffmpeg
        else
            die "Homebrew not found. Install brew first (https://brew.sh) or install ffmpeg manually."
        fi
    else
        if   command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y ffmpeg
        elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y ffmpeg
        elif command -v pacman  >/dev/null 2>&1; then sudo pacman -S --needed --noconfirm ffmpeg
        elif command -v zypper  >/dev/null 2>&1; then sudo zypper install -y ffmpeg
        elif command -v apk     >/dev/null 2>&1; then sudo apk add ffmpeg
        else die "Could not detect a package manager. Install ffmpeg manually and re-run."
        fi
    fi
    ok "ffmpeg installed"
fi

# 3. Script -----------------------------------------------------------
say "Downloading tome_speeder.py to ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
curl -fsSL "${SCRIPT_URL}" -o "${SCRIPT_PATH}"
chmod +x "${SCRIPT_PATH}"
ok "Script saved"

# 4. Icon -------------------------------------------------------------
say "Rendering app icon (this also primes the uv venv, may take a moment)..."
QT_QPA_PLATFORM=offscreen uv run --quiet "${SCRIPT_PATH}" --write-icon "${ICON_PATH}" 256
ok "Icon saved to ${ICON_PATH}"

# 5. Launcher ---------------------------------------------------------
UV_BIN="$(command -v uv)"
if [[ "$PLATFORM" == "linux" ]]; then
    APPS_DIR="${HOME}/.local/share/applications"
    DESKTOP_PATH="${APPS_DIR}/tome-speeder.desktop"
    mkdir -p "${APPS_DIR}"
    cat > "${DESKTOP_PATH}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Tome Speeder
GenericName=Audiobook Speed Converter
Comment=Quicken m4b and other audiobooks without warping the bard's voice
Exec=${UV_BIN} run --quiet ${SCRIPT_PATH}
Icon=${ICON_PATH}
Terminal=false
Categories=AudioVideo;AudioVideoEditing;
Keywords=audiobook;m4b;mp3;speed;tempo;ffmpeg;
StartupNotify=true
StartupWMClass=Tome Speeder
EOF
    chmod +x "${DESKTOP_PATH}"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "${APPS_DIR}" || true
    ok "Desktop entry installed: ${DESKTOP_PATH}"
    echo
    say "Done. Search for 'Tome Speeder' in your Activities/menu, or run:"
    echo "    ${UV_BIN} run ${SCRIPT_PATH}"
else
    APPS_DIR="${HOME}/Applications"
    LAUNCHER="${APPS_DIR}/Tome Speeder.command"
    mkdir -p "${APPS_DIR}"
    cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
exec "${UV_BIN}" run --quiet "${SCRIPT_PATH}"
EOF
    chmod +x "${LAUNCHER}"
    ok "Launcher installed: ${LAUNCHER}"
    echo
    say "Done. Double-click 'Tome Speeder.command' in ~/Applications, or run:"
    echo "    ${UV_BIN} run ${SCRIPT_PATH}"
fi
