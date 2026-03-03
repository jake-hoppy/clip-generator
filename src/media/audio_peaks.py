"""
Detect loud moments in a video by computing audio RMS over time and finding peaks.
Used to create clips centered on the loudest moments (instead of fixed sliding windows).
"""
import logging
import math
import struct
import subprocess
from pathlib import Path

from src.media.ffmpeg import require_ffmpeg, get_duration_seconds, extract_clip, FFmpegError

logger = logging.getLogger(__name__)

FFMPEG_CMD = "ffmpeg"
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # s16le


def get_audio_rms_series(
    video_path: Path,
    window_sec: float = 0.5,
    sample_rate: int = SAMPLE_RATE,
    timeout: int = 600,
) -> list[tuple[float, float]]:
    """
    Extract audio from video and return [(time_sec, rms), ...] at each window.
    One pass over the file; streams in chunks to avoid loading full audio into memory.
    """
    require_ffmpeg()
    samples_per_window = int(window_sec * sample_rate)
    bytes_per_window = samples_per_window * BYTES_PER_SAMPLE

    cmd = [
        FFMPEG_CMD, "-i", str(video_path),
        "-vn", "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-loglevel", "error", "-"
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise FFmpegError("ffmpeg not found", command=cmd, stderr="", returncode=-1)

    series: list[tuple[float, float]] = []
    t = 0.0
    while True:
        chunk = proc.stdout.read(bytes_per_window)
        if not chunk or len(chunk) < BYTES_PER_SAMPLE:
            break
        n = len(chunk) // BYTES_PER_SAMPLE
        samples = struct.unpack(f"<{n}h", chunk[: n * BYTES_PER_SAMPLE])
        rms = 0.0
        for s in samples:
            rms += s * s
        rms = math.sqrt(rms / n) if n else 0.0
        series.append((t, rms))
        t += window_sec
    proc.wait(timeout=1)
    return series


def find_peaks(
    rms_series: list[tuple[float, float]],
    min_distance_sec: float,
    top_n: int,
    duration_sec: float,
) -> list[tuple[float, float]]:
    """
    Find local maxima in the RMS series, at least min_distance_sec apart.
    Returns list of (peak_time_sec, peak_rms) sorted by rms descending, length <= top_n.
    """
    if len(rms_series) < 3:
        return []
    window_sec = rms_series[1][0] - rms_series[0][0] if len(rms_series) > 1 else 0.5
    min_idx_distance = max(1, int(min_distance_sec / window_sec))

    peaks: list[tuple[float, float]] = []
    for i in range(1, len(rms_series) - 1):
        t, r = rms_series[i]
        if r <= 0:
            continue
        if rms_series[i - 1][1] >= r or rms_series[i + 1][1] >= r:
            continue
        # local max; check distance from last peak
        if peaks and (t - peaks[-1][0]) < min_distance_sec:
            if r > peaks[-1][1]:
                peaks[-1] = (t, r)
            continue
        peaks.append((t, r))

    peaks.sort(key=lambda x: x[1], reverse=True)
    return peaks[:top_n]


def segments_around_peaks(
    peaks: list[tuple[float, float]],
    duration_sec: float,
    clip_length_sec: float,
) -> list[tuple[float, float, float]]:
    """
    For each (peak_time, peak_rms), create segment (start, end) centered on peak, clamped to [0, duration].
    Returns list of (start_sec, end_sec, score_rms).
    """
    half = clip_length_sec / 2.0
    out: list[tuple[float, float, float]] = []
    for t, rms in peaks:
        start = max(0.0, t - half)
        end = min(duration_sec, start + clip_length_sec)
        start = max(0.0, end - clip_length_sec)
        if end - start < 1.0:
            continue
        out.append((start, end, rms))
    return out


def get_loud_segments_for_video(
    video_path: Path,
    video_id: str,
    clip_length_sec: float = 18.0,
    peaks_per_video: int = 50,
    min_peak_distance_sec: float = 20.0,
    window_sec: float = 0.5,
) -> list[dict]:
    """
    Compute RMS over time, find peaks, return list of segment dicts:
    {video_id, start_sec, end_sec, score, clip_id, filepath (None until extracted)}.
    """
    require_ffmpeg()
    if not video_path.exists():
        logger.warning("Video not found: %s", video_path)
        return []

    duration = get_duration_seconds(video_path)
    rms_series = get_audio_rms_series(video_path, window_sec=window_sec)
    if not rms_series:
        logger.warning("No audio RMS for %s", video_id)
        return []

    peaks = find_peaks(rms_series, min_peak_distance_sec, peaks_per_video, duration)
    segments = segments_around_peaks(peaks, duration, clip_length_sec)

    result = []
    for start, end, score in segments:
        start_ms = int(start * 1000)
        end_ms = int(end * 1000)
        clip_id = f"{video_id}_t{start_ms}_{end_ms}"
        result.append({
            "video_id": video_id,
            "start_sec": start,
            "end_sec": end,
            "score": round(score, 2),
            "clip_id": clip_id,
            "duration_seconds": round(end - start, 2),
        })
    return result
