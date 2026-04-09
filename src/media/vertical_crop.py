"""
Vertical (9:16) top-stack layout for landscape video clips with optional subtitle burning.
Landscape sources are scaled to 1080px wide and placed at the top of a 1080×1920 frame
with black filling the bottom. Subtitles render one word at a time in the black area
using actual Whisper word-level timestamps when available.
"""
import json
import logging
import os
import tempfile
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


def crop_to_vertical(
    input_path: Path,
    output_path: Path,
    segments: list[dict] | None = None,
) -> None:
    """
    Place clips inside a 1080×1920 (9:16) frame using a top-stack layout:
    video fills the top portion at 1080px wide, black fills below.
    Portrait clips are scaled to fit within 1080×1920.
    When *segments* are provided, word-by-word subtitles are burned onto
    the final output as a second pass.
    """
    width, height = _get_dimensions(input_path)
    has_subs = segments and len(segments) > 0

    if width > height:
        vf = "scale=1080:-2,pad=1080:1920:0:0:black"
        logger.info("Top-stack %s (%dx%d) -> 1080x1920 (9:16)", input_path.name, width, height)
    else:
        vf = "scale=-2:1920,pad=1080:1920:(ow-iw)/2:0:black"
        logger.info("Portrait fit %s (%dx%d) -> 1080x1920 (9:16)", input_path.name, width, height)

    if not has_subs:
        run_ffmpeg([
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "23",
            "-c:a", "aac",
            str(output_path),
        ])
        return

    fd, tmp_raw = tempfile.mkstemp(suffix=".mp4", prefix="clipfarm_stack_")
    os.close(fd)
    tmp_path = Path(tmp_raw)
    try:
        run_ffmpeg([
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "23",
            "-c:a", "aac",
            str(tmp_path),
        ])
        burn_subtitles(tmp_path, output_path, segments)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def burn_subtitles(
    input_path: Path,
    output_path: Path,
    segments: list[dict],
) -> None:
    """
    Burn word-by-word subtitles onto the video using an ASS subtitle overlay.
    The ASS canvas is always 1080×1920 to match the output frame.
    """
    if not segments:
        logger.warning("No segments provided for subtitle burning, copying without subtitles")
        run_ffmpeg(["-i", str(input_path), "-c", "copy", str(output_path)])
        return

    ass_content = _build_ass_subtitles(segments)

    tmp_dir = tempfile.mkdtemp(prefix="clipfarm_subs_")
    ass_path = Path(tmp_dir) / "subs.ass"
    try:
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
        run_ffmpeg([
            "-i", str(input_path),
            "-vf", f"ass={escaped}",
            "-c:v", "libx264", "-crf", "18",
            "-c:a", "aac",
            str(output_path),
        ], timeout=300)
    finally:
        if ass_path.exists():
            ass_path.unlink(missing_ok=True)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


def _build_ass_subtitles(segments: list[dict]) -> str:
    """
    Build an ASS subtitle file for a 1080×1920 canvas.
    Shows one word at a time using Whisper word-level timestamps when
    available; falls back to 2-word chunks with even distribution.
    """
    def _sec_to_ass(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,88,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,1,5,0,2,60,60,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for seg in segments:
        start = float(seg.get("start_sec", 0))
        end = float(seg.get("end_sec", start + 1))
        words_ts = seg.get("words") or []

        if words_ts:
            for word_entry in words_ts:
                word = word_entry["word"].strip()
                if not word:
                    continue
                w_start = float(word_entry["start"])
                w_end = float(word_entry["end"])
                if w_end <= w_start:
                    w_end = w_start + 0.1
                safe = word.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                events.append(
                    f"Dialogue: 0,{_sec_to_ass(w_start)},{_sec_to_ass(w_end)},"
                    f"Default,,0,0,0,,{safe}"
                )
        else:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            word_list = text.split()
            if not word_list:
                continue
            chunks = [" ".join(word_list[i:i + 2]) for i in range(0, len(word_list), 2)]
            chunk_duration = (end - start) / len(chunks)
            for j, chunk in enumerate(chunks):
                chunk_start = start + j * chunk_duration
                chunk_end = chunk_start + chunk_duration
                safe = chunk.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                events.append(
                    f"Dialogue: 0,{_sec_to_ass(chunk_start)},{_sec_to_ass(chunk_end)},"
                    f"Default,,0,0,0,,{safe}"
                )

    return header + "\n".join(events) + "\n"
