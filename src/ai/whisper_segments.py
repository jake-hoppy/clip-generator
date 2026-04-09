"""
Get timestamped segments from video/audio using mlx-whisper (local, free).
Uses Apple's MLX framework for native Apple Silicon GPU acceleration via Metal.
Returns list of {start_sec, end_sec, text, duration_seconds, words} with
optional merging into target length chunks and scene-boundary snapping.
"""
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Model to use. Recommended options:
# "mlx-community/whisper-large-v2-mlx"        — best accuracy, ~3GB
# "mlx-community/whisper-medium-mlx"           — good accuracy, faster, ~1.5GB
# "mlx-community/distil-whisper-large-v3"      — fastest, excellent for English
DEFAULT_MODEL = "mlx-community/distil-whisper-large-v3"


def _extract_audio(video_path: Path) -> tuple[Path, bool]:
    """
    Extract audio from video to a temp WAV file for transcription.
    mlx-whisper handles MP4 directly but WAV at 16kHz is most reliable.
    Returns (audio_path, should_delete).
    """
    from src.media.ffmpeg import run_ffmpeg
    fd, raw = tempfile.mkstemp(suffix=".wav", prefix="clipfarm_mlx_")
    os.close(fd)
    tmp_path = Path(raw)
    try:
        run_ffmpeg([
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y", str(tmp_path),
        ], timeout=600)
        return tmp_path, True
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def get_whisper_segments(
    video_path: Path,
    video_id: str,
    min_duration_sec: float = 12.0,
    max_duration_sec: float = 20.0,
    model: str = DEFAULT_MODEL,
    scene_boundaries: list[float] | None = None,
) -> list[dict]:
    """
    Transcribe video locally using mlx-whisper on Apple Silicon GPU.
    The `model` parameter accepts mlx-community HuggingFace repo IDs.
    Update config.yaml whisper_model to a full repo ID or short alias.

    Returns list of dicts: start_sec, end_sec, text, duration_seconds, words.
    Word timestamps are always populated.
    """
    if not video_path.exists():
        logger.warning("Video not found: %s", video_path)
        return []

    _MODEL_ALIASES = {
        "large-v2": "mlx-community/whisper-large-v2-mlx",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "distil-large-v3": "mlx-community/distil-whisper-large-v3",
    }
    resolved_model = _MODEL_ALIASES.get(model, model)

    audio_path, should_delete = _extract_audio(video_path)
    try:
        import mlx_whisper

        logger.info(
            "Transcribing %s with mlx-whisper (%s) on Apple Silicon GPU...",
            video_id, resolved_model,
        )
        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=resolved_model,
            word_timestamps=True,
            language="en",
        )
    finally:
        if should_delete and audio_path.exists():
            audio_path.unlink(missing_ok=True)

    raw_segments = result.get("segments") or []
    if not raw_segments:
        logger.warning("No segments from mlx-whisper for %s", video_id)
        return []

    logger.info(
        "Transcribed %s: %d raw segments detected",
        video_id, len(raw_segments),
    )

    all_words = _extract_words_mlx(raw_segments)
    logger.info(
        "mlx-whisper returned %d word timestamps for %s",
        len(all_words), video_id,
    )

    merged = _merge_segments_mlx(
        raw_segments,
        min_duration_sec=min_duration_sec,
        max_duration_sec=max_duration_sec,
        scene_boundaries=scene_boundaries,
    )

    out = []
    for start, end, text in merged:
        duration = round(end - start, 2)
        chunk_words = [
            w for w in all_words
            if w["start"] >= start - 0.05 and w["start"] < end + 0.05
        ]
        out.append({
            "start_sec": start,
            "end_sec": end,
            "text": text.strip() if text else "",
            "duration_seconds": duration,
            "words": chunk_words,
        })

    if out:
        logger.debug(
            "First merged segment has %d words (sample: %s)",
            len(out[0].get("words", [])),
            [w["word"] for w in out[0].get("words", [])[:5]],
        )
    return out


def _extract_words_mlx(segments: list[dict]) -> list[dict]:
    """
    Extract word-level timestamps from mlx-whisper segment dicts.
    mlx-whisper returns segments as plain dicts with a "words" key
    containing list of {"word", "start", "end", "probability"} dicts.
    """
    result = []
    for seg in segments:
        words = seg.get("words") or []
        for w in words:
            word = w.get("word", "").strip()
            start = float(w.get("start", 0))
            end = float(w.get("end", 0))
            if word:
                result.append({"word": word, "start": start, "end": end})
    return result


def _snap_to_scene(time_sec: float, boundaries: list[float], tolerance: float = 2.0) -> float:
    """Return the nearest scene boundary within ±tolerance, or time_sec unchanged."""
    best = time_sec
    best_dist = tolerance + 1.0
    for b in boundaries:
        dist = abs(b - time_sec)
        if dist < best_dist:
            best_dist = dist
            best = b
    return best if best_dist <= tolerance else time_sec


