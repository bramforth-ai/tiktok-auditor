"""
TikTok Auditor - Analyser Service
Orchestrates the scoring and analysis pipeline for Mode 1 (Self-Audit)
and Mode 2 (Competitor Intel). Tracks state via processed.json.
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone

from services.tiktok import (
    download_videos,
    load_metadata,
    get_video_path,
    DATA_DIR,
)
from services.transcriber import extract_transcript, transcribe_with_whisper, save_transcript
from services.gemini_client import GeminiClient


def _load_prompt(name: str) -> str:
    """Load a prompt template from data/prompts/."""
    prompt_path = Path(__file__).parent.parent / "data" / "prompts" / name
    return prompt_path.read_text(encoding="utf-8")


def _load_processed(username: str) -> dict:
    """Load processed.json for a channel. Returns empty dict if not found."""
    processed_path = DATA_DIR / username / "processed.json"
    if processed_path.exists():
        with open(processed_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_processed(username: str, processed: dict):
    """Save processed.json for a channel."""
    processed_path = DATA_DIR / username / "processed.json"
    with open(processed_path, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2, ensure_ascii=False)


def _update_processed(username: str, video_id: str, **kwargs):
    """Update a single video's entry in processed.json."""
    processed = _load_processed(username)
    if video_id not in processed:
        processed[video_id] = {}
    processed[video_id].update(kwargs)
    processed[video_id]["timestamp"] = datetime.now(timezone.utc).isoformat()
    _save_processed(username, processed)


def _get_video_stats(username: str, video_id: str) -> dict | None:
    """Get engagement stats for a video from metadata.json."""
    metadata = load_metadata(username)
    if not metadata:
        return None
    for video in metadata["videos"]:
        if video["video_id"] == video_id:
            return video
    return None


def _ensure_transcript(username: str, video_id: str) -> dict:
    """
    Ensure a transcript exists for a video.
    Downloads video if needed, extracts transcript, saves it.

    Returns:
        {
            "success": bool,
            "text": str,
            "word_count": int,
            "source": str,
            "error": str (if failed)
        }
    """
    # Check if transcript already exists
    transcript_path = DATA_DIR / username / "transcripts" / f"{video_id}.txt"
    if transcript_path.exists():
        text = transcript_path.read_text(encoding="utf-8")
        word_count = len(text.split())
        return {
            "success": True,
            "text": text,
            "word_count": word_count,
            "source": "cached",
        }

    # Download video if not already present
    video_path = get_video_path(username, video_id)
    if not video_path:
        results = download_videos(username, [video_id])
        if not results or not results[0]["success"]:
            error = results[0]["error"] if results else "Download failed"
            return {"success": False, "text": "", "word_count": 0, "source": None, "error": error}
        video_path = Path(results[0]["path"])

    # Extract transcript (ffmpeg subtitles first)
    result = extract_transcript(video_path)

    if result["success"]:
        save_transcript(username, video_id, result["text"], result["source"])
        _maybe_delete_video(video_path)
        return result

    # Fallback to Whisper if needed and available
    if result["needs_whisper"]:
        whisper_result = transcribe_with_whisper(video_path)
        if whisper_result["success"] and whisper_result["text"]:
            save_transcript(username, video_id, whisper_result["text"], whisper_result["source"])
            _maybe_delete_video(video_path)
            return whisper_result
        elif whisper_result["success"] and not whisper_result["text"]:
            # Whisper ran but found no speech (music, silence, etc.)
            _maybe_delete_video(video_path)
            return {
                "success": False,
                "text": "",
                "word_count": 0,
                "source": None,
                "error": "No speech detected (audio may be music or silence)",
            }
        elif not whisper_result["success"]:
            # Do NOT delete — keep video so user can retry
            return whisper_result

    # No subtitles and no Whisper API key configured
    return {
        "success": False,
        "text": "",
        "word_count": 0,
        "source": None,
        "error": "No subtitles found and Whisper API key not configured",
    }


