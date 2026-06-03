#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyQt6>=6.6"]
# ///
"""
Tome Speeder — a fantasy-themed audiobook speed converter.
Reads m4b/mp3/m4a/aac/opus/ogg/flac/wav files and re-encodes at a chosen tempo
(0.25x – 3.0x) using ffmpeg's atempo filter chain. Pitch is preserved.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


# ---------- ffmpeg helpers ----------

SUPPORTED_INPUT = {".m4b", ".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".wma"}
DEFAULT_LIBRARY = Path("/mnt/TrueNAS/media/Library/audiobooks")


def atempo_chain(speed: float) -> str:
    """Compose an atempo filter chain. Each atempo accepts 0.5–2.0, so chain
    multiple stages for values outside that range."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    parts: list[float] = []
    remaining = speed
    while remaining < 0.5:
        parts.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        parts.append(2.0)
        remaining /= 2.0
    parts.append(remaining)
    return ",".join(f"atempo={p:.6f}" for p in parts)


def probe_duration(path: Path) -> float | None:
    """Get audio duration in seconds via ffprobe, or None on failure."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def probe_audio_bitrate(path: Path) -> int | None:
    """Return source audio bitrate in bits/sec, or None if unknown.

    Prefers the audio stream's reported bit_rate, falling back to the
    container's overall bit_rate (accurate for audio-only files like m4b).
    """
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=bit_rate:format=bit_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for line in out.stdout.splitlines():
        line = line.strip()
        if line and line != "N/A":
            try:
                return int(line)
            except ValueError:
                continue
    return None


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------- conversion thread ----------

TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")


class ConvertWorker(QThread):
    progress = pyqtSignal(int)          # 0..100
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(Path)
    failed = pyqtSignal(str)

    def __init__(self, src: Path, speed: float) -> None:
        super().__init__()
        self.src = src
        # Encode to local disk for speed (avoids network round-trips per frame
        # and the second pass when ffmpeg rewrites the moov atom for faststart);
        # we'll move the finished file into place at the end.
        self.dst = Path(tempfile.gettempdir()) / f".tome_speeder.{os.getpid()}{src.suffix}"
        self.speed = speed
        self._proc: subprocess.Popen | None = None
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self) -> None:
        in_duration = probe_duration(self.src)
        # Output duration scales by 1/speed
        out_duration = (in_duration / self.speed) if in_duration else None

        ext = self.dst.suffix.lower()
        # Match source bitrate (capped) so we don't inflate small audiobook
        # files; keeps quality at the source ceiling and saves encode time.
        src_kbps = probe_audio_bitrate(self.src)

        def lossy_kbps(cap: int, floor: int = 32) -> int:
            if not src_kbps:
                return cap
            return max(floor, min(cap, round(src_kbps / 1000)))

        codec_args: list[str]
        if ext in {".m4b", ".m4a", ".aac"}:
            codec_args = ["-c:a", "aac", "-b:a", f"{lossy_kbps(96)}k"]
        elif ext == ".mp3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", f"{lossy_kbps(128)}k"]
        elif ext == ".opus":
            codec_args = ["-c:a", "libopus", "-b:a", f"{lossy_kbps(64)}k"]
        elif ext == ".ogg":
            codec_args = ["-c:a", "libvorbis", "-b:a", f"{lossy_kbps(96)}k"]
        elif ext == ".flac":
            codec_args = ["-c:a", "flac"]
        elif ext == ".wav":
            codec_args = ["-c:a", "pcm_s16le"]
        else:
            codec_args = ["-c:a", "aac", "-b:a", f"{lossy_kbps(96)}k"]

        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-i", str(self.src),
            "-vn",
            "-filter:a", atempo_chain(self.speed),
            *codec_args,
            "-movflags", "+faststart" if ext in {".m4b", ".m4a"} else "+empty_moov",
            "-progress", "pipe:1",
            "-loglevel", "error",
            str(self.dst),
        ]
        # +empty_moov is harmless for non-mov; trim if not applicable
        if ext not in {".m4b", ".m4a", ".mp4", ".mov"}:
            # remove movflags pair
            i = cmd.index("-movflags")
            del cmd[i:i + 2]

        self.status.emit(f"Transcribing at {self.speed:.2f}×…")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self.failed.emit("ffmpeg is not installed or not on PATH.")
            return

        assert self._proc.stdout is not None
        cur_out_secs = 0.0
        cur_speed: float | None = None
        last_status_ts = 0.0
        for line in self._proc.stdout:
            if self._cancel:
                break
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                    cur_out_secs = us / 1_000_000
                    if out_duration and out_duration > 0:
                        pct = max(0, min(100, int(cur_out_secs / out_duration * 100)))
                        self.progress.emit(pct)
                except ValueError:
                    pass
            elif line.startswith("speed="):
                val = line.split("=", 1)[1].rstrip("x").strip()
                try:
                    cur_speed = float(val)
                except ValueError:
                    cur_speed = None
            elif line == "progress=end":
                self.progress.emit(100)

            # Throttle status updates to ~2/sec so we don't spam the UI thread.
            now = time.monotonic()
            if cur_speed and now - last_status_ts > 0.5:
                last_status_ts = now
                parts = [f"Encoding at {cur_speed:.0f}× realtime"]
                if out_duration and cur_speed > 0:
                    eta = max(0.0, (out_duration - cur_out_secs) / cur_speed)
                    parts.append(f"ETA {_fmt_eta(eta)}")
                self.status.emit(" — ".join(parts))

        rc = self._proc.wait()
        stderr = self._proc.stderr.read() if self._proc.stderr else ""

        if self._cancel:
            try:
                self.dst.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit("Conjuration cancelled.")
            return

        if rc != 0:
            try:
                self.dst.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit(stderr.strip() or f"ffmpeg exited with code {rc}")
            return

        # Move tmp -> source. tmp is on local disk and source likely lives on
        # NFS, so os.replace can't span filesystems. Copy via a sibling staging
        # file on the source's own filesystem, then os.replace it onto the
        # original (atomic) and delete the local tmp.
        self.status.emit("Replacing original tome…")
        self.progress.emit(100)
        staging = self.src.with_name(f".{self.src.stem}.replacing{self.src.suffix}")
        try:
            shutil.copyfile(self.dst, staging)
            os.replace(staging, self.src)
        except OSError as exc:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit(f"Could not replace original: {exc}")
            return
        finally:
            try:
                self.dst.unlink(missing_ok=True)
            except OSError:
                pass

        self.finished_ok.emit(self.src)


# ---------- UI ----------

PARCHMENT = "#f1e2bf"
PARCHMENT_DARK = "#d9c190"
INK = "#3a2412"
INK_SOFT = "#5a3a1f"
GOLD = "#a8802c"
GOLD_LIGHT = "#d8b04a"
WAX = "#7a1f1f"


def fantasy_qss() -> str:
    return f"""
    QMainWindow, QWidget#root {{
        background: {PARCHMENT};
    }}
    QLabel {{
        color: {INK};
        background: transparent;
    }}
    QLabel#title {{
        color: {INK};
        font-size: 30px;
        font-weight: bold;
        letter-spacing: 2px;
    }}
    QLabel#subtitle {{
        color: {INK_SOFT};
        font-style: italic;
        font-size: 13px;
    }}
    QLabel#speedReadout {{
        color: {WAX};
        font-size: 56px;
        font-weight: bold;
    }}
    QLabel#fileLabel {{
        color: {INK_SOFT};
        font-style: italic;
        padding: 8px 14px;
        background: #faf2dc;
        border: 1px solid {GOLD};
        border-radius: 4px;
    }}
    QFrame#card {{
        background: #f8ebc6;
        border: 2px solid {GOLD};
        border-radius: 8px;
    }}
    QFrame#divider {{
        background: {GOLD};
        max-height: 2px;
        min-height: 2px;
        border: none;
    }}
    QPushButton {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #7d3a1a, stop:1 #4d2110);
        color: #f4e3b8;
        border: 2px solid {GOLD};
        border-radius: 6px;
        padding: 10px 22px;
        font-size: 14px;
        font-weight: bold;
        letter-spacing: 1px;
    }}
    QPushButton:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #94481f, stop:1 #5d2914);
        border-color: {GOLD_LIGHT};
    }}
    QPushButton:pressed {{
        background: #3a1a0c;
    }}
    QPushButton:disabled {{
        background: #b8a98a;
        color: #7a6a4f;
        border-color: #8e7a4d;
    }}
    QPushButton#cancel {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #5a4030, stop:1 #2e1e10);
    }}
    QPushButton#preset {{
        padding: 6px 10px;
        font-size: 13px;
        letter-spacing: 0px;
    }}
    QSlider::groove:horizontal {{
        height: 10px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #c9a566, stop:1 #8b6a30);
        border: 1px solid {INK_SOFT};
        border-radius: 5px;
    }}
    QSlider::handle:horizontal {{
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.4, fy:0.4,
            stop:0 #f3d27a, stop:1 #8b5a1a);
        border: 2px solid {INK};
        width: 26px;
        height: 26px;
        margin: -10px 0;
        border-radius: 14px;
    }}
    QSlider::handle:horizontal:hover {{
        border-color: {WAX};
    }}
    QSlider::sub-page:horizontal {{
        background: {WAX};
        border-radius: 5px;
    }}
    QProgressBar {{
        background: #e8d49a;
        border: 2px solid {GOLD};
        border-radius: 6px;
        text-align: center;
        color: {INK};
        font-weight: bold;
        height: 22px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #7d3a1a, stop:0.5 #a85528, stop:1 #7d3a1a);
        border-radius: 4px;
    }}
    """


def make_window_icon() -> QIcon:
    """A small parchment-and-wax-seal icon."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # parchment rectangle
    p.setBrush(Qt.GlobalColor.transparent)
    path = QPainterPath()
    path.addRoundedRect(6, 4, 52, 56, 4, 4)
    p.fillPath(path, Qt.GlobalColor.white)
    from PyQt6.QtGui import QColor, QPen
    p.fillPath(path, QColor(PARCHMENT))
    p.setPen(QPen(QColor(GOLD), 2))
    p.drawPath(path)
    # wax seal
    p.setBrush(QColor(WAX))
    p.setPen(QPen(QColor("#3a0a0a"), 1))
    p.drawEllipse(20, 24, 24, 24)
    p.setPen(QPen(QColor(GOLD_LIGHT), 1))
    p.drawEllipse(26, 30, 12, 12)
    p.end()
    return QIcon(pm)