def _merge_segments_mlx(
    segments: list[dict],
    min_duration_sec: float,
    max_duration_sec: float,
    scene_boundaries: list[float] | None = None,
) -> list[tuple[float, float, str]]:
    """
    Merge mlx-whisper segment dicts into chunks using silence-gap detection.
    mlx-whisper returns plain dicts with "start", "end", "text", "words" keys.

    Cuts happen at natural speech pauses (silence gaps >= 0.4s between words)
    rather than arbitrary time boundaries, producing complete scene clips.

    Returns list of (start_sec, end_sec, combined_text).
    """
    if not segments:
        return []

    SILENCE_CUT_THRESHOLD = 0.4

    def _snap_end(t: float) -> float:
        return _snap_to_scene(t, scene_boundaries) if scene_boundaries else t

    flat_words: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        for w in words:
            word = w.get("word", "").strip()
            if word:
                flat_words.append({
                    "word": word,
                    "start": float(w.get("start", 0)),
                    "end": float(w.get("end", 0)),
                    "seg_end": float(seg.get("end", 0)),
                })

    if not flat_words:
        return _merge_by_segments_mlx(
            segments, min_duration_sec, max_duration_sec, scene_boundaries
        )

    cut_points: set[float] = set()
    for i in range(len(flat_words) - 1):
        gap = flat_words[i + 1]["start"] - flat_words[i]["end"]
        if gap >= SILENCE_CUT_THRESHOLD:
            cut_points.add(round(flat_words[i]["end"], 3))

    out: list[tuple[float, float, str]] = []
    chunk_start = float(segments[0]["start"])
    chunk_texts: list[str] = []
    last_good_cut: float | None = None
    last_good_cut_text: list[str] = []
    seg_end = chunk_start

    for seg in segments:
        seg_start = float(seg.get("start", 0))
        seg_end = float(seg.get("end", 0))
        seg_text = (seg.get("text") or "").strip()
        chunk_texts.append(seg_text)
        chunk_duration = seg_end - chunk_start

        is_silence_cut = any(abs(seg_end - cp) < 0.15 for cp in cut_points)

        if chunk_duration >= max_duration_sec:
            if last_good_cut is not None and last_good_cut > chunk_start:
                out.append((
                    chunk_start,
                    _snap_end(last_good_cut),
                    " ".join(last_good_cut_text),
                ))
                remaining = [
                    s for s in segments
                    if float(s.get("start", 0)) >= last_good_cut - 0.1
                    and float(s.get("start", 0)) > chunk_start + 0.1
                ]
                chunk_start = float(remaining[0]["start"]) if remaining else seg_start
                chunk_texts = [seg_text]
                last_good_cut = None
                last_good_cut_text = []
            else:
                out.append((chunk_start, _snap_end(seg_end), " ".join(chunk_texts)))
                chunk_start = seg_end
                chunk_texts = []
                last_good_cut = None
                last_good_cut_text = []
            continue

        if chunk_duration >= min_duration_sec and is_silence_cut:
            out.append((chunk_start, _snap_end(seg_end), " ".join(chunk_texts)))
            chunk_start = seg_end
            chunk_texts = []
            last_good_cut = None
            last_good_cut_text = []
            continue

        if is_silence_cut and chunk_duration >= min_duration_sec * 0.6:
            last_good_cut = seg_end
            last_good_cut_text = list(chunk_texts)

    if chunk_texts and seg_end > chunk_start:
        if seg_end - chunk_start >= min_duration_sec * 0.5:
            out.append((chunk_start, _snap_end(seg_end), " ".join(chunk_texts)))

    return out


def _merge_by_segments_mlx(
    segments: list[dict],
    min_duration_sec: float,
    max_duration_sec: float,
    scene_boundaries: list[float] | None = None,
) -> list[tuple[float, float, str]]:
    """
    Fallback merger when word timestamps are unavailable.
    Merges by segment (sentence) boundaries within duration constraints.
    """
    if not segments:
        return []

    def _snap_end(t: float) -> float:
        return _snap_to_scene(t, scene_boundaries) if scene_boundaries else t

    out: list[tuple[float, float, str]] = []
    chunk_start = float(segments[0].get("start", 0))
    chunk_end = float(segments[0].get("end", 0))
    chunk_text: list[str] = [(segments[0].get("text") or "").strip()]

    for s in segments[1:]:
        seg_end = float(s.get("end", 0))
        seg_text = (s.get("text") or "").strip()
        if not chunk_text:
            chunk_start = float(s.get("start", 0))
            chunk_end = seg_end
            chunk_text = [seg_text]
            continue
        would_be = seg_end - chunk_start
        if would_be > max_duration_sec:
            out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text)))
            chunk_start = float(s.get("start", 0))
            chunk_end = seg_end
            chunk_text = [seg_text]
        elif would_be >= min_duration_sec:
            chunk_end = seg_end
            chunk_text.append(seg_text)
            out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text)))
            chunk_text = []
        else:
            chunk_end = seg_end
            chunk_text.append(seg_text)

    if chunk_text and chunk_end > chunk_start:
        out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text)))
    return out
