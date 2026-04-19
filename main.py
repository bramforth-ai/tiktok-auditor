"""
TikTok Auditor - FastAPI Application
Main entry point with all routes.
"""

from dotenv import load_dotenv
load_dotenv()

import json
import shutil
import threading
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from services.tiktok import scan_channel, load_metadata, DATA_DIR
from services.gemini_client import GeminiClient
from services.analyser import run_self_audit, run_competitor_analysis
from services.reporter import (
    generate_audit_report,
    generate_competitor_index,
    load_style_profile,
    get_profile_stats,
    get_latest_audit_stats,
)
from services.trend_generator import (
    run_trend_research,
    generate_trend_scripts,
    generate_trend_index,
    list_trend_batches,
    list_trend_batch_scripts,
)


# ============================================================
# Path-safety helper for delete endpoints
# ============================================================

def _safe_channel_path(*parts: str) -> Path:
    """Resolve a path inside DATA_DIR and reject any traversal."""
    data_root = DATA_DIR.resolve()
    target = (DATA_DIR.joinpath(*parts)).resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid path")
    return target


def _reset_processed_entries(username: str, video_ids: list[str]):
    """Remove video_ids from a channel's processed.json so the dashboard shows them
    as unprocessed again (and they can be re-rewritten)."""
    if not video_ids:
        return
    processed_path = DATA_DIR / username / "processed.json"
    if not processed_path.exists():
        return
    try:
        data = json.loads(processed_path.read_text(encoding="utf-8"))
    except Exception:
        return
    changed = False
    for vid in video_ids:
        if vid in data:
            del data[vid]
            changed = True
    if changed:
        processed_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

app = FastAPI(title="TikTok Auditor", version="1.0.0")

# ============================================================
# Config — remembers which channel is "yours"
# ============================================================

CONFIG_PATH = Path(__file__).parent / "data" / "config.json"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _get_own_username() -> str | None:
    return _load_config().get("own_username")


def _set_own_username(username: str):
    config = _load_config()
    config["own_username"] = username
    _save_config(config)

# Static files and templates
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
templates_path = Path(__file__).parent / "templates"
templates_path.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=static_path), name="static")
templates = Jinja2Templates(directory=templates_path)

# ============================================================
# Global scanning state (for polling)
# ============================================================

scan_state = {
    "is_scanning": False,
    "username": None,
    "videos_found": 0,
    "finished": False,
    "error": None,
}

scan_lock = threading.Lock()


def _reset_scan_state():
    scan_state.update({
        "is_scanning": False,
        "username": None,
        "videos_found": 0,
        "finished": False,
        "error": None,
    })


def _run_scan_bg(username: str, date_from: str, date_to: str, max_videos: int):
    """Run channel scan in background thread."""
    try:
        videos = scan_channel(
            username,
            date_from=date_from,
            date_to=date_to,
            max_videos=max_videos,
            progress_callback=lambda count: scan_state.update({"videos_found": count}),
        )
        scan_state["videos_found"] = len(videos)
    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        scan_state["is_scanning"] = False
        scan_state["finished"] = True


# ============================================================
# Global processing state (for polling)
# ============================================================

processing_state = {
    "is_processing": False,
    "mode": None,
    "username": None,
    "current_video": None,
    "current_index": 0,
    "total": 0,
    "completed": 0,
    "scored": 0,
    "failed": 0,
    "triaged_out": 0,
    "no_transcript": 0,
    "results": [],  # last N results
    "cancel_requested": False,
    "finished": False,
    "stage": "",  # "scoring", "triage", "analysis"
}

processing_lock = threading.Lock()


# ============================================================
# Trend-generation state (separate from per-video processing)
# ============================================================

trend_state = {
    "is_running": False,
    "stage": "",            # research | scripts | index | done
    "batch_date": None,
    "research_path": None,
    "script_paths": [],
    "error": None,
    "finished": False,
}
trend_lock = threading.Lock()


def _reset_trend_state():
    trend_state.update({
        "is_running": False,
        "stage": "",
        "batch_date": None,
        "research_path": None,
        "script_paths": [],
        "error": None,
        "finished": False,
    })


