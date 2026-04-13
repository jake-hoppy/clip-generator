"""
ClipGen Web API — wraps the pipeline via subprocess only (never imports src/).
YouTube upload is the one exception — it imports src/youtube/upload directly.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from web.backend.progress import estimate_progress_from_log, read_log_tail

app = FastAPI(title="ClipGen API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Repo root (clip-generator/): web/backend/main.py -> parent.parent.parent
PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_ROOT = PIPELINE_ROOT / "data"
CONFIG_PATH = PIPELINE_ROOT / "config" / "config.yaml"
LOG_PATH = DATA_ROOT / "logs" / "run.log"
OUTPUTS_DIR = DATA_ROOT / "outputs"
MANIFESTS_VIDEOS = DATA_ROOT / "manifests" / "videos"
RANKED_MANIFEST = DATA_ROOT / "manifests" / "candidates_ranked" / "top_ranked_manifest.json"
USED_CLIPS_FILE = DATA_ROOT / "registry" / "used_clips.json"
PROCESSED_VIDEOS_FILE = DATA_ROOT / "registry" / "processed_videos.json"
INCOMING_DIR = DATA_ROOT / "videos" / "incoming"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_processes: dict[str, subprocess.Popen] = {}
_process_status: dict[str, str] = {}


def _python() -> str:
    return os.environ.get("CLIP_FARM_PYTHON", "python3")


def _safe_filename(name: str) -> str:
    base = Path(name).name
    if ".." in base or "/" in base or "\\" in base:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return base


def clip_id_from_output_filename(name: str) -> str:
    stem = Path(name).stem
    parts = stem.split("_", 2)
    return parts[2] if len(parts) >= 3 else stem


def video_id_from_clip_id(clip_id: str) -> str:
    if "_t" in clip_id:
        return clip_id.split("_t", 1)[0]
    return ""


def _parse_top_n_override(raw: str) -> int | None:
    if not raw or not str(raw).strip():
        return None
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    if n < 1 or n > 500:
        return None
    return n


def datetime_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ============================================================
# Core API
# ============================================================

@app.get("/")
def root():
    return RedirectResponse(url="/app/")


@app.get("/api/stats")
def api_stats():
    outputs_count = 0
    if OUTPUTS_DIR.exists():
        outputs_count = len(list(OUTPUTS_DIR.glob("*.mp4")))

    videos_count = 0
    if MANIFESTS_VIDEOS.exists():
        videos_count = len(list(MANIFESTS_VIDEOS.glob("*.json")))

    used_clips = 0
    if USED_CLIPS_FILE.exists():
        try:
            with open(USED_CLIPS_FILE) as f:
                used_clips = len(json.load(f).get("used_clip_ids", []))
        except Exception:
            pass

    avg_score = 0.0
    if RANKED_MANIFEST.exists():
        try:
            with open(RANKED_MANIFEST) as f:
                clips = json.load(f).get("clips", [])
            if clips:
                avg_score = round(sum(c.get("score", 0) for c in clips) / len(clips), 1)
        except Exception:
            pass

    pipeline_running = False
    proc = _processes.get("main")
    if proc is not None and proc.poll() is None:
        pipeline_running = True

    return {
        "outputs_count": outputs_count,
        "videos_count": videos_count,
        "used_clips": used_clips,
        "avg_score": avg_score,
        "pipeline_running": pipeline_running,
    }


@app.get("/api/clips")
def api_clips():
    if not RANKED_MANIFEST.exists():
        return {"clips": [], "total": 0}
    try:
        with open(RANKED_MANIFEST) as f:
            data = json.load(f)
        clips = data.get("clips", [])
        return {"clips": clips, "total": len(clips)}
    except Exception:
        return {"clips": [], "total": 0}


@app.get("/api/outputs")
def api_outputs(video_id: str | None = None):
    manifest_by_id: dict[str, dict] = {}
    if RANKED_MANIFEST.exists():
        try:
            with open(RANKED_MANIFEST) as f:
                data = json.load(f)
            for c in data.get("clips", []):
                cid = c.get("clip_id")
                if cid:
                    manifest_by_id[cid] = c
        except Exception:
            pass

    out = []
    if not OUTPUTS_DIR.exists():
        return {"clips": []}

    for p in sorted(OUTPUTS_DIR.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        cid = clip_id_from_output_filename(p.name)
        if video_id and not cid.startswith(f"{video_id}_t"):
            continue
        meta = manifest_by_id.get(cid, {})
        st = p.stat()
        vid = video_id_from_clip_id(cid)
        out.append({
            "filename": p.name,
            "clip_id": cid,
            "video_id": vid,
            "size_mb": round(st.st_size / 1024 / 1024, 2),
            "created": datetime_iso(st.st_mtime),
            "score": meta.get("score"),
            "text": meta.get("text", ""),
            "duration": meta.get("duration_seconds"),
            "duration_seconds": meta.get("duration_seconds"),
        })
    return {"clips": out}


@app.get("/api/outputs/{filename}")
def api_output_video(filename: str):
    safe = _safe_filename(filename)
    path = OUTPUTS_DIR / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="video/mp4", filename=safe)


@app.get("/api/config")
def api_get_config():
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


@app.post("/api/config")
def api_post_config(body: dict[str, Any] = Body(...)):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(body, f, default_flow_style=False, allow_unicode=True)
    return {"ok": True}


@app.get("/api/log")
def api_log():
    if not LOG_PATH.exists():
        return PlainTextResponse("")
    try:
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        return PlainTextResponse("\n".join(lines[-80:]))
    except Exception:
        return PlainTextResponse("")


# ============================================================
# Pipeline control
# ============================================================

@app.post("/api/run")
def api_run(body: dict[str, Any] = Body(...)):
    cmd_name = body.get("command")
    if cmd_name not in ("run", "rank", "refresh"):
        raise HTTPException(status_code=400, detail="command must be run, rank, or refresh")

    proc = _processes.get("main")
    if proc is not None and proc.poll() is None:
        raise HTTPException(status_code=409, detail="Pipeline already running")

    cmd = [_python(), "-m", "src.main", cmd_name]
    limit = body.get("limit_videos")
    if limit is not None and cmd_name == "run":
        try:
            cmd.extend(["--limit-videos", str(int(limit))])
        except (TypeError, ValueError):
            pass
    if body.get("dry_run"):
        cmd.append("--dry-run")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(LOG_PATH, "a", encoding="utf-8")
    try:
        p = subprocess.Popen(cmd, cwd=str(PIPELINE_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
    except Exception as e:
        log_f.close()
        raise HTTPException(status_code=500, detail=str(e)) from e

    _processes["main"] = p
    _process_status["main"] = "running"
    return {"started": True, "pid": p.pid}


@app.get("/api/run/status")
def api_run_status():
    proc = _processes.get("main")
    if proc is None:
        return {"running": False, "pid": None, "exit_code": None}
    code = proc.poll()
    if code is None:
        return {"running": True, "pid": proc.pid, "exit_code": None}
    return {"running": False, "pid": proc.pid, "exit_code": code}


@app.get("/api/run/stages")
def api_run_stages():
    """Detect which pipeline stage is running from log keywords."""
    if not LOG_PATH.exists():
        return {"stage": "idle", "progress": 0, "running": False}

    proc = _processes.get("main")
    running = proc is not None and proc.poll() is None

    try:
        lines = LOG_PATH.read_text(errors="replace").splitlines()
        recent = "\n".join(lines[-80:]).lower()
    except Exception:
        return {"stage": "idle", "progress": 0, "running": running}

    stage = "idle"
    progress = 0

    if not running and proc is not None:
        code = proc.poll()
        if code == 0:
            stage = "done"
            progress = 100
        else:
            stage = "failed"
            progress = 0
    elif "exported" in recent and "vertical" in recent:
        stage = "exporting"
        progress = 90
    elif "scored" in recent or "scoring" in recent or "score_segment" in recent:
        stage = "scoring"
        progress = 70
    elif "transcrib" in recent or "whisper" in recent or "mlx" in recent:
        stage = "transcribing"
        progress = 45
    elif "ingest" in recent or "copied" in recent or "registered" in recent:
        stage = "ingesting"
        progress = 20
    elif "download" in recent or "yt-dlp" in recent:
        stage = "downloading"
        progress = 15
    elif running:
        stage = "starting"
        progress = 5

    return {"stage": stage, "progress": progress, "running": running}


@app.get("/api/run/progress")
def api_run_progress():
    proc = _processes.get("main")
    running = proc is not None and proc.poll() is None
    exit_code: int | None = None
    if proc is not None:
        code = proc.poll()
        if code is not None:
            exit_code = code

    tail = read_log_tail(LOG_PATH)
    pct, label = estimate_progress_from_log(tail)

    if not running:
        if proc is None:
            return {"running": False, "percent": 0.0, "label": "Idle", "exit_code": None}
        if exit_code == 0:
            return {"running": False, "percent": 100.0, "label": "Complete", "exit_code": 0}
        return {"running": False, "percent": min(pct, 99.0), "label": "Failed", "exit_code": exit_code}

    return {"running": True, "percent": min(pct, 98.0), "label": label, "exit_code": None}


@app.post("/api/run/stop")
def api_run_stop():
    proc = _processes.get("main")
    if proc is None or proc.poll() is not None:
        return {"stopped": False}
    try:
        proc.terminate()
    except Exception:
        pass
    return {"stopped": True}


# ============================================================
# Upload / ingest
# ============================================================

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
CHUNK_SIZE = 1024 * 1024  # 1 MB


@app.post("/api/ingest")
async def api_ingest(
    file: UploadFile = File(...),
    title: str = Form(""),
    top_n: str = Form(""),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    proc = _processes.get("main")
    if proc is not None and proc.poll() is None:
        raise HTTPException(status_code=409, detail="Pipeline already running")

    safe = _safe_filename(file.filename)
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    dest = INCOMING_DIR / safe

    try:
        total = 0
        with open(dest, "wb") as out_f:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File too large (2 GB max)")
                out_f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    cmd = [_python(), "-m", "src.main", "ingest", str(dest), "--rank"]
    if title.strip():
        cmd.extend(["--title", title.strip()])
    tn = _parse_top_n_override(top_n)
    if tn is not None:
        cmd.extend(["--top-n", str(tn)])

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(LOG_PATH, "a", encoding="utf-8")
    try:
        p = subprocess.Popen(cmd, cwd=str(PIPELINE_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
        _processes["main"] = p
        _process_status["main"] = "running"
    except Exception as e:
        log_f.close()
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"started": True, "filename": file.filename, "size_bytes": total}


# ============================================================
# Library
# ============================================================

@app.get("/api/library")
def api_library():
    if not MANIFESTS_VIDEOS.exists():
        return {"videos": []}

    candidates_dir = DATA_ROOT / "manifests" / "candidates"
    clips_per_video: dict[str, int] = {}
    ranked_video_ids: set[str] = set()
    if candidates_dir.exists():
        for cp in candidates_dir.glob("*.json"):
            vid = cp.stem
            ranked_video_ids.add(vid)
            try:
                with open(cp) as f:
                    cdata = json.load(f)
                clips_per_video[vid] = len(cdata.get("clips", []))
            except Exception:
                clips_per_video[vid] = 0

    exhausted_ids: set[str] = set()
    if PROCESSED_VIDEOS_FILE.exists():
        try:
            with open(PROCESSED_VIDEOS_FILE) as f:
                pv = json.load(f).get("processed_videos", {})
            exhausted_ids = {vid for vid, count in pv.items() if count >= 2}
        except Exception:
            pass

    videos = []
    for p in sorted(MANIFESTS_VIDEOS.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                d = json.load(f)
        except Exception:
            continue

        duration = float(d.get("duration_seconds") or 0)
        if duration <= 0:
            continue

        vid = d.get("video_id", "")
        if vid in exhausted_ids:
            status = "exhausted"
        elif vid in ranked_video_ids:
            status = "ranked"
        else:
            status = "ingested"

        videos.append({
            "video_id": vid,
            "title": d.get("title", ""),
            "duration_seconds": duration,
            "download_time": d.get("download_time"),
            "uploader": d.get("uploader", ""),
            "local": d.get("uploader") == "local",
            "status": status,
            "clip_count": clips_per_video.get(vid, 0),
        })

    return {"videos": videos, "total": len(videos)}


# ============================================================
# YouTube integration
# ============================================================

@app.get("/api/youtube/status")
def api_youtube_status():
    try:
        from src.youtube.upload import is_connected
        connected, channel_name = is_connected()
        return {"connected": connected, "channel_name": channel_name}
    except ImportError:
        return {"connected": False, "channel_name": "", "error": "Missing dependencies"}
    except Exception as e:
        return {"connected": False, "channel_name": "", "error": str(e)}


@app.get("/api/youtube/auth-url")
def api_youtube_auth_url():
    try:
        from src.youtube.upload import get_auth_url
        url = get_auth_url()
        if url:
            return {"url": url}
        return {"url": None, "error": "client_secrets.json not found in config/"}
    except ImportError:
        return {"url": None, "error": "Missing google-auth-oauthlib dependency"}
    except Exception as e:
        return {"url": None, "error": str(e)}


@app.get("/api/youtube/callback")
def api_youtube_callback(code: str = "", error: str = ""):
    """OAuth2 callback — Google redirects here after user authorizes."""
    if error:
        return PlainTextResponse(f"Authorization failed: {error}\n\nYou can close this tab.")
    if not code:
        return PlainTextResponse("No authorization code received.\n\nYou can close this tab.")
    try:
        from src.youtube.upload import exchange_code
        success = exchange_code(code=code)
        if success:
            return PlainTextResponse(
                "YouTube connected successfully!\n\nYou can close this tab and return to ClipGen."
            )
        return PlainTextResponse("Failed to exchange authorization code.\n\nYou can close this tab.")
    except Exception as e:
        return PlainTextResponse(f"Error: {e}\n\nYou can close this tab.")


@app.post("/api/youtube/upload-top")
def api_youtube_upload_top():
    """Upload the top 2 output clips to YouTube."""
    try:
        from src.youtube.upload import upload_top_clips
        results = upload_top_clips(n=2, privacy="private")
        urls = [r["url"] for r in results if "url" in r]
        return {"uploaded": len(results), "results": results, "urls": urls}
    except ImportError:
        raise HTTPException(status_code=500, detail="Missing google-auth-oauthlib dependency")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Static frontend
# ============================================================

if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