class TomeSpeeder(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Tome Speeder — Audiobook Incantation")
        self.setWindowIcon(make_window_icon())
        self.setMinimumSize(720, 560)

        self.src_path: Path | None = None
        self.worker: ConvertWorker | None = None

        self._build_ui()
        self.setStyleSheet(fantasy_qss())
        self._update_speed_readout(self.speed_slider.value())

    # ---- UI build ----
    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)

        # Title
        title = QLabel("⚜ Tome Speeder ⚜", objectName="title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel(
            "“Quicken thy spoken tomes without warping the bard's voice.”",
            objectName="subtitle",
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Title font: try a serif
        serif = self._pick_font(["Palatino", "Book Antiqua", "Garamond",
                                  "Liberation Serif", "DejaVu Serif", "Serif"])
        if serif:
            title.setFont(QFont(serif, 22, QFont.Weight.Bold))
            subtitle.setFont(QFont(serif, 11, italic=True))

        outer.addWidget(title)
        outer.addWidget(subtitle)

        divider = QFrame(objectName="divider")
        outer.addWidget(divider)

        # File card
        file_card = QFrame(objectName="card")
        fc = QVBoxLayout(file_card)
        fc.setContentsMargins(18, 16, 18, 16)
        fc.setSpacing(10)

        fc.addWidget(self._section_label("I. Choose thy tome"))
        row = QHBoxLayout()
        self.file_label = QLabel("…no tome selected…", objectName="fileLabel")
        self.file_label.setMinimumHeight(40)
        pick_btn = QPushButton("📜  Open Tome…")
        pick_btn.clicked.connect(self.pick_file)
        row.addWidget(self.file_label, 1)
        row.addWidget(pick_btn)
        fc.addLayout(row)
        outer.addWidget(file_card)

        # Speed card
        speed_card = QFrame(objectName="card")
        sc = QVBoxLayout(speed_card)
        sc.setContentsMargins(18, 16, 18, 16)
        sc.setSpacing(8)

        sc.addWidget(self._section_label("II. Set the pace of the tale"))

        self.speed_readout = QLabel("1.00×", objectName="speedReadout")
        self.speed_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if serif:
            self.speed_readout.setFont(QFont(serif, 44, QFont.Weight.Bold))
        sc.addWidget(self.speed_readout)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        # represent 0.25..3.00 in steps of 0.05 => 5..60
        self.speed_slider.setMinimum(5)
        self.speed_slider.setMaximum(60)
        self.speed_slider.setSingleStep(1)
        self.speed_slider.setPageStep(5)
        self.speed_slider.setValue(20)  # 1.00x
        self.speed_slider.setTickInterval(5)
        self.speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed_slider.valueChanged.connect(self._update_speed_readout)
        sc.addWidget(self.speed_slider)

        ticks = QHBoxLayout()
        for label in ["0.25×", "1×", "2×", "3×"]:
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {INK_SOFT}; font-style: italic;")
            ticks.addWidget(lbl, 1, Qt.AlignmentFlag.AlignCenter)
        sc.addLayout(ticks)

        # quick preset buttons
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addStretch(1)
        for label, val in [("1×", 20), ("1.25×", 25), ("1.5×", 30),
                           ("1.75×", 35), ("2×", 40), ("2.5×", 50)]:
            b = QPushButton(label, objectName="preset")
            # Size to content so labels like "1.75×" don't clip.
            fm = b.fontMetrics()
            b.setMinimumWidth(fm.horizontalAdvance(label) + 32)
            b.clicked.connect(lambda _=False, v=val: self.speed_slider.setValue(v))
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        sc.addLayout(preset_row)

        outer.addWidget(speed_card)

        # Action card
        action_card = QFrame(objectName="card")
        ac = QVBoxLayout(action_card)
        ac.setContentsMargins(18, 16, 18, 16)
        ac.setSpacing(10)

        ac.addWidget(self._section_label("III. Cast the spell"))

        btn_row = QHBoxLayout()
        self.convert_btn = QPushButton("✦  Quicken the Tome  ✦")
        self.convert_btn.clicked.connect(self.start_convert)
        self.cancel_btn = QPushButton("Abjure", objectName="cancel")
        self.cancel_btn.clicked.connect(self.cancel_convert)
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.convert_btn, 2)
        btn_row.addWidget(self.cancel_btn, 1)
        ac.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("Awaiting incantation…")
        ac.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(f"color: {INK_SOFT}; font-style: italic;")
        ac.addWidget(self.status_label)

        outer.addWidget(action_card)
        outer.addStretch(1)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {WAX}; font-weight: bold; font-size: 14px; letter-spacing: 1px;"
        )
        return lbl

    @staticmethod
    def _pick_font(candidates: list[str]) -> str | None:
        families = set(QFontDatabase.families())
        for c in candidates:
            if c in families:
                return c
        return None

    # ---- handlers ----
    def _update_speed_readout(self, slider_val: int) -> None:
        speed = slider_val / 20.0
        self.speed_readout.setText(f"{speed:.2f}×")

    @property
    def speed(self) -> float:
        return self.speed_slider.value() / 20.0

    def pick_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_INPUT))
        # Remember the last folder used; first run starts in the library.
        start_dir = getattr(self, "_last_dir", None)
        if not start_dir or not start_dir.exists():
            start_dir = DEFAULT_LIBRARY if DEFAULT_LIBRARY.exists() else Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an audiobook",
            str(start_dir),
            f"Audiobooks ({exts});;All files (*)",
        )
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in SUPPORTED_INPUT:
            QMessageBox.warning(self, "Unsupported scroll",
                                f"Format {p.suffix} is not recognised.")
            return
        self.src_path = p
        self._last_dir = p.parent
        self.file_label.setText(f"📖  {p.name}")
        self.file_label.setStyleSheet(
            f"color: {INK}; font-style: normal; font-weight: bold; "
            f"padding: 8px 14px; background: #faf2dc; "
            f"border: 1px solid {GOLD}; border-radius: 4px;"
        )

    def start_convert(self) -> None:
        if not self.src_path:
            QMessageBox.information(self, "No tome",
                                    "First open an audiobook to quicken.")
            return
        if not shutil.which("ffmpeg"):
            QMessageBox.critical(self, "Missing tool",
                                 "ffmpeg is not installed or not on PATH.")
            return

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Replace the original?")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            f"This will <b>overwrite</b> the original tome at {self.speed:.2f}× speed:<br><br>"
            f"<code>{self.src_path}</code><br><br>"
            "There is no undo. Proceed?"
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.speed_slider.setEnabled(False)
        self.status_label.setText("")

        self.worker = ConvertWorker(self.src_path, self.speed)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def cancel_convert(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.status_label.setText("Abjuring…")

    def _reset_buttons(self) -> None:
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.speed_slider.setEnabled(True)

    def _on_done(self, out: Path) -> None:
        self._reset_buttons()
        self.progress.setValue(100)
        self.progress.setFormat("Complete!")
        self.status_label.setText(f"✦ Saved to {out} ✦")
        QMessageBox.information(self, "Tome quickened",
                                f"Your audiobook awaits at:\n{out}")

    def _on_failed(self, msg: str) -> None:
        self._reset_buttons()
        self.progress.setFormat("Spell fizzled")
        self.status_label.setText(msg.splitlines()[0][:160] if msg else "Failed")
        QMessageBox.critical(self, "Spell fizzled", msg or "Unknown error")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Tome Speeder")
    win = TomeSpeeder()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
