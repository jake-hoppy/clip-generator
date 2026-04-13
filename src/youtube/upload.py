"""
YouTube upload via YouTube Data API v3 (OAuth2).

Setup:
  1. Create OAuth2 credentials in Google Cloud Console (Desktop app type).
  2. Download client_secrets.json and place it in config/client_secrets.json.
  3. On first use, a browser window opens for authorization.
     The resulting token is saved to config/youtube_token.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
CLIENT_SECRETS = _CONFIG_DIR / "client_secrets.json"
TOKEN_FILE = _CONFIG_DIR / "youtube_token.json"


def _get_credentials(headless: bool = False) -> Credentials | None:
    """Load or refresh OAuth2 credentials. Returns None if not configured."""
    creds = None

    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if not CLIENT_SECRETS.exists():
        log.warning("YouTube OAuth not configured: %s not found", CLIENT_SECRETS)
        return None

    if headless:
        return None

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=8090, open_browser=True)
    TOKEN_FILE.write_text(creds.to_json())
    return creds


REDIRECT_URI = "http://localhost:8000/api/youtube/callback"
_VERIFIER_FILE = _CONFIG_DIR / ".youtube_code_verifier"


def _make_flow():
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_secrets_file(
        str(CLIENT_SECRETS), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )


def get_auth_url() -> str | None:
    """Generate an OAuth2 authorization URL for the frontend to open."""
    if not CLIENT_SECRETS.exists():
        return None
    flow = _make_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent", access_type="offline",
    )
    # Save code_verifier to disk so it survives server reloads
    _VERIFIER_FILE.write_text(flow.code_verifier or "")
    return auth_url


def exchange_code(code: str) -> bool:
    """Exchange authorization code for credentials and save token."""
    if not CLIENT_SECRETS.exists():
        return False
    flow = _make_flow()
    # Restore code_verifier from disk
    if _VERIFIER_FILE.exists():
        verifier = _VERIFIER_FILE.read_text().strip()
        flow.code_verifier = verifier if verifier else None
        _VERIFIER_FILE.unlink(missing_ok=True)
    flow.fetch_token(code=code)
    creds = flow.credentials
    TOKEN_FILE.write_text(creds.to_json())
    return True


def is_connected() -> tuple[bool, str]:
    """Check if we have valid YouTube credentials. Returns (connected, channel_name)."""
    creds = _get_credentials(headless=True)
    if not creds:
        return False, ""
    try:
        yt = build("youtube", "v3", credentials=creds)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            return True, items[0]["snippet"]["title"]
        return True, "YouTube"
    except Exception as e:
        log.warning("YouTube connection check failed: %s", e)
        return False, ""


def upload_video(
    filepath: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category_id: str = "22",
    privacy: str = "private",
) -> dict:
    """
    Upload a single video to YouTube.

    Returns dict with 'id', 'url', 'title' on success.
    Raises RuntimeError on failure.
    """
    filepath = Path(filepath)
    if not filepath.is_file():
        raise FileNotFoundError(f"Video file not found: {filepath}")

    creds = _get_credentials(headless=True)
    if not creds:
        raise RuntimeError("YouTube not authenticated. Connect via Settings first.")

    yt = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(filepath),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,
    )

    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    log.info("Uploading to YouTube: %s", filepath.name)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("  Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    log.info("Uploaded: %s -> %s", filepath.name, url)

    return {"id": video_id, "url": url, "title": title}


def upload_top_clips(n: int = 2, privacy: str = "public") -> list[dict]:
    """
    Upload the top N ranked output clips to YouTube.

    Looks in data/outputs/ for the top-ranked files and uploads them.
    Returns list of upload result dicts.
    """
    data_root = Path(__file__).resolve().parent.parent.parent / "data"
    outputs_dir = data_root / "outputs"
    manifest_path = data_root / "manifests" / "candidates_ranked" / "top_ranked_manifest.json"

    if not outputs_dir.exists():
        log.warning("No outputs directory found")
        return []

    # Load manifest for metadata
    clip_meta = {}
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            for c in manifest.get("clips", []):
                cid = c.get("clip_id", "")
                clip_meta[cid] = c
        except Exception:
            pass

    # Get output files sorted by name (rank_001, rank_002, etc.)
    output_files = sorted(outputs_dir.glob("*.mp4"))[:n]
    if not output_files:
        log.warning("No output files to upload")
        return []

    results = []
    for fpath in output_files:
        stem = fpath.stem
        # Extract clip_id from filename like rank_001_<clip_id>
        parts = stem.split("_", 2)
        clip_id = parts[2] if len(parts) >= 3 else stem

        meta = clip_meta.get(clip_id, {})
        text = meta.get("text", "")
        score = meta.get("score", 0)

        title = f"Clip: {text[:60]}" if text else fpath.stem
        desc = f"Score: {score:.1f}\n\n{text}" if text else f"Auto-generated clip - Score: {score:.1f}"

        try:
            result = upload_video(
                filepath=fpath,
                title=title,
                description=desc,
                tags=["clips", "shorts", "auto-generated"],
                privacy=privacy,
            )
            results.append(result)
        except Exception as e:
            log.error("Failed to upload %s: %s", fpath.name, e)

    return results
