"""
YouTube search + download via yt-dlp (subprocess).
Supports search queries and direct URLs; idempotent (skips if file + manifest exist).
"""
import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.utils.paths import (
    video_file_path,
    video_manifest_path,
    videos_dir,
    manifests_videos_dir,
)
from src.utils.hashing import stable_video_id

logger = logging.getLogger(__name__)

YT_DLP_CMD = "yt-dlp"


class YtDlpError(Exception):
    """Raised when yt-dlp fails."""

    def __init__(self, message: str, command: list[str], stderr: str, returncode: int):
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.returncode = returncode


@dataclass
class VideoMeta:
    """Metadata for a downloaded source video."""

    video_id: str
    original_url: str
    title: str
    uploader: str
    duration_seconds: float
    upload_date: str | None
    filename: str
    download_time: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VideoMeta":
        return cls(
            video_id=d["video_id"],
            original_url=d["original_url"],
            title=d["title"],
            uploader=d["uploader"],
            duration_seconds=float(d["duration_seconds"]),
            upload_date=d.get("upload_date"),
            filename=d["filename"],
            download_time=d["download_time"],
        )


def _run_yt_dlp(args: list[str], timeout: int = 600) -> str:
    """Run yt-dlp; return stdout. Raises YtDlpError on failure."""
    cmd = [YT_DLP_CMD] + args
    logger.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise YtDlpError("yt-dlp timed out", command=cmd, stderr=str(e), returncode=-1) from e
    except FileNotFoundError as e:
        raise YtDlpError(
            "yt-dlp not found. Install with: pip install yt-dlp",
            command=cmd,
            stderr=str(e),
            returncode=-1,
        ) from e

    if result.returncode != 0:
        raise YtDlpError(
            f"yt-dlp exited with code {result.returncode}",
            command=cmd,
            stderr=result.stderr or result.stdout or "",
            returncode=result.returncode,
        )
    return result.stdout


def _get_video_info(url: str) -> dict:
    """Fetch JSON info for one video (no download)."""
    out = _run_yt_dlp([
        "--dump-json",
        "--no-download",
        "--no-playlist",
        url,
    ])
    lines = out.strip().split("\n")
    if not lines:
        raise YtDlpError("No output from yt-dlp", command=[], stderr=out, returncode=-1)
    return json.loads(lines[0])


def _search_youtube(query: str, max_results: int) -> list[str]:
    """Return list of video URLs from YouTube search (ytsearchN:query)."""
    search_str = f"ytsearch{max_results}:{query}"
    out = _run_yt_dlp([
        "--dump-json",
        "--no-download",
        "--flat-playlist",
        search_str,
    ])
    urls = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            vid = entry.get("id")
            if vid:
                urls.append(f"https://www.youtube.com/watch?v={vid}")
        except json.JSONDecodeError:
            continue
    return urls


def _already_downloaded(video_id: str, ext: str = "mp4") -> bool:
    """True if video file and manifest both exist."""
    vpath = video_file_path(video_id, ext)
    mpath = video_manifest_path(video_id)
    return vpath.exists() and mpath.exists()


def _download_one(
    url: str,
    download_format: str = "mp4",
    min_duration: float = 0,
    max_duration: float = 1e9,
) -> VideoMeta | None:
    """
    Download one video. Returns VideoMeta or None if skipped (duration filter).
    """
    info = _get_video_info(url)
    duration = float(info.get("duration") or 0)
    if duration < min_duration or duration > max_duration:
        logger.info("Skipping %s: duration %.0fs outside [%s, %s]", url, duration, min_duration, max_duration)
        return None

    title = info.get("title") or "unknown"
    video_id = stable_video_id(url, title)
    uploader = info.get("uploader") or ""
    upload_date = info.get("upload_date")
    out_path = video_file_path(video_id, download_format)

    if _already_downloaded(video_id, download_format):
        logger.info("Already have %s (%s), skipping download", video_id, title[:50])
        manifest_path = video_manifest_path(video_id)
        with open(manifest_path, encoding="utf-8") as f:
            return VideoMeta.from_dict(json.load(f))

    # Download
    _run_yt_dlp([
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", str(out_path),
        "--no-playlist",
        url,
    ], timeout=900)

    if not out_path.exists():
        # yt-dlp might have used a different extension
        candidates = list(out_path.parent.glob(f"{out_path.stem}.*"))
        if not candidates:
            raise YtDlpError(f"Download did not produce file at {out_path}", command=[], stderr="", returncode=-1)
        out_path = candidates[0]

    meta = VideoMeta(
        video_id=video_id,
        original_url=url,
        title=title,
        uploader=uploader,
        duration_seconds=duration,
        upload_date=str(upload_date) if upload_date else None,
        filename=out_path.name,
        download_time=datetime.now(timezone.utc).isoformat(),
    )
    manifests_videos_dir().mkdir(parents=True, exist_ok=True)
    with open(video_manifest_path(video_id), "w", encoding="utf-8") as f:
        json.dump(meta.to_dict(), f, indent=2)
    return meta


def build_video_pool(
    queries: list[str],
    urls: list[str],
    results_per_query: int,
    max_videos_total: int,
    download_format: str,
    min_video_duration_seconds: float,
    max_video_duration_seconds: float,
    seed: int | None = None,
    dry_run: bool = False,
    limit_videos: int | None = None,
    limit_queries: int | None = None,
) -> list[VideoMeta]:
    """
    Build pool of source videos: search each query, add direct URLs, download (or skip if exists).
    Returns list of VideoMeta for downloaded/skipped videos. Idempotent.
    """
    videos_dir().mkdir(parents=True, exist_ok=True)
    manifests_videos_dir().mkdir(parents=True, exist_ok=True)

    all_urls: list[str] = []
    if limit_queries is not None:
        queries = queries[:limit_queries]
    for q in queries:
        try:
            found = _search_youtube(q, results_per_query)
            all_urls.extend(found)
        except (YtDlpError, json.JSONDecodeError) as e:
            logger.warning("Search failed for query %r: %s", q, e)
            continue

    # Add direct URLs (dedupe by normalized URL)
    seen = {u.rstrip("/") for u in all_urls}
    for u in urls:
        u = u.strip()
        if u and u.rstrip("/") not in seen:
            all_urls.append(u)
            seen.add(u.rstrip("/"))

    if seed is not None:
        import random
        rng = random.Random(seed)
        rng.shuffle(all_urls)

    max_total = limit_videos if limit_videos is not None else max_videos_total
    all_urls = all_urls[:max_total]

    if dry_run:
        logger.info("Dry run: would process %d URLs", len(all_urls))
        return []

    results: list[VideoMeta] = []
    for url in all_urls:
        try:
            info = _get_video_info(url)
            duration = float(info.get("duration") or 0)
            if duration < min_video_duration_seconds or duration > max_video_duration_seconds:
                logger.info("Skipping %s: duration %.0fs outside range", url, duration)
                continue
            title = info.get("title") or "unknown"
            video_id = stable_video_id(url, title)
            if _already_downloaded(video_id, download_format):
                mpath = video_manifest_path(video_id)
                with open(mpath, encoding="utf-8") as f:
                    results.append(VideoMeta.from_dict(json.load(f)))
                continue
            out_path = video_file_path(video_id, download_format)
            meta = _download_one(
                url,
                download_format=download_format,
                min_duration=min_video_duration_seconds,
                max_duration=max_video_duration_seconds,
            )
            if meta:
                results.append(meta)
        except (YtDlpError, json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to process %s: %s", url, e)
            continue

    return results
