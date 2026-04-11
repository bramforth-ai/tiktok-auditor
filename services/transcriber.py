"""
TikTok Auditor - Transcriber Service
Extracts transcripts from TikTok videos.
Primary: ffmpeg embedded subtitle extraction (TikTok auto-captions)
Fallback: Groq Whisper API (if no subs and GROQ_API_KEY configured)
"""

import os
import re
import json
import math
import wave
import struct
import subprocess
from pathlib import Path


# Whisper config
MAX_FILE_SIZE_MB = 20
CHUNK_DURATION_SEC = 5 * 60  # 5 minutes per chunk
WHISPER_MODEL = "whisper-large-v3-turbo"
SILENCE_THRESHOLD_DB = -60

# Known Whisper hallucination phrases
HALLUCINATION_PHRASES = {
    "thank you", "thank you for watching", "thanks", "thanks for watching",
    "thanks for listening", "hello", "hi", "hey", "bye", "goodbye",
    "good bye", "the end", "subscribe", "please subscribe",
    "like and subscribe",
}
HALLUCINATION_MAX_WORDS = 5


def extract_transcript(video_path: Path) -> dict:
    """
    Extract transcript from a video file.

    Strategy:
    1. Try ffmpeg subtitle extraction (embedded TikTok auto-captions)
    2. If no subtitle stream, check for .vtt/.srt sidecar files
    3. If nothing found, return needs_whisper=True

    Args:
        video_path: Path to the MP4 file

    Returns:
        {
            "success": bool,
            "text": str,
            "word_count": int,
            "source": "embedded_subs" | "sidecar_subs" | "whisper",
            "needs_whisper": bool
        }
    """
    video_path = Path(video_path)

    if not video_path.exists():
        return {
            "success": False,
            "text": "",
            "word_count": 0,
            "source": None,
            "needs_whisper": False,
            "error": f"Video file not found: {video_path}",
        }

    # Strategy 1: Extract embedded subtitles via ffmpeg
    text = _extract_embedded_subs(video_path)
    if text:
        word_count = len(text.split())
        print(f"  Extracted embedded subs: {word_count} words")
        return {
            "success": True,
            "text": text,
            "word_count": word_count,
            "source": "embedded_subs",
            "needs_whisper": False,
        }

    # Strategy 2: Check for sidecar subtitle files
    text = _extract_sidecar_subs(video_path)
    if text:
        word_count = len(text.split())
        print(f"  Extracted sidecar subs: {word_count} words")
        return {
            "success": True,
            "text": text,
            "word_count": word_count,
            "source": "sidecar_subs",
            "needs_whisper": False,
        }

    # No subtitles found
    print(f"  No subtitles found — needs Whisper")
    return {
        "success": False,
        "text": "",
        "word_count": 0,
        "source": None,
        "needs_whisper": True,
    }


def _extract_embedded_subs(video_path: Path) -> str | None:
    """
    Extract embedded subtitle stream from video using ffmpeg.
    Returns cleaned transcript text, or None if no subs found.
    """
    srt_output = video_path.parent / f"{video_path.stem}_subs.srt"

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-map", "0:s:0", str(srt_output)],
            capture_output=True,
            text=True,
        )

        if srt_output.exists() and srt_output.stat().st_size > 0:
            text = _parse_srt(srt_output)
            # Clean up temp file
            srt_output.unlink(missing_ok=True)
            return text if text else None

        # Clean up empty file if created
        srt_output.unlink(missing_ok=True)
        return None

    except Exception as e:
        print(f"  ffmpeg subtitle extraction error: {e}")
        srt_output.unlink(missing_ok=True)
        return None


def _extract_sidecar_subs(video_path: Path) -> str | None:
    """
    Check for .srt or .vtt sidecar files next to the video.
    Returns cleaned transcript text, or None if no sidecar found.
    """
    video_dir = video_path.parent
    video_stem = video_path.stem

    # Check for SRT files
    for srt_file in video_dir.glob(f"{video_stem}*.srt"):
        text = _parse_srt(srt_file)
        if text:
            return text

    # Check for VTT files
    for vtt_file in video_dir.glob(f"{video_stem}*.vtt"):
        text = _parse_vtt(vtt_file)
        if text:
            return text

    return None


