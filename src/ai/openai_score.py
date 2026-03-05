"""
Score a transcript segment using OpenAI Chat Completions (e.g. gpt-4o-mini).
Also: let the LLM choose clip boundaries from a full transcript (start/end + score).
"""
import logging
import re
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = """You are judging how good a short video clip would be for social media (e.g. TikTok, Reels). Rate the following transcript segment from 1 to 10.

Consider: Is it funny, surprising, or engaging? Would it work as a standalone clip? Is the payoff clear in this segment?

Reply with ONLY a single number from 1 to 10 (no explanation). Example: 7"""

CLIP_CHOICE_SYSTEM = """You are an expert at finding moments in video transcripts that are interesting, viral-worthy, and have clear resolve (a satisfying payoff or punchline).

You will receive a transcript with timestamps in the format [start_sec - end_sec] text. Your task: identify standalone clips that would work as short viral clips (e.g. TikTok, Reels). For each clip YOU choose the start and end time—cut where the moment naturally begins and ends, and include the resolve. Clip length must be between {min_duration:.0f} and {max_duration:.0f} seconds. Prefer clips that are interesting, engaging, and feel complete (they have resolve).

Output exactly one line per clip: START END SCORE
- START and END are in seconds (use the timestamps from the transcript).
- SCORE is 1-100 (rate how interesting and viral-worthy the clip is; prefer clips with clear resolve). Use the full range (e.g. 85, 42, 91).
- Use only times that appear in the transcript. Output up to 25 clips, best first.
Example:
12.5 28.0 85
45.2 58.1 72
Do not add headers or other text—only lines of the form START END SCORE."""


def _get_client() -> OpenAI:
    import os
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in your environment or in a .env file."
        )
    return OpenAI(api_key=key)


def check_openai_api(model: str = "gpt-4o-mini") -> bool:
    """
    Make a minimal request to OpenAI to verify the API key and connectivity.
    Returns True if the request succeeds. Logs result to console and logger.
    """
    import sys
    try:
        client = _get_client()
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_completion_tokens=5,
        )
        msg = "OpenAI API check: OK (request reached OpenAI and succeeded)"
        logger.info(msg)
        print(msg, file=sys.stderr)
        return True
    except Exception as e:
        msg = f"OpenAI API check: failed — {e}"
        logger.warning(msg)
        print(msg, file=sys.stderr)
        return False


def score_segment(
    text: str,
    video_title: str | None = None,
    model: str = "gpt-4o-mini",
    prompt_override: str | None = None,
) -> float:
    """
    Send segment text to OpenAI and get a numeric score (1-10).
    Uses the default prompt about viral/clip potential unless prompt_override is set.
    """
    if not (text or "").strip():
        return 0.0

    prompt = prompt_override or DEFAULT_PROMPT
    user_content = text.strip()
    if video_title:
        user_content = f"[Video title: {video_title}]\n\n{user_content}"

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=10,
        )
    except Exception as e:
        logger.warning("OpenAI score request failed: %s", e)
        return 0.0

    content = (response.choices[0].message.content or "").strip()
    # Parse a number (allow "7" or "7.5" or "Score: 8")
    match = re.search(r"(\d+(?:\.\d+)?)", content)
    if not match:
        logger.warning("Could not parse score from: %r", content)
        return 0.0
    score = float(match.group(1))
    return max(0.0, min(10.0, score))


def get_llm_clip_choices(
    transcript: str,
    video_title: str | None,
    video_duration_sec: float,
    min_duration_sec: float = 5.0,
    max_duration_sec: float = 60.0,
    model: str = "gpt-4o-mini",
) -> list[dict]:
    """
    Send full transcript (with timestamps) to the LLM; it returns clip boundaries and scores.
    Each clip: YOU decide start and end within the transcript. Returns list of
    {start_sec, end_sec, score, text} (text may be empty; we don't ask for it in the reply).
    """
    if not (transcript or "").strip():
        return []

    system = CLIP_CHOICE_SYSTEM.format(
        min_duration=min_duration_sec,
        max_duration=max_duration_sec,
    )
    user_parts = []
    if video_title:
        user_parts.append(f"Video title: {video_title}\n")
    user_parts.append("Transcript with timestamps (start_sec - end_sec: text):\n")
    user_parts.append(transcript.strip())
    user_parts.append(f"\n\nVideo total duration: {video_duration_sec:.1f} seconds. Output lines: START END SCORE (SCORE 1-100).")
    user_content = "".join(user_parts)

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=1500,
        )
    except Exception as e:
        logger.warning("LLM clip choice request failed: %s", e)
        return []

    content = (response.choices[0].message.content or "").strip()
    clips = _parse_clip_lines(content, video_duration_sec, min_duration_sec, max_duration_sec)
    if not clips and content:
        logger.warning("LLM returned content but no clips parsed. First 500 chars: %s", content[:500])
    return clips


def _parse_clip_lines(
    content: str,
    video_duration_sec: float,
    min_duration_sec: float,
    max_duration_sec: float,
) -> list[dict]:
    """Parse LLM output: lines with START END SCORE (seconds). Tolerates headers, labels, and punctuation."""
    clips = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Find all triples of numbers on the line; keep the one that looks like start_sec, end_sec, score
        # (start < end, score in 0-10). Accepts "12.5 28.0 9", "Clip 1: 12.5 28.0 9", "12.5 - 28.0 - 9"
        for match in re.finditer(r"([\d.]+)\s*[,\-–—:\s]+\s*([\d.]+)\s*[,\-–—:\s]+\s*([\d.]+)", line):
            try:
                a = float(match.group(1))
                b = float(match.group(2))
                c = float(match.group(3))
            except ValueError:
                continue
            # Prefer triple where a < b (start < end) and c is 0-100 (score)
            if a >= b or c < 0 or c > 100:
                continue
            start = max(0.0, min(video_duration_sec, a))
            end = max(0.0, min(video_duration_sec, b))
            if start >= end:
                continue
            duration = end - start
            if duration < min_duration_sec or duration > max_duration_sec:
                continue
            score = max(0.0, min(100.0, c))
            clips.append({
                "start_sec": round(start, 2),
                "end_sec": round(end, 2),
                "duration_seconds": round(duration, 2),
                "score": round(score, 2),
            })
            break  # One clip per line
    return clips