def _run_trend_generation_bg(
    own_username: str,
    date_window_days: int,
    topic_focus: str,
    topic_exclude: str,
    script_count: int,
):
    """Run trend research + script generation in a background thread."""
    try:
        gemini = GeminiClient()

        trend_state["stage"] = "research"
        result = run_trend_research(
            own_username, date_window_days, topic_focus, topic_exclude, gemini
        )
        trend_state["batch_date"] = result["batch_date"]
        trend_state["research_path"] = result["research_path"]

        trend_state["stage"] = "scripts"
        script_paths = generate_trend_scripts(
            own_username,
            result["batch_date"],
            result["research_path"],
            script_count,
            gemini,
        )
        trend_state["script_paths"] = script_paths

        trend_state["stage"] = "index"
        generate_trend_index(own_username, result["batch_date"])

        trend_state["stage"] = "done"

    except Exception as e:
        trend_state["error"] = str(e)
    finally:
        trend_state["is_running"] = False
        trend_state["finished"] = True


def _reset_processing_state():
    """Reset processing state for a new batch."""
    processing_state.update({
        "is_processing": False,
        "mode": None,
        "username": None,
        "current_video": None,
        "current_index": 0,
        "total": 0,
        "completed": 0,
        "scored": 0,
        "failed": 0,
        "triaged_out": 0,
        "no_transcript": 0,
        "results": [],
        "cancel_requested": False,
        "finished": False,
        "stage": "",
    })


# ============================================================
# Background processing functions
# ============================================================


def _run_self_audit_bg(username: str, video_ids: list[str]):
    """Run self-audit scoring in background thread."""
    try:
        gemini = GeminiClient()
        processing_state["stage"] = "scoring"

        for i, video_id in enumerate(video_ids):
            if processing_state["cancel_requested"]:
                break

            processing_state["current_video"] = video_id
            processing_state["current_index"] = i + 1

            from services.analyser import score_video
            try:
                result = score_video(username, video_id, gemini)
                processing_state["completed"] += 1

                if result["success"]:
                    processing_state["scored"] += 1
                    status = "scored"
                else:
                    if "no_transcript" in (result.get("error") or ""):
                        processing_state["no_transcript"] += 1
                        status = "no_transcript"
                    else:
                        processing_state["failed"] += 1
                        status = "failed"

                processing_state["results"].append({
                    "video_id": video_id,
                    "status": status,
                    "error": result.get("error"),
                })
                # Keep last 20 results
                if len(processing_state["results"]) > 20:
                    processing_state["results"] = processing_state["results"][-20:]

            except Exception as e:
                processing_state["completed"] += 1
                processing_state["failed"] += 1
                processing_state["results"].append({
                    "video_id": video_id,
                    "status": "failed",
                    "error": str(e),
                })

    finally:
        processing_state["is_processing"] = False
        processing_state["finished"] = True
        processing_state["current_video"] = None


def _run_competitor_analysis_bg(
    username: str,
    video_ids: list[str],
    style_profile_username: str = None,
):
    """Run competitor analysis in background thread."""
    try:
        gemini = GeminiClient()

        # Load style profile
        style_profile = None
        if style_profile_username:
            style_profile = load_style_profile(style_profile_username)

        # STAGE 1: TRIAGE
        processing_state["stage"] = "triage"
        passed_ids = []

        from services.analyser import triage_video, rewrite_video_script

        for i, video_id in enumerate(video_ids):
            if processing_state["cancel_requested"]:
                break

            processing_state["current_video"] = video_id
            processing_state["current_index"] = i + 1

            try:
                result = triage_video(username, video_id, gemini)
                processing_state["completed"] += 1

                if result["success"] and result["passed"]:
                    passed_ids.append(video_id)
                    processing_state["results"].append({
                        "video_id": video_id,
                        "status": "passed_triage",
                    })
                elif result["success"] and not result["passed"]:
                    processing_state["triaged_out"] += 1
                    reason = ""
                    if result.get("triage"):
                        reason = result["triage"].get("reason", "")
                    processing_state["results"].append({
                        "video_id": video_id,
                        "status": "triaged_out",
                        "reason": reason,
                    })
                else:
                    processing_state["failed"] += 1
                    processing_state["results"].append({
                        "video_id": video_id,
                        "status": "failed",
                        "error": result.get("error"),
                    })

            except Exception as e:
                processing_state["completed"] += 1
                processing_state["failed"] += 1
                processing_state["results"].append({
                    "video_id": video_id,
                    "status": "failed",
                    "error": str(e),
                })

            if len(processing_state["results"]) > 20:
                processing_state["results"] = processing_state["results"][-20:]

        if processing_state["cancel_requested"] or not passed_ids:
            processing_state["is_processing"] = False
            processing_state["finished"] = True
            processing_state["current_video"] = None
            return

        # STAGE 2: REWRITE
        processing_state["stage"] = "rewrite"
        processing_state["total"] = processing_state["completed"] + len(passed_ids)

        if not style_profile_username:
            print("[COMPETITOR] No style profile selected — cannot rewrite scripts. Skipping stage 2.")
            for vid in passed_ids:
                processing_state["failed"] += 1
                processing_state["results"].append({
                    "video_id": vid,
                    "status": "failed",
                    "error": "No creator style profile selected — choose one before running.",
                })
            return

        for i, video_id in enumerate(passed_ids):
            if processing_state["cancel_requested"]:
                break

            processing_state["current_video"] = video_id
            processing_state["current_index"] = processing_state["completed"] + 1

            try:
                result = rewrite_video_script(
                    username, video_id, gemini, style_profile, style_profile_username,
                )
                processing_state["completed"] += 1

                if result["success"]:
                    processing_state["scored"] += 1  # reuse counter for "rewritten"
                    processing_state["results"].append({
                        "video_id": video_id,
                        "status": "rewritten",
                        "script_path": result.get("script_path"),
                    })
                else:
                    processing_state["failed"] += 1
                    processing_state["results"].append({
                        "video_id": video_id,
                        "status": "failed",
                        "error": result.get("error"),
                    })

            except Exception as e:
                processing_state["completed"] += 1
                processing_state["failed"] += 1
                processing_state["results"].append({
                    "video_id": video_id,
                    "status": "failed",
                    "error": str(e),
                })

            if len(processing_state["results"]) > 20:
                processing_state["results"] = processing_state["results"][-20:]

    finally:
        processing_state["is_processing"] = False
        processing_state["finished"] = True
        processing_state["current_video"] = None


