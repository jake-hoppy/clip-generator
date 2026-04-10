"""
Estimate pipeline progress from run.log (heuristic, monotonic stages).
"""
from __future__ import annotations

import re
from pathlib import Path

# (regex, percent 0-100, user-facing label) — scan all log lines, take max percent.
_STAGE_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (re.compile(r"Copied .+ ->"), 8.0, "Copying upload…"),
    (re.compile(r"Ingested:"), 10.0, "Registering video…"),
    (re.compile(r"Ingest complete:"), 14.0, "Ingest complete…"),
    (re.compile(r"Detected \d+ scene boundaries for"), 22.0, "Scene detection…"),
    (re.compile(r"Transcribing .+ with mlx-whisper"), 34.0, "Transcribing (Whisper)…"),
    (re.compile(r"Transcribed .+: \d+ raw segments"), 52.0, "Merging segments…"),
    (re.compile(r"mlx-whisper returned \d+ word timestamps"), 56.0, "Scoring segments…"),
    (re.compile(r"Wrote candidates to .+, top \d+ ranked to"), 90.0, "Ranking top clips…"),
    (re.compile(r"Wrote candidates to"), 82.0, "Writing candidates…"),
    (re.compile(r"Exported \d+ vertical clips"), 96.0, "Vertical export…"),
    # Full pipeline / download (Run pipeline)
    (re.compile(r"Prepended \d+ trending URLs"), 6.0, "Fetching trending…"),
    (re.compile(r"Download|yt-dlp|Writing video"), 12.0, "Downloading…"),
    (re.compile(r"Refresh: removed"), 92.0, "Cleaning up…"),
]


def estimate_progress_from_log(log_tail: str) -> tuple[float, str]:
    """
    Return best (percent, label) from the last chunk of log text.
    Percent is capped below 99 while work may still be running.
    """
    best_pct = 0.0
    best_label = "Starting…"
    for line in log_tail.splitlines():
        line = line.strip()
        if not line:
            continue
        for rx, pct, label in _STAGE_PATTERNS:
            if rx.search(line):
                if pct > best_pct:
                    best_pct = pct
                    best_label = label
    return best_pct, best_label


def read_log_tail(path: Path, max_chars: int = 120_000) -> str:
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(raw) <= max_chars:
        return raw
    return raw[-max_chars:]
