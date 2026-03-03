"""
Centralized filesystem paths for clip-farm.
All output directories and naming conventions live here.
"""
from pathlib import Path


def project_root() -> Path:
    """Root of the clip-farm project (parent of src/)."""
    return Path(__file__).resolve().parent.parent.parent


def data_root() -> Path:
    """Runtime output root: ./data"""
    return project_root() / "data"


def videos_dir() -> Path:
    """Downloaded source videos: data/videos/"""
    return data_root() / "videos"


def candidates_dir() -> Path:
    """Candidate clips: data/candidates/"""
    return data_root() / "candidates"


def candidates_ranked_dir() -> Path:
    """Top-N ranked clip MP4s: data/candidates_ranked/ (outputs/ reserved for final results later)"""
    return data_root() / "candidates_ranked"


def outputs_dir() -> Path:
    """Reserved for final formatted vertical exports: data/outputs/"""
    return data_root() / "outputs"


def outputs_ranked_dir() -> Path:
    """Reserved for final results later; prefer candidates_ranked_dir() for ranked clips."""
    return data_root() / "outputs" / "ranked"


def logs_dir() -> Path:
    """Log files: data/logs/"""
    return data_root() / "logs"


def manifests_dir() -> Path:
    """JSON metadata: data/manifests/"""
    return data_root() / "manifests"


def manifests_videos_dir() -> Path:
    """Video metadata: data/manifests/videos/"""
    return manifests_dir() / "videos"


def manifests_candidates_dir() -> Path:
    """Candidate clip manifests per video: data/manifests/candidates/"""
    return manifests_dir() / "candidates"


def manifests_candidates_ranked_dir() -> Path:
    """Ranked candidate lists (when audio scoring enabled): data/manifests/candidates_ranked/"""
    return manifests_dir() / "candidates_ranked"


def video_file_path(video_id: str, ext: str = "mp4") -> Path:
    """Path for a downloaded video: data/videos/<video_id>.mp4"""
    return videos_dir() / f"{video_id}.{ext}"


def video_manifest_path(video_id: str) -> Path:
    """Path for video metadata JSON: data/manifests/videos/<video_id>.json"""
    return manifests_videos_dir() / f"{video_id}.json"


def candidates_dir_for_video(video_id: str) -> Path:
    """Directory for candidate clips of one video: data/candidates/<video_id>/"""
    return candidates_dir() / video_id


def candidate_clip_path(video_id: str, start_ms: int, end_ms: int, ext: str = "mp4") -> Path:
    """Path for one candidate clip: data/candidates/<video_id>/<video_id>_t{start_ms}_{end_ms}.mp4"""
    return candidates_dir_for_video(video_id) / f"{video_id}_t{start_ms}_{end_ms}.{ext}"


def candidates_manifest_path(video_id: str) -> Path:
    """Manifest listing all candidate clips for a video: data/manifests/candidates/<video_id>.json"""
    return manifests_candidates_dir() / f"{video_id}.json"


def candidates_ranked_manifest_path(video_id: str) -> Path:
    """Ranked manifest: data/manifests/candidates_ranked/<video_id>.json"""
    return manifests_candidates_ranked_dir() / f"{video_id}.json"


def run_log_path() -> Path:
    """Main run log: data/logs/run.log"""
    return logs_dir() / "run.log"


def ensure_data_dirs() -> None:
    """Create all data subdirectories if they do not exist."""
    dirs = [
        data_root(),
        videos_dir(),
        candidates_dir(),
        candidates_ranked_dir(),
        outputs_dir(),
        outputs_ranked_dir(),
        logs_dir(),
        manifests_dir(),
        manifests_videos_dir(),
        manifests_candidates_dir(),
        manifests_candidates_ranked_dir(),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
