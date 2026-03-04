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
) -> list[dict]:
    """
    Run Whisper API on the video (or extracted audio) and return segments.
    Merges consecutive Whisper segments so each output segment is between
    min_duration_sec and max_duration_sec (where possible).
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
            )
    finally:
        if should_delete and file_path.exists():
            file_path.unlink(missing_ok=True)

    if not getattr(result, "segments", None):
        logger.warning("No segments in Whisper result for %s", video_id)
        return []

    # Merge segments into chunks of target duration
    merged = _merge_segments(
        result.segments,
        min_duration_sec=min_duration_sec,
        max_duration_sec=max_duration_sec,
    )
    out = []
    for start, end, text in merged:
        duration = round(end - start, 2)
        out.append({
            "start_sec": start,
            "end_sec": end,
            "text": text.strip() if text else "",
            "duration_seconds": duration,
        })
    return out


def _merge_segments(
    segments: list,
    min_duration_sec: float,
    max_duration_sec: float,
) -> list[tuple[float, float, str]]:
    """
    Merge consecutive segments so each chunk is between min and max duration.
    Returns list of (start_sec, end_sec, combined_text).
    """
    if not segments:
        return []
    out: list[tuple[float, float, str]] = []
    chunk_start = getattr(segments[0], "start", 0.0)
    chunk_end = getattr(segments[0], "end", 0.0)
    chunk_text = [getattr(segments[0], "text", "") or ""]

    for s in segments[1:]:
        start = getattr(s, "start", 0.0)
        end = getattr(s, "end", 0.0)
        text = getattr(s, "text", "") or ""
        candidate_end = end
        candidate_duration = candidate_end - chunk_start

        if candidate_duration >= max_duration_sec:
            # Emit current chunk
            out.append((chunk_start, chunk_end, " ".join(chunk_text)))
            chunk_start = start
            chunk_end = end
            chunk_text = [text]
        elif candidate_duration >= min_duration_sec and (end - chunk_end) > 0.5:
            # Could emit now; emit and start new
            out.append((chunk_start, chunk_end, " ".join(chunk_text)))
            chunk_start = start
            chunk_end = end
            chunk_text = [text]
        else:
            # Extend current chunk
            chunk_end = end
            chunk_text.append(text)

    if chunk_end > chunk_start:
        out.append((chunk_start, chunk_end, " ".join(chunk_text)))
    return out
