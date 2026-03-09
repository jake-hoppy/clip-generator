"""
Helpers for burning subtitles into clips using transcript data from the top-ranked manifest.
Transcript entries use timestamps relative to clip start (seconds). Use with FFmpeg subtitles filter.
"""
from pathlib import Path


def _sec_to_srt_time(sec: float) -> str:
    """Convert seconds (float) to SRT timestamp HH:MM:SS,mmm."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcript_to_srt(transcript: list[dict]) -> str:
    """
    Convert transcript (list of {start_sec, end_sec, text}) to SRT content.
    Timestamps are already relative to clip start; use the output with FFmpeg's subtitles filter.
    """
    lines = []
    for i, seg in enumerate(transcript, start=1):
        start_sec = seg.get("start_sec", 0.0)
        end_sec = seg.get("end_sec", 0.0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{_sec_to_srt_time(start_sec)} --> {_sec_to_srt_time(end_sec)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()


def write_srt_for_clip(transcript: list[dict], out_path: Path) -> None:
    """Write SRT file for a clip's transcript. Use with: ffmpeg -i clip.mp4 -vf \"subtitles=clip.srt\" out.mp4"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(transcript_to_srt(transcript), encoding="utf-8")