def _parse_srt(srt_path: Path) -> str | None:
    """
    Parse SRT file to plain text.
    Strips timestamps, sequence numbers, and HTML tags.
    """
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            raw = f.read()

        lines = raw.strip().split("\n")
        text_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip sequence numbers
            if re.match(r"^\d+$", line):
                continue
            # Skip timestamp lines
            if re.match(r"\d{2}:\d{2}:\d{2}", line):
                continue
            # Remove HTML tags (e.g., <font>)
            line = re.sub(r"<[^>]+>", "", line)
            if line:
                text_lines.append(line)

        if not text_lines:
            return None

        return " ".join(text_lines)

    except Exception as e:
        print(f"  Error parsing SRT {srt_path}: {e}")
        return None


def _parse_vtt(vtt_path: Path) -> str | None:
    """
    Parse VTT (WebVTT) file to plain text.
    Similar to SRT but with slightly different format.
    """
    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            raw = f.read()

        lines = raw.strip().split("\n")
        text_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip WEBVTT header
            if line.startswith("WEBVTT"):
                continue
            # Skip NOTE lines
            if line.startswith("NOTE"):
                continue
            # Skip sequence numbers
            if re.match(r"^\d+$", line):
                continue
            # Skip timestamp lines (VTT uses . instead of , for milliseconds)
            if re.match(r"\d{2}:\d{2}:\d{2}\.\d{3}", line):
                continue
            if "-->" in line:
                continue
            # Remove HTML tags
            line = re.sub(r"<[^>]+>", "", line)
            if line:
                text_lines.append(line)

        if not text_lines:
            return None

        return " ".join(text_lines)

    except Exception as e:
        print(f"  Error parsing VTT {vtt_path}: {e}")
        return None


# ============================================================
# Groq Whisper fallback (from Ghost Stream transcribe.py)
# ============================================================