def _maybe_delete_video(video_path: Path):
    """Delete video file if configured to save disk space."""
    if os.environ.get("DELETE_VIDEOS_AFTER_TRANSCRIPT", "true").lower() == "true":
        try:
            video_path.unlink(missing_ok=True)
        except Exception:
            pass


def _build_score_prompt(video_id: str, stats: dict, transcript: str) -> str:
    """Build the scoring prompt by injecting data into the template."""
    template = _load_prompt("score_video.txt")

    # Calculate WPM
    duration = stats.get("duration", 0)
    word_count = len(transcript.split())
    estimated_wpm = round(word_count / (duration / 60)) if duration > 0 else 0

    # Replace placeholders
    prompt = template.replace("{video_id}", video_id)
    prompt = prompt.replace("{view_count}", str(stats.get("view_count", 0)))
    prompt = prompt.replace("{like_count}", str(stats.get("like_count", 0)))
    prompt = prompt.replace("{comment_count}", str(stats.get("comment_count", 0)))
    prompt = prompt.replace("{repost_count}", str(stats.get("repost_count", 0)))
    prompt = prompt.replace("{save_count}", str(stats.get("save_count", 0)))
    prompt = prompt.replace("{engagement_rate}", str(stats.get("engagement_rate", 0)))
    prompt = prompt.replace("{duration}", str(duration))
    prompt = prompt.replace("{upload_date}", stats.get("upload_date", ""))
    prompt = prompt.replace("{transcript}", transcript)

    return prompt


def score_video(username: str, video_id: str, gemini: GeminiClient) -> dict:
    """
    Score a single video (Mode 1).

    Downloads, transcribes, and sends to triage model for scoring.
    Saves score card to /scores/{video_id}.json.
    Updates processed.json.

    Returns:
        {"success": bool, "score_card": dict | None, "error": str | None}
    """
    print(f"\nScoring {video_id}...")

    # Check if already scored
    processed = _load_processed(username)
    if video_id in processed and processed[video_id].get("status") == "scored":
        print(f"  Already scored — skipping")
        # Load existing score card
        score_path = DATA_DIR / username / "scores" / f"{video_id}.json"
        if score_path.exists():
            with open(score_path, "r", encoding="utf-8") as f:
                return {"success": True, "score_card": json.load(f), "error": None}
        return {"success": True, "score_card": None, "error": None}

    # Get video stats
    stats = _get_video_stats(username, video_id)
    if not stats:
        error = f"Video {video_id} not found in metadata"
        _update_processed(username, video_id, status="failed", mode="self_audit", error=error)
        return {"success": False, "score_card": None, "error": error}

    # Ensure transcript
    transcript_result = _ensure_transcript(username, video_id)
    if not transcript_result["success"]:
        status = "no_transcript" if transcript_result.get("error", "").startswith("No subtitles") else "failed"
        _update_processed(
            username, video_id,
            status=status, mode="self_audit",
            error=transcript_result.get("error"),
        )
        return {"success": False, "score_card": None, "error": transcript_result.get("error")}

    _update_processed(
        username, video_id,
        status="transcribed", mode="self_audit",
        transcript_source=transcript_result["source"],
    )

    # Build prompt and score
    prompt = _build_score_prompt(video_id, stats, transcript_result["text"])

    try:
        response = gemini.call_triage(prompt, json_mode=True)
        score_card = json.loads(response)
    except json.JSONDecodeError:
        # Retry once if JSON is invalid
        print(f"  Invalid JSON response — retrying...")
        try:
            response = gemini.call_triage(prompt, json_mode=True)
            score_card = json.loads(response)
        except (json.JSONDecodeError, Exception) as e:
            error = f"Invalid JSON from Gemini after retry: {str(e)[:200]}"
            _update_processed(username, video_id, status="failed", mode="self_audit", error=error)
            # Save raw response for debugging
            raw_path = DATA_DIR / username / "scores" / f"{video_id}_raw.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(response, encoding="utf-8")
            return {"success": False, "score_card": None, "error": error}
    except Exception as e:
        error = f"Gemini API error: {str(e)[:200]}"
        _update_processed(username, video_id, status="failed", mode="self_audit", error=error)
        return {"success": False, "score_card": None, "error": error}

    # Save score card
    scores_dir = DATA_DIR / username / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    score_path = scores_dir / f"{video_id}.json"
    with open(score_path, "w", encoding="utf-8") as f:
        json.dump(score_card, f, indent=2, ensure_ascii=False)

    _update_processed(
        username, video_id,
        status="scored", mode="self_audit",
        transcript_source=transcript_result["source"],
    )

    print(f"  Scored: hook={score_card.get('scores', {}).get('hook_strength', '?')}, "
          f"save={score_card.get('scores', {}).get('save_worthiness', '?')}")

    return {"success": True, "score_card": score_card, "error": None}


