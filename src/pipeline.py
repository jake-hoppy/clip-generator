"""
Orchestrates pipeline steps: download -> loud (top N loud moments globally).
Loads config from YAML; supports dry-run and limit overrides.
"""
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import yaml

from src.utils.paths import (
    ensure_data_dirs,
    data_root,
    videos_dir,
    candidates_dir,
    manifests_dir,
    manifests_videos_dir,
    manifests_candidates_dir,
    manifests_candidates_ranked_dir,
    outputs_ranked_dir,
)
from src.media.ffmpeg import require_ffmpeg, extract_clip
from src.youtube.search_download import build_video_pool, VideoMeta
from src.media.audio_peaks import get_loud_segments_for_video
from src.utils.paths import video_file_path

logger = logging.getLogger(__name__)


def load_config(config_path: Path) -> dict[str, Any]:
    """Load YAML config. Raises if file missing or invalid."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_download(
    config: dict[str, Any],
    dry_run: bool = False,
    limit_videos: int | None = None,
    limit_queries: int | None = None,
) -> list[VideoMeta]:
    """Download step only: build video pool from queries + urls."""
    ensure_data_dirs()
    return build_video_pool(
        queries=config.get("queries", []) or [],
        urls=config.get("urls", []) or [],
        results_per_query=int(config.get("results_per_query", 10)),
        max_videos_total=int(config.get("max_videos_total", 25)),
        download_format=config.get("download_format", "mp4"),
        min_video_duration_seconds=float(config.get("min_video_duration_seconds", 60)),
        max_video_duration_seconds=float(config.get("max_video_duration_seconds", 1800)),
        seed=config.get("seed"),
        dry_run=dry_run,
        limit_videos=limit_videos,
        limit_queries=limit_queries,
    )


def run_full(
    config: dict[str, Any],
    dry_run: bool = False,
    limit_videos: int | None = None,
    limit_queries: int | None = None,
) -> tuple[list[VideoMeta], list[dict]]:
    """Run full pipeline: download -> loud (top N loud moments across all videos)."""
    videos = run_download(config, dry_run=dry_run, limit_videos=limit_videos, limit_queries=limit_queries)
    loud_clips = run_loud(config, dry_run=dry_run)
    return videos, loud_clips


def _resolve_video_path(video_id: str) -> Path | None:
    """Return path to downloaded video file (any extension)."""
    p = video_file_path(video_id)
    if p.exists():
        return p
    for f in videos_dir().glob(f"{video_id}.*"):
        return f
    return None


def run_loud(
    config: dict[str, Any],
    dry_run: bool = False,
) -> list[dict]:
    """
    Detect loud moments per video (audio peak detection), rank all segments globally by loudness,
    take top N (config: top_n_loud_global), extract those clips to data/outputs/ranked/.
    Does not use fixed-window chunking or data/candidates/.
    """
    require_ffmpeg()
    ensure_data_dirs()
    clip_length = float(config.get("clip_length_seconds", 18))
    top_n = int(config.get("top_n_loud_global", 20))
    peaks_per_video = int(config.get("loud_peaks_per_video", 50))
    min_peak_distance = float(config.get("loud_min_peak_distance_seconds", 20))

    manifests_videos_dir().mkdir(parents=True, exist_ok=True)
    all_segments: list[dict] = []
    for mpath in manifests_videos_dir().glob("*.json"):
        video_id = mpath.stem
        video_path = _resolve_video_path(video_id)
        if not video_path:
            logger.warning("No video file for %s, skipping", video_id)
            continue
        try:
            segments = get_loud_segments_for_video(
                video_path,
                video_id,
                clip_length_sec=clip_length,
                peaks_per_video=peaks_per_video,
                min_peak_distance_sec=min_peak_distance,
            )
            for seg in segments:
                seg["_video_path"] = video_path
            all_segments.extend(segments)
        except Exception as e:
            logger.warning("Loud segments failed for %s: %s", video_id, e)
            continue

    all_segments.sort(key=lambda s: s["score"], reverse=True)
    top = all_segments[:top_n]
    if not top:
        logger.info("No loud segments found")
        return []

    if dry_run:
        logger.info("Would extract top %d loud clips to %s", len(top), outputs_ranked_dir())
        return top

    out_dir = outputs_ranked_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_list = []
    for i, seg in enumerate(top, start=1):
        video_path = seg.get("_video_path")
        if not video_path:
            continue
        start_sec = seg["start_sec"]
        end_sec = seg["end_sec"]
        duration_sec = end_sec - start_sec
        out_name = f"rank_{i:03d}_{seg['clip_id']}.mp4"
        out_path = out_dir / out_name
        try:
            extract_clip(video_path, out_path, start_sec, duration_sec, use_stream_copy=True)
        except Exception as e:
            logger.warning("Extract failed %s: %s", out_name, e)
            continue
        entry = {k: v for k, v in seg.items() if k != "_video_path"}
        entry["filepath"] = str(out_path)
        manifest_list.append(entry)

    manifest_path = out_dir / "top_loud_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"top_n": top_n, "clips": manifest_list}, f, indent=2)
    logger.info("Wrote %d loud clips to %s", len(manifest_list), out_dir)
    return manifest_list


def run_refresh(dry_run: bool = False) -> tuple[int, int]:
    """
    Delete all candidate clips and their manifests (candidates + candidates_ranked).
    Source videos in data/videos/ and data/manifests/videos/ are left intact.
    Returns (clips_dirs_removed, manifest_files_removed).
    """
    ensure_data_dirs()
    clips_removed = 0
    manifests_removed = 0

    # Remove each video's clip directory under data/candidates/
    cand_dir = candidates_dir()
    if cand_dir.exists():
        for sub in cand_dir.iterdir():
            if sub.is_dir():
                if dry_run:
                    logger.info("Would remove %s", sub)
                else:
                    shutil.rmtree(sub, ignore_errors=True)
                clips_removed += 1

    # Remove candidate manifests (data/manifests/candidates/*.json)
    mc_dir = manifests_candidates_dir()
    if mc_dir.exists():
        for f in mc_dir.glob("*.json"):
            if dry_run:
                manifests_removed += 1
                logger.info("Would remove %s", f)
            else:
                f.unlink(missing_ok=True)
                manifests_removed += 1

    # Remove ranked manifests (data/manifests/candidates_ranked/*.json)
    rank_dir = manifests_candidates_ranked_dir()
    if rank_dir.exists():
        for f in rank_dir.glob("*.json"):
            if dry_run:
                manifests_removed += 1
                logger.info("Would remove %s", f)
            else:
                f.unlink(missing_ok=True)
                manifests_removed += 1

    if not dry_run:
        logger.info("Refresh: removed %d clip dirs, %d manifest files", clips_removed, manifests_removed)
    return clips_removed, manifests_removed


def print_summary(videos: list[VideoMeta], dry_run: bool) -> None:
    """Print summary for download-only run."""
    print("\n" + "=" * 60)
    print("CLIP-FARM SUMMARY")
    print("=" * 60)
    if dry_run:
        print("(dry run — no files written)")
    print(f"  Videos in pool:     {len(videos)}")
    print(f"\n  Data root:          {data_root()}")
    print(f"  Videos:             {videos_dir()}")
    print("=" * 60)


def print_run_summary(
    videos: list[VideoMeta],
    loud_clips: list[dict],
    dry_run: bool,
) -> None:
    """Print summary for full run (download + loud)."""
    print("\n" + "=" * 60)
    print("CLIP-FARM SUMMARY (download + top loud moments)")
    print("=" * 60)
    if dry_run:
        print("(dry run — no files written)")
    print(f"  Videos in pool:     {len(videos)}")
    print(f"  Loud clips:         {len(loud_clips)} (top globally)")
    print(f"\n  Data root:          {data_root()}")
    print(f"  Videos:             {videos_dir()}")
    print(f"  Output (ranked):    {outputs_ranked_dir()}")
    print("=" * 60)
