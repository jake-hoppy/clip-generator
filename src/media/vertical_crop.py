"""
Vertical (9:16) framing for landscape video clips with optional subtitle burning.
Landscape sources are zoomed in slightly (trimming some sides), then centred in a
1080×1920 frame with black bars above and below. Subtitles render three words at a
time using actual Whisper word-level timestamps when available.
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
    Place clips inside a 1080×1920 (9:16) frame, centred vertically.
    Landscape sources are zoomed ~35 % (mild side-crop) so the video is
    larger in the frame, then centred with black bars top and bottom.
    Portrait clips are scaled to fit within 1080×1920.
    When *segments* are provided, 3-word subtitles are burned onto
    the final output as a second pass.
    """
    width, height = _get_dimensions(input_path)
    has_subs = segments and len(segments) > 0

    if width > height:
        zoom = 1.35
        scaled_w = int(1080 * zoom)
        if scaled_w % 2:
            scaled_w += 1
        vf = (
            f"scale={scaled_w}:-2,"
            f"crop=1080:ih:(iw-1080)/2:0,"
            f"pad=1080:1920:0:(1920-ih)/2:black"
        )
        logger.info("Zoom-center %s (%dx%d, %.0f%% zoom) -> 1080x1920 (9:16)",
                     input_path.name, width, height, zoom * 100)
    else:
        vf = "scale=-2:1920,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
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
    Shows 3 words at a time using Whisper word-level timestamps when
    available; falls back to 3-word chunks with even distribution.
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
            for i in range(0, len(words_ts), 3):
                group = words_ts[i:i + 3]
                chunk_text = " ".join(w["word"].strip() for w in group)
                if not chunk_text:
                    continue
                w_start = float(group[0]["start"])
                w_end = float(group[-1]["end"])
                if w_end <= w_start:
                    w_end = w_start + 0.15
                safe = chunk_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
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
            chunks = [" ".join(word_list[i:i + 3]) for i in range(0, len(word_list), 3)]
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
