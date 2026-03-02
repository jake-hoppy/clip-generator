"""
CLI entry for clip-farm.
Subcommands: download | chunk | run
"""
import argparse
import sys
from pathlib import Path

from src.utils.paths import project_root
from src.utils.logging_setup import setup_logging
from src.pipeline import load_config, run_download, run_chunk, run_audio_score, run_full, run_refresh, print_summary
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
    print_summary(videos, [], {}, args.dry_run)


def cmd_chunk(args: argparse.Namespace, config: dict) -> None:
    clips = run_chunk(config, dry_run=args.dry_run)
    print_summary([], clips, {}, args.dry_run)


def cmd_run(args: argparse.Namespace, config: dict) -> None:
    videos, clips, ranked = run_full(
        config,
        dry_run=args.dry_run,
        limit_videos=getattr(args, "limit_videos", None),
        limit_queries=getattr(args, "limit_queries", None),
    )
    print_summary(videos, clips, ranked, args.dry_run)


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
    print("  Run 'chunk' or 'run' to regenerate clips.")
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

    # chunk
    p_chunk = subparsers.add_parser("chunk", help="Chunk downloaded videos into candidate clips only")
    p_chunk.add_argument("--dry-run", action="store_true", help="Do not write files")
    p_chunk.set_defaults(func=cmd_chunk)

    # run
    p_run = subparsers.add_parser("run", help="Full pipeline: download + chunk + optional audio score")
    p_run.add_argument("--limit-videos", type=int, default=None, help="Override max_videos_total")
    p_run.add_argument("--limit-queries", type=int, default=None, help="Limit number of queries to run")
    p_run.add_argument("--dry-run", action="store_true", help="Do not write files")
    p_run.set_defaults(func=cmd_run)

    # refresh
    p_refresh = subparsers.add_parser(
        "refresh",
        help="Delete all candidate clips and their manifests; keep source videos. Run chunk/run to regenerate.",
    )
    p_refresh.add_argument("--dry-run", action="store_true", help="Only log what would be deleted")
    p_refresh.set_defaults(func=cmd_refresh)

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.command in ("chunk", "run"):
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
