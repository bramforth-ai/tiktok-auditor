"""
TikTok Auditor - FastAPI Application
Main entry point with all routes.
"""

from dotenv import load_dotenv
load_dotenv()

import json
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
    generate_full_audit,
    generate_competitor_index,
    load_style_profile,
)

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
    production_style: str = "talking_head",
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
                    username, video_id, gemini, style_profile,
                    style_profile_username, production_style,
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

    return templates.TemplateResponse(request, "index.html", {
        "own_channel": own_channel,
        "competitors": competitors,
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

    # Enrich videos with processing status
    for video in metadata["videos"]:
        vid = video["video_id"]
        if vid in processed:
            video["status"] = processed[vid].get("status", "unprocessed")
        else:
            video["status"] = "unprocessed"

    # Check for style profile
    has_profile = (DATA_DIR / username / "style_profile.md").exists()

    # List available reports
    reports = []
    reports_dir = DATA_DIR / username / "reports"
    if reports_dir.exists():
        for report_file in sorted(reports_dir.glob("*.md"), reverse=True):
            reports.append({
                "filename": report_file.name,
                "created": report_file.stat().st_mtime,
                "type": "audit" if "audit" in report_file.name else "competitor",
            })

    # Find style profiles for Mode 2
    style_profiles = []
    if DATA_DIR.exists():
        for channel_dir in DATA_DIR.iterdir():
            if channel_dir.is_dir() and (channel_dir / "style_profile.md").exists():
                style_profiles.append(channel_dir.name)

    # Determine if this is the user's own channel
    own_username = _get_own_username()
    is_own_channel = (username == own_username)

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
    })


@app.get("/scripts/{own_username}/{competitor_username}/{date}/{video_id}")
async def view_script(
    request: Request,
    own_username: str,
    competitor_username: str,
    date: str,
    video_id: str,
):
    """View a rewritten competitor script."""
    script_path = (
        DATA_DIR / own_username / "generated_scripts"
        / f"competitor_{competitor_username}" / date / f"{video_id}.md"
    )
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Script not found")

    content = script_path.read_text(encoding="utf-8")

    return templates.TemplateResponse(request, "report.html", {
        "username": competitor_username,
        "filename": f"{video_id}.md",
        "content": content,
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
    production_style = body.get("production_style", "talking_head")
    if production_style not in ("talking_head", "editorial"):
        production_style = "talking_head"

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
            args=(username, video_ids, style_profile_username, production_style),
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
            gemini = GeminiClient()
            result = generate_full_audit(username, gemini)
            return {
                "success": True,
                "report_path": result["audit_report_path"],
                "filename": Path(result["audit_report_path"]).name,
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