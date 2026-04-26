"""
TikTok Auditor — Trend Generator Service

Two-step pipeline:
  1. run_trend_research  → Gemini w/ Google Search grounding produces a
     research brief on current AI/agent/automation trends.
  2. generate_trend_scripts → Gemini applies the creator's style profile
     to the research and produces N ready-to-film scripts.

Output lives under data/channels/<own_username>/generated_scripts/trend_<date>/
"""

import json
import re
from pathlib import Path
from datetime import datetime, timedelta

from services.gemini_client import GeminiClient
from services.tiktok import DATA_DIR
from services.analyser import DUAL_VERSION_BLOCK
from services.reporter import load_style_profile, _load_lazy_defaults


def _load_prompt(name: str) -> str:
    prompt_path = Path(__file__).parent.parent / "data" / "prompts" / name
    return prompt_path.read_text(encoding="utf-8")


def _trend_batch_dir(own_username: str, batch_date: str) -> Path:
    return (
        DATA_DIR / own_username / "generated_scripts" / f"trend_{batch_date}"
    )


def run_trend_research(
    own_username: str,
    date_window_days: int = 60,
    topic_focus: str = "",
    topic_exclude: str = "",
    gemini: GeminiClient = None,
) -> dict:
    """
    Run the trend research call. Returns a dict with the batch_date and research_path.

    Side effect: writes research.md under the batch directory.
    """
    if gemini is None:
        gemini = GeminiClient()

    today = datetime.now()
    batch_date = today.strftime("%Y-%m-%d")
    window_start = (today - timedelta(days=date_window_days)).strftime("%Y-%m-%d")

    template = _load_prompt("trend_research.txt")
    prompt = template.replace("{date_window}", f"last {date_window_days} days")
    prompt = prompt.replace("{date_absolute}", f"{window_start} to {batch_date}")
    prompt = prompt.replace("{topic_focus}", topic_focus.strip() or "(none)")
    prompt = prompt.replace("{topic_exclude}", topic_exclude.strip() or "(none)")

    print(f"Running trend research (last {date_window_days} days) with Google Search grounding...")
    research_md = gemini.call_smart_with_search(prompt)

    batch_dir = _trend_batch_dir(own_username, batch_date)
    batch_dir.mkdir(parents=True, exist_ok=True)
    research_path = batch_dir / "research.md"
    research_path.write_text(research_md, encoding="utf-8")

    print(f"  Research saved to {research_path}")
    return {
        "batch_date": batch_date,
        "research_path": str(research_path),
        "batch_dir": str(batch_dir),
    }


def _clean_slug(raw: str, fallback_idx: int) -> str:
    """Normalise a slug to kebab-case, safe for filenames and URLs."""
    if not raw:
        return f"script-{fallback_idx:02d}"
    s = raw.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:60] or f"script-{fallback_idx:02d}"


def _render_script_md(script: dict, batch_date: str) -> str:
    """Render a single script dict from the LLM into frontmatter+markdown with
    both talking-head and screen-recording versions plus a Sources section."""
    versions = script.get("versions") or {}
    th = versions.get("talking_head") or {}
    sr = versions.get("with_screen_recording") or {}

    def version_block(title: str, subtitle: str, v: dict) -> str:
        return (
            f"## {title}\n"
            f"*{subtitle}*\n\n"
            "**Hook (0–3s):**\n\n"
            f"{(v.get('hook') or '').strip() or '—'}\n\n"
            "**Body:**\n\n"
            f"{(v.get('body') or '').strip() or '—'}\n\n"
            "**CTA:**\n\n"
            f"{(v.get('cta') or '').strip() or '—'}\n"
        )

    sources = script.get("sources") or []
    if sources:
        lines = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            claim = (s.get("claim") or "").strip()
            section = (s.get("research_section") or "").strip()
            url = (s.get("url") or "").strip()
            parts = []
            if claim:
                parts.append(f'"{claim}"')
            if section:
                parts.append(f"research § {section}")
            if url:
                parts.append(f"<{url}>")
            if parts:
                lines.append("- " + " — ".join(parts))
        sources_block = "\n".join(lines) if lines else "_No sources cited — treat claims with caution._"
    else:
        sources_block = "_No sources cited — treat claims with caution._"

    return (
        "---\n"
        f"topic: {script.get('topic', '').strip()}\n"
        f"slug: {script.get('slug', '').strip()}\n"
        f"hook_type: {script.get('hook_type', '').strip()}\n"
        f"format_tag: {script.get('format_tag', '').strip()}\n"
        f"target_duration_seconds: {script.get('target_duration_seconds', '')}\n"
        f"generated: {batch_date}\n"
        "---\n\n"
        f"# {script.get('topic', '').strip()}\n\n"
        "---\n\n"
        + version_block(
            "Version A — Talking Head Only",
            "No screen recording. Film in one take with zero prep.",
            th,
        )
        + "\n---\n\n"
        + version_block(
            "Version B — With Screen Recording",
            "Same beats, with [screen recording: ...] segments where they add value.",
            sr,
        )
        + "\n---\n\n## Sources\n\n"
        f"{sources_block}\n\n"
        "## Notes\n\n"
        f"{(script.get('notes') or '').strip() or '—'}\n"
    )


