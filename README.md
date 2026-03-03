# clip-farm

Build a pool of short vertical-ready candidate clips from multiple YouTube videos. MVP v0/v1: YouTube search + download + chunk into fixed-length clips + optional audio-based ranking.

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
- **clip_length_seconds** – length of each candidate clip (e.g. 18)
- **clip_step_seconds** – step between clip starts (e.g. 12; step < length = overlap)
- **min_video_duration_seconds** / **max_video_duration_seconds** – filter source videos by duration
- **enable_audio_scoring** – if true, score clips by audio energy and write ranked manifests
- **top_k_per_video** – when scoring, how many top clips per video to list in ranked manifest
- **top_n_loud_global** – (command `loud`) number of loudest clips to keep globally across all videos (e.g. 20)
- **loud_peaks_per_video** – max loud-moment peaks to consider per video before ranking (e.g. 50)
- **loud_min_peak_distance_seconds** – minimum seconds between peak centers (e.g. 20)

## How to run

From the **project root** (`clip-farm/`):

```bash
# Download only (build video pool from queries + URLs)
python -m src.main download

# Chunk only (segment all already-downloaded videos into clips)
python -m src.main chunk

# Full pipeline: download + chunk + optional audio scoring
python -m src.main run

# Score: rank existing candidates by audio, then copy top-K MP4s to data/outputs/ranked/
python -m src.main score

# Loud: find loudest moments per video (peak detection), take top 20 globally, extract to data/outputs/ranked/
python -m src.main loud

# Refresh: delete all candidate clips and their manifests (keeps source videos in data/videos/)
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
python -m src.main run --config config/config.yaml --dry-run
python -m src.main download --limit-videos 5 --limit-queries 1
python -m src.main run --verbose
python -m src.main refresh --dry-run   # show what would be deleted without deleting
python -m src.main score               # rank existing candidates (uses top_k_per_video from config)
python -m src.main loud                # top 20 loud moments across all downloaded videos (uses top_n_loud_global)
```

## Output layout

All output lives under `./data/` (gitignored):

```
data/
  videos/              # Downloaded source videos (<video_id>.mp4)
  candidates/          # Candidate clips per video (<video_id>/<video_id>_t{start_ms}_{end_ms}.mp4)
  outputs/
    ranked/            # Top ranked clips: from score (top-K per video) or loud (top 20 loud globally)
  logs/                # run.log
  manifests/
    videos/            # JSON metadata per downloaded video
    candidates/        # JSON manifest of clips per video
    candidates_ranked/ # Top-K per video (used by score; outputs/ranked gets the MP4s)
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
