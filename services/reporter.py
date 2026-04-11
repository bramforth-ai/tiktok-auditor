"""
TikTok Auditor - Reporter Service
Compiles scored videos into style profiles and audit reports.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from services.gemini_client import GeminiClient
from services.tiktok import DATA_DIR, load_metadata


def _load_prompt(name: str) -> str:
    """Load a prompt template from data/prompts/."""
    prompt_path = Path(__file__).parent.parent / "data" / "prompts" / name
    return prompt_path.read_text(encoding="utf-8")


def _load_playbook() -> str:
    """Load the TikTok research playbook."""
    playbook_path = Path(__file__).parent.parent / "reference" / "tiktok_playbook.md"
    if playbook_path.exists():
        return playbook_path.read_text(encoding="utf-8")
    return "(No playbook available)"


def _load_all_score_cards(username: str) -> list[dict]:
    """Load all score card JSON files for a channel."""
    scores_dir = DATA_DIR / username / "scores"
    if not scores_dir.exists():
        return []

    score_cards = []
    for score_file in sorted(scores_dir.glob("*.json")):
        # Skip raw response files
        if score_file.stem.endswith("_raw"):
            continue
        try:
            with open(score_file, "r", encoding="utf-8") as f:
                score_cards.append(json.load(f))
        except Exception as e:
            print(f"  Warning: could not load {score_file.name}: {e}")

    return score_cards


def _load_transcripts(username: str, max_count: int = 50) -> str:
    """
    Load transcripts for prompt inclusion.
    If more than max_count exist, sample: top 30 + bottom 10 + random 10.
    Returns concatenated text with video ID headers.
    """
    transcripts_dir = DATA_DIR / username / "transcripts"
    if not transcripts_dir.exists():
        return "(No transcripts available)"

    # Get all transcript files
    files = sorted(transcripts_dir.glob("*.txt"))
    if not files:
        return "(No transcripts available)"

    # If within limit, use all
    if len(files) <= max_count:
        selected = files
    else:
        # Sample strategy: load metadata for ranking
        metadata = load_metadata(username)
        if metadata:
            # Sort video IDs by engagement rate
            ranked_ids = [v["video_id"] for v in metadata["videos"]]
            file_lookup = {f.stem: f for f in files}

            # Top 30
            top_ids = [vid for vid in ranked_ids[:30] if vid in file_lookup]
            # Bottom 10
            bottom_ids = [vid for vid in ranked_ids[-10:] if vid in file_lookup]
            # Random 10 from the middle
            middle_ids = [vid for vid in ranked_ids[30:-10] if vid in file_lookup]
            import random
            random_ids = random.sample(middle_ids, min(10, len(middle_ids)))

            selected_ids = list(set(top_ids + bottom_ids + random_ids))
            selected = [file_lookup[vid] for vid in selected_ids if vid in file_lookup]
        else:
            selected = files[:max_count]

    # Concatenate
    parts = []
    for f in selected:
        video_id = f.stem
        text = f.read_text(encoding="utf-8").strip()
        # Truncate very long transcripts
        if len(text) > 2000:
            text = text[:2000] + "... [truncated]"
        parts.append(f"--- Video {video_id} ---\n{text}")

    return "\n\n".join(parts)


def _get_channel_stats(username: str, score_cards: list[dict]) -> dict:
    """Calculate channel-level stats for prompts."""
    metadata = load_metadata(username)
    if not metadata:
        return {
            "total_videos": len(score_cards),
            "date_range": "unknown",
            "avg_engagement": 0,
            "total_views": 0,
            "avg_views": 0,
        }

    videos = metadata["videos"]
    total_views = sum(v.get("view_count", 0) for v in videos)
    avg_views = round(total_views / len(videos)) if videos else 0
    avg_engagement = round(
        sum(v.get("engagement_rate", 0) for v in videos) / len(videos), 2
    ) if videos else 0

    # Date range
    dates = [v.get("upload_date", "") for v in videos if v.get("upload_date")]
    if dates:
        date_range = f"{min(dates)} to {max(dates)}"
    else:
        date_range = "unknown"

    return {
        "total_videos": len(score_cards),
        "date_range": date_range,
        "avg_engagement": avg_engagement,
        "total_views": total_views,
        "avg_views": avg_views,
    }


def generate_style_profile(username: str, gemini: GeminiClient = None) -> str:
    """
    Generate a style profile from all scored videos.

    Args:
        username: TikTok username
        gemini: GeminiClient instance

    Returns:
        Path to saved style_profile.md
    """
    if gemini is None:
        gemini = GeminiClient()

    print(f"Generating style profile for @{username}...")

    # Load data
    score_cards = _load_all_score_cards(username)
    if not score_cards:
        raise ValueError(f"No score cards found for @{username}. Run scoring first.")

    transcripts = _load_transcripts(username)
    stats = _get_channel_stats(username, score_cards)

    # Build prompt
    template = _load_prompt("style_profile.txt")
    prompt = template.replace("{username}", username)
    prompt = prompt.replace("{total_videos}", str(stats["total_videos"]))
    prompt = prompt.replace("{date_range}", stats["date_range"])
    prompt = prompt.replace("{avg_engagement}", str(stats["avg_engagement"]))
    prompt = prompt.replace("{score_cards}", json.dumps(score_cards, indent=1))
    prompt = prompt.replace("{transcripts}", transcripts)

    # Call smart model (prose output)
    print(f"  Sending to smart model ({len(score_cards)} score cards, transcripts)...")
    response = gemini.call_smart(prompt, json_mode=False)

    # Save
    profile_path = DATA_DIR / username / "style_profile.md"
    profile_path.write_text(response, encoding="utf-8")

    print(f"  Style profile saved to {profile_path}")
    return str(profile_path)


def generate_audit_report(username: str, gemini: GeminiClient = None) -> str:
    """
    Generate a comprehensive audit report.

    Args:
        username: TikTok username
        gemini: GeminiClient instance

    Returns:
        Path to saved audit report
    """
    if gemini is None:
        gemini = GeminiClient()

    print(f"Generating audit report for @{username}...")

    # Load data
    score_cards = _load_all_score_cards(username)
    if not score_cards:
        raise ValueError(f"No score cards found for @{username}. Run scoring first.")

    stats = _get_channel_stats(username, score_cards)

    # Load style profile (should have been generated first)
    profile_path = DATA_DIR / username / "style_profile.md"
    if profile_path.exists():
        style_profile = profile_path.read_text(encoding="utf-8")
    else:
        style_profile = "(Style profile not yet generated)"

    # Load playbook
    playbook = _load_playbook()

    # Build prompt
    template = _load_prompt("audit_report.txt")
    prompt = template.replace("{username}", username)
    prompt = prompt.replace("{total_videos}", str(stats["total_videos"]))
    prompt = prompt.replace("{date_range}", stats["date_range"])
    prompt = prompt.replace("{avg_engagement}", str(stats["avg_engagement"]))
    prompt = prompt.replace("{total_views}", f"{stats['total_views']:,}")
    prompt = prompt.replace("{avg_views}", f"{stats['avg_views']:,}")
    prompt = prompt.replace("{style_profile}", style_profile)
    prompt = prompt.replace("{score_cards}", json.dumps(score_cards, indent=1))
    prompt = prompt.replace("{playbook}", playbook)
    prompt = prompt.replace("{date}", datetime.now().strftime("%Y-%m-%d"))

    # Call smart model
    print(f"  Sending to smart model ({len(score_cards)} score cards + playbook)...")
    response = gemini.call_smart(prompt, json_mode=False)

    # Save
    reports_dir = DATA_DIR / username / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"audit_{timestamp}.md"
    report_path.write_text(response, encoding="utf-8")

    print(f"  Audit report saved to {report_path}")
    return str(report_path)


def generate_full_audit(username: str, gemini: GeminiClient = None) -> dict:
    """
    Generate both style profile and audit report in one call.
    Always regenerates the style profile from ALL current score cards
    before building the report, ensuring it reflects the latest data.

    Args:
        username: TikTok username
        gemini: GeminiClient instance

    Returns:
        {"style_profile_path": str, "audit_report_path": str}
    """
    if gemini is None:
        gemini = GeminiClient()

    # Always regenerate style profile from current scores
    profile_path = generate_style_profile(username, gemini)

    # Then generate audit report using fresh profile
    report_path = generate_audit_report(username, gemini)

    return {
        "style_profile_path": profile_path,
        "audit_report_path": report_path,
    }


# ============================================================
# Mode 2 — Competitor Intelligence Report
# ============================================================


def _load_all_analyses(username: str) -> list[dict]:
    """Load all analysis JSON files for a competitor channel."""
    analyses_dir = DATA_DIR / username / "analyses"
    if not analyses_dir.exists():
        return []

    analyses = []
    for analysis_file in sorted(analyses_dir.glob("*.json")):
        # Skip raw response files
        if analysis_file.stem.endswith("_raw"):
            continue
        try:
            with open(analysis_file, "r", encoding="utf-8") as f:
                analyses.append(json.load(f))
        except Exception as e:
            print(f"  Warning: could not load {analysis_file.name}: {e}")

    return analyses


def _get_competitor_stats(username: str, analyses: list[dict]) -> dict:
    """Calculate stats for competitor report prompt."""
    metadata = load_metadata(username)

    # Count triaged out from processed.json
    processed_path = DATA_DIR / username / "processed.json"
    triaged_out = 0
    if processed_path.exists():
        with open(processed_path, "r", encoding="utf-8") as f:
            processed = json.load(f)
        triaged_out = sum(
            1 for v in processed.values() if v.get("status") == "triaged_out"
        )

    # Engagement rate from analysed videos
    engagement_rates = []
    if metadata:
        analysed_ids = set()
        for a in analyses:
            vid = a.get("video_id", "")
            if vid:
                analysed_ids.add(vid)
        for video in metadata["videos"]:
            if video["video_id"] in analysed_ids:
                engagement_rates.append(video.get("engagement_rate", 0))

    avg_engagement = round(
        sum(engagement_rates) / len(engagement_rates), 2
    ) if engagement_rates else 0

    # Date range
    dates = []
    if metadata:
        for video in metadata["videos"]:
            if video.get("upload_date"):
                dates.append(video["upload_date"])
    date_range = f"{min(dates)} to {max(dates)}" if dates else "unknown"

    return {
        "total_analysed": len(analyses),
        "triaged_out": triaged_out,
        "date_range": date_range,
        "avg_engagement": avg_engagement,
    }


def generate_competitor_report(
    competitor_username: str,
    style_profile_username: str = None,
    gemini: GeminiClient = None,
) -> str:
    """
    Generate a competitor intelligence report.

    Args:
        competitor_username: The competitor's TikTok username
        style_profile_username: Username whose style profile to include (optional)
        gemini: GeminiClient instance

    Returns:
        Path to saved competitor report
    """
    if gemini is None:
        gemini = GeminiClient()

    print(f"Generating competitor report for @{competitor_username}...")

    # Load analyses
    analyses = _load_all_analyses(competitor_username)
    if not analyses:
        raise ValueError(
            f"No analyses found for @{competitor_username}. Run competitor analysis first."
        )

    stats = _get_competitor_stats(competitor_username, analyses)

    # Load playbook
    playbook = _load_playbook()

    # Style profile section
    if style_profile_username:
        profile = load_style_profile(style_profile_username)
        if profile:
            style_profile_section = (
                f"## Your Style Profile (@{style_profile_username})\n\n"
                "Use this profile to contextualise recommendations — "
                "how should this creator specifically apply lessons from the competitor?\n\n"
                f"{profile}"
            )
        else:
            style_profile_section = "(No style profile available)"
    else:
        style_profile_section = "(No style profile provided — give generic recommendations)"

    # Build prompt
    template = _load_prompt("competitor_report.txt")
    prompt = template.replace("{competitor_username}", competitor_username)
    prompt = prompt.replace("{total_analysed}", str(stats["total_analysed"]))
    prompt = prompt.replace("{triaged_out}", str(stats["triaged_out"]))
    prompt = prompt.replace("{date_range}", stats["date_range"])
    prompt = prompt.replace("{avg_engagement}", str(stats["avg_engagement"]))
    prompt = prompt.replace("{analyses}", json.dumps(analyses, indent=1))
    prompt = prompt.replace("{playbook}", playbook)
    prompt = prompt.replace("{style_profile_section}", style_profile_section)
    prompt = prompt.replace("{date}", datetime.now().strftime("%Y-%m-%d"))

    # Call smart model
    print(f"  Sending to smart model ({len(analyses)} analyses + playbook)...")
    response = gemini.call_smart(prompt, json_mode=False)

    # Save
    reports_dir = DATA_DIR / competitor_username / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"competitor_{competitor_username}_{timestamp}.md"
    report_path.write_text(response, encoding="utf-8")

    print(f"  Competitor report saved to {report_path}")
    return str(report_path)


def load_style_profile(username: str) -> str | None:
    """
    Load an existing style profile for a channel.
    Returns the markdown text, or None if not found.
    """
    profile_path = DATA_DIR / username / "style_profile.md"
    if profile_path.exists():
        return profile_path.read_text(encoding="utf-8")
    return None
