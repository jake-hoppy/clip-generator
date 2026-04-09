"""
Orchestrates pipeline steps: download -> Whisper segments + OpenAI ranking.
Loads config from YAML; supports dry-run and limit overrides.
"""
import heapq
import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from src.utils.paths import (
    ensure_data_dirs,
    data_root,
    videos_dir,
    candidates_dir,
    candidates_dir_for_video,
    candidates_ranked_dir,
    manifests_dir,
    manifests_videos_dir,
    manifests_candidates_dir,
    manifests_candidates_ranked_dir,
)
from src.media.ffmpeg import require_ffmpeg, extract_clip
from src.youtube.search_download import build_video_pool, VideoMeta, get_trending_urls
from src.ai.whisper_segments import get_whisper_segments
from src.ai.openai_score import score_segment
from src.ai.audio_score import score_audio
from src.ai.visual_score import score_visual
from src.ai.scene_segments import get_scene_boundaries
from src.media.vertical_crop import crop_to_vertical
from src.utils.paths import video_file_path, outputs_dir

logger = logging.getLogger(__name__)

_SCORING_WORKERS = 8


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
    """Download step only: build video pool from queries + urls (+ optional trending)."""
    ensure_data_dirs()

    urls = list(config.get("urls", []) or [])

    if config.get("use_trending", False):
        try:
            trending = get_trending_urls(
                region_code=config.get("trending_region", "US"),
                category_id=str(config.get("trending_category_id", "0")),
                max_results=int(config.get("trending_max_results", 50)),
            )
            urls = trending + urls
            logger.info("Prepended %d trending URLs to pool", len(trending))
        except Exception as e:
            logger.warning("Trending fetch failed, continuing without: %s", e)

    return build_video_pool(
        queries=config.get("queries", []) or [],
        urls=urls,
        results_per_query=int(config.get("results_per_query", 10)),
        max_videos_total=int(config.get("max_videos_total", 25)),
        download_format=config.get("download_format", "mp4"),
        min_video_duration_seconds=float(config.get("min_video_duration_seconds", 60)),
        max_video_duration_seconds=float(config.get("max_video_duration_seconds", 1800)),
        seed=config.get("seed"),
        dry_run=dry_run,
        limit_videos=limit_videos,
        limit_queries=limit_queries,
        title_required_keywords=config.get("title_required_keywords") or [],
        title_blocked_keywords=config.get("title_blocked_keywords") or [],
        download_quality=config.get("download_quality", "best"),
    )


def run_full(
    config: dict[str, Any],
    dry_run: bool = False,
    limit_videos: int | None = None,
    limit_queries: int | None = None,
) -> tuple[list[VideoMeta], list[dict]]:
    """Run full pipeline: download -> Whisper segments + OpenAI ranking (top N globally)."""
    videos = run_download(config, dry_run=dry_run, limit_videos=limit_videos, limit_queries=limit_queries)
    ranked_clips = run_whisper_rank(config, dry_run=dry_run)
    return videos, ranked_clips


def _resolve_video_path(video_id: str) -> Path | None:
    """Return path to downloaded video file (any extension)."""
    p = video_file_path(video_id)
    if p.exists():
        return p
    for f in videos_dir().glob(f"{video_id}.*"):
        return f
    return None


def _compute_fused_weights(config: dict[str, Any]) -> dict[str, float]:
    """
    Return normalised weights for enabled scoring signals.
    Base weights: text=0.40, audio=0.35, visual=0.25.
    Disabled signals have their weight redistributed proportionally.
    """
    base = {
        "text": 0.40,
        "audio": 0.35,
        "visual": 0.25,
    }
    enabled = {}
    if config.get("scoring_text", True):
        enabled["text"] = base["text"]
    if config.get("scoring_audio", True):
        enabled["audio"] = base["audio"]
    if config.get("scoring_visual", False):
        enabled["visual"] = base["visual"]
    if not enabled:
        return {"text": 1.0, "audio": 0.0, "visual": 0.0}
    total = sum(enabled.values())
    return {k: enabled.get(k, 0.0) / total for k in base}


