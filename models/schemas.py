"""
TikTok Auditor - Data Models
Pydantic models for all data structures used across both modes.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ============================================================
# Video Metadata (from yt-dlp scan)
# ============================================================


class VideoMetadata(BaseModel):
    """Single video metadata from channel scan."""
    video_id: str
    title: str = ""
    description: str = ""
    upload_date: str = ""  # YYYYMMDD
    duration: int = 0  # seconds
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    repost_count: int = 0  # shares
    save_count: int = 0
    engagement_rate: float = 0.0
    url: str = ""
    has_subtitles: bool = False


class ChannelMetadata(BaseModel):
    """Full channel scan result."""
    username: str
    scanned_at: str  # ISO datetime
    date_from: Optional[str] = None  # YYYYMMDD
    date_to: Optional[str] = None  # YYYYMMDD
    total_videos: int = 0
    videos: list[VideoMetadata] = []


# ============================================================
# Processing State (processed.json)
# ============================================================


class ProcessedVideo(BaseModel):
    """Processing state for a single video."""
    status: str  # downloaded, transcribed, scored, triaged_out, analysed, failed, no_transcript
    mode: str  # self_audit, competitor_intel
    timestamp: str  # ISO datetime
    transcript_source: Optional[str] = None  # embedded_subs, sidecar_subs, whisper
    triage_reason: Optional[str] = None  # reason for triaged_out
    error: Optional[str] = None  # error message if failed


# ============================================================
# Score Card (Mode 1 per-video scoring output)
# ============================================================


class ScoreValues(BaseModel):
    """Numerical scores for a video (all 1-10)."""
    hook_strength: int = Field(ge=1, le=10)
    hook_type: str = ""
    content_structure: int = Field(ge=1, le=10)
    pacing: int = Field(ge=1, le=10)
    cta_presence: int = Field(ge=1, le=10)
    educational_value: int = Field(ge=1, le=10)
    entertainment_value: int = Field(ge=1, le=10)
    rewatch_potential: int = Field(ge=1, le=10)
    save_worthiness: int = Field(ge=1, le=10)
    share_worthiness: int = Field(ge=1, le=10)


class ScoreFlags(BaseModel):
    """Boolean flags for video features."""
    has_hook_in_2_sec: bool = False
    has_pattern_interrupts: bool = False
    has_open_loops: bool = False
    has_cta: bool = False
    shows_result_first: bool = False
    uses_contrarian_hook: bool = False
    demonstrates_not_describes: bool = False


class ScoreOneLiners(BaseModel):
    """Single-sentence observations."""
    hook_note: str = ""
    strongest_moment: str = ""
    biggest_miss: str = ""
    suggested_hook: str = ""


class ScoreMeta(BaseModel):
    """Metadata about the content."""
    topic_category: str = ""
    content_format: str = ""
    estimated_wpm: int = 0
    transcript_word_count: int = 0


class ScoreCard(BaseModel):
    """Complete per-video score card (Mode 1 output)."""
    video_id: str
    scores: ScoreValues
    flags: ScoreFlags
    one_liners: ScoreOneLiners
    meta: ScoreMeta


# ============================================================
# Triage Output (Mode 2 cheap model)
# ============================================================


class TriageResult(BaseModel):
    """Triage pass/fail result from cheap model."""
    passed: bool = Field(alias="pass", default=False)
    relevance_score: int = Field(ge=1, le=10, default=5)
    content_type: str = ""
    reason: str = ""

    class Config:
        populate_by_name = True


# ============================================================
# Analysis + Script (Mode 2 smart model)
# ============================================================


class AnalysisDetail(BaseModel):
    """Detailed analysis of why a video worked."""
    why_it_worked: str = ""
    hook_technique: str = ""
    structure_breakdown: str = ""
    topic_appeal: str = ""
    key_takeaway: str = ""


class RecreatedScript(BaseModel):
    """Recreated script ready to film."""
    hook: str = ""
    body: str = ""
    cta: str = ""
    estimated_duration_seconds: int = 0
    notes: str = ""


class VideoAnalysis(BaseModel):
    """Complete per-video analysis (Mode 2 output)."""
    video_id: str
    triage: TriageResult
    score_card: ScoreCard
    analysis: AnalysisDetail
    recreated_script: RecreatedScript


# ============================================================
# API Request/Response Models
# ============================================================


class ScanRequest(BaseModel):
    """Request to scan a channel."""
    username: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class ProcessRequest(BaseModel):
    """Request to start processing videos."""
    username: str
    video_ids: list[str]
    mode: str  # self_audit or competitor_intel


class ProcessStatus(BaseModel):
    """Current processing status (for polling)."""
    is_processing: bool = False
    current_video: Optional[str] = None
    total: int = 0
    completed: int = 0
    failed: int = 0
    triaged_out: int = 0
    results: list[dict] = []  # last N completed results


class ReportRequest(BaseModel):
    """Request to generate a report."""
    username: str
    mode: str  # self_audit or competitor_intel
