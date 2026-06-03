# Tome Speeder one-line installer for Windows.
#
# Usage (in PowerShell):
#   irm https://raw.githubusercontent.com/AlfiePRIME/TomeSpeeder/main/install.ps1 | iex
#
# What it does:
#   1. Installs uv (Astral) if not already present.
#   2. Installs ffmpeg via winget if not already present.
#   3. Downloads tome_speeder.py to %LOCALAPPDATA%\TomeSpeeder\.
#   4. Renders the app icon to icon.png in that folder.
#   5. Creates a Start Menu shortcut.

$ErrorActionPreference = 'Stop'

$Repo       = 'AlfiePRIME/TomeSpeeder'
$Branch     = 'main'
$ScriptUrl  = "https://raw.githubusercontent.com/$Repo/$Branch/tome_speeder.py"
$InstallDir = Join-Path $env:LOCALAPPDATA 'TomeSpeeder'
$ScriptPath = Join-Path $InstallDir 'tome_speeder.py'
$IconPath   = Join-Path $InstallDir 'icon.png'

function Say  ($m) { Write-Host ":: $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "✓  $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "!  $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "✗  $m" -ForegroundColor Red; exit 1 }

Say "Installing Tome Speeder to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# 1. uv ---------------------------------------------------------------
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Ok "uv already installed ($(uv --version))"
} else {
    Say "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die "uv install completed but uv is not on PATH. Open a new PowerShell and re-run."
    }
    Ok "uv installed"
}

# 2. ffmpeg -----------------------------------------------------------
if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and `
    (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Ok "ffmpeg already installed"
} else {
    Say "Installing ffmpeg via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Die "winget not found. Install ffmpeg manually from https://ffmpeg.org and re-run."
    }
    winget install --silent --accept-source-agreements --accept-package-agreements `
        --id Gyan.FFmpeg
    # Refresh PATH for the current process.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Warn "ffmpeg installed but PATH not updated yet. You may need to open a new terminal."
    } else {
        Ok "ffmpeg installed"
    }
}

# 3. Script -----------------------------------------------------------
Say "Downloading tome_speeder.py"
Invoke-WebRequest -Uri $ScriptUrl -OutFile $ScriptPath -UseBasicParsing
Ok "Script saved"

# 4. Icon -------------------------------------------------------------
Say "Rendering app icon (this also primes the uv venv, may take a moment)..."
$env:QT_QPA_PLATFORM = 'offscreen'
& uv run --quiet $ScriptPath --write-icon $IconPath 256
$env:QT_QPA_PLATFORM = $null
Ok "Icon saved to $IconPath"

# 5. Start Menu shortcut ---------------------------------------------
$UvCmd = (Get-Command uv).Source
$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Tome Speeder.lnk'
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($StartMenu)
$shortcut.TargetPath        = $UvCmd
$shortcut.Arguments         = "run --quiet `"$ScriptPath`""
$shortcut.WorkingDirectory  = $InstallDir
$shortcut.IconLocation      = $IconPath
$shortcut.WindowStyle       = 7  # minimised — keep the console out of sight
$shortcut.Description       = 'Quicken audiobooks without warping pitch'
$shortcut.Save()
Ok "Start Menu shortcut created: $StartMenu"

Write-Host ""
Say "Done. Press the Windows key and search for 'Tome Speeder'."
Write-Host "    Or run: uv run `"$ScriptPath`""
