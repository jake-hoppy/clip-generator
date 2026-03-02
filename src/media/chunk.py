"""
Chunk source videos into fixed-length candidate clips.
Uses ffmpeg (stream copy when possible). Writes manifest per video.
"""
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from src.utils.paths import (
    video_file_path,
    video_manifest_path,
    candidates_dir_for_video,
    candidate_clip_path,
    candidates_manifest_path,
    manifests_candidates_dir,
)
from src.media.ffmpeg import get_duration_seconds, extract_clip, require_ffmpeg, FFmpegError

logger = logging.getLogger(__name__)


@dataclass
class ClipMeta:
    """Metadata for one candidate clip."""

    clip_id: str
    video_id: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    filepath: str
    audio_score: float | None = None  # Optional; set by audio_score module

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ClipMeta":
        return cls(
            clip_id=d["clip_id"],
            video_id=d["video_id"],
            start_seconds=float(d["start_seconds"]),
            end_seconds=float(d["end_seconds"]),
            duration_seconds=float(d["duration_seconds"]),
            filepath=d["filepath"],
            audio_score=float(d["audio_score"]) if d.get("audio_score") is not None else None,
        )


def _clip_segments(
    duration_seconds: float,
    clip_length_seconds: float,
    clip_step_seconds: float,
    allow_final_short_chunk: bool,
) -> list[tuple[float, float]]:
    """
    Return list of (start_sec, end_sec) for each segment.
    end_sec - start_sec == clip_length_seconds (or shorter for final if allowed).
    """
    if clip_length_seconds <= 0 or clip_step_seconds <= 0:
        return []
    segments = []
    start = 0.0
    while start < duration_seconds:
        end = min(start + clip_length_seconds, duration_seconds)
        seg_dur = end - start
        if seg_dur < clip_length_seconds and not allow_final_short_chunk:
            break
        segments.append((start, end))
        start += clip_step_seconds
    return segments


def chunk_video(
    video_id: str,
    clip_length_seconds: float,
    clip_step_seconds: float,
    allow_final_short_chunk: bool = False,
    dry_run: bool = False,
) -> list[ClipMeta]:
    """
    Chunk one source video into candidate clips. Idempotent: if manifest exists
    and all clip files exist, returns loaded manifest (no re-chunk).
    """
    require_ffmpeg()
    video_path = video_file_path(video_id)
    if not video_path.exists():
        # Try without extension
        for p in video_path.parent.glob(f"{video_id}.*"):
            video_path = p
            break
        if not video_path.exists():
            logger.warning("Video file not found for %s", video_id)
            return []

    manifest_path = candidates_manifest_path(video_id)
    candidates_dir = candidates_dir_for_video(video_id)

    if manifest_path.exists() and not dry_run:
        # Check idempotency: all clips on disk?
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        clips = [ClipMeta.from_dict(c) for c in data.get("clips", [])]
        if clips and all(Path(c.filepath).exists() for c in clips):
            logger.info("Candidates already exist for %s (%d clips), skipping", video_id, len(clips))
            return clips

    duration = get_duration_seconds(video_path)
    segments = _clip_segments(
        duration,
        clip_length_seconds,
        clip_step_seconds,
        allow_final_short_chunk,
    )
    if not segments:
        logger.info("No segments for %s (duration %.1fs)", video_id, duration)
        return []

    clips: list[ClipMeta] = []
    for start_sec, end_sec in segments:
        start_ms = int(start_sec * 1000)
        end_ms = int(end_sec * 1000)
        clip_id = f"{video_id}_t{start_ms}_{end_ms}"
        out_path = candidate_clip_path(video_id, start_ms, end_ms)

        if dry_run:
            clips.append(ClipMeta(
                clip_id=clip_id,
                video_id=video_id,
                start_seconds=start_sec,
                end_seconds=end_sec,
                duration_seconds=end_sec - start_sec,
                filepath=str(out_path),
            ))
            continue

        candidates_dir.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            # Already have this clip
            clips.append(ClipMeta(
                clip_id=clip_id,
                video_id=video_id,
                start_seconds=start_sec,
                end_seconds=end_sec,
                duration_seconds=end_sec - start_sec,
                filepath=str(out_path),
            ))
            continue

        try:
            extract_clip(
                video_path,
                out_path,
                start_sec,
                end_sec - start_sec,
                use_stream_copy=True,
            )
        except FFmpegError as e:
            logger.warning("FFmpeg failed for clip %s: %s", clip_id, e)
            continue

        if not out_path.exists() or out_path.stat().st_size == 0:
            logger.warning("Clip produced empty file %s", out_path)
            continue

        clips.append(ClipMeta(
            clip_id=clip_id,
            video_id=video_id,
            start_seconds=start_sec,
            end_seconds=end_sec,
            duration_seconds=end_sec - start_sec,
            filepath=str(out_path),
        ))

    if not dry_run and clips:
        manifests_candidates_dir().mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "video_id": video_id,
                "clip_length_seconds": clip_length_seconds,
                "clip_step_seconds": clip_step_seconds,
                "clips": [c.to_dict() for c in clips],
            }, f, indent=2)

    return clips


def chunk_all_downloaded(
    clip_length_seconds: float,
    clip_step_seconds: float,
    allow_final_short_chunk: bool = False,
    dry_run: bool = False,
) -> list[ClipMeta]:
    """
    Chunk every video that has a manifest in data/manifests/videos/.
    Returns all ClipMeta from all videos.
    """
    from src.utils.paths import manifests_videos_dir

    manifests_videos_dir().mkdir(parents=True, exist_ok=True)
    video_manifests = list(manifests_videos_dir().glob("*.json"))
    all_clips: list[ClipMeta] = []
    for mpath in video_manifests:
        video_id = mpath.stem
        clips = chunk_video(
            video_id,
            clip_length_seconds,
            clip_step_seconds,
            allow_final_short_chunk=allow_final_short_chunk,
            dry_run=dry_run,
        )
        all_clips.extend(clips)
    return all_clips