def transcribe_with_whisper(video_path: Path) -> dict:
    """
    Fallback: Send audio to Groq Whisper API for transcription.

    Only called if extract_transcript returns needs_whisper=True
    AND GROQ_API_KEY is configured.

    Args:
        video_path: Path to the video file

    Returns:
        Same dict structure as extract_transcript.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return {
            "success": False,
            "text": "",
            "word_count": 0,
            "source": None,
            "needs_whisper": True,
            "error": "GROQ_API_KEY not configured",
        }

    try:
        from groq import Groq
    except ImportError:
        return {
            "success": False,
            "text": "",
            "word_count": 0,
            "source": None,
            "needs_whisper": True,
            "error": "groq package not installed",
        }

    video_path = Path(video_path)

    # Step 1: Extract audio from video as mono 16kHz WAV
    audio_path = video_path.parent / f"{video_path.stem}_audio.wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-ac", "1",       # mono
                "-ar", "16000",   # 16kHz
                "-acodec", "pcm_s16le",  # 16-bit PCM
                str(audio_path),
            ],
            capture_output=True,
            check=True,
        )
    except Exception as e:
        return {
            "success": False,
            "text": "",
            "word_count": 0,
            "source": None,
            "needs_whisper": True,
            "error": f"Audio extraction failed: {e}",
        }

    try:
        # Step 2: Check for silence
        if _is_silent_audio(audio_path):
            print(f"  Audio is silent — skipping Whisper")
            return {
                "success": True,
                "text": "",
                "word_count": 0,
                "source": "whisper",
                "needs_whisper": False,
            }

        # Step 3: Chunk if needed, then transcribe
        chunks = _chunk_audio(audio_path)
        all_segments = []

        client = Groq(api_key=api_key)

        for i, chunk_info in enumerate(chunks):
            chunk_path = chunk_info["path"]

            # Skip silent chunks
            if _is_silent_audio(chunk_path):
                print(f"  Skipping silent chunk {i + 1}")
                if chunk_info["is_temp"]:
                    chunk_path.unlink(missing_ok=True)
                continue

            print(f"  Transcribing chunk {i + 1}/{len(chunks)}...")

            try:
                with open(chunk_path, "rb") as audio_file:
                    transcription = client.audio.transcriptions.create(
                        file=(chunk_path.name, audio_file, "audio/wav"),
                        model=WHISPER_MODEL,
                        response_format="verbose_json",
                        language="en",
                    )

                # Extract segments
                if hasattr(transcription, "segments") and transcription.segments:
                    offset_sec = chunk_info["offset_ms"] / 1000.0
                    for seg in transcription.segments:
                        start = (seg.start if hasattr(seg, "start") else seg.get("start", 0))
                        end = (seg.end if hasattr(seg, "end") else seg.get("end", 0))
                        text = (seg.text if hasattr(seg, "text") else seg.get("text", ""))
                        all_segments.append({
                            "start": start + offset_sec,
                            "end": end + offset_sec,
                            "text": text.strip(),
                        })

            finally:
                if chunk_info["is_temp"]:
                    chunk_path.unlink(missing_ok=True)

        # Clean up temp chunk directory
        chunk_dir = audio_path.parent / "temp_chunks"
        if chunk_dir.exists():
            try:
                chunk_dir.rmdir()
            except OSError:
                pass

        # Filter hallucinations
        all_segments = _filter_hallucinations(all_segments)

        # Combine text
        text = " ".join(seg["text"] for seg in all_segments if seg["text"])
        word_count = len(text.split()) if text else 0

        print(f"  Whisper transcription: {word_count} words")

        return {
            "success": True,
            "text": text,
            "word_count": word_count,
            "source": "whisper",
            "needs_whisper": False,
        }

    finally:
        # Clean up audio file
        audio_path.unlink(missing_ok=True)


def _is_silent_audio(audio_path: Path) -> bool:
    """Check if audio file is mostly silence."""
    try:
        with wave.open(str(audio_path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            n_frames = wf.getnframes()

            if n_frames == 0:
                return True

            raw_data = wf.readframes(n_frames)

            if sample_width == 2:
                fmt = f"<{n_frames * n_channels}h"
                samples = struct.unpack(fmt, raw_data)
                max_val = 32767.0
            elif sample_width == 1:
                samples = [b - 128 for b in raw_data]
                max_val = 127.0
            else:
                return False

            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / len(samples))

            if rms == 0:
                return True

            db = 20 * math.log10(rms / max_val)
            return db < SILENCE_THRESHOLD_DB

    except Exception:
        return False


def _chunk_audio(audio_path: Path) -> list[dict]:
    """
    Split audio file into chunks if over MAX_FILE_SIZE_MB.
    Returns list of {path, offset_ms, is_temp}.
    """
    file_size_mb = audio_path.stat().st_size / (1024 * 1024)

    if file_size_mb <= MAX_FILE_SIZE_MB:
        return [{"path": audio_path, "offset_ms": 0, "is_temp": False}]

    print(f"  Audio is {file_size_mb:.1f}MB — chunking...")

    # Get duration
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True, text=True, check=True,
        )
        total_duration = float(result.stdout.strip())
    except Exception:
        return [{"path": audio_path, "offset_ms": 0, "is_temp": False}]

    chunk_dir = audio_path.parent / "temp_chunks"
    chunk_dir.mkdir(exist_ok=True)

    chunks = []
    offset_sec = 0
    chunk_index = 0

    while offset_sec < total_duration:
        duration = min(CHUNK_DURATION_SEC, total_duration - offset_sec)
        chunk_path = chunk_dir / f"chunk_{chunk_index:03d}.wav"

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(audio_path),
                "-ss", str(offset_sec),
                "-t", str(duration),
                "-acodec", "copy",
                str(chunk_path),
            ],
            capture_output=True, check=True,
        )

        chunks.append({
            "path": chunk_path,
            "offset_ms": int(offset_sec * 1000),
            "is_temp": True,
        })

        offset_sec += duration
        chunk_index += 1

    print(f"  Split into {len(chunks)} chunks")
    return chunks


def _filter_hallucinations(segments: list[dict]) -> list[dict]:
    """
    Remove hallucinated segments that Whisper produces during silence.
    """
    if not segments:
        return segments

    filtered = []
    repeat_count = 0
    last_text = None

    for seg in segments:
        text = seg["text"].strip()
        text_clean = re.sub(r"[^\w\s]", "", text).strip().lower()
        word_count = len(text_clean.split()) if text_clean else 0

        # Skip empty
        if not text_clean:
            continue

        # Skip known hallucinations
        if text_clean in HALLUCINATION_PHRASES:
            continue

        # Skip repeated short phrases (3+ consecutive)
        if word_count <= HALLUCINATION_MAX_WORDS:
            if text_clean == last_text:
                repeat_count += 1
                if repeat_count >= 2:
                    continue
            else:
                repeat_count = 0
                last_text = text_clean
        else:
            repeat_count = 0
            last_text = None

        filtered.append(seg)

    removed = len(segments) - len(filtered)
    if removed > 0:
        print(f"  Filtered {removed} hallucinated segments")

    return filtered


def save_transcript(username: str, video_id: str, text: str, source: str) -> Path:
    """
    Save transcript text to the channel's transcripts folder.

    Args:
        username: TikTok username
        video_id: Video ID
        text: Transcript text
        source: How transcript was obtained

    Returns:
        Path to saved transcript file
    """
    from services.tiktok import DATA_DIR

    transcript_dir = DATA_DIR / username / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = transcript_dir / f"{video_id}.txt"
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(text)

    return transcript_path