# ============================================================
# Page Routes
# ============================================================


@app.get("/")
async def index(request: Request):
    """Landing page — Your Channel + Competitor Analysis."""
    own_username = _get_own_username()
    own_channel = None
    competitors = []

    if DATA_DIR.exists():
        for channel_dir in sorted(DATA_DIR.iterdir()):
            if not channel_dir.is_dir():
                continue
            metadata_path = channel_dir / "metadata.json"
            if not metadata_path.exists():
                continue

            try:
                with open(metadata_path, encoding="utf-8") as f:
                    meta = json.load(f)

                has_profile = (channel_dir / "style_profile.md").exists()
                reports_dir = channel_dir / "reports"
                report_files = sorted(reports_dir.glob("*.md"), reverse=True) if reports_dir.exists() else []

                channel_info = {
                    "username": meta["username"],
                    "total_videos": meta["total_videos"],
                    "scanned_at": meta["scanned_at"][:10],
                    "has_profile": has_profile,
                    "report_count": len(report_files),
                    "reports": [r.name for r in report_files[:5]],
                }

                if meta["username"] == own_username:
                    own_channel = channel_info
                else:
                    competitors.append(channel_info)
            except Exception as e:
                print(f"[INDEX] Error loading channel {channel_dir.name}: {e}")

    # If own_username is set but channel has no metadata yet (just a style profile)
    if own_username and not own_channel:
        channel_dir = DATA_DIR / own_username
        if channel_dir.exists():
            has_profile = (channel_dir / "style_profile.md").exists()
            if has_profile:
                own_channel = {
                    "username": own_username,
                    "total_videos": 0,
                    "scanned_at": "—",
                    "has_profile": True,
                    "report_count": 0,
                    "reports": [],
                }

    # Trend batches for the own channel — home page doubles as output index.
    trend_batches = list_trend_batches(own_username) if own_username else []

    return templates.TemplateResponse(request, "index.html", {
        "own_channel": own_channel,
        "competitors": competitors,
        "trend_batches": trend_batches,
        "own_username": own_username,
    })


