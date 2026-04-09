"""
Get timestamped segments from video/audio using faster-whisper (local, free).
Runs on Apple Silicon via CoreML/Metal or CPU fallback on other hardware.
Returns list of {start_sec, end_sec, text, duration_seconds, words} with
optional merging into target length chunks and scene-boundary snapping.
"""
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL_SIZE = "large-v2"
DEFAULT_DEVICE = "auto"
DEFAULT_COMPUTE_TYPE = "float16"


@lru_cache(maxsize=1)
def _get_model(model_size: str, device: str, compute_type: str):
    """
    Load and cache the faster-whisper model.
    First call downloads the model (~1.5 GB for large-v2) and caches it locally.
    Subsequent calls reuse the loaded model.
    """
    from faster_whisper import WhisperModel

    resolved_device = device
    resolved_compute = compute_type
    if device == "auto":
        try:
            import torch
            if torch.backends.mps.is_available():
                resolved_device = "mps"
                resolved_compute = "float16"
            elif torch.cuda.is_available():
                resolved_device = "cuda"
                resolved_compute = "float16"
            else:
                resolved_device = "cpu"
                resolved_compute = "int8"
        except ImportError:
            resolved_device = "cpu"
            resolved_compute = "int8"

    logger.info(
        "Loading faster-whisper model '%s' on %s (%s) — first run downloads model",
        model_size, resolved_device, resolved_compute,
    )
    model = WhisperModel(
        model_size,
        device=resolved_device,
        compute_type=resolved_compute,
    )
    logger.info("faster-whisper model loaded successfully")
    return model


def _extract_audio(video_path: Path) -> tuple[Path, bool]:
    """
    Extract audio from video to a temp WAV file for transcription.
    Returns (audio_path, should_delete).
    """
    from src.media.ffmpeg import run_ffmpeg
    fd, raw = tempfile.mkstemp(suffix=".wav", prefix="clipfarm_fw_")
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
    model: str = DEFAULT_MODEL_SIZE,
    scene_boundaries: list[float] | None = None,
) -> list[dict]:
    """
    Transcribe video locally using faster-whisper and return merged segments.
    The ``model`` parameter accepts faster-whisper model sizes: tiny, base,
    small, medium, large-v2, large-v3.

    Returns list of dicts: start_sec, end_sec, text, duration_seconds, words.
    Word timestamps are always populated (faster-whisper natively supports them).
    """
    if not video_path.exists():
        logger.warning("Video not found: %s", video_path)
        return []

    audio_path, should_delete = _extract_audio(video_path)
    try:
        fw_model = _get_model(
            model_size=model,
            device=DEFAULT_DEVICE,
            compute_type=DEFAULT_COMPUTE_TYPE,
        )

        logger.info("Transcribing %s with faster-whisper (%s)...", video_id, model)
        segments_gen, info = fw_model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language="en",
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,
            ),
        )
        raw_segments = list(segments_gen)
        logger.info(
            "Transcribed %s: %.1fs audio, %d raw segments detected",
            video_id, info.duration, len(raw_segments),
        )
    finally:
        if should_delete and audio_path.exists():
            audio_path.unlink(missing_ok=True)

    if not raw_segments:
        logger.warning("No segments from faster-whisper for %s", video_id)
        return []

    all_words = _extract_words_fw(raw_segments)
    logger.info("faster-whisper returned %d word timestamps for %s", len(all_words), video_id)

    merged = _merge_segments_fw(
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


def _extract_words_fw(segments: list) -> list[dict]:
    """
    Extract word-level timestamps from faster-whisper segment objects.
    Each segment has a .words attribute with WordTiming objects
    (.word, .start, .end, .probability).
    """
    result = []
    for seg in segments:
        words = getattr(seg, "words", None) or []
        for w in words:
            word = getattr(w, "word", "").strip()
            start = float(getattr(w, "start", 0))
            end = float(getattr(w, "end", 0))
            if word:
                result.append({"word": word, "start": start, "end": end})
    return result


def _snap_to_scene(time_sec: float, boundaries: list[float], tolerance: float = 2.0) -> float:
    """Return the nearest scene boundary within +/-tolerance, or time_sec unchanged."""
    best = time_sec
    best_dist = tolerance + 1.0
    for b in boundaries:
        dist = abs(b - time_sec)
        if dist < best_dist:
            best_dist = dist
            best = b
    return best if best_dist <= tolerance else time_sec


def _merge_segments_fw(
    segments: list,
    min_duration_sec: float,
    max_duration_sec: float,
    scene_boundaries: list[float] | None = None,
) -> list[tuple[float, float, str]]:
    """
    Merge consecutive faster-whisper segments into chunks between min/max duration.
    Chunks are emitted once they reach min_duration_sec; the segment that crosses
    the threshold is included so clips aren't cut short.
    Returns list of (start_sec, end_sec, combined_text).
    """
    if not segments:
        return []

    def _snap_end(t: float) -> float:
        return _snap_to_scene(t, scene_boundaries) if scene_boundaries else t

    out: list[tuple[float, float, str]] = []
    chunk_start = float(segments[0].start)
    chunk_end = float(segments[0].end)
    chunk_text: list[str] = [(segments[0].text or "").strip()]

    for s in segments[1:]:
        seg_start = float(s.start)
        seg_end = float(s.end)
        seg_text = (s.text or "").strip()

        if not chunk_text:
            chunk_start = seg_start
            chunk_end = seg_end
            chunk_text = [seg_text]
            continue

        would_be = seg_end - chunk_start

        if would_be > max_duration_sec:
            out.append((chunk_start, _snap_end(chunk_end), " ".join(chunk_text)))
            chunk_start = seg_start
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
