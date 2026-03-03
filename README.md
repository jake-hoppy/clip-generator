# clip-farm

Build a pool of short vertical-ready clips from multiple YouTube videos: search + download, then extract the **top N loudest moments** across all videos (audio peak detection) into `data/outputs/ranked/`.

## Prerequisites

- **Python 3.11+**
- **pip**
- **FFmpeg** (must be installed separately; not a pip package)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`

## Setup

```bash
cd clip-farm
pip install -r requirements.txt
```

Optional: use a virtual environment so `yt-dlp` is on PATH when the script runs:

```bash
python3 -m venv .venv
source .venv/bin/activate   # or: .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Configuration

Edit `config/config.yaml` to set:

- **queries** – list of YouTube search queries (e.g. `"friends funniest moments"`)
- **urls** – optional list of direct YouTube URLs
- **results_per_query** – how many search results per query (e.g. 10)
- **max_videos_total** – cap on total source videos (e.g. 25)
- **min_video_duration_seconds** / **max_video_duration_seconds** – filter source videos by duration
- **clip_length_seconds** – length of each extracted clip around a loud peak (e.g. 18)
- **top_n_loud_global** – number of loudest clips to keep globally across all videos (e.g. 20)
- **loud_peaks_per_video** – max loud-moment peaks to consider per video before ranking (e.g. 50)
- **loud_min_peak_distance_seconds** – minimum seconds between peak centers (e.g. 20)

## How to run

From the **project root** (`clip-farm/`):

```bash
# Download only (build video pool from queries + URLs)
python -m src.main download

# Full pipeline: download + find top N loud moments across all videos → data/outputs/ranked/
python -m src.main run

# Loud only: find top N loud moments from already-downloaded videos, extract to data/outputs/ranked/
python -m src.main loud

# Refresh: delete old candidate data (keeps source videos); then run or loud to regenerate
python -m src.main refresh
```

### Optional flags

- `--config path/to/config.yaml` – use a different config file (default: `config/config.yaml`)
- `--dry-run` – don’t write files; log what would be done
- `--limit-videos N` – override `max_videos_total` (download / run)
- `--limit-queries N` – only run the first N queries (download / run)
- `--verbose` / `-v` – verbose logging

Examples:

```bash
python -m src.main run --limit-videos 3 --limit-queries 1   # 3 videos, 1 query, then top 20 loud
python -m src.main download --limit-videos 5 --limit-queries 1
python -m src.main run --verbose
python -m src.main loud                # top 20 loud moments from existing downloads (uses top_n_loud_global)
python -m src.main refresh --dry-run   # show what would be deleted without deleting
```

## Output layout

All output lives under `./data/` (gitignored):

```
data/
  videos/              # Downloaded source videos (<video_id>.mp4)
  outputs/
    ranked/            # Top N loudest clip MP4s (rank_001_..., rank_002_..., top_loud_manifest.json)
  logs/                # run.log
  manifests/
    videos/            # JSON metadata per downloaded video
```

After a run, the CLI prints a short summary: number of videos, number of clips, and paths.

## Example config

```yaml
queries:
  - "friends funniest moments"
  - "the office best moments"
urls: []
results_per_query: 10
max_videos_total: 25
download_format: "mp4"
clip_length_seconds: 18
clip_step_seconds: 12
allow_final_short_chunk: false
min_video_duration_seconds: 60
max_video_duration_seconds: 1800
enable_audio_scoring: false
top_k_per_video: 5
seed: 42
```

## Troubleshooting

### FFmpeg missing

**Error:** `ffmpeg is not installed or not on PATH`

- Install FFmpeg: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux).
- Ensure `ffmpeg` and `ffprobe` are on your PATH: `which ffmpeg` and `which ffprobe`.

### yt-dlp errors

- **No results / search fails:** Check queries and network. Try a direct URL in `urls` to confirm yt-dlp works.
- **Rate limits / 429:** Reduce `results_per_query` or `max_videos_total`; add short delays if you extend the script.
- **Install/version:** `pip install -U yt-dlp`

### Chunk step finds no videos

- Run `download` first (or ensure `data/manifests/videos/` contains JSON manifests for the videos you expect).
- Chunk only processes videos that have both a manifest and a file in `data/videos/`.

### Logs

- Console: INFO by default; use `--verbose` for DEBUG.
- Full log file: `data/logs/run.log`.

## Design notes

- **Idempotent:** Re-running download skips videos that already have a file + manifest; re-running chunk skips videos that already have a full candidates manifest and clip files.
- **Extensible:** Layout and modules are set up so you can add Whisper/LLM later under e.g. `src/ai/` without changing the core pipeline.
- **No database:** All metadata is stored as JSON in `data/manifests/`.