@app.get("/channel/{username}")
async def dashboard(request: Request, username: str):
    """Channel dashboard — video list, selection, processing controls."""
    metadata = load_metadata(username)
    if not metadata:
        raise HTTPException(status_code=404, detail=f"Channel @{username} not scanned yet")

    # Load processed state
    processed_path = DATA_DIR / username / "processed.json"
    processed = {}
    if processed_path.exists():
        with open(processed_path, encoding="utf-8") as f:
            processed = json.load(f)

    # Enrich videos with processing status + count them for the pipeline status bar.
    status_counts = {
        "unprocessed": 0, "scored": 0, "rewritten": 0,
        "failed": 0, "triaged_out": 0, "no_transcript": 0,
    }
    for video in metadata["videos"]:
        vid = video["video_id"]
        if vid in processed:
            video["status"] = processed[vid].get("status", "unprocessed")
        else:
            video["status"] = "unprocessed"
        status_counts[video["status"]] = status_counts.get(video["status"], 0) + 1

    # Determine if this is the user's own channel
    own_username = _get_own_username()
    is_own_channel = (username == own_username)

    # Pipeline status — only meaningful for the creator's own channel
    profile_stats = get_profile_stats(username) if is_own_channel else None
    audit_stats = get_latest_audit_stats(username) if is_own_channel else None

    # For consumer pages (competitor dashboards), surface which creator-profile
    # is being used — the inline banner template expects this.
    creator_profile_stats = None
    if not is_own_channel and own_username:
        creator_profile_stats = get_profile_stats(own_username)

    # List available reports
    reports = []
    reports_dir = DATA_DIR / username / "reports"
    if reports_dir.exists():
        for report_file in sorted(reports_dir.glob("*.md"), reverse=True):
            mtime = report_file.stat().st_mtime
            reports.append({
                "filename": report_file.name,
                "created": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                "type": "audit" if "audit" in report_file.name else "competitor",
            })

    # Find style profiles for Mode 2
    style_profiles = []
    if DATA_DIR.exists():
        for channel_dir in DATA_DIR.iterdir():
            if channel_dir.is_dir() and (channel_dir / "style_profile.md").exists():
                style_profiles.append(channel_dir.name)

    has_profile = (DATA_DIR / username / "style_profile.md").exists()

    return templates.TemplateResponse(request, "dashboard.html", {
        "username": username,
        "metadata": metadata,
        "videos": metadata["videos"],
        "processed": processed,
        "has_profile": has_profile,
        "reports": reports,
        "style_profiles": style_profiles,
        "is_own_channel": is_own_channel,
        "own_username": own_username,
        "status_counts": status_counts,
        "profile_stats": profile_stats,
        "audit_stats": audit_stats,
        "creator_profile_stats": creator_profile_stats,
    })


@app.get("/channel/{username}/report/{filename}")
async def view_report(request: Request, username: str, filename: str):
    """View a generated report."""
    report_path = DATA_DIR / username / "reports" / filename
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    content = report_path.read_text(encoding="utf-8")

    return templates.TemplateResponse(request, "report.html", {
        "username": username,
        "filename": filename,
        "content": content,
        "back_url": f"/channel/{username}",
        "delete_url": f"/api/report/{username}/{filename}/delete",
        "delete_redirect": f"/channel/{username}",
        "delete_label": f"Delete report “{filename}”",
    })


# ============================================================
# Trend Script Generator
# ============================================================


@app.get("/trend/{own_username}")
async def trend_page(request: Request, own_username: str):
    """Trend generator home: form + list of past batches."""
    batches = list_trend_batches(own_username)
    has_profile = (DATA_DIR / own_username / "style_profile.md").exists()
    profile_stats = get_profile_stats(own_username)
    return templates.TemplateResponse(request, "trend.html", {
        "username": own_username,
        "batches": batches,
        "has_profile": has_profile,
        "profile_stats": profile_stats,
    })


@app.post("/api/trend/generate")
async def api_trend_generate(request: Request):
    """Kick off a trend research + script generation batch."""
    with trend_lock:
        if trend_state["is_running"]:
            return JSONResponse(
                {"error": "Trend generation already running"}, status_code=409
            )

    body = await request.json()
    own_username = (body.get("own_username") or "").strip().lstrip("@")
    if not own_username:
        return JSONResponse({"error": "own_username required"}, status_code=400)

    try:
        date_window_days = int(body.get("date_window_days", 60))
    except (TypeError, ValueError):
        date_window_days = 60
    try:
        script_count = int(body.get("script_count", 5))
    except (TypeError, ValueError):
        script_count = 5

    date_window_days = max(7, min(180, date_window_days))
    script_count = max(1, min(15, script_count))

    topic_focus = (body.get("topic_focus") or "").strip()
    topic_exclude = (body.get("topic_exclude") or "").strip()

    _reset_trend_state()
    trend_state["is_running"] = True

    thread = threading.Thread(
        target=_run_trend_generation_bg,
        args=(
            own_username, date_window_days, topic_focus, topic_exclude,
            script_count,
        ),
        daemon=True,
    )
    thread.start()

    return {"success": True, "message": "Trend generation started"}