def run_self_audit(username: str, video_ids: list[str], gemini: GeminiClient = None) -> dict:
    """
    Run Mode 1 self-audit scoring on selected videos.

    Args:
        username: TikTok username
        video_ids: List of video IDs to process
        gemini: GeminiClient instance (creates one if not provided)

    Returns:
        {
            "total": int,
            "scored": int,
            "failed": int,
            "no_transcript": int,
            "skipped": int,
            "results": list[dict]
        }
    """
    if gemini is None:
        gemini = GeminiClient()

    results = []
    scored = 0
    failed = 0
    no_transcript = 0
    skipped = 0

    for i, video_id in enumerate(video_ids):
        print(f"\n[{i + 1}/{len(video_ids)}] Processing {video_id}")

        try:
            result = score_video(username, video_id, gemini)
            results.append({"video_id": video_id, **result})

            if result["success"]:
                scored += 1
            elif "no_transcript" in (result.get("error") or ""):
                no_transcript += 1
            else:
                failed += 1

        except Exception as e:
            print(f"  Unexpected error: {e}")
            _update_processed(
                username, video_id,
                status="failed", mode="self_audit",
                error=f"Unexpected: {str(e)[:200]}",
            )
            results.append({"video_id": video_id, "success": False, "error": str(e)})
            failed += 1

    summary = {
        "total": len(video_ids),
        "scored": scored,
        "failed": failed,
        "no_transcript": no_transcript,
        "skipped": skipped,
        "results": results,
    }

    print(f"\n{'=' * 60}")
    print(f"Self-audit complete: {scored} scored, {failed} failed, "
          f"{no_transcript} no transcript out of {len(video_ids)} total")
    print(f"{'=' * 60}")

    return summary


# ============================================================
# Mode 2 — Competitor Intelligence
# ============================================================


def _build_triage_prompt(video_id: str, stats: dict, transcript: str) -> str:
    """Build the triage prompt by injecting data into the template."""
    template = _load_prompt("triage_video.txt")

    prompt = template.replace("{video_id}", video_id)
    prompt = prompt.replace("{view_count}", str(stats.get("view_count", 0)))
    prompt = prompt.replace("{like_count}", str(stats.get("like_count", 0)))
    prompt = prompt.replace("{comment_count}", str(stats.get("comment_count", 0)))
    prompt = prompt.replace("{repost_count}", str(stats.get("repost_count", 0)))
    prompt = prompt.replace("{save_count}", str(stats.get("save_count", 0)))
    prompt = prompt.replace("{engagement_rate}", str(stats.get("engagement_rate", 0)))
    prompt = prompt.replace("{duration}", str(stats.get("duration", 0)))
    prompt = prompt.replace("{upload_date}", stats.get("upload_date", ""))
    prompt = prompt.replace("{transcript}", transcript)

    return prompt


