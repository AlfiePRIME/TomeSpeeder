# Tome Speeder

A small fantasy-themed Linux GUI for permanently speeding up audiobook files
(m4b, m4a, mp3, opus, ogg, flac, wav, aac, wma) without warping the narrator's
pitch. The output replaces the original file in place.

Built on PyQt6 + ffmpeg (`atempo` filter chain). Distributed as a single
self-bootstrapping Python script — [uv](https://docs.astral.sh/uv/) handles
dependencies on first run.

## Features

- Speed range 0.25× – 3.00× in 0.05 steps, with quick presets (1×, 1.25×,
  1.5×, 1.75×, 2×, 2.5×).
- Pitch preserved (ffmpeg `atempo`, auto-chained for values outside 0.5–2.0).
- Replaces the original file atomically — encode happens on local disk, then
  one bulk copy back to the source location (much friendlier for NFS-mounted
  libraries than streaming the encode straight onto the network).
- Output bitrate matches the source (capped per-format), so a 64k mono m4b
  doesn't get inflated to 96k stereo.
- Live status: `Encoding at 74× realtime — ETA 0:02`.
- Confirmation prompt before overwriting the original.
- Remembers the last folder you browsed.

## One-line install

**Linux / macOS** (bash, zsh):

```sh
curl -fsSL https://raw.githubusercontent.com/AlfiePRIME/TomeSpeeder/main/install.sh | bash
```

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/AlfiePRIME/TomeSpeeder/main/install.ps1 | iex
```

Each installer:

1. Installs [`uv`](https://docs.astral.sh/uv/) if missing.
2. Installs `ffmpeg` if missing (apt / dnf / pacman / zypper / apk / brew /
   winget — auto-detected).
3. Downloads `tome_speeder.py` to a stable location.
4. Renders the app icon.
5. Creates a launcher (Linux `.desktop`, macOS `.command` in `~/Applications`,
   Windows Start Menu shortcut).

After install, search your launcher for **Tome Speeder**.

## Requirements

- A desktop environment (tested on GNOME; works on KDE / macOS / Windows).
- `ffmpeg` and `ffprobe` on `PATH` (handled by the installer).
- [`uv`](https://docs.astral.sh/uv/) for the zero-config Python/PyQt6
  bootstrap (handled by the installer). Alternatively, install `PyQt6`
  yourself and run the script with `python3`.

## Manual run

```sh
./tome_speeder.py
# or explicitly
uv run tome_speeder.py
```

First launch downloads PyQt6 into a uv-managed venv (a few seconds);
subsequent launches are instant.

## Library folder

The file picker opens in `/mnt/TrueNAS/media/Library/audiobooks` by default
and falls back to `$HOME` if that path is unavailable. Change the
`DEFAULT_LIBRARY` constant near the top of `tome_speeder.py` to point at
your own library.

## How fast is the encode?

ffmpeg's native AAC encoder on a modern CPU hits roughly **125× realtime**,
so an 18-hour audiobook takes ~9 minutes of pure encode time, plus whatever
your storage adds for reading and writing the file. If your build of ffmpeg
includes `libfdk_aac` (license-restricted, usually not in distro packages),
that drops further. The live status indicator shows the current speed and
ETA so you can see whether the bottleneck is CPU or IO.

## Notes / limitations

- M4B chapter markers survive the re-encode; cover art on tracks does not
  always come through cleanly.
- Cancelling mid-encode (the "Abjure" button) leaves the original file
  untouched — only the temporary working file is deleted.
- Output codec is chosen from the file extension; the original extension is
  preserved end-to-end so an `.m4b` stays an `.m4b`.

## License

MIT.