@app.get("/api/trend/status")
async def api_trend_status():
    """Poll trend generation progress."""
    return dict(trend_state)


@app.get("/trend/{own_username}/{batch_date}/research")
async def trend_research_view(request: Request, own_username: str, batch_date: str):
    """Render research.md for a trend batch."""
    rel_path = f"channels/{own_username}/generated_scripts/trend_{batch_date}/research.md"
    research_path = DATA_DIR / own_username / "generated_scripts" / f"trend_{batch_date}" / "research.md"
    if not research_path.exists():
        raise HTTPException(status_code=404, detail="Research not found")
    return templates.TemplateResponse(request, "report.html", {
        "username": own_username,
        "filename": f"trend_{batch_date}/research.md",
        "content": research_path.read_text(encoding="utf-8"),
        "back_url": f"/trend/{own_username}/{batch_date}",
        "download_url": f"/api/download?path={rel_path}",
        "delete_url": f"/api/trend/{own_username}/{batch_date}/research/delete",
        "delete_redirect": f"/trend/{own_username}/{batch_date}",
        "delete_label": "Delete research brief (scripts will remain)",
    })


@app.get("/trend/{own_username}/{batch_date}/{slug}")
async def trend_script_view(
    request: Request, own_username: str, batch_date: str, slug: str
):
    """Render a single trend script."""
    rel_path = f"channels/{own_username}/generated_scripts/trend_{batch_date}/{slug}.md"
    script_path = DATA_DIR / own_username / "generated_scripts" / f"trend_{batch_date}" / f"{slug}.md"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Trend script not found")
    return templates.TemplateResponse(request, "report.html", {
        "username": own_username,
        "filename": f"trend_{batch_date}/{slug}.md",
        "content": script_path.read_text(encoding="utf-8"),
        "back_url": f"/trend/{own_username}/{batch_date}",
        "download_url": f"/api/download?path={rel_path}",
        "delete_url": f"/api/trend/{own_username}/{batch_date}/{slug}/delete",
        "delete_redirect": f"/trend/{own_username}/{batch_date}",
        "delete_label": f"Delete this script ({slug}.md)",
    })


@app.get("/trend/{own_username}/{batch_date}")
async def trend_batch_view(request: Request, own_username: str, batch_date: str):
    """Interactive batch view: lists research + scripts with per-item delete buttons.
    Regenerates index.md on the fly so the Download Index link stays accurate."""
    try:
        scripts = list_trend_batch_scripts(own_username, batch_date)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trend batch not found")

    # Best-effort index regeneration so the Download Index link is fresh.
    # Ignore failures (e.g. empty batch) — the page still renders without it.
    try:
        generate_trend_index(own_username, batch_date)
    except ValueError:
        pass

    batch_dir = DATA_DIR / own_username / "generated_scripts" / f"trend_{batch_date}"
    has_research = (batch_dir / "research.md").exists()

    return templates.TemplateResponse(request, "trend_batch.html", {
        "username": own_username,
        "batch_date": batch_date,
        "scripts": scripts,
        "has_research": has_research,
    })


# ============================================================
# Competitor script viewer
# ============================================================


@app.get("/scripts/{own_username}/{competitor_username}/{date}/{video_id}")
async def view_script(
    request: Request,
    own_username: str,
    competitor_username: str,
    date: str,
    video_id: str,
):
    """View a rewritten competitor script."""
    rel_path = (
        f"channels/{own_username}/generated_scripts/"
        f"competitor_{competitor_username}/{date}/{video_id}.md"
    )
    script_path = DATA_DIR / own_username / "generated_scripts" / f"competitor_{competitor_username}" / date / f"{video_id}.md"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Script not found")

    return templates.TemplateResponse(request, "report.html", {
        "username": competitor_username,
        "filename": f"{video_id}.md",
        "content": script_path.read_text(encoding="utf-8"),
        "back_url": f"/channel/{competitor_username}",
        "download_url": f"/api/download?path={rel_path}",
        "delete_url": f"/api/competitor-script/{own_username}/{competitor_username}/{date}/{video_id}/delete",
        "delete_redirect": f"/channel/{competitor_username}",
        "delete_label": f"Delete this rewrite (video {video_id})",
    })


# ============================================================
# API Routes
# ============================================================