def _build_analysis_prompt(
    video_id: str, stats: dict, transcript: str, style_profile: str = None
) -> str:
    """Build the analysis + script generation prompt."""
    template = _load_prompt("analyse_and_generate.txt")

    prompt = template.replace("{video_id}", video_id)
    prompt = prompt.replace("{view_count}", str(stats.get("view_count", 0)))
    prompt = prompt.replace("{like_count}", str(stats.get("like_count", 0)))
    prompt = prompt.replace("{comment_count}", str(stats.get("comment_count", 0)))
    prompt = prompt.replace("{repost_count}", str(stats.get("repost_count", 0)))
    prompt = prompt.replace("{save_count}", str(stats.get("save_count", 0)))
    prompt = prompt.replace("{engagement_rate}", str(stats.get("engagement_rate", 0)))
    prompt = prompt.replace("{duration}", str(stats.get("duration", 0)))
    prompt = prompt.replace("{upload_date}", stats.get("upload_date", ""))
    prompt = prompt.replace("{transcript}", transcript)

    # Style profile injection
    if style_profile:
        style_instructions = (
            "## Creator Style Profile\n\n"
            "Adapt the recreated script to match this creator's authentic voice and style. "
            "Preserve the structural elements that drove engagement in the original, "
            "but write the script as if this creator were delivering it.\n\n"
            f"{style_profile}"
        )
    else:
        style_instructions = (
            "Write the recreated script in a clear, direct, tutorial style "
            "suitable for a tech/AI creator to adapt to their own voice."
        )

    prompt = prompt.replace("{style_instructions}", style_instructions)

    return prompt


def triage_video(username: str, video_id: str, gemini: GeminiClient) -> dict:
    """
    Triage a single competitor video (Mode 2, Stage 1).

    Downloads, transcribes, and sends to triage model for pass/fail.
    Updates processed.json.

    Returns:
        {"success": bool, "passed": bool, "triage": dict | None, "error": str | None}
    """
    print(f"\nTriaging {video_id}...")

    # Check if already processed
    processed = _load_processed(username)
    if video_id in processed:
        status = processed[video_id].get("status")
        if status == "triaged_out":
            print(f"  Already triaged out — skipping")
            return {"success": True, "passed": False, "triage": None, "error": None}
        if status in ("analysed", "transcribed"):
            print(f"  Already past triage — skipping")
            return {"success": True, "passed": True, "triage": None, "error": None}

    # Get video stats
    stats = _get_video_stats(username, video_id)
    if not stats:
        error = f"Video {video_id} not found in metadata"
        _update_processed(username, video_id, status="failed", mode="competitor_intel", error=error)
        return {"success": False, "passed": False, "triage": None, "error": error}

    # Ensure transcript
    transcript_result = _ensure_transcript(username, video_id)
    if not transcript_result["success"]:
        status = "no_transcript" if "No subtitles" in (transcript_result.get("error") or "") else "failed"
        _update_processed(
            username, video_id,
            status=status, mode="competitor_intel",
            error=transcript_result.get("error"),
        )
        return {"success": False, "passed": False, "triage": None, "error": transcript_result.get("error")}

    _update_processed(
        username, video_id,
        status="downloaded", mode="competitor_intel",
        transcript_source=transcript_result["source"],
    )

    # Build prompt and triage
    prompt = _build_triage_prompt(video_id, stats, transcript_result["text"])

    try:
        response = gemini.call_triage(prompt, json_mode=True)
        triage = json.loads(response)
    except (json.JSONDecodeError, Exception) as e:
        # Retry once
        try:
            response = gemini.call_triage(prompt, json_mode=True)
            triage = json.loads(response)
        except Exception as e2:
            error = f"Triage failed: {str(e2)[:200]}"
            _update_processed(username, video_id, status="failed", mode="competitor_intel", error=error)
            return {"success": False, "passed": False, "triage": None, "error": error}

    passed = triage.get("pass", False)
    reason = triage.get("reason", "")

    if passed:
        _update_processed(
            username, video_id,
            status="transcribed", mode="competitor_intel",
            transcript_source=transcript_result["source"],
        )
        print(f"  PASSED: {reason}")
    else:
        _update_processed(
            username, video_id,
            status="triaged_out", mode="competitor_intel",
            triage_reason=reason,
            transcript_source=transcript_result["source"],
        )
        print(f"  TRIAGED OUT: {reason}")

    return {"success": True, "passed": passed, "triage": triage, "error": None}


