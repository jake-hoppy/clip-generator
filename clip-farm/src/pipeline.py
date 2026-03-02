"""
Orchestrates pipeline steps: download -> chunk -> (optional) audio score.
Loads config from YAML; supports dry-run and limit overrides.
"""
import logging
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
)
from src.utils.logging_setup import setup_logging
from src.media.ffmpeg import require_ffmpeg
from src.youtube.search_download import build_video_pool, VideoMeta
from src.media.chunk import chunk_all_downloaded, ClipMeta
from src.media.audio_score import score_all_candidates

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


def run_chunk(
    config: dict[str, Any],
    dry_run: bool = False,
) -> list[ClipMeta]:
    """Chunk step only: segment all downloaded videos into candidate clips."""
    require_ffmpeg()
    ensure_data_dirs()
    return chunk_all_downloaded(
        clip_length_seconds=float(config.get("clip_length_seconds", 18)),
        clip_step_seconds=float(config.get("clip_step_seconds", 12)),
        allow_final_short_chunk=bool(config.get("allow_final_short_chunk", False)),
        dry_run=dry_run,
    )


def run_audio_score(
    config: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, list[dict]]:
    """Score and rank candidates (optional)."""
    if not config.get("enable_audio_scoring", False):
        return {}
    require_ffmpeg()
    ensure_data_dirs()
    return score_all_candidates(
        top_k_per_video=int(config.get("top_k_per_video", 5)),
        dry_run=dry_run,
    )


def run_full(
    config: dict[str, Any],
    dry_run: bool = False,
    limit_videos: int | None = None,
    limit_queries: int | None = None,
) -> tuple[list[VideoMeta], list[ClipMeta], dict[str, list[dict]]]:
    """Run full pipeline: download -> chunk -> (optional) audio score."""
    videos = run_download(config, dry_run=dry_run, limit_videos=limit_videos, limit_queries=limit_queries)
    clips = run_chunk(config, dry_run=dry_run)
    ranked = run_audio_score(config, dry_run=dry_run)
    return videos, clips, ranked


def print_summary(
    videos: list[VideoMeta],
    clips: list[ClipMeta],
    ranked: dict[str, list[dict]],
    dry_run: bool,
) -> None:
    """Print final summary to console."""
    print("\n" + "=" * 60)
    print("CLIP-FARM SUMMARY")
    print("=" * 60)
    if dry_run:
        print("(dry run — no files written)")
    print(f"  Videos in pool:     {len(videos)}")
    print(f"  Candidate clips:    {len(clips)}")
    if ranked:
        total_ranked = sum(len(v) for v in ranked.values())
        print(f"  Ranked (top-K):     {total_ranked} clips across {len(ranked)} videos")
    print(f"\n  Data root:          {data_root()}")
    print(f"  Videos:             {videos_dir()}")
    print(f"  Candidates:         {candidates_dir()}")
    print(f"  Manifests:          {manifests_dir()}")
    if ranked:
        print(f"  Ranked manifests:   {manifests_candidates_ranked_dir()}")
    print("=" * 60)
