"""
Helper wrapper to run ffmpeg and ffprobe via subprocess.
Captures stderr and raises clear exceptions on failure.
"""
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_CMD = "ffmpeg"
FFPROBE_CMD = "ffprobe"


class FFmpegError(Exception):
    """Raised when an ffmpeg or ffprobe command fails."""

    def __init__(self, message: str, command: list[str], stderr: str, returncode: int):
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.returncode = returncode


def check_ffmpeg_available() -> bool:
    """Return True if ffmpeg is on PATH and executable."""
    return shutil.which(FFMPEG_CMD) is not None


def check_ffprobe_available() -> bool:
    """Return True if ffprobe is on PATH and executable."""
    return shutil.which(FFPROBE_CMD) is not None


def require_ffmpeg() -> None:
    """Raise a helpful error if ffmpeg is not available."""
    if not check_ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed or not on PATH. "
            "Install it with: macOS: brew install ffmpeg, Ubuntu/Debian: sudo apt install ffmpeg"
        )
    if not check_ffprobe_available():
        raise RuntimeError(
            "ffprobe is not installed or not on PATH. "
            "It usually comes with ffmpeg. Install ffmpeg: brew install ffmpeg / apt install ffmpeg"
        )


def run_ffmpeg(args: list[str], timeout: int | None = 3600) -> None:
    """
    Run ffmpeg with the given args (e.g. ['-i', 'in.mp4', '-t', '10', 'out.mp4']).
    Raises FFmpegError on non-zero exit; captures stderr in the exception.
    """
    cmd = [FFMPEG_CMD, "-y", "-hide_banner", "-loglevel", "error"] + args
    logger.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(
            f"ffmpeg timed out after {timeout}s",
            command=cmd,
            stderr=str(e),
            returncode=-1,
        ) from e
    except FileNotFoundError as e:
        raise FFmpegError(
            "ffmpeg not found on PATH",
            command=cmd,
            stderr=str(e),
            returncode=-1,
        ) from e

    if result.returncode != 0:
        raise FFmpegError(
            f"ffmpeg exited with code {result.returncode}",
            command=cmd,
            stderr=result.stderr or result.stdout or "",
            returncode=result.returncode,
        )


def run_ffprobe(args: list[str], timeout: int = 30) -> str:
    """
    Run ffprobe with the given args; returns stdout.
    Raises FFmpegError on non-zero exit.
    """
    cmd = [FFPROBE_CMD, "-v", "error"] + args
    logger.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(
            f"ffprobe timed out after {timeout}s",
            command=cmd,
            stderr=str(e),
            returncode=-1,
        ) from e
    except FileNotFoundError as e:
        raise FFmpegError(
            "ffprobe not found on PATH",
            command=cmd,
            stderr=str(e),
            returncode=-1,
        ) from e

    if result.returncode != 0:
        raise FFmpegError(
            f"ffprobe exited with code {result.returncode}",
            command=cmd,
            stderr=result.stderr or result.stdout or "",
            returncode=result.returncode,
        )
    return result.stdout


def get_duration_seconds(path: Path) -> float:
    """Return duration of media file in seconds using ffprobe."""
    out = run_ffprobe(["-i", str(path), "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"])
    return float(out.strip())


def extract_clip(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    use_stream_copy: bool = True,
) -> None:
    """
    Extract a segment from input_path to output_path.
    start_seconds: start time, duration_seconds: length.
    use_stream_copy: -c copy for speed when cuts are keyframe-friendly; else re-encode.
    """
    args = [
        "-i", str(input_path),
        "-ss", str(start_seconds),
        "-t", str(duration_seconds),
    ]
    if use_stream_copy:
        args.extend(["-c", "copy"])
    else:
        args.extend(["-c:v", "libx264", "-c:a", "aac"])
    args.append(str(output_path))
    run_ffmpeg(args)