def run_whisper_rank(
    config: dict[str, Any],
    dry_run: bool = False,
) -> list[dict]:
    """
    Get segments from Whisper API per video, score each with multi-signal
    fusion (text + audio + visual), rank globally.
    Extract all segments to data/candidates/<video_id>/, write manifests.
    Take top N by fused score; copy to data/candidates_ranked/.
    Optionally export vertical (9:16) crops to data/outputs/.
    """
    require_ffmpeg()
    ensure_data_dirs()
    top_n = int(config.get("top_n_global", 20))
    min_duration = float(config.get("segment_min_duration_seconds", 12.0))
    max_duration = float(config.get("segment_max_duration_seconds", 20.0))
    whisper_model = config.get("whisper_model", "whisper-1")
    openai_model = config.get("openai_model", "gpt-4o-mini")
    openai_prompt = config.get("openai_prompt")

    use_scene = config.get("use_scene_detection", True)
    weights = _compute_fused_weights(config)
    do_text = config.get("scoring_text", True)
    do_audio = config.get("scoring_audio", True)
    do_visual = config.get("scoring_visual", False)

    manifests_videos_dir().mkdir(parents=True, exist_ok=True)
    manifests_candidates_dir().mkdir(parents=True, exist_ok=True)
    all_segments: list[dict] = []

    for mpath in manifests_videos_dir().glob("*.json"):
        video_id = mpath.stem
        video_path = _resolve_video_path(video_id)
        if not video_path:
            logger.warning("No video file for %s, skipping", video_id)
            continue
        try:
            with open(mpath, encoding="utf-8") as f:
                video_meta = json.load(f)
            video_title = video_meta.get("title") or ""
        except Exception:
            video_title = ""

        # Scene detection (optional)
        scene_boundaries: list[float] | None = None
        if use_scene:
            try:
                scene_boundaries = get_scene_boundaries(video_path)
                logger.info("Detected %d scene boundaries for %s", len(scene_boundaries), video_id)
            except Exception as e:
                logger.warning("Scene detection failed for %s, proceeding without: %s", video_id, e)

        try:
            raw_segments = get_whisper_segments(
                video_path,
                video_id,
                min_duration_sec=min_duration,
                max_duration_sec=max_duration,
                model=whisper_model,
                scene_boundaries=scene_boundaries,
            )
        except Exception as e:
            logger.warning("Whisper failed for %s: %s", video_id, e)
            continue

        this_video_manifest_clips: list[dict] = []
        _seg_lock = threading.Lock()

        def _score_segment(seg: dict) -> None:
            start_ms = int(seg["start_sec"] * 1000)
            end_ms = int(seg["end_sec"] * 1000)
            clip_id = f"{video_id}_t{start_ms}_{end_ms}"
            seg["video_id"] = video_id
            seg["clip_id"] = clip_id
            seg["_video_path"] = video_path
            seg["_video_title"] = video_title
            cand_path = candidates_dir_for_video(video_id) / f"{clip_id}.mp4"
            seg["filepath"] = str(cand_path)

            if dry_run:
                seg["text_score"] = 0.0
                seg["audio_score"] = 0.0
                seg["visual_score"] = 0.0
                seg["score"] = 0.0
                with _seg_lock:
                    all_segments.append(seg)
                return

            text_score = 0.0
            if do_text:
                try:
                    text_score = score_segment(
                        seg["text"],
                        video_title=video_title or None,
                        model=openai_model,
                        prompt_override=openai_prompt,
                    )
                except Exception as e:
                    logger.warning("Text scoring failed for %s: %s", clip_id, e)

            audio_score_val = 0.0
            if do_audio:
                try:
                    audio_score_val = score_audio(video_path, seg["start_sec"], seg["end_sec"])
                except Exception as e:
                    logger.warning("Audio scoring failed for %s: %s", clip_id, e)

            visual_score_val = 0.0
            if do_visual:
                try:
                    visual_score_val = score_visual(video_path, seg["start_sec"], seg["end_sec"])
                except Exception as e:
                    logger.warning("Visual scoring failed for %s: %s", clip_id, e)

            fused = (
                text_score * weights["text"]
                + audio_score_val * weights["audio"]
                + visual_score_val * weights["visual"]
            )
            seg["text_score"] = round(text_score, 2)
            seg["audio_score"] = round(audio_score_val, 2)
            seg["visual_score"] = round(visual_score_val, 2)
            seg["score"] = round(fused, 2)

            cand_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                extract_clip(
                    video_path,
                    cand_path,
                    seg["start_sec"],
                    seg["duration_seconds"],
                    use_stream_copy=True,
                )
            except Exception as e:
                logger.warning("Extract failed %s: %s", clip_id, e)
                return

            manifest_entry = {k: v for k, v in seg.items() if k not in ("_video_path", "_video_title")}
            with _seg_lock:
                this_video_manifest_clips.append(manifest_entry)
                all_segments.append(seg)

        with ThreadPoolExecutor(max_workers=_SCORING_WORKERS) as pool:
            futures = [pool.submit(_score_segment, seg) for seg in raw_segments]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    logger.warning("Segment scoring worker failed: %s", e)

        if not dry_run and this_video_manifest_clips:
            manifest_data = {
                "video_id": video_id,
                "clips": this_video_manifest_clips,
            }
            manifest_path = manifests_candidates_dir() / f"{video_id}.json"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)

    top = heapq.nlargest(top_n, all_segments, key=lambda s: s["score"])
    if not top:
        logger.info("No segments found")
        return []

    if dry_run:
        logger.info("Would write candidates to %s, top %d to %s", candidates_dir(), len(top), candidates_ranked_dir())
        return top

    rank_dir = candidates_ranked_dir()
    rank_dir.mkdir(parents=True, exist_ok=True)
    manifest_list = []
    for i, seg in enumerate(top, start=1):
        src = Path(seg["filepath"])
        if not src.exists():
            continue
        out_name = f"rank_{i:03d}_{seg['clip_id']}.mp4"
        out_path = rank_dir / out_name
        try:
            shutil.copy2(src, out_path)
        except Exception as e:
            logger.warning("Copy failed %s: %s", out_name, e)
            continue
        entry = {k: v for k, v in seg.items() if k not in ("_video_path", "_video_title")}
        entry["filepath"] = str(out_path)
        manifest_list.append(entry)

    manifest_path = manifests_candidates_ranked_dir() / "top_ranked_manifest.json"
    manifests_candidates_ranked_dir().mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"top_n": top_n, "clips": manifest_list}, f, indent=2)
    logger.info("Wrote candidates to %s, top %d ranked to %s", candidates_dir(), len(manifest_list), rank_dir)

    # --- Vertical export (optional) ---
    if config.get("export_vertical", False):
        _run_vertical_export(manifest_list)

    return manifest_list


