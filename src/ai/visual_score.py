"""
Score a video segment's visual appeal using GPT-4o vision.
Extracts frames via ffmpeg, base64-encodes them, and asks the model for a 1-10 virality rating.
"""
import base64
import logging
import os
import re
import tempfile
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

_MAX_FRAMES = 6

_SYSTEM_PROMPT = (
    "You are rating short video clips for social media virality. "
    "You will see frames from a clip."
)

_USER_TEXT = (
    "Rate this clip's VISUAL appeal for going viral on TikTok/Reels from 1 to 10. "
    "Consider: facial expressions, motion, visual energy, scene variety, lighting quality, "
    "on-screen action. Reply with ONLY a single number."
)


def _get_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in your environment or in a .env file."
        )
    return OpenAI(api_key=key)


def _extract_frames(video_path: Path, start_sec: float, end_sec: float, out_dir: Path) -> list[Path]:
    """Extract 1 frame per second from the segment into out_dir as JPEGs."""
    from src.media.ffmpeg import run_ffmpeg

    duration = end_sec - start_sec
    if duration <= 0:
        return []

    run_ffmpeg([
        "-ss", str(start_sec),
        "-i", str(video_path),
        "-t", str(duration),
        "-vf", "fps=1",
        str(out_dir / "frame_%04d.jpg"),
    ], timeout=120)

    frames = sorted(out_dir.glob("frame_*.jpg"))
    return frames


def _select_evenly(frames: list[Path], max_count: int) -> list[Path]:
    """Pick up to max_count evenly-spaced frames from the list."""
    if len(frames) <= max_count:
        return frames
    step = len(frames) / max_count
    return [frames[int(i * step)] for i in range(max_count)]


def _encode_frame(path: Path) -> str:
    """Base64-encode a JPEG file."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def score_visual(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    model: str = "gpt-4o",
) -> float:
    """
    Extract frames from the video segment, send to OpenAI vision model,
    and return a visual-appeal score (0-10).

    Returns 0.0 on any failure.
    """
    tmp_dir = tempfile.mkdtemp(prefix="clipfarm_vis_")
    try:
        frames = _extract_frames(video_path, start_sec, end_sec, Path(tmp_dir))
        if not frames:
            logger.warning("No frames extracted for %s [%.1f-%.1f]", video_path.name, start_sec, end_sec)
            return 0.0

        selected = _select_evenly(frames, _MAX_FRAMES)

        content: list[dict] = []
        for fp in selected:
            b64 = _encode_frame(fp)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        content.append({"type": "text", "text": _USER_TEXT})

        client = _get_client()
        response = client.chat.completions.create(
            model=model,
            max_tokens=20,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )

        reply = (response.choices[0].message.content or "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)", reply)
        if not match:
            logger.warning("Could not parse visual score from: %r", reply)
            return 0.0
        score = float(match.group(1))
        return max(0.0, min(10.0, score))

    except Exception as e:
        logger.warning("Visual scoring failed for %s [%.1f-%.1f]: %s", video_path.name, start_sec, end_sec, e)
        return 0.0
    finally:
        for fp in Path(tmp_dir).glob("*"):
            try:
                fp.unlink()
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
