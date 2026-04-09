"""
Score a video segment's audio energy using librosa.
Computes RMS energy, dynamic range, tempo, and silence ratio; returns a weighted composite 0-10.
"""
import logging
import os
import tempfile
from pathlib import Path

import librosa
import numpy as np

from src.media.ffmpeg import run_ffmpeg

logger = logging.getLogger(__name__)

# Weights for composite score (sum to 1.0)
_W_RMS = 0.30
_W_DYNAMIC = 0.25
_W_TEMPO = 0.25
_W_SILENCE = 0.20

_SILENCE_RMS_THRESHOLD = 0.01
_TEMPO_CAP_BPM = 180.0


def _normalize(value: float, low: float, high: float) -> float:
    """Linearly scale value from [low, high] into [0, 10], clamped."""
    if high <= low:
        return 0.0
    return max(0.0, min(10.0, (value - low) / (high - low) * 10.0))


def score_audio(video_path: Path, start_sec: float, end_sec: float) -> float:
    """
    Extract the audio segment [start_sec, end_sec] from video_path and return
    a composite audio-energy score (0-10) based on RMS energy, dynamic range,
    tempo, and silence ratio.

    Returns 0.0 on any extraction or analysis failure.
    """
    duration = end_sec - start_sec
    if duration <= 0:
        return 0.0

    tmp_dir = tempfile.mkdtemp(prefix="clipfarm_audio_")
    tmp_path = Path(tmp_dir) / "segment.wav"
    try:
        run_ffmpeg([
            "-ss", str(start_sec),
            "-i", str(video_path),
            "-t", str(duration),
            "-vn",
            "-ac", "1",
            "-ar", "22050",
            str(tmp_path),
        ], timeout=120)

        y, sr = librosa.load(str(tmp_path), sr=22050, mono=True)
        if len(y) == 0:
            logger.warning("Empty audio segment for %s [%.1f-%.1f]", video_path.name, start_sec, end_sec)
            return 0.0

        rms = librosa.feature.rms(y=y)[0]
        rms_mean = float(np.mean(rms))
        rms_dynamic = float(np.max(rms) - np.min(rms))

        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo).flat[0])

        silence_frames = np.sum(rms < _SILENCE_RMS_THRESHOLD)
        silence_ratio = float(silence_frames / max(len(rms), 1))

        rms_score = _normalize(rms_mean, 0.0, 0.3)
        dynamic_score = _normalize(rms_dynamic, 0.0, 0.4)
        tempo_score = _normalize(min(tempo_val, _TEMPO_CAP_BPM), 40.0, _TEMPO_CAP_BPM)
        silence_score = _normalize(1.0 - silence_ratio, 0.0, 1.0)

        composite = (
            rms_score * _W_RMS
            + dynamic_score * _W_DYNAMIC
            + tempo_score * _W_TEMPO
            + silence_score * _W_SILENCE
        )
        return round(max(0.0, min(10.0, composite)), 2)

    except Exception as e:
        logger.warning("Audio scoring failed for %s [%.1f-%.1f]: %s", video_path.name, start_sec, end_sec, e)
        return 0.0
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
