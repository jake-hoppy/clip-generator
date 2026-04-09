"""
Detect scene-change boundaries in a video using PySceneDetect.
Returns a list of timestamps (seconds) where scene transitions occur.
"""
import logging
from pathlib import Path

from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector

logger = logging.getLogger(__name__)


def get_scene_boundaries(video_path: Path, threshold: float = 27.0) -> list[float]:
    """
    Analyse video_path with PySceneDetect's ContentDetector and return
    a sorted list of scene-change timestamps in seconds.

    Returns an empty list if detection fails or no scenes are found.
    """
    if not video_path.exists():
        logger.warning("Video not found for scene detection: %s", video_path)
        return []

    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    boundaries: list[float] = []
    for _start, end in scene_list:
        boundaries.append(end.get_seconds())

    boundaries.sort()
    return boundaries