def generate_trend_scripts(
    own_username: str,
    batch_date: str,
    research_path: str,
    script_count: int = 5,
    gemini: GeminiClient = None,
) -> list[str]:
    """
    Generate N scripts from a research brief, applying the creator's style profile.

    Each script contains two versions: talking-head-only and with-screen-recording.
    Writes each script to <batch_dir>/<nn>_<slug>.md and returns the list of paths.
    """
    if gemini is None:
        gemini = GeminiClient()

    research = Path(research_path).read_text(encoding="utf-8")

    style_profile = load_style_profile(own_username)
    if not style_profile:
        raise ValueError(
            f"No style profile found for @{own_username}. "
            f"Generate it via the Profile screen first."
        )

    lazy_defaults = _load_lazy_defaults(own_username)

    template = _load_prompt("trend_script.txt")
    prompt = template.replace("{research}", research)
    prompt = prompt.replace("{style_profile}", style_profile)
    prompt = prompt.replace("{lazy_defaults}", lazy_defaults)
    prompt = prompt.replace("{production_style_instructions}", DUAL_VERSION_BLOCK)
    prompt = prompt.replace("{script_count}", str(script_count))
    prompt = prompt.replace("{date}", batch_date)

    print(f"Generating {script_count} trend scripts (dual versions)...")
    response = gemini.call_smart(prompt, json_mode=True)

    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        # Retry once — mirror the pattern used elsewhere
        print("  Invalid JSON — retrying once...")
        response = gemini.call_smart(prompt, json_mode=True)
        payload = json.loads(response)

    batch_dir = _trend_batch_dir(own_username, batch_date)
    batch_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(payload, list):
        scripts = payload
    elif isinstance(payload, dict):
        scripts = payload.get("scripts", [])
    else:
        scripts = []

    scripts = [s for s in scripts if isinstance(s, dict)]

    if not scripts:
        # Persist the raw response so the failure is debuggable instead of opaque.
        debug_path = batch_dir / "scripts_raw_response.json"
        debug_path.write_text(response, encoding="utf-8")
        raise ValueError(
            f"Gemini returned no usable scripts. Raw response saved to {debug_path}."
        )

    written = []
    for i, script in enumerate(scripts, start=1):
        slug = _clean_slug(script.get("slug", ""), i)
        filename = f"{i:02d}_{slug}.md"
        rendered = _render_script_md(script, batch_date)
        path = batch_dir / filename
        path.write_text(rendered, encoding="utf-8")
        written.append(str(path))
        print(f"  [{i:02d}] {filename}")

    return written


def list_trend_batch_scripts(own_username: str, batch_date: str) -> list[dict]:
    """Return metadata for every script file in a trend batch. No LLM calls.

    Each entry: {filename, stem, meta (parsed frontmatter dict)}.
    Raises ValueError if the batch folder itself is missing (but returns [] if
    the folder exists with no scripts yet)."""
    batch_dir = _trend_batch_dir(own_username, batch_date)
    if not batch_dir.exists():
        raise ValueError(f"Trend batch {batch_date} not found for @{own_username}.")

    scripts = []
    for script_file in sorted(batch_dir.glob("*.md")):
        if script_file.name in ("research.md", "index.md"):
            continue
        meta = _parse_frontmatter(script_file.read_text(encoding="utf-8"))
        scripts.append({
            "filename": script_file.name,
            "stem": script_file.stem,
            "meta": meta,
        })
    return scripts


def generate_trend_index(own_username: str, batch_date: str) -> str:
    """
    Build a filesystem-scan index for one trend batch. No LLM calls.
    Writes index.md in the batch directory and returns its path.
    """
    scripts = list_trend_batch_scripts(own_username, batch_date)
    batch_dir = _trend_batch_dir(own_username, batch_date)

    if not scripts:
        raise ValueError(f"No scripts found in trend batch {batch_date}.")

    lines = [
        f"# Trend Scripts — @{own_username} — {batch_date}",
        "",
        f"Total scripts: {len(scripts)}  ",
        f"Research: [research.md](/trend/{own_username}/{batch_date}/research)",
        "",
        "| # | Topic | Hook Type | Format | Target | Open |",
        "|---|-------|-----------|--------|--------|------|",
    ]
    for i, s in enumerate(scripts, start=1):
        m = s["meta"]
        view_url = f"/trend/{own_username}/{batch_date}/{s['stem']}"
        lines.append(
            f"| {i} | {m.get('topic', '?')} | "
            f"`{m.get('hook_type', '?')}` | "
            f"{m.get('format_tag', '?')} | "
            f"{m.get('target_duration_seconds', '?')}s | "
            f"[view]({view_url}) |"
        )

    index_path = batch_dir / "index.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(index_path)


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(markdown: str) -> dict:
    m = _FRONTMATTER_RE.match(markdown)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def list_trend_batches(own_username: str) -> list[dict]:
    """Return all trend batch dates (with script counts + last-modified time) for a
    creator, newest first."""
    trend_root = DATA_DIR / own_username / "generated_scripts"
    if not trend_root.exists():
        return []
    batches = []
    for child in trend_root.iterdir():
        if not child.is_dir() or not child.name.startswith("trend_"):
            continue
        date = child.name[len("trend_"):]
        script_files = [
            p for p in child.glob("*.md")
            if p.name not in ("research.md", "index.md")
        ]
        # Use the newest file's mtime as "last modified" (captures reruns into the
        # same date folder). Fall back to the folder mtime if nothing else.
        mtimes = [p.stat().st_mtime for p in script_files]
        if not mtimes:
            mtimes = [child.stat().st_mtime]
        last_modified = datetime.fromtimestamp(max(mtimes)).strftime("%Y-%m-%d %H:%M")
        batches.append({
            "date": date,
            "script_count": len(script_files),
            "has_research": (child / "research.md").exists(),
            "last_modified": last_modified,
        })
    batches.sort(key=lambda b: b["date"], reverse=True)
    return batches
