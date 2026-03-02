"""
Optional: compute simple audio energy/loudness per candidate clip using ffmpeg.
Rank clips and optionally write top-K manifest (data/manifests/candidates_ranked/).
"""
import json
import logging
import re
from pathlib import Path

from src.utils.paths import (
    candidates_manifest_path,
    candidates_ranked_manifest_path,
    manifests_candidates_ranked_dir,
)
from src.media.ffmpeg import require_ffmpeg

logger = logging.getLogger(__name__)


def _parse_volumedetect(stderr: str) -> float:
    """
    Run ffmpeg with volumedetect filter; parse mean_volume from stderr.
    We need to actually run ffmpeg to get volumedetect output (it goes to stderr).
    """
    # mean_volume: -20.0 dB
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", stderr)
    if m:
        return float(m.group(1))
    return -99.0  # fallback quiet


def get_audio_energy_volumedetect(clip_path: Path) -> float:
    """
    Use ffmpeg volumedetect to get mean volume in dB.
    Higher (less negative) = louder. Returns -99 if parsing fails.
    """
    # volumedetect outputs to stderr; we need to run without -loglevel error to see it
    import subprocess
    cmd = [
        "ffmpeg", "-i", str(clip_path),
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return _parse_volumedetect(result.stderr or "")
    except Exception as e:
        logger.debug("volumedetect failed for %s: %s", clip_path, e)
        return -99.0


def score_clip(clip_path: Path) -> float:
    """
    Return a single scalar "energy" score for the clip (higher = louder/more energy).
    Uses volumedetect mean_volume; normalizes to positive scale.
    """
    import subprocess
    cmd = [
        "ffmpeg", "-i", str(clip_path),
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        db = _parse_volumedetect(result.stderr or "")
        return db + 60.0  # -60..0 dB -> 0..60 score
    except Exception:
        return 0.0


def score_candidates_for_video(
    video_id: str,
    top_k: int = 5,
    dry_run: bool = False,
) -> list[dict]:
    """
    Load candidate manifest for video_id, score each clip, sort by score descending.
    Write data/manifests/candidates_ranked/<video_id>.json with top K and scores.
    Returns list of clip dicts with audio_score, sorted by score (best first).
    """
    require_ffmpeg()
    manifest_path = candidates_manifest_path(video_id)
    if not manifest_path.exists():
        logger.warning("No candidates manifest for %s", video_id)
        return []

    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    clips = data.get("clips", [])
    if not clips:
        return []

    scored = []
    for c in clips:
        fp = c.get("filepath")
        if not fp:
            continue
        path = Path(fp)
        if not path.exists():
            continue
        s = score_clip(path)
        scored.append({**c, "audio_score": round(s, 2)})

    scored.sort(key=lambda x: x.get("audio_score", 0), reverse=True)
    top = scored[:top_k]

    if dry_run:
        return top

    # Store score in candidate manifest (update existing manifest with audio_score per clip)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)
    score_by_id = {c["clip_id"]: c.get("audio_score") for c in scored}
    for clip in manifest_data.get("clips", []):
        clip["audio_score"] = score_by_id.get(clip["clip_id"])
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    manifests_candidates_ranked_dir().mkdir(parents=True, exist_ok=True)
    out_path = candidates_ranked_manifest_path(video_id)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "video_id": video_id,
            "top_k": top_k,
            "clips_ranked": top,
            "all_scores": [{"clip_id": c.get("clip_id"), "audio_score": c.get("audio_score")} for c in scored],
        }, f, indent=2)
    logger.info("Wrote ranked manifest for %s (top %d)", video_id, len(top))
    return top


def score_all_candidates(
    top_k_per_video: int = 5,
    dry_run: bool = False,
) -> dict[str, list[dict]]:
    """
    For each video that has a candidates manifest, score and rank clips.
    Returns dict video_id -> list of top-K clip dicts with audio_score.
    """
    from src.utils.paths import manifests_candidates_dir

    manifests_candidates_dir().mkdir(parents=True, exist_ok=True)
    results = {}
    for mpath in manifests_candidates_dir().glob("*.json"):
        video_id = mpath.stem
        results[video_id] = score_candidates_for_video(video_id, top_k=top_k_per_video, dry_run=dry_run)
    return results