@app.post("/api/scan")
async def api_scan(request: Request):
    """Start a channel scan in the background."""
    with scan_lock:
        if scan_state["is_scanning"]:
            return JSONResponse({"error": "Scan already in progress"}, status_code=409)

    body = await request.json()
    username = body.get("username", "").strip().lstrip("@")
    date_from = body.get("date_from") or None
    date_to = body.get("date_to") or None
    max_videos = body.get("max_videos") or None
    is_own = body.get("is_own", False)

    if not username:
        return JSONResponse({"error": "Username required"}, status_code=400)

    if max_videos:
        max_videos = int(max_videos)

    # If this is the user's own channel, remember it
    if is_own:
        _set_own_username(username)

    _reset_scan_state()
    scan_state["is_scanning"] = True
    scan_state["username"] = username

    thread = threading.Thread(
        target=_run_scan_bg,
        args=(username, date_from, date_to, max_videos),
        daemon=True,
    )
    thread.start()

    return {"success": True, "message": f"Scanning @{username}..."}


@app.get("/api/scan/status")
async def api_scan_status():
    """Get current scan status (for polling)."""
    return {
        "is_scanning": scan_state["is_scanning"],
        "username": scan_state["username"],
        "videos_found": scan_state["videos_found"],
        "finished": scan_state["finished"],
        "error": scan_state["error"],
    }


@app.get("/api/channel/{username}/videos")
async def api_videos(username: str):
    """Get video list with processing status."""
    metadata = load_metadata(username)
    if not metadata:
        return JSONResponse({"error": "Channel not found"}, status_code=404)

    processed_path = DATA_DIR / username / "processed.json"
    processed = {}
    if processed_path.exists():
        with open(processed_path, encoding="utf-8") as f:
            processed = json.load(f)

    for video in metadata["videos"]:
        vid = video["video_id"]
        if vid in processed:
            video["status"] = processed[vid].get("status", "unprocessed")
        else:
            video["status"] = "unprocessed"

    return {"videos": metadata["videos"]}


@app.post("/api/process")
async def api_process(request: Request):
    """Start processing a batch of videos."""
    with processing_lock:
        if processing_state["is_processing"]:
            return JSONResponse(
                {"error": "Processing already in progress"}, status_code=409
            )

    body = await request.json()
    username = body.get("username", "")
    video_ids = body.get("video_ids", [])
    mode = body.get("mode", "self_audit")
    style_profile_username = body.get("style_profile_username")

    if not username or not video_ids:
        return JSONResponse({"error": "Username and video_ids required"}, status_code=400)

    _reset_processing_state()
    processing_state["is_processing"] = True
    processing_state["mode"] = mode
    processing_state["username"] = username
    processing_state["total"] = len(video_ids)

    if mode == "self_audit":
        thread = threading.Thread(
            target=_run_self_audit_bg,
            args=(username, video_ids),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=_run_competitor_analysis_bg,
            args=(username, video_ids, style_profile_username),
            daemon=True,
        )

    thread.start()

    return {"success": True, "message": f"Processing {len(video_ids)} videos"}


@app.get("/api/process/status")
async def api_process_status():
    """Get current processing status (for polling)."""
    return {
        "is_processing": processing_state["is_processing"],
        "mode": processing_state["mode"],
        "username": processing_state["username"],
        "stage": processing_state["stage"],
        "current_video": processing_state["current_video"],
        "current_index": processing_state["current_index"],
        "total": processing_state["total"],
        "completed": processing_state["completed"],
        "scored": processing_state["scored"],
        "failed": processing_state["failed"],
        "triaged_out": processing_state["triaged_out"],
        "no_transcript": processing_state["no_transcript"],
        "results": processing_state["results"][-10:],
        "finished": processing_state["finished"],
        "cancel_requested": processing_state["cancel_requested"],
    }


@app.post("/api/process/cancel")
async def api_process_cancel():
    """Cancel current processing batch."""
    processing_state["cancel_requested"] = True
    return {"success": True, "message": "Cancel requested"}


@app.post("/api/set-own-channel")
async def api_set_own_channel(request: Request):
    """Set which channel is 'yours' for self-audit."""
    body = await request.json()
    username = body.get("username", "").strip().lstrip("@")
    if not username:
        return JSONResponse({"error": "Username required"}, status_code=400)
    _set_own_username(username)
    return {"success": True, "username": username}


@app.post("/api/delete-style-profile/{username}")
async def api_delete_style_profile(username: str):
    """Delete a channel's style profile."""
    profile_path = DATA_DIR / username / "style_profile.md"
    lock_path = DATA_DIR / username / "style_profile.md.locked"
    deleted = False
    if profile_path.exists():
        profile_path.unlink()
        deleted = True
    if lock_path.exists():
        lock_path.unlink()
    if deleted:
        return {"success": True, "message": f"Style profile for @{username} deleted"}
    return {"success": False, "error": "No style profile found"}


