# clip-farm

Build a pool of short vertical-ready clips from multiple YouTube videos: search + download, then **Whisper** transcribes and the **LLM (gpt-4o-mini)** chooses where each funny clip starts and ends (within a min/max duration) and scores it. Top N clips go to `data/candidates_ranked/`.

## Prerequisites

- **Python 3.11+**
- **pip**
- **FFmpeg** (must be installed separately; not a pip package)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
- **OpenAI API key** (for Whisper transcription and GPT scoring)

## Setup

```bash
cd clip-farm
pip install -r requirements.txt
```

Copy the example env file and add your OpenAI API key (never commit `.env`):

```bash
cp .env.example .env
# Edit .env and set: OPENAI_API_KEY=sk-your-key-here
```

Optional: use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate   # or: .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Configuration

Edit `config/config.yaml` to set:

- **queries** – list of YouTube search queries
- **urls** – optional list of direct YouTube URLs
- **results_per_query** / **max_videos_total** – how many videos to download
- **min_video_duration_seconds** / **max_video_duration_seconds** – filter source videos by duration
- **clip_min_duration_seconds** / **clip_max_duration_seconds** – min and max length for each clip (AI chooses exact boundaries within this range, e.g. 5–60 s)
- **top_n_global** – number of top-ranked clips to keep globally (e.g. 20)
- **whisper_model** – Whisper API model (default: `whisper-1`)
- **openai_model** – model for scoring (default: `gpt-4o-mini`)
- **openai_prompt** – optional; override the default scoring prompt (only used for the legacy per-segment scorer; clip boundaries use a built-in prompt)

## How to run

From the **project root** (`clip-farm/`):

```bash
# Download only (build video pool from queries + URLs)
python -m src.main download

# Full pipeline: download + Whisper segments + OpenAI ranking → top N to data/candidates_ranked/
python -m src.main run

# Rank only: Whisper + OpenAI on already-downloaded videos; top N to data/candidates_ranked/
python -m src.main rank

# Refresh: delete candidate/ranked clips and manifests (keeps source videos); run or rank to regenerate
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
python -m src.main run --limit-videos 3 --limit-queries 1
python -m src.main rank
python -m src.main refresh --dry-run
```

## Output layout

All output lives under `./data/` (gitignored):

```
data/
  videos/              # Downloaded source videos (<video_id>.mp4)
  candidates/          # All extracted segments per video (<video_id>/<clip_id>.mp4)
  candidates_ranked/   # Top N ranked clip MP4s (rank_001_..., rank_002_...)
  outputs/             # Reserved for final results later (empty for now)
  logs/                # run.log
  manifests/
    videos/            # JSON metadata per downloaded video
    candidates/        # One JSON per video listing its candidate segments
    candidates_ranked/ # top_ranked_manifest.json (top N clips and paths)
```

## API key

Set **OPENAI_API_KEY** in your environment or in a `.env` file in the project root. The app loads `.env` automatically (via `python-dotenv`). Do not commit `.env`; it is in `.gitignore`. Use `.env.example` as a template.

## Troubleshooting

### OPENAI_API_KEY not set

- Create `.env` from `.env.example` and set `OPENAI_API_KEY=sk-...`
- Or: `export OPENAI_API_KEY='sk-...'` in your shell before running.

### FFmpeg missing

- Install: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux).
- Ensure `ffmpeg` and `ffprobe` are on PATH: `which ffmpeg` and `which ffprobe`.

### yt-dlp errors

- No results: check queries and network. Try a direct URL in `urls`.
- Rate limits: reduce `results_per_query` or `max_videos_total`.
- Upgrade: `pip install -U yt-dlp`

### Rank step finds no videos

- Run `download` first (or ensure `data/manifests/videos/` has JSON manifests and `data/videos/` has the video files).

### Logs

- Console: INFO by default; use `--verbose` for DEBUG.
- Full log file: `data/logs/run.log`.

## Design notes

- **Clip boundaries:** Whisper returns a full timestamped transcript; the LLM is given the transcript and chooses start/end times for each funny clip (within clip_min/max_duration_seconds). No fixed window—the AI decides where to cut.
- **Scoring:** The same LLM call returns a 1–10 score per suggested clip; clips are ranked globally and top N are extracted.
- **Idempotency:** Re-running download skips videos that already have a file + manifest. Re-running rank overwrites candidates and ranked output.
- **No database:** All metadata is stored as JSON in `data/manifests/`.
