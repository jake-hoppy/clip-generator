"""
Logging setup: console + file (data/logs/run.log).
"""
import logging
import sys
from pathlib import Path

from .paths import ensure_data_dirs, run_log_path


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger: console (INFO or DEBUG) and data/logs/run.log (DEBUG)."""
    ensure_data_dirs()
    level_console = logging.DEBUG if verbose else logging.INFO
    level_file = logging.DEBUG

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Avoid adding handlers multiple times (idempotent)
    if root.handlers:
        for h in root.handlers[:]:
            root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level_console)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        log_file = run_log_path()
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level_file)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        root.warning("Could not open log file %s: %s", run_log_path(), e)

    # Reduce noise from third-party libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
