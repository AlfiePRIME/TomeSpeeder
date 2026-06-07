"""Tome Speeder web — FastAPI front-end for the same atempo conversion logic.

Serves a single-page UI that lets you browse the configured library root,
pick an audiobook, choose a speed, and overwrite the original in place.
Progress streams over a per-job WebSocket.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request


# ---------- configuration ----------

LIBRARY_ROOT = Path(os.environ.get("LIBRARY_ROOT", "/audiobooks")).resolve()
ALLOW_REPLACE = os.environ.get("ALLOW_REPLACE", "1") != "0"
SUPPORTED_INPUT = {".m4b", ".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga",
                   ".flac", ".wav", ".wma"}


# ---------- ffmpeg helpers (mirrors tome_speeder.py) ----------

def atempo_chain(speed: float) -> str:
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


FFMPEG_INPUT_FLAGS = ["-ignore_chapters", "1"]
# Some audiobook m4a/m4b files (typically iTunes/Audible-derived) carry a
# malformed chapter track that makes ffmpeg refuse to open the file even though
# the audio streams are fine. -ignore_chapters 1 skips that track. We lose
# chapter markers in the output, but the alternative is "won't open at all".


def probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", *FFMPEG_INPUT_FLAGS,
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def probe_audio_bitrate(path: Path) -> int | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", *FFMPEG_INPUT_FLAGS,
             "-select_streams", "a:0",
             "-show_entries", "stream=bit_rate:format=bit_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
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


def codec_args_for(ext: str, src_kbps: int | None) -> list[str]:
    def k(cap: int, floor: int = 32) -> int:
        if not src_kbps:
            return cap
        return max(floor, min(cap, round(src_kbps / 1000)))
    if ext in {".m4b", ".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", f"{k(96)}k"]
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", f"{k(128)}k"]
    if ext == ".opus":
        return ["-c:a", "libopus", "-b:a", f"{k(64)}k"]
    if ext == ".ogg":
        return ["-c:a", "libvorbis", "-b:a", f"{k(96)}k"]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "aac", "-b:a", f"{k(96)}k"]


def fmt_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------- path safety ----------

def resolve_under_root(rel: str) -> Path:
    """Resolve a request path under LIBRARY_ROOT, refusing escapes."""
    candidate = (LIBRARY_ROOT / rel.lstrip("/")).resolve()
    if not candidate.is_relative_to(LIBRARY_ROOT):
        raise HTTPException(400, "path escapes library root")
    return candidate


# ---------- job state ----------

@dataclass
class Job:
    id: str
    src: Path
    speed: float
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    proc: asyncio.subprocess.Process | None = None
    cancelled: bool = False
    finished: bool = False


JOBS: dict[str, Job] = {}
# One conversion at a time per source path, to avoid clobbering races.
PATH_LOCKS: dict[str, asyncio.Lock] = {}


def lock_for(path: Path) -> asyncio.Lock:
    key = str(path)
    if key not in PATH_LOCKS:
        PATH_LOCKS[key] = asyncio.Lock()
    return PATH_LOCKS[key]


# ---------- conversion coroutine ----------

async def run_conversion(job: Job) -> None:
    async def emit(payload: dict) -> None:
        await job.queue.put(payload)

    async with lock_for(job.src):
        await emit({"type": "status", "msg": "Probing the tome…"})
        in_duration = probe_duration(job.src)
        out_duration = (in_duration / job.speed) if in_duration else None
        src_kbps = probe_audio_bitrate(job.src)

        ext = job.src.suffix.lower()
        tmp = Path(tempfile.gettempdir()) / f".tome_speeder.{os.getpid()}.{job.id}{ext}"

        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            *FFMPEG_INPUT_FLAGS,
            "-i", str(job.src),
            "-vn",
            "-filter:a", atempo_chain(job.speed),
            *codec_args_for(ext, src_kbps),
            "-progress", "pipe:1",
            "-loglevel", "error",
            str(tmp),
        ]
        if ext in {".m4b", ".m4a", ".mp4", ".mov"}:
            cmd[-1:-1] = ["-movflags", "+faststart"]

        await emit({"type": "status", "msg": f"Encoding at {job.speed:.2f}×…"})
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            await emit({"type": "error", "msg": "ffmpeg not installed in the container"})
            job.finished = True
            return

        cur_out = 0.0
        cur_speed: float | None = None
        last_tick = 0.0
        assert job.proc.stdout is not None
        try:
            async for raw in job.proc.stdout:
                if job.cancelled:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                        cur_out = us / 1_000_000
                        if out_duration and out_duration > 0:
                            pct = max(0, min(100, cur_out / out_duration * 100))
                            await emit({"type": "progress", "pct": round(pct, 1)})
                    except ValueError:
                        pass
                elif line.startswith("speed="):
                    try:
                        cur_speed = float(line.split("=", 1)[1].rstrip("x").strip())
                    except ValueError:
                        cur_speed = None
                elif line == "progress=end":
                    await emit({"type": "progress", "pct": 100})

                now = time.monotonic()
                if cur_speed and now - last_tick > 0.4:
                    last_tick = now
                    eta = ""
                    if out_duration and cur_speed > 0:
                        eta = f" — ETA {fmt_eta((out_duration - cur_out) / cur_speed)}"
                    await emit({"type": "status",
                                "msg": f"Encoding at {cur_speed:.0f}× realtime{eta}"})
        except asyncio.CancelledError:
            job.cancelled = True

        rc = await job.proc.wait()
        stderr_bytes = await job.proc.stderr.read() if job.proc.stderr else b""
        stderr = stderr_bytes.decode("utf-8", "replace").strip()

        if job.cancelled:
            tmp.unlink(missing_ok=True)
            await emit({"type": "error", "msg": "Conjuration cancelled."})
            job.finished = True
            return

        if rc != 0:
            tmp.unlink(missing_ok=True)
            await emit({"type": "error",
                        "msg": stderr or f"ffmpeg exited with code {rc}"})
            job.finished = True
            return

        await emit({"type": "status", "msg": "Replacing original tome…"})
        await emit({"type": "progress", "pct": 100})
        staging = job.src.with_name(f".{job.src.stem}.replacing{job.src.suffix}")
        try:
            await asyncio.to_thread(shutil.copyfile, tmp, staging)
            os.replace(staging, job.src)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            await emit({"type": "error", "msg": f"Could not replace original: {exc}"})
            job.finished = True
            return
        finally:
            tmp.unlink(missing_ok=True)

        await emit({"type": "done", "path": str(job.src.relative_to(LIBRARY_ROOT))})
        job.finished = True


# ---------- FastAPI app ----------

app = FastAPI(title="Tome Speeder")
HERE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "library_root": str(LIBRARY_ROOT)},
    )


@app.get("/api/files")
async def list_files(path: str = "") -> dict:
    """List subfolders and supported audio files under LIBRARY_ROOT/path."""
    target = resolve_under_root(path)
    if not target.exists():
        raise HTTPException(404, f"not found: {target}")
    if not target.is_dir():
        raise HTTPException(400, "not a directory")

    folders, files = [], []
    try:
        entries = sorted(target.iterdir(),
                         key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        raise HTTPException(403, "permission denied")

    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                folders.append({
                    "name": entry.name,
                    "path": str(entry.relative_to(LIBRARY_ROOT)),
                })
            elif entry.is_file() and entry.suffix.lower() in SUPPORTED_INPUT:
                stat = entry.stat()
                files.append({
                    "name": entry.name,
                    "path": str(entry.relative_to(LIBRARY_ROOT)),
                    "size": stat.st_size,
                    "ext": entry.suffix.lower(),
                })
        except OSError:
            continue

    rel = target.relative_to(LIBRARY_ROOT)
    crumbs = [{"name": "Library", "path": ""}]
    accum = Path()
    for part in rel.parts:
        accum = accum / part
        crumbs.append({"name": part, "path": str(accum)})

    return {
        "path": "" if str(rel) == "." else str(rel),
        "crumbs": crumbs,
        "folders": folders,
        "files": files,
    }


@app.get("/api/file")
async def file_info(path: str) -> dict:
    target = resolve_under_root(path)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    if target.suffix.lower() not in SUPPORTED_INPUT:
        raise HTTPException(400, "unsupported format")
    return {
        "path": str(target.relative_to(LIBRARY_ROOT)),
        "name": target.name,
        "size": target.stat().st_size,
        "duration": probe_duration(target),
        "bitrate": probe_audio_bitrate(target),
    }


@app.post("/api/jobs")
async def create_job(payload: dict) -> dict:
    if not ALLOW_REPLACE:
        raise HTTPException(403, "replacement disabled by server config")
    path = payload.get("path")
    speed = payload.get("speed")
    if not path or not isinstance(speed, (int, float)):
        raise HTTPException(400, "path and speed required")
    if not (0.25 <= float(speed) <= 3.0):
        raise HTTPException(400, "speed must be between 0.25 and 3.0")

    src = resolve_under_root(path)
    if not src.is_file():
        raise HTTPException(404, "file not found")
    if src.suffix.lower() not in SUPPORTED_INPUT:
        raise HTTPException(400, "unsupported format")

    job = Job(id=uuid.uuid4().hex[:12], src=src, speed=float(speed))
    JOBS[job.id] = job
    job.task = asyncio.create_task(run_conversion(job))
    return {"job_id": job.id}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job.cancelled = True
    if job.proc and job.proc.returncode is None:
        try:
            job.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
    return {"ok": True}


@app.websocket("/ws/jobs/{job_id}")
async def job_ws(ws: WebSocket, job_id: str) -> None:
    await ws.accept()
    job = JOBS.get(job_id)
    if not job:
        await ws.send_json({"type": "error", "msg": "unknown job"})
        await ws.close()
        return
    try:
        while True:
            event = await job.queue.get()
            await ws.send_json(event)
            if event["type"] in {"done", "error"}:
                break
    except WebSocketDisconnect:
        return
    finally:
        # Tidy completed jobs.
        if job.finished:
            JOBS.pop(job_id, None)
        try:
            await ws.close()
        except RuntimeError:
            pass