# ============================================================
# Profile Management
# ============================================================


@app.get("/profile/{username}")
async def profile_page(request: Request, username: str):
    """Profile management screen — view, edit lazy defaults, regenerate."""
    profile_path = DATA_DIR / username / "style_profile.md"
    lock_path = DATA_DIR / username / "style_profile.md.locked"
    lazy_path = DATA_DIR / username / "lazy_defaults.md"

    profile_content = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    lazy_defaults = lazy_path.read_text(encoding="utf-8") if lazy_path.exists() else ""
    is_locked = lock_path.exists()

    scores_dir = DATA_DIR / username / "scores"
    score_count = len(list(scores_dir.glob("*.json"))) if scores_dir.exists() else 0
    # Skip the `<id>_raw.txt` debug files (not counted as scorecards)
    if scores_dir.exists():
        score_count = len([
            p for p in scores_dir.glob("*.json") if not p.stem.endswith("_raw")
        ])

    last_generated = None
    if profile_path.exists():
        last_generated = datetime.fromtimestamp(
            profile_path.stat().st_mtime
        ).strftime("%Y-%m-%d %H:%M")

    return templates.TemplateResponse(request, "profile.html", {
        "username": username,
        "profile_content": profile_content,
        "lazy_defaults": lazy_defaults,
        "is_locked": is_locked,
        "score_count": score_count,
        "last_generated": last_generated,
        "has_profile": profile_path.exists(),
    })


@app.post("/api/profile/{username}/lazy_defaults")
async def api_save_lazy_defaults(username: str, request: Request):
    """Save the creator's self-declared lazy defaults."""
    body = await request.json()
    content = body.get("content", "").strip()
    lazy_path = DATA_DIR / username / "lazy_defaults.md"
    lazy_path.parent.mkdir(parents=True, exist_ok=True)
    if content:
        lazy_path.write_text(content, encoding="utf-8")
    elif lazy_path.exists():
        lazy_path.unlink()
    return {"success": True}


@app.post("/api/profile/{username}/unlock")
async def api_unlock_profile(username: str):
    """Remove the lock sentinel so regeneration can overwrite the profile."""
    lock_path = DATA_DIR / username / "style_profile.md.locked"
    if lock_path.exists():
        lock_path.unlink()
    return {"success": True}


@app.post("/api/profile/{username}/regenerate")
async def api_regenerate_profile(username: str):
    """Regenerate the style profile from current scorecards."""
    from services.reporter import generate_style_profile

    lock_path = DATA_DIR / username / "style_profile.md.locked"
    profile_path = DATA_DIR / username / "style_profile.md"
    if lock_path.exists() and profile_path.exists():
        return JSONResponse(
            {"error": "Profile is locked. Unlock first."}, status_code=409
        )

    try:
        gemini = GeminiClient()
        path = generate_style_profile(username, gemini)
        return {"success": True, "path": path}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/report/generate")
async def api_generate_report(request: Request):
    """Generate a compilation report."""
    body = await request.json()
    username = body.get("username", "")
    mode = body.get("mode", "self_audit")
    style_profile_username = body.get("style_profile_username")

    if not username:
        return JSONResponse({"error": "Username required"}, status_code=400)

    try:
        if mode == "self_audit":
            # Audit report no longer silently builds the profile. Require it.
            profile_path = DATA_DIR / username / "style_profile.md"
            if not profile_path.exists():
                return JSONResponse(
                    {"error": "No style profile yet. Build your profile on the Profile page before building an audit report."},
                    status_code=400,
                )
            gemini = GeminiClient()
            report_path = generate_audit_report(username, gemini)
            return {
                "success": True,
                "report_path": report_path,
                "filename": Path(report_path).name,
            }
        else:
            # Competitor mode: no LLM call — index the already-generated scripts.
            report_path = generate_competitor_index(
                competitor_username=username,
                style_profile_username=style_profile_username,
            )
            return {
                "success": True,
                "report_path": report_path,
                "filename": Path(report_path).name,
            }

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/report/{username}/{filename}/download")
async def api_download_report(username: str, filename: str):
    """Download a report as markdown."""
    report_path = DATA_DIR / username / "reports" / filename
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(report_path, filename=filename, media_type="text/markdown")