def _run_vertical_export(ranked_clips: list[dict]) -> None:
    """Crop each ranked clip to 9:16, burn subtitles, and save to data/outputs/."""
    out_dir = outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    exported = 0
    for entry in ranked_clips:
        src = Path(entry["filepath"])
        if not src.exists():
            continue
        dst = out_dir / src.name

        clip_duration = entry.get("duration_seconds") or (entry["end_sec"] - entry["start_sec"])
        clip_start = float(entry.get("start_sec", 0))
        raw_words = entry.get("words") or []
        adjusted_words = [
            {"word": w["word"], "start": max(0.0, w["start"] - clip_start), "end": max(0.0, w["end"] - clip_start)}
            for w in raw_words
        ]
        segments = [{
            "start_sec": 0.0,
            "end_sec": clip_duration,
            "text": entry.get("text", ""),
            "words": adjusted_words,
        }]

        try:
            crop_to_vertical(src, dst, segments=segments)
            exported += 1
        except Exception as e:
            logger.warning("Vertical crop/subtitle failed for %s: %s", src.name, e)
    logger.info("Exported %d vertical clips with subtitles to %s", exported, out_dir)


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
    rank_manifest_dir = manifests_candidates_ranked_dir()
    if rank_manifest_dir.exists():
        for f in rank_manifest_dir.glob("*.json"):
            if dry_run:
                manifests_removed += 1
                logger.info("Would remove %s", f)
            else:
                f.unlink(missing_ok=True)
                manifests_removed += 1

    # Remove ranked clip files (data/candidates_ranked/*.mp4)
    rank_clips_dir = candidates_ranked_dir()
    if rank_clips_dir.exists():
        for f in rank_clips_dir.glob("*.mp4"):
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
    ranked_clips: list[dict],
    dry_run: bool,
) -> None:
    """Print summary for full run (download + Whisper + OpenAI ranking)."""
    print("\n" + "=" * 60)
    print("CLIP-FARM SUMMARY (download + Whisper segments + OpenAI ranking)")
    print("=" * 60)
    if dry_run:
        print("(dry run — no files written)")
    print(f"  Videos in pool:     {len(videos)}")
    print(f"  Ranked clips:       {len(ranked_clips)} (top globally)")
    print(f"\n  Data root:          {data_root()}")
    print(f"  Videos:             {videos_dir()}")
    print(f"  Candidates:        {candidates_dir()}")
    print(f"  Candidates (ranked): {candidates_ranked_dir()}")
    print("=" * 60)
