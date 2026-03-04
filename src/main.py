"""
CLI entry for clip-farm.
Subcommands: download | run | rank | refresh
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.utils.paths import project_root
from src.utils.logging_setup import setup_logging
from src.pipeline import (
    load_config,
    run_download,
    run_full,
    run_whisper_rank,
    run_refresh,
    print_summary,
    print_run_summary,
)
from src.utils.paths import candidates_ranked_dir
from src.media.ffmpeg import require_ffmpeg


def _config_path(s: str) -> Path:
    p = Path(s)
    if not p.is_absolute():
        p = project_root() / p
    return p


def cmd_download(args: argparse.Namespace, config: dict) -> None:
    videos = run_download(
        config,
        dry_run=args.dry_run,
        limit_videos=getattr(args, "limit_videos", None),
        limit_queries=getattr(args, "limit_queries", None),
    )
    print_summary(videos, args.dry_run)


def cmd_run(args: argparse.Namespace, config: dict) -> None:
    """Full pipeline: download + Whisper segments + OpenAI ranking (top N globally)."""
    videos, ranked_clips = run_full(
        config,
        dry_run=args.dry_run,
        limit_videos=getattr(args, "limit_videos", None),
        limit_queries=getattr(args, "limit_queries", None),
    )
    print_run_summary(videos, ranked_clips, args.dry_run)


def cmd_rank(args: argparse.Namespace, config: dict) -> None:
    """Whisper segments + OpenAI scoring; take top N globally, extract to candidates_ranked/."""
    clips = run_whisper_rank(config, dry_run=args.dry_run)
    print("\n" + "=" * 60)
    print("RANK (Whisper segments + OpenAI scoring, top N globally)")
    print("=" * 60)
    if args.dry_run:
        print("  (dry run — no files written)")
    print(f"  Clips ranked:     {len(clips)}")
    print(f"  Output:           {candidates_ranked_dir()}")
    print("=" * 60)


def cmd_refresh(args: argparse.Namespace, config: dict) -> None:
    clips_dirs, manifest_files = run_refresh(dry_run=args.dry_run)
    print("\n" + "=" * 60)
    print("REFRESH (clips cleared)")
    print("=" * 60)
    if args.dry_run:
        print("  (dry run — nothing deleted)")
    print(f"  Clip dirs removed:     {clips_dirs}")
    print(f"  Manifest files removed: {manifest_files}")
    print("  Source videos in data/videos/ were kept.")
    print("  Run 'run' or 'rank' to regenerate top ranked clips.")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="clip-farm",
        description="Build a pool of short vertical-ready candidate clips from YouTube videos.",
    )
    parser.add_argument(
        "--config",
        type=_config_path,
        default=project_root() / "config" / "config.yaml",
        help="Path to config YAML",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download
    p_dl = subparsers.add_parser("download", help="Download video pool only")
    p_dl.add_argument("--limit-videos", type=int, default=None, help="Override max_videos_total")
    p_dl.add_argument("--limit-queries", type=int, default=None, help="Limit number of queries to run")
    p_dl.add_argument("--dry-run", action="store_true", help="Do not write files")
    p_dl.set_defaults(func=cmd_download)

    # run (download + Whisper + OpenAI rank: top N globally)
    p_run = subparsers.add_parser(
        "run",
        help="Full pipeline: download videos, then Whisper segments + OpenAI scoring; top N to data/candidates_ranked/.",
    )
    p_run.add_argument("--limit-videos", type=int, default=None, help="Override max_videos_total")
    p_run.add_argument("--limit-queries", type=int, default=None, help="Limit number of queries to run")
    p_run.add_argument("--dry-run", action="store_true", help="Do not write files")
    p_run.set_defaults(func=cmd_run)

    # rank (Whisper + OpenAI on already-downloaded videos -> top N globally)
    p_rank = subparsers.add_parser(
        "rank",
        help="Whisper segments + OpenAI scoring on downloaded videos; top N to data/candidates_ranked/.",
    )
    p_rank.add_argument("--dry-run", action="store_true", help="Do not extract clips")
    p_rank.set_defaults(func=cmd_rank)

    # refresh
    p_refresh = subparsers.add_parser(
        "refresh",
        help="Delete candidate clips and manifests from old runs (if any); keep source videos. Run run/rank to regenerate.",
    )
    p_refresh.add_argument("--dry-run", action="store_true", help="Only log what would be deleted")
    p_refresh.set_defaults(func=cmd_refresh)

    args = parser.parse_args()
    load_dotenv(project_root() / ".env")
    setup_logging(verbose=args.verbose)

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.command in ("run", "rank"):
        try:
            require_ffmpeg()
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        args.func(args, config)
        return 0
    except Exception as e:
        if args.verbose:
            import traceback
            traceback.print_exc()
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