def analyse_video(
    username: str, video_id: str, gemini: GeminiClient, style_profile: str = None
) -> dict:
    """
    Deep analysis + script generation for a single video (Mode 2, Stage 2).

    Sends to smart model for full analysis.
    Saves to /analyses/{video_id}.json.
    Updates processed.json.

    Returns:
        {"success": bool, "analysis": dict | None, "error": str | None}
    """
    print(f"\nAnalysing {video_id}...")

    # Check if already analysed
    processed = _load_processed(username)
    if video_id in processed and processed[video_id].get("status") == "analysed":
        print(f"  Already analysed — skipping")
        analysis_path = DATA_DIR / username / "analyses" / f"{video_id}.json"
        if analysis_path.exists():
            with open(analysis_path, "r", encoding="utf-8") as f:
                return {"success": True, "analysis": json.load(f), "error": None}
        return {"success": True, "analysis": None, "error": None}

    # Get video stats
    stats = _get_video_stats(username, video_id)
    if not stats:
        error = f"Video {video_id} not found in metadata"
        _update_processed(username, video_id, status="failed", mode="competitor_intel", error=error)
        return {"success": False, "analysis": None, "error": error}

    # Load transcript (should already exist from triage stage)
    transcript_path = DATA_DIR / username / "transcripts" / f"{video_id}.txt"
    if not transcript_path.exists():
        error = f"No transcript found for {video_id}. Run triage first."
        _update_processed(username, video_id, status="failed", mode="competitor_intel", error=error)
        return {"success": False, "analysis": None, "error": error}

    transcript = transcript_path.read_text(encoding="utf-8")

    # Build prompt
    prompt = _build_analysis_prompt(video_id, stats, transcript, style_profile)

    try:
        response = gemini.call_smart(prompt, json_mode=True)
        analysis = json.loads(response)
    except json.JSONDecodeError:
        # Retry once
        print(f"  Invalid JSON — retrying...")
        try:
            response = gemini.call_smart(prompt, json_mode=True)
            analysis = json.loads(response)
        except (json.JSONDecodeError, Exception) as e:
            error = f"Analysis failed (invalid JSON): {str(e)[:200]}"
            _update_processed(username, video_id, status="failed", mode="competitor_intel", error=error)
            # Save raw response
            raw_dir = DATA_DIR / username / "analyses"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{video_id}_raw.txt").write_text(response, encoding="utf-8")
            return {"success": False, "analysis": None, "error": error}
    except Exception as e:
        error = f"Gemini API error: {str(e)[:200]}"
        _update_processed(username, video_id, status="failed", mode="competitor_intel", error=error)
        return {"success": False, "analysis": None, "error": error}

    # Save analysis
    analyses_dir = DATA_DIR / username / "analyses"
    analyses_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = analyses_dir / f"{video_id}.json"
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    _update_processed(username, video_id, status="analysed", mode="competitor_intel")

    hook = analysis.get("score_card", {}).get("scores", {}).get("hook_strength", "?")
    print(f"  Analysed: hook={hook}, script generated")

    return {"success": True, "analysis": analysis, "error": None}