@app.get("/api/download")
async def api_download(path: str):
    """Generic markdown download. `path` is relative to DATA_DIR. Path traversal is blocked."""
    data_root = DATA_DIR.resolve()
    target = (DATA_DIR / path).resolve()
    try:
        target.relative_to(data_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name, media_type="text/markdown")


# ============================================================
# Delete endpoints — surfaced via UI for management
# ============================================================


@app.post("/api/trend/{own_username}/{batch_date}/delete")
async def api_delete_trend_batch(own_username: str, batch_date: str):
    """Delete a whole trend batch folder (research + all scripts + index)."""
    target = _safe_channel_path(
        own_username, "generated_scripts", f"trend_{batch_date}"
    )
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Trend batch not found")
    shutil.rmtree(target)
    return {"success": True}


@app.post("/api/trend/{own_username}/{batch_date}/{slug}/delete")
async def api_delete_trend_script(own_username: str, batch_date: str, slug: str):
    """Delete a single trend script file, or the research brief when slug='research'."""
    target = _safe_channel_path(
        own_username, "generated_scripts", f"trend_{batch_date}", f"{slug}.md"
    )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Script not found")
    target.unlink()
    # Remove stale index — will be regenerated on next batch view
    index_path = target.parent / "index.md"
    if index_path.exists():
        index_path.unlink()
    # If the batch is now empty, remove the folder too
    try:
        if not any(target.parent.iterdir()):
            target.parent.rmdir()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/competitor-batch/{own_username}/{competitor_username}/{date}/delete")
async def api_delete_competitor_batch(
    own_username: str, competitor_username: str, date: str
):
    """Delete one date folder of competitor rewrites and reset the corresponding
    processed.json entries so those videos can be re-rewritten."""
    target = _safe_channel_path(
        own_username, "generated_scripts",
        f"competitor_{competitor_username}", date,
    )
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Competitor batch not found")
    deleted_vids = [p.stem for p in target.glob("*.md")]
    shutil.rmtree(target)
    _reset_processed_entries(competitor_username, deleted_vids)
    # Clean up empty competitor_<name> parent if now empty
    try:
        parent = target.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        pass
    return {"success": True, "deleted": len(deleted_vids)}


@app.post("/api/competitor-script/{own_username}/{competitor_username}/{date}/{video_id}/delete")
async def api_delete_competitor_script(
    own_username: str, competitor_username: str, date: str, video_id: str
):
    """Delete one rewritten competitor script + reset its processed.json entry."""
    target = _safe_channel_path(
        own_username, "generated_scripts",
        f"competitor_{competitor_username}", date, f"{video_id}.md",
    )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Script not found")
    target.unlink()
    _reset_processed_entries(competitor_username, [video_id])
    # Clean up empty date / competitor dirs
    try:
        date_dir = target.parent
        if not any(date_dir.iterdir()):
            date_dir.rmdir()
            comp_dir = date_dir.parent
            if comp_dir.exists() and not any(comp_dir.iterdir()):
                comp_dir.rmdir()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/report/{username}/{filename}/delete")
async def api_delete_report(username: str, filename: str):
    """Delete a single file from a channel's reports/ folder."""
    target = _safe_channel_path(username, "reports", filename)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    target.unlink()
    return {"success": True}


@app.post("/api/channel/{username}/delete")
async def api_delete_channel(username: str, request: Request):
    """Nuke a competitor channel entirely: metadata, scores, transcripts, videos,
    reports, and any rewritten scripts living under the own user's generated_scripts/.
    Requires the caller to echo the username as confirmation. Refuses to delete
    the user's own channel."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    confirmation = (body.get("confirm_username") or "").strip().lstrip("@")
    if confirmation != username:
        return JSONResponse(
            {"error": "Type the exact username to confirm deletion."},
            status_code=400,
        )
    if username == _get_own_username():
        return JSONResponse(
            {"error": "This is your own channel — delete the style profile or rescan instead."},
            status_code=400,
        )
    target = _safe_channel_path(username)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Channel not found")
    shutil.rmtree(target)
    # Also remove any rewritten scripts the own user has for this competitor
    own = _get_own_username()
    if own:
        comp_scripts = _safe_channel_path(
            own, "generated_scripts", f"competitor_{username}"
        )
        if comp_scripts.exists() and comp_scripts.is_dir():
            shutil.rmtree(comp_scripts)
    return {"success": True}


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("TikTok Auditor")
    print("=" * 50)
    print("\nOpen http://localhost:8000 in your browser")
    print("Press Ctrl+C to stop\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)