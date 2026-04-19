# 🎬 TikTok Auditor

**A self-hosted AI workbench that learns your TikTok voice from your own videos, then writes ready-to-film scripts in that voice — adapted from competitors you admire, or anchored to current trends you research in real time.**

Scan any public TikTok channel with `yt-dlp`. Pull transcripts from embedded captions (with a Whisper fallback). Let Google Gemini score each video against best-practice rubrics. Build a style profile that captures *your* actual voice. Then use that profile to generate scripts you'd otherwise spend hours writing. Everything runs on your machine. Your data never leaves it.

---

## ✨ What You Can Do

Three workflows, one shared voice model.

### 🪞 Self Audit — your channel
Scan your own TikTok. Score videos. Build a **style profile** that captures your voice, the patterns that work for you, and the anti-patterns you want to avoid. Optionally produce a human-readable audit report.

### 🎭 Competitor Adapter — learn from others
Scan any competitor. Triage filters out videos that won't adapt well to your voice. For each video that survives, get a **rewritten script in your voice**, fact-checked via live Google Search. Output is dual-version: a talking-head-only cut you can film with zero prep, and a richer version with screen-recording segments where they genuinely add value.

### 📈 Trend Generator — what should I talk about now?
Gemini researches current AI / agent / automation trends with live Google Search grounding and writes a research brief. Then it generates **N ready-to-film scripts anchored to that research**, in your voice, with source citations. Dual-version output like the Competitor Adapter.

Every script lives as a markdown file you can open in-browser, edit, download, or delete.

---

## 🧠 How It Thinks

The single biggest thing to understand: there's **one central artifact (your style profile)** and **three workflows that consume it**. You build the profile once, then reuse it everywhere.

```
                ┌───────────────────┐
                │      Scan         │   yt-dlp pulls video metadata + transcripts
                └─────────┬─────────┘
                          ▼
                ┌───────────────────┐
                │    Scorecards     │   per-video LLM analysis (one call per video)
                └─────────┬─────────┘
                          ▼
                ┌───────────────────┐
                │   Style Profile   │   ◄── the central artifact
                └─────────┬─────────┘
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
 ┌─────────────┐   ┌─────────────┐    ┌─────────────┐
 │Audit Report │   │ Competitor  │    │    Trend    │
 │ (optional)  │   │  Rewrites   │    │   Scripts   │
 └─────────────┘   └─────────────┘    └─────────────┘
```

**Scan** pulls videos and transcripts from TikTok — no AI work yet.
**Score** runs one LLM call per selected video. The output is a scorecard JSON.
**Style Profile** reads all your scorecards + transcripts and distils your voice into a markdown document. You build it once and lock it until you choose to refresh.
**Audit Report, Competitor Rewrites, Trend Scripts** all read the profile. Audit is optional (it's just a diagnostic). The other two are what actually produce content you can film.

---

## 🧰 Tech Stack

- **Python 3.13+** with FastAPI + Uvicorn (local web server)
- **Jinja2** templates for the UI
- **yt-dlp** for TikTok scraping
- **ffmpeg** for transcript extraction from embedded subtitles
- **Google Gemini** (two-tier: cheap triage model + smart analysis model)
- **Groq Whisper** (optional fallback when a video has no embedded captions)

---

## 📋 Prerequisites

Install these **before** setting up the project.

### 1. Python 3.13 or newer
Check with `python3 --version`. If you don't have it:
- **macOS:** `brew install python@3.13`
- **Linux (Debian/Ubuntu):** `sudo apt install python3.13 python3.13-venv`
- **Windows:** Download from [python.org](https://www.python.org/downloads/)

### 2. ffmpeg
Used to extract embedded captions from TikTok videos.
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`
- **Windows:** [ffmpeg.org/download.html](https://ffmpeg.org/download.html) (add to PATH)

### 3. yt-dlp
Used to scan channels and download videos.
- **macOS:** `brew install yt-dlp`
- **Linux:** `sudo apt install yt-dlp` *(or `pipx install yt-dlp` for the latest)*
- **Windows:** `winget install yt-dlp` *(or grab a release from the [yt-dlp releases page](https://github.com/yt-dlp/yt-dlp/releases))*

### 4. A Google Gemini API key (required, free)
Get one at **[aistudio.google.com/apikey](https://aistudio.google.com/apikey)**. The free tier is generous enough to run real audits.

### 5. A Groq API key (optional, also free)
Only needed if you want Whisper as a fallback when a video has no embedded captions. Get one at **[console.groq.com/keys](https://console.groq.com/keys)**.

---

## 🚀 Setup

Pick the section for your OS. Run the commands one at a time from a terminal.

### 🍎 macOS / 🐧 Linux

```bash
# 1. Clone the repo
git clone https://github.com/bramforth-ai/tiktok-auditor.git
cd tiktok-auditor

# 2. Create a virtual environment (uses Python 3.13)
python3.13 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp env.example .env
# Now open .env in your editor and paste in your Gemini (and optionally Groq) keys

# 5. Run the app
python main.py
```

### 🪟 Windows (PowerShell)

```powershell
# 1. Clone the repo
git clone https://github.com/bramforth-ai/tiktok-auditor.git
cd tiktok-auditor

# 2. Create a virtual environment
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Configure environment variables
copy env.example .env
# Now open .env in Notepad (or your editor) and paste in your Gemini (and optionally Groq) keys
notepad .env

# 5. Run the app
python main.py
```

> 💡 **Windows note:** If PowerShell blocks the activation script with an "execution policy" error, run this once in an **Admin** PowerShell window:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
> Then re-run `.venv\Scripts\Activate.ps1`.

---

Once the server is running, open **[http://localhost:8000](http://localhost:8000)** in your browser. Press `Ctrl+C` in the terminal to stop it.

To come back later:
1. `cd` into the project folder
2. Re-activate the venv (`source .venv/bin/activate` on macOS/Linux, `.venv\Scripts\Activate.ps1` on Windows)
3. Run `python main.py`

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Your Google Gemini key |
| `GEMINI_TRIAGE_MODEL` | ✅ | `gemini-3-flash-preview` | Cheap/fast model for triage + scoring |
| `GEMINI_SMART_MODEL` | ✅ | `gemini-3.1-pro-preview` | Smart model for deep analysis + reports + script generation |
| `GROQ_API_KEY` | ❌ | — | Only needed for Whisper fallback |
| `GEMINI_DELAY_SECONDS` | ❌ | `1` | Delay between Gemini calls (rate-limit safety) |
| `DELETE_VIDEOS_AFTER_TRANSCRIPT` | ❌ | `true` | Delete MP4 files after transcript extraction to save disk |
| `MAX_CONCURRENT_DOWNLOADS` | ❌ | `3` | Parallel downloads when scanning |

> 💡 **Tip:** Gemini model names change over time. If you get a "model not found" error, check [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) and update `.env`.

---

## 🗺️ How To Use It

### First-time setup (once per creator)

1. Open [http://localhost:8000](http://localhost:8000).
2. Under **Your Channel**, enter your TikTok username (no `@`) and click **Scan My Channel**.
3. When scanning finishes, click **Open Dashboard**. You'll see a four-card pipeline at the top: `1 · Scan`, `2 · Score`, `3 · Profile`, `4 · Audit (optional)`.
4. Scroll down to the video table. Click **Next 50** in the Select row (it skips anything already scored). Then click **Score selected videos** in the action bar at the bottom.
5. Wait. Each video takes a few seconds. Repeat in batches until you've scored as much as you want — 50–100 videos is enough for a solid profile; more is better if you have the Gemini quota.
6. When scoring is done, click **Manage** on the Profile pipe-card (or the Profile page from the top nav). Write your **Lazy Defaults** — phrases, openings, or habits you know underperform. Click **Build profile**.
7. The profile is auto-locked after generation. The dashboard's Profile pipe-card now shows *"Built from N scorecards · [date]"*.
8. **(Optional)** Click **Build audit report** in the Audit pipe-card for a readable diagnostic of your channel. The audit report doesn't feed any other workflow — it's just for you to read.

### Everyday use

#### 🔄 Keep your profile current
After you post new videos: on the dashboard, click **Check for new videos** in the Scan pipe-card. It pulls your 20 most recent uploads. Score them as normal. When the dashboard warns *"N new scorecards since profile was built — rebuild?"* in the Profile pipe-card, head to the Profile page and hit **Unlock** → **Build profile** to refresh it.

#### 🎭 Adapt a competitor
1. On the home page, enter a competitor's username under **Competitor Analysis** and click **Scan Competitor**.
2. Open the competitor's dashboard. A banner at the top confirms *"Using profile from @you"* so you know your voice is in play.
3. Use the Select shortcuts (Next 10 / 20 / 50) to pick strong performers.
4. Click **Adapt selected videos**. Gemini triages them (cheap model filters weak fits), then rewrites each passing script in your voice, dual-version: a talking-head-only cut and a version with screen-recording segments.
5. Each script appears as a markdown file at `/scripts/<you>/<competitor>/<date>/<video_id>` — open in browser, edit, download, or delete.
6. **(Optional)** Click **Build index** to generate a summary table listing every rewrite for that competitor.

#### 📈 Generate trend scripts
1. From home, click **Open Trend Generator** (or navigate to any trend batch listed there).
2. Pick a date window (last 60 days is the default), script count (1–15), optional topic focus ("agent tools, MCP") and exclude ("Make.com, n8n tutorials").
3. Click **Run Trend Generator**. Gemini researches with live Google Search grounding (produces `research.md`), then writes N scripts anchored to that research, in your voice.
4. The batch page lists each script with its topic, hook type, format, and target duration. Click any to view, or use its Delete button to drop it.

All outputs live in `data/channels/<you>/` and every view page has a Download and Delete button.

---

## 🎛️ Customising The Prompts

Every Gemini prompt is a plain `.txt` file in `data/prompts/`. **Edit them** to fit your niche. After saving, the next call picks up the new prompt automatically — no server restart needed.

| File | What it does |
|---|---|
| `style_profile.txt` | Distils your voice, proven patterns, and anti-patterns from your scorecards + transcripts |
| `score_video.txt` | How a single video gets scored against TikTok best practices |
| `audit_report.txt` | Compiles scorecards + profile into your readable audit report |
| `triage_video.txt` | Filters competitor videos that are a bad fit before spending on full analysis |
| `competitor_script.txt` | Rewrites a competitor's script in your voice (with fact-check grounding) |
| `trend_research.txt` | Drives the Google-Search-grounded trend research brief |
| `trend_script.txt` | Writes scripts from the research brief, in your voice (with source citations) |

Tune these if the default style feels off, if you want different output formats, or if you want to add your own rules (e.g. "never mention platform X", "always end with a save ask").

---

## 🧹 Managing Your Work

Things you'll want to know the moment something accumulates.

**Pipeline dashboard** — the four cards at the top of your own channel page (`Scan → Score → Profile → Audit`) always show the current state. If the Profile card shows a drift warning, you've scored videos since the profile was built — rebuild when the drift feels meaningful.

**Row-visibility filter** — the Score pipe-card has a **"Show unprocessed only"** toggle that hides scored rows from the video table so you can focus on what's left.

**Delete buttons** — every competitor card, trend batch, script, and report has a Delete button. Audit reports specifically require you to type the full filename to confirm (they're expensive to regenerate — the single-click delete was causing accidents).

**Orphan scorecards** — sometimes you'll end up with scorecards on disk for videos that are no longer in your channel listing (you deleted them from TikTok, TikTok hid them, or an older scan had a different date range). On the Profile page, a section shows the current orphan count. You can **Try to rescue** (a single yt-dlp call that refetches metadata for each orphan — any video still publicly available comes back) and then **Delete** whatever TikTok confirms is dead.

**Server-side idempotency** — if you accidentally select a video that's already scored and click Score, the server skips it rather than burning an LLM call. Shown in the progress feed as *"already scored — skipped"*.

---

## 📂 Project Layout

```
tiktok-auditor/
├── main.py                  # FastAPI app + all routes
├── requirements.txt
├── env.example
├── LICENSE
├── data/
│   ├── prompts/             # Editable Gemini prompt templates (see above)
│   └── channels/            # Per-channel scans, transcripts, scores, reports, scripts
│       └── <username>/
│           ├── metadata.json            # Video list (rebuilt after each scan)
│           ├── processed.json           # Per-video status (scored / failed / triaged / etc.)
│           ├── videos/                  # yt-dlp .info.json (+ temp .mp4 during transcribe)
│           ├── transcripts/             # .txt per video
│           ├── scores/                  # Scorecard JSON per scored video
│           ├── style_profile.md         # Your voice model
│           ├── style_profile.meta.json  # Built-at + built-from-count (drift tracking)
│           ├── style_profile.md.locked  # Lock sentinel (presence = locked)
│           ├── lazy_defaults.md         # Your self-declared anti-patterns
│           ├── reports/                 # Audit reports + competitor scripts indexes
│           └── generated_scripts/
│               ├── competitor_<name>/<date>/<video_id>.md   # Rewritten competitor scripts
│               └── trend_<date>/                            # One folder per trend batch
│                   ├── research.md
│                   ├── index.md
│                   └── NN_<slug>.md
├── services/
│   ├── tiktok.py            # yt-dlp wrapper (scan, download, refetch)
│   ├── transcriber.py       # ffmpeg caption extraction + Groq Whisper fallback
│   ├── gemini_client.py     # Gemini API wrapper (triage + smart + search-grounded calls)
│   ├── analyser.py          # Score + triage + competitor rewrite pipelines
│   ├── reporter.py          # Style profile, audit report, orphan management, stats
│   └── trend_generator.py   # Trend research + trend script generation
├── models/
│   └── schemas.py           # Pydantic models
├── reference/
│   └── tiktok_playbook.md   # Best-practice reference fed into prompts
├── templates/               # Jinja2 HTML (dashboard, profile, trend, trend_batch, report, index)
└── static/                  # CSS / JS / assets
```

---

## 🛠️ Troubleshooting

**`ModuleNotFoundError: No module named 'dotenv'`**
Your venv isn't active or deps weren't installed. Run `source .venv/bin/activate` (or `.venv\Scripts\Activate.ps1` on Windows) then `pip install -r requirements.txt`.

**`yt-dlp: command not found`**
Install yt-dlp via your package manager (see Prerequisites). On macOS the binary should land at `/opt/homebrew/bin/yt-dlp`.

**Scan finds 0 videos**
TikTok occasionally rate-limits or rotates its scraping defences. Update yt-dlp (`brew upgrade yt-dlp` on macOS) — it ships fixes constantly.

**Gemini errors about an unknown model**
The model IDs in `env.example` drift over time. Check [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) and update `GEMINI_TRIAGE_MODEL` / `GEMINI_SMART_MODEL` in `.env`.

**A video has no transcript**
TikTok auto-captions aren't always present. Add a `GROQ_API_KEY` to enable the Whisper fallback (free tier).

**"No style profile yet" when building an audit report**
Profile generation is now an explicit step. Head to the Profile page and click **Build profile** first. (Earlier versions silently created the profile when you clicked Generate Audit Report — that's no longer the behaviour.)

**Dashboard shows drift warning on the Profile card**
You've scored more videos since your profile was last built. It's not an error; it's telling you the profile could be sharper. When the count feels meaningful, Unlock + Build profile on the Profile page.

**Dashboard shows a different scorecard count than the Profile page**
The Profile page shows *total scorecards on disk*. The dashboard Score card shows *scorecards that match videos currently in metadata.json*. The difference is "orphan scorecards" — use the orphan-cleanup section on the Profile page to rescue or remove them.

**Code changes aren't showing up in the browser**
FastAPI doesn't hot-reload in this setup. `Ctrl+C` your `python main.py` and rerun. Hard-refresh the browser (`Cmd+Shift+R` / `Ctrl+Shift+R`) to shake off cached JS/CSS.

---

## 🎯 Community & Support

- 🏫 **Skool Community:** Join our AI Freedom Finders community for support, discussions, and updates: https://www.skool.com/ai-freedom-finders
- 📱 **TikTok:** Follow for AI tutorials, tips, and behind-the-scenes content: https://www.tiktok.com/@ai_entrepreneur_educator
- 🌐 **Website:** https://bramforth.ai
- 🐛 **GitHub Issues:** Report bugs and request features
- 💬 **Discussions:** Ask questions and share your implementations

Built with ❤️ for the community

Happy building! 🚀

---

## 📝 License

MIT License — feel free to use this in your projects, commercial or otherwise.

Questions? Open an issue or reach out on TikTok [@ai_entrepreneur_educator](https://www.tiktok.com/@ai_entrepreneur_educator)
