"""
Score a transcript segment using OpenAI Chat Completions (e.g. gpt-4o-mini).
Returns a numeric score (1-10) for clip potential; used for ranking segments.
"""
import logging
import re
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = """You are judging how good a short video clip would be for social media (e.g. TikTok, Reels). Rate the following transcript segment from 1 to 10.

Consider: Is it funny, surprising, or engaging? Would it work as a standalone clip? Is the payoff clear in this segment?

Reply with ONLY a single number from 1 to 10 (no explanation). Example: 7"""


def _get_client() -> OpenAI:
    import os
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in your environment or in a .env file."
        )
    return OpenAI(api_key=key)


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
            max_tokens=10,
            temperature=0.3,
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
