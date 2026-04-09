"""
Get timestamped segments from video/audio using the OpenAI Whisper API.
Returns list of {start_sec, end_sec, text, duration_seconds} with optional merging into target length.
"""
import logging
import tempfile
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

# Whisper API file size limit (use audio extraction for large files)
WHISPER_MAX_FILE_BYTES = 24 * 1024 * 1024  # 24 MB


def _get_client() -> OpenAI:
    """OpenAI client using OPENAI_API_KEY from environment."""
    import os
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in your environment or in a .env file. "
            "Example: export OPENAI_API_KEY='sk-...'"
        )
    return OpenAI(api_key=key)


def _file_for_whisper(video_path: Path) -> tuple[Path, bool]:
    """
    Return (path, should_delete). If video is small enough, use it as-is.
    Otherwise extract audio to a temp file for the API (25 MB limit).
    """
    try:
        size = video_path.stat().st_size
    except OSError:
        return video_path, False
    if size <= WHISPER_MAX_FILE_BYTES:
        return video_path, False
    # Extract audio to temp file to stay under limit
    from src.media.ffmpeg import run_ffmpeg
    suffix = ".m4a"
    fd, raw = tempfile.mkstemp(suffix=suffix)
    import os
    os.close(fd)
    temp_path = Path(raw)
    try:
        run_ffmpeg([
            "-i", str(video_path),
            "-vn", "-acodec", "copy",
            "-y", str(temp_path),
        ], timeout=600)
        return temp_path, True
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def get_whisper_segments(
    video_path: Path,
    video_id: str,
    min_duration_sec: float = 12.0,
    max_duration_sec: float = 20.0,
    model: str = "whisper-1",
    scene_boundaries: list[float] | None = None,
) -> list[dict]:
    """
    Run Whisper API on the video (or extracted audio) and return segments.
    Merges consecutive Whisper segments so each output segment is between
    min_duration_sec and max_duration_sec (where possible).

    When *scene_boundaries* are provided, chunk break points are nudged to
    the nearest scene boundary within ±2 seconds, producing visually cleaner cuts.

    Returns list of dicts: start_sec, end_sec, text, duration_seconds.
    """
    if not video_path.exists():
        logger.warning("Video not found: %s", video_path)
        return []

    file_path, should_delete = _file_for_whisper(video_path)
    try:
        client = _get_client()
        with open(file_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model=model,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
    finally:
        if should_delete and file_path.exists():
            file_path.unlink(missing_ok=True)

    if not getattr(result, "segments", None):
        logger.warning("No segments in Whisper result for %s", video_id)
        return []

    all_words = _extract_words(result)
    logger.info("Whisper returned %d word-level timestamps for %s", len(all_words), video_id)

    merged = _merge_segments(
        result.segments,
        min_duration_sec=min_duration_sec,
        max_duration_sec=max_duration_sec,
        scene_boundaries=scene_boundaries,
    )
    out = []
    for start, end, text, _seg_words in merged:
        duration = round(end - start, 2)
        chunk_words = [w for w in all_words if w["start"] >= start - 0.05 and w["start"] < end + 0.05]
        out.append({
            "start_sec": start,
            "end_sec": end,
            "text": text.strip() if text else "",
            "duration_seconds": duration,
            "words": chunk_words,
        })
    if out:
        logger.debug("First merged segment has %d words (sample: %s)",
                      len(out[0].get("words", [])),
                      [w["word"] for w in out[0].get("words", [])[:5]])
    return out


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


def _extract_words(segment) -> list[dict]:
    """Convert Whisper API word objects to plain dicts for serialisation.
    Handles both object-style (with .word/.start/.end attrs) and dict-style entries.
    """
    raw = getattr(segment, "words", None) or []
    result = []
    for w in raw:
        if isinstance(w, dict):
            word = w.get("word", "").strip()
            start = float(w.get("start", 0))
            end = float(w.get("end", 0))
        else:
            word = getattr(w, "word", str(w)).strip()
            start = float(getattr(w, "start", 0))
            end = float(getattr(w, "end", 0))
        if word:
            result.append({"word": word, "start": start, "end": end})
    return result


def _merge_segments(
    segments: list,
    min_duration_sec: float,
    max_duration_sec: float,
    scene_boundaries: list[float] | None = None,
) -> list[tuple[float, float, str, list[dict]]]:
    """
    Merge consecutive Whisper segments into chunks between min and max duration.
    Chunks are emitted once they reach min_duration_sec; the segment that crosses
    the threshold is included in the emitted chunk so clips aren't cut short.
    When *scene_boundaries* are given, each chunk's end time is snapped to the
    nearest scene boundary within ±2 s.
    Returns list of (start_sec, end_sec, combined_text, words).
    """
    if not segments:
        return []

    out: list[tuple[float, float, str, list[dict]]] = []
    chunk_start = getattr(segments[0], "start", 0.0)
    chunk_end = getattr(segments[0], "end", 0.0)
    chunk_text: list[str] = [getattr(segments[0], "text", "") or ""]
    chunk_words: list[dict] = _extract_words(segments[0])

    def _snap_end(t: float) -> float:
        return _snap_to_scene(t, scene_boundaries) if scene_boundaries else t

    for s in segments[1:]:
        seg_start = getattr(s, "start", 0.0)
        seg_end = getattr(s, "end", 0.0)
        seg_text = getattr(s, "text", "") or ""
        seg_words = _extract_words(s)

        if not chunk_text:
            chunk_start = seg_start
            chunk_end = seg_end
            chunk_text = [seg_text]
            chunk_words = seg_words
            continue

        would_be = seg_end - chunk_start

        if would_be > max_duration_sec:
            out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text), chunk_words))
            chunk_start = seg_start
            chunk_end = seg_end
            chunk_text = [seg_text]
            chunk_words = seg_words
        elif would_be >= min_duration_sec:
            chunk_end = seg_end
            chunk_text.append(seg_text)
            chunk_words.extend(seg_words)
            out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text), chunk_words))
            chunk_text = []
            chunk_words = []
        else:
            chunk_end = seg_end
            chunk_text.append(seg_text)
            chunk_words.extend(seg_words)

    if chunk_text and chunk_end > chunk_start:
        out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text), chunk_words))

    return out
