"""
Smart vertical (9:16) crop for landscape video clips.
Uses ffmpeg to centre-crop landscape sources; portrait clips are passed through unchanged.
"""
import json
import logging
from pathlib import Path

from src.media.ffmpeg import run_ffmpeg, run_ffprobe

logger = logging.getLogger(__name__)


def _get_dimensions(input_path: Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream via ffprobe."""
    out = run_ffprobe([
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(input_path),
    ])
    data = json.loads(out)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"])


def crop_to_vertical(input_path: Path, output_path: Path) -> None:
    """
    If the clip is landscape (width > height), centre-crop to 9:16 and
    re-encode to output_path.  Portrait clips are copied without re-encoding.
    """
    width, height = _get_dimensions(input_path)

    if width <= height:
        logger.info("Already portrait (%dx%d), copying %s", width, height, input_path.name)
        run_ffmpeg([
            "-i", str(input_path),
            "-c", "copy",
            str(output_path),
        ])
        return

    crop_w = int(height * 9 / 16)
    crop_h = height
    x_offset = (width - crop_w) // 2

    logger.info(
        "Cropping %s (%dx%d) -> %dx%d (9:16 centre crop)",
        input_path.name, width, height, crop_w, crop_h,
    )
    run_ffmpeg([
        "-i", str(input_path),
        "-vf", f"crop={crop_w}:{crop_h}:{x_offset}:0",
        "-c:v", "libx264", "-crf", "23",
        "-c:a", "aac",
        str(output_path),
    ])
