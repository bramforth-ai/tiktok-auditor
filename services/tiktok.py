"""
TikTok Auditor - TikTok Service
Wraps yt-dlp for channel scanning and video downloading.
"""

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone


# Base data directory
DATA_DIR = Path(__file__).parent.parent / "data" / "channels"


def _get_channel_dir(username: str) -> Path:
    """Get or create the channel directory."""
    channel_dir = DATA_DIR / username
    channel_dir.mkdir(parents=True, exist_ok=True)
    return channel_dir


def _find_ytdlp() -> str:
    """Find yt-dlp executable on PATH."""
    return "yt-dlp"


def calculate_engagement_rate(video: dict) -> float:
    """
    Weighted engagement rate reflecting TikTok's algorithm priorities.
    Shares and saves weighted more heavily.
    """
    views = video.get("view_count", 0)
    if views == 0:
        return 0.0

    weighted = (
        video.get("like_count", 0) * 1
        + video.get("comment_count", 0) * 2
        + video.get("repost_count", 0) * 3  # shares
        + video.get("save_count", 0) * 2
    )
    return round((weighted / views) * 100, 2)


def _parse_info_json(info_json_path: Path) -> dict | None:
    """
    Parse a yt-dlp .info.json file into our video metadata format.
    Returns None for non-video entries (channel pages, playlists, etc.)
    """
    with open(info_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Skip channel-level or playlist entries (not actual videos)
    entry_type = data.get("_type", "")
    if entry_type in ("playlist", "channel"):
        return None

    # Skip entries with no upload date or no view count (not real videos)
    if not data.get("upload_date") or not data.get("webpage_url"):
        return None

    # Skip entries whose URL doesn't look like a TikTok video
    url = data.get("webpage_url", "")
    if url and "/video/" not in url:
        return None

    # Check for subtitle availability
    subtitles = data.get("subtitles", {})
    automatic_captions = data.get("automatic_captions", {})
    has_subtitles = bool(subtitles) or bool(automatic_captions)

    video = {
        "video_id": str(data.get("id", "")),
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "upload_date": data.get("upload_date", ""),
        "duration": data.get("duration", 0),
        "view_count": data.get("view_count", 0) or 0,
        "like_count": data.get("like_count", 0) or 0,
        "comment_count": data.get("comment_count", 0) or 0,
        "repost_count": data.get("repost_count", 0) or 0,
        "save_count": data.get("save_count", 0) or 0,
        "url": data.get("webpage_url", ""),
        "has_subtitles": has_subtitles,
    }

    # Calculate engagement rate
    video["engagement_rate"] = calculate_engagement_rate(video)

    return video


def scan_channel(
    username: str, date_from: str = None, date_to: str = None,
    max_videos: int = None, progress_callback=None
) -> list[dict]:
    """
    Download metadata for all videos in a channel within optional date range.

    Args:
        username: TikTok username (without @)
        date_from: Optional YYYYMMDD string (yt-dlp --dateafter format)
        date_to: Optional YYYYMMDD string (yt-dlp --datebefore format)
        max_videos: Optional limit — stops yt-dlp after N videos (most recent first)
        progress_callback: Optional callable(count) called as videos are found

    Returns:
        List of video metadata dicts sorted by engagement rate (descending).
        Also saves to data/channels/{username}/metadata.json
    """
    channel_dir = _get_channel_dir(username)
    videos_dir = channel_dir / "videos"
    videos_dir.mkdir(exist_ok=True)

    ytdlp = _find_ytdlp()
    url = f"https://www.tiktok.com/@{username}"

    # Safety cap: if date filtering is set but no max_videos, auto-cap at 200
    # This is because --dateafter/--datebefore don't stop yt-dlp early —
    # it still crawls the entire channel. --playlist-end is what actually stops it.
    DEFAULT_CAP = 200
    if (date_from or date_to) and not max_videos:
        max_videos = DEFAULT_CAP
        print(f"  Auto-capping at {DEFAULT_CAP} videos (date filtering doesn't speed up yt-dlp)")

    # Build yt-dlp command
    cmd = [
        ytdlp,
        "--skip-download",
        "--write-info-json",
        "--restrict-filenames",
        "--no-overwrites",
        "-o", str(videos_dir / "%(id)s.%(ext)s"),
    ]

    # Max videos — actually stops yt-dlp early (unlike date filtering)
    if max_videos:
        cmd.extend(["--playlist-end", str(max_videos)])

    # Date filtering (note: yt-dlp still crawls all videos, just skips non-matching)
    if date_from:
        cmd.extend(["--dateafter", date_from])
    if date_to:
        cmd.extend(["--datebefore", date_to])

    cmd.append(url)

    print(f"Scanning @{username}...")
    if max_videos:
        print(f"  Limit: {max_videos} most recent videos")
    if date_from or date_to:
        print(f"  Date range: {date_from or 'start'} to {date_to or 'now'}")

    # Run yt-dlp with real-time output so it doesn't look frozen
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Show progress in real-time
    video_count = 0
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        # Count videos as they're processed
        if "Writing video metadata" in line or ".info.json" in line:
            video_count += 1
            if video_count % 10 == 0:
                print(f"  Scanned {video_count} videos...")
            if progress_callback:
                progress_callback(video_count)
        # Show errors
        elif "ERROR" in line:
            print(f"  {line[:200]}")

    process.wait()

    if process.returncode != 0 and video_count == 0:
        print(f"  yt-dlp failed (exit code {process.returncode})")

    # Parse all .info.json files in the videos directory
    videos = []
    for info_file in videos_dir.glob("*.info.json"):
        try:
            video = _parse_info_json(info_file)
            if video and video["video_id"]:
                videos.append(video)
        except Exception as e:
            print(f"  Error parsing {info_file.name}: {e}")

    # Sort by engagement rate (highest first)
    videos.sort(key=lambda v: v["engagement_rate"], reverse=True)

    # Save metadata.json
    metadata = {
        "username": username,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from,
        "date_to": date_to,
        "total_videos": len(videos),
        "videos": videos,
    }

    metadata_path = channel_dir / "metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"  Found {len(videos)} videos")
    print(f"  Metadata saved to {metadata_path}")

    return videos


def download_videos(username: str, video_ids: list[str]) -> list[dict]:
    """
    Download specific videos with embedded subtitles.

    Args:
        username: TikTok username
        video_ids: List of video IDs to download

    Returns:
        List of dicts with download results per video:
        {video_id, success, path, error}
    """
    channel_dir = _get_channel_dir(username)
    videos_dir = channel_dir / "videos"
    videos_dir.mkdir(exist_ok=True)

    # Load metadata to get URLs
    metadata_path = channel_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"No metadata.json for @{username}. Run scan_channel first."
        )

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    # Build URL lookup
    url_lookup = {}
    for video in metadata["videos"]:
        url_lookup[video["video_id"]] = video["url"]

    ytdlp = _find_ytdlp()
    archive_file = channel_dir / "archive.txt"
    results = []

    for video_id in video_ids:
        url = url_lookup.get(video_id)
        if not url:
            results.append({
                "video_id": video_id,
                "success": False,
                "path": None,
                "error": f"Video ID {video_id} not found in metadata",
            })
            continue

        # Check if already downloaded
        expected_path = videos_dir / f"{video_id}.mp4"
        if expected_path.exists():
            print(f"  {video_id}: already downloaded")
            results.append({
                "video_id": video_id,
                "success": True,
                "path": str(expected_path),
                "error": None,
            })
            continue

        print(f"  Downloading {video_id}...")

        cmd = [
            ytdlp,
            "--embed-subs",
            "--all-subs",
            "--write-info-json",
            "--restrict-filenames",
            "--download-archive", str(archive_file),
            "-f", "b",
            "--merge-output-format", "mp4",
            "-o", str(videos_dir / "%(id)s.%(ext)s"),
            url,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        # Check if file was created (might be .mp4, .mp3, or other extension)
        found_path = None
        if expected_path.exists() and expected_path.stat().st_size > 10000:
            found_path = expected_path
        else:
            # Check for any media file with this video ID (mp4, mp3, webm, etc.)
            for f in videos_dir.glob(f"{video_id}.*"):
                if f.suffix in (".mp4", ".mp3", ".webm", ".m4a", ".mkv") and f.stat().st_size > 10000:
                    found_path = f
                    break

        if found_path:
            # Keep the original extension — don't rename mp3 to mp4
            actual_path = videos_dir / f"{video_id}{found_path.suffix}"
            if found_path != actual_path:
                found_path.rename(actual_path)
            print(f"  {video_id}: downloaded OK ({actual_path.suffix})")
            results.append({
                "video_id": video_id,
                "success": True,
                "path": str(actual_path),
                "error": None,
            })
            # Clean up tiny corrupt files
            for f in videos_dir.glob(f"{video_id}.*"):
                if f.suffix != ".info.json" and f != actual_path and f.stat().st_size < 10000:
                    f.unlink(missing_ok=True)
        else:
            error_msg = "Download failed"
            if result.stderr:
                # Get the last meaningful error line
                stderr_lines = [
                    l for l in result.stderr.strip().split("\n") if l.strip()
                ]
                if stderr_lines:
                    error_msg = stderr_lines[-1][:200]

            print(f"  {video_id}: FAILED - {error_msg}")
            results.append({
                "video_id": video_id,
                "success": False,
                "path": None,
                "error": error_msg,
            })

    return results


def rebuild_metadata_from_disk(username: str) -> dict:
    """Re-glob videos/*.info.json and rewrite metadata.json. No network call.
    Useful after operations that add or remove .info.json files on disk
    (refetches, manual cleanup, etc.)."""
    channel_dir = DATA_DIR / username
    videos_dir = channel_dir / "videos"
    if not videos_dir.exists():
        return {"username": username, "total_videos": 0, "videos": []}

    videos = []
    for info_file in videos_dir.glob("*.info.json"):
        try:
            video = _parse_info_json(info_file)
            if video and video["video_id"]:
                videos.append(video)
        except Exception as e:
            print(f"  Error parsing {info_file.name}: {e}")

    videos.sort(key=lambda v: v["engagement_rate"], reverse=True)

    # Preserve existing scan-timestamp metadata if present; just refresh totals.
    metadata_path = channel_dir / "metadata.json"
    existing = {}
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    metadata = {
        "username": username,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "date_from": existing.get("date_from"),
        "date_to": existing.get("date_to"),
        "total_videos": len(videos),
        "videos": videos,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def refetch_video_metadata(username: str, video_ids: list[str]) -> dict:
    """Try to re-fetch .info.json for specific video_ids via yt-dlp. Used to
    rescue orphan scorecards whose video might still exist on TikTok.

    Returns: {"refetched": [video_id, ...], "dead": [video_id, ...]}.
    A video is 'refetched' if a valid .info.json file exists for it on disk
    after the call. 'dead' means TikTok no longer returns metadata for it."""
    if not video_ids:
        return {"refetched": [], "dead": []}

    channel_dir = _get_channel_dir(username)
    videos_dir = channel_dir / "videos"
    videos_dir.mkdir(exist_ok=True)

    urls = [f"https://www.tiktok.com/@{username}/video/{vid}" for vid in video_ids]

    cmd = [
        _find_ytdlp(),
        "--skip-download",
        "--write-info-json",
        "--restrict-filenames",
        "--ignore-errors",
        "--no-overwrites",
        "-o", str(videos_dir / "%(id)s.%(ext)s"),
    ] + urls

    subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    refetched = []
    dead = []
    for vid in video_ids:
        if (videos_dir / f"{vid}.info.json").exists():
            refetched.append(vid)
        else:
            dead.append(vid)
    return {"refetched": refetched, "dead": dead}


def load_metadata(username: str) -> dict | None:
    """
    Load existing metadata.json for a channel.
    Returns None if not found.
    """
    metadata_path = DATA_DIR / username / "metadata.json"
    if not metadata_path.exists():
        return None

    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_video_path(username: str, video_id: str) -> Path | None:
    """
    Get path to a downloaded video/audio file.
    Returns None if not downloaded. Checks mp4, mp3, webm, m4a, mkv.
    """
    videos_dir = DATA_DIR / username / "videos"
    for ext in (".mp4", ".mp3", ".webm", ".m4a", ".mkv"):
        path = videos_dir / f"{video_id}{ext}"
        if path.exists() and path.stat().st_size > 10000:
            return path
    return None
