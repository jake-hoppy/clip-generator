"""
YouTube search + download via yt-dlp (subprocess).
Supports search queries, direct URLs, and YouTube Trending (via Data API v3).
Idempotent (skips if file + manifest exist).
"""
import json
import logging
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _search_with_metadata(query: str, max_results: int) -> list[dict]:
    """Search YouTube, return [{url, title_hint, duration_hint}, ...] from flat playlist."""
    search_str = f"ytsearch{max_results}:{query}"
    out = _run_yt_dlp([
        "--dump-json",
        "--no-download",
        "--flat-playlist",
        search_str,
    ])
    results = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            vid = entry.get("id")
            if vid:
                results.append({
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title_hint": entry.get("title") or "",
                    "duration_hint": float(entry.get("duration") or 0),
                })
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return results


_DL_WORKERS = 4


def _already_downloaded(video_id: str, ext: str = "mp4") -> bool:
    """True if video file and manifest both exist."""
    vpath = video_file_path(video_id, ext)
    mpath = video_manifest_path(video_id)
    return vpath.exists() and mpath.exists()


def _title_passes_filter(title: str, required: list[str], blocked: list[str]) -> bool:
    """Return False if title contains blocked keywords or lacks required keywords."""
    title_lower = title.lower()
    if any(kw.lower() in title_lower for kw in blocked):
        return False
    if required and not any(kw.lower() in title_lower for kw in required):
        return False
    return True


_FORMAT_STRINGS = {
    "480p": "best[height<=480][ext=mp4]/best[height<=480]/worst[ext=mp4]/worst",
    "720p": "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best",
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
}


def _download_one(
    url: str,
    info: dict,
    download_format: str = "mp4",
    download_quality: str = "best",
) -> VideoMeta:
    """
    Download one video given its pre-fetched info dict.
    Raises on failure; caller is responsible for duration/title filtering.
    """
    title = info.get("title") or "unknown"
    duration = float(info.get("duration") or 0)
    video_id = stable_video_id(url, title)
    uploader = info.get("uploader") or ""
    upload_date = info.get("upload_date")
    out_path = video_file_path(video_id, download_format)

    if _already_downloaded(video_id, download_format):
        logger.info("Already have %s (%s), skipping download", video_id, title[:50])
        manifest_path = video_manifest_path(video_id)
        with open(manifest_path, encoding="utf-8") as f:
            return VideoMeta.from_dict(json.load(f))

    fmt_str = _FORMAT_STRINGS.get(download_quality, _FORMAT_STRINGS["best"])
    _run_yt_dlp([
        "-f", fmt_str,
        "-o", str(out_path),
        "--no-playlist",
        url,
    ], timeout=900)

    if not out_path.exists():
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


def get_trending_urls(
    region_code: str = "US",
    category_id: str = "0",
    max_results: int = 50,
) -> list[str]:
    """
    Fetch currently trending YouTube video URLs via the YouTube Data API v3.
    Requires the YOUTUBE_API_KEY environment variable to be set.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key or not api_key.strip():
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set. "
            "Get one at https://console.cloud.google.com/apis/credentials "
            "and enable the YouTube Data API v3."
        )

    from googleapiclient.discovery import build as build_service

    youtube = build_service("youtube", "v3", developerKey=api_key)
    request = youtube.videos().list(
        part="id",
        chart="mostPopular",
        regionCode=region_code,
        videoCategoryId=category_id,
        maxResults=min(max_results, 50),
    )
    response = request.execute()

    urls: list[str] = []
    for item in response.get("items", []):
        vid = item["id"]
        urls.append(f"https://www.youtube.com/watch?v={vid}")
    logger.info("Fetched %d trending video URLs (region=%s, category=%s)", len(urls), region_code, category_id)
    return urls


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
    title_required_keywords: list[str] | None = None,
    title_blocked_keywords: list[str] | None = None,
    download_quality: str = "best",
) -> list[VideoMeta]:
    """
    Build pool of source videos: search each query, add direct URLs, download (or skip if exists).
    Filters by title keywords and duration before downloading.
    Returns list of VideoMeta for downloaded/skipped videos. Idempotent.
    """
    videos_dir().mkdir(parents=True, exist_ok=True)
    manifests_videos_dir().mkdir(parents=True, exist_ok=True)

    req_kw = title_required_keywords or []
    blk_kw = title_blocked_keywords or []

    max_total = limit_videos if limit_videos is not None else max_videos_total
    fetch_per_query = min(50, max(results_per_query, max_total * 15))

    all_urls: list[str] = []
    if limit_queries is not None:
        queries = queries[:limit_queries]
    for q in queries:
        try:
            found = _search_with_metadata(q, fetch_per_query)
            for item in found:
                url = item["url"]
                title_hint = item.get("title_hint", "")
                dur_hint = item.get("duration_hint", 0)
                if title_hint and not _title_passes_filter(title_hint, req_kw, blk_kw):
                    logger.info("Skipping %s: title pre-filter (%s)", url, title_hint[:60])
                    continue
                if dur_hint > 0 and (dur_hint < min_video_duration_seconds or dur_hint > max_video_duration_seconds):
                    logger.info("Skipping %s: duration pre-filter %.0fs", url, dur_hint)
                    continue
                all_urls.append(url)
        except (YtDlpError, json.JSONDecodeError) as e:
            logger.warning("Search failed for query %r: %s", q, e)
            continue

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

    max_candidates = max(len(all_urls), max_total * 20)
    all_urls = all_urls[:max_candidates]

    if dry_run:
        logger.info("Dry run: would process up to %d URLs until %d videos", len(all_urls), max_total)
        return []

    results: list[VideoMeta] = []
    _results_lock = threading.Lock()

    def _process_url(url: str) -> VideoMeta | None:
        with _results_lock:
            if len(results) >= max_total:
                return None
        try:
            info = _get_video_info(url)
            title = info.get("title") or "unknown"

            if not _title_passes_filter(title, req_kw, blk_kw):
                logger.info("Skipping %s: title filter failed (%s)", url, title[:60])
                return None

            duration = float(info.get("duration") or 0)
            if duration < min_video_duration_seconds or duration > max_video_duration_seconds:
                logger.info("Skipping %s: duration %.0fs outside range", url, duration)
                return None

            video_id = stable_video_id(url, title)
            if _already_downloaded(video_id, download_format):
                mpath = video_manifest_path(video_id)
                with open(mpath, encoding="utf-8") as f:
                    return VideoMeta.from_dict(json.load(f))

            with _results_lock:
                if len(results) >= max_total:
                    return None
            return _download_one(
                url,
                info=info,
                download_format=download_format,
                download_quality=download_quality,
            )
        except (YtDlpError, json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to process %s: %s", url, e)
            return None

    pool = ThreadPoolExecutor(max_workers=_DL_WORKERS)
    futures = [pool.submit(_process_url, url) for url in all_urls]
    try:
        for fut in as_completed(futures):
            try:
                meta = fut.result()
            except Exception:
                continue
            if meta is not None:
                with _results_lock:
                    if len(results) < max_total:
                        results.append(meta)
                        if len(results) >= max_total:
                            break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    if len(results) < max_total:
        logger.warning(
            "Only found %d video(s) matching filters (target was %d). Try relaxing filters or adding more queries.",
            len(results),
            max_total,
        )

    return results