def run_competitor_analysis(
    username: str,
    video_ids: list[str],
    style_profile_username: str = None,
    gemini: GeminiClient = None,
) -> dict:
    """
    Run Mode 2 competitor intelligence pipeline on selected videos.

    Stage 1: Triage all videos (cheap model)
    Stage 2: Analyse + generate scripts for videos that pass triage (smart model)

    Args:
        username: Competitor's TikTok username
        video_ids: List of video IDs to process
        style_profile_username: Username whose style profile to use (optional)
        gemini: GeminiClient instance

    Returns:
        {
            "total": int,
            "triaged_out": int,
            "analysed": int,
            "failed": int,
            "no_transcript": int,
            "results": list[dict]
        }
    """
    if gemini is None:
        gemini = GeminiClient()

    # Load style profile if available
    style_profile = None
    if style_profile_username:
        from services.reporter import load_style_profile
        style_profile = load_style_profile(style_profile_username)
        if style_profile:
            print(f"Using style profile from @{style_profile_username}")
        else:
            print(f"No style profile found for @{style_profile_username} — using generic style")

    results = []
    triaged_out = 0
    analysed = 0
    failed = 0
    no_transcript = 0

    # ---- STAGE 1: TRIAGE ----
    print(f"\n{'=' * 60}")
    print(f"STAGE 1: TRIAGE ({len(video_ids)} videos)")
    print(f"{'=' * 60}")

    passed_ids = []

    for i, video_id in enumerate(video_ids):
        print(f"\n[Triage {i + 1}/{len(video_ids)}] {video_id}")

        try:
            result = triage_video(username, video_id, gemini)

            if result["success"] and result["passed"]:
                passed_ids.append(video_id)
            elif result["success"] and not result["passed"]:
                triaged_out += 1
                results.append({
                    "video_id": video_id,
                    "stage": "triage",
                    "passed": False,
                    "reason": result.get("triage", {}).get("reason", ""),
                })
            else:
                failed += 1
                results.append({
                    "video_id": video_id,
                    "stage": "triage",
                    "success": False,
                    "error": result.get("error"),
                })

        except Exception as e:
            print(f"  Unexpected error: {e}")
            _update_processed(
                username, video_id,
                status="failed", mode="competitor_intel",
                error=f"Unexpected: {str(e)[:200]}",
            )
            failed += 1
            results.append({"video_id": video_id, "stage": "triage", "success": False, "error": str(e)})

    print(f"\nTriage complete: {len(passed_ids)} passed, {triaged_out} filtered out, {failed} failed")

    if not passed_ids:
        print("No videos passed triage — nothing to analyse.")
        return {
            "total": len(video_ids),
            "triaged_out": triaged_out,
            "analysed": 0,
            "failed": failed,
            "no_transcript": no_transcript,
            "results": results,
        }

    # ---- STAGE 2: ANALYSE + GENERATE ----
    print(f"\n{'=' * 60}")
    print(f"STAGE 2: ANALYSE + GENERATE ({len(passed_ids)} videos)")
    print(f"{'=' * 60}")

    for i, video_id in enumerate(passed_ids):
        print(f"\n[Analysis {i + 1}/{len(passed_ids)}] {video_id}")

        try:
            result = analyse_video(username, video_id, gemini, style_profile)

            if result["success"]:
                analysed += 1
                results.append({
                    "video_id": video_id,
                    "stage": "analysis",
                    "success": True,
                    "analysis": result["analysis"],
                })
            else:
                failed += 1
                results.append({
                    "video_id": video_id,
                    "stage": "analysis",
                    "success": False,
                    "error": result.get("error"),
                })

        except Exception as e:
            print(f"  Unexpected error: {e}")
            _update_processed(
                username, video_id,
                status="failed", mode="competitor_intel",
                error=f"Unexpected: {str(e)[:200]}",
            )
            failed += 1
            results.append({"video_id": video_id, "stage": "analysis", "success": False, "error": str(e)})

    summary = {
        "total": len(video_ids),
        "triaged_out": triaged_out,
        "analysed": analysed,
        "failed": failed,
        "no_transcript": no_transcript,
        "results": results,
    }

    print(f"\n{'=' * 60}")
    print(f"Competitor analysis complete: {analysed} analysed, "
          f"{triaged_out} triaged out, {failed} failed out of {len(video_ids)} total")
    print(f"{'=' * 60}")

    return summary
