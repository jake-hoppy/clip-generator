"""
Stable IDs for videos and clips based on URL/title.
"""
import hashlib
import re


def stable_video_id(url: str, title: str | None = None) -> str:
    """
    Produce a stable, filesystem-safe ID for a video.
    Prefer YouTube video ID if extractable from URL; otherwise hash URL (and optional title).
    """
    # Try to extract YouTube video ID
    yt_id = _extract_youtube_id(url)
    if yt_id:
        return _sanitize_id(yt_id)

    # Fallback: hash URL + title for stability
    raw = url
    if title:
        raw = f"{url}|{title}"
    h = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    return _sanitize_id(h[:16])


def _extract_youtube_id(url: str) -> str | None:
    """Extract video ID from common YouTube URL patterns."""
    patterns = [
        r"(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _sanitize_id(s: str) -> str:
    """Keep only alphanumeric, underscore, hyphen."""
    return re.sub(r"[^\w\-]", "_", s)
