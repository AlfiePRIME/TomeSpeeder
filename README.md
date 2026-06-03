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

## Requirements

- Linux with a desktop environment (tested on GNOME).
- `ffmpeg` and `ffprobe` on `PATH`.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for the
  zero-config Python/PyQt6 bootstrap. Alternatively install `PyQt6` via your
  system package manager and run the script with `python3`.

## Run it

```sh
./tome_speeder.py
# or explicitly
uv run tome_speeder.py
```

First launch downloads PyQt6 into a uv-managed venv (a few seconds);
subsequent launches are instant.

## Install as a GNOME app

Save the example below to `~/.local/share/applications/tome-speeder.desktop`,
edit the two absolute paths, then run
`update-desktop-database ~/.local/share/applications`. The app will appear in
the Activities overview.

```ini
[Desktop Entry]
Type=Application
Version=1.0
Name=Tome Speeder
GenericName=Audiobook Speed Converter
Comment=Quicken m4b and other audiobooks without warping the bard's voice
Exec=/absolute/path/to/uv run --quiet /absolute/path/to/tome_speeder.py
Icon=/absolute/path/to/icon.png
Terminal=false
Categories=AudioVideo;AudioVideoEditing;
Keywords=audiobook;m4b;mp3;speed;tempo;ffmpeg;
StartupNotify=true
StartupWMClass=Tome Speeder
```

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
