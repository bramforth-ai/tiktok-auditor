# 🎬 TikTok Auditor

**A self-hosted AI tool that audits your TikTok channel and reverse-engineers what makes competitors win.**

Scan any public TikTok channel, transcribe the videos automatically, and let Google Gemini score your own content against your style or break down a competitor's hooks, structure, and pacing — all from a clean local web UI.

---

## ✨ What It Does

TikTok Auditor runs entirely on your own machine and gives you two workflows:

### 🪞 Mode 1 — Self Audit (Your Channel)
1. Scans your TikTok channel via `yt-dlp`
2. Pulls transcripts (embedded auto-captions, with a Groq Whisper fallback)
3. Builds a **style profile** that captures *your* voice, structure, and tone
4. Scores each video against that profile using Gemini
5. Generates a full audit report — strengths, weaknesses, and concrete improvements

### 🔭 Mode 2 — Competitor Analysis
1. Scans a competitor's channel
2. **Triages** videos with a cheap/fast Gemini model to filter out noise
3. **Analyses** the survivors with a smarter Gemini model to extract hooks, beats, CTAs, retention tactics
4. Compares their patterns against *your* style profile
5. Generates a competitor report you can actually act on

Everything is saved locally as Markdown so you own your data.

---

## 🧰 Tech Stack

- **Python 3.13+** with FastAPI + Uvicorn (local web server)
- **Jinja2** templates for the UI
- **yt-dlp** for TikTok scraping
- **ffmpeg** for transcript extraction from embedded subtitles
- **Google Gemini** (two-tier: cheap triage model + smart analysis model)
- **Groq Whisper** (optional fallback when videos have no embedded captions)

---

## 📋 Prerequisites

You need these installed **before** setting up the project:

### 1. Python 3.13 or newer
Check with `python3 --version`. If you don't have it:
- **macOS:** `brew install python@3.13`
- **Linux (Debian/Ubuntu):** `sudo apt install python3.13 python3.13-venv`
- **Windows:** Download from [python.org](https://www.python.org/downloads/)

### 2. ffmpeg
Used to pull embedded captions out of TikTok videos.
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg`
- **Windows:** [ffmpeg.org/download.html](https://ffmpeg.org/download.html) (add to PATH)

### 3. yt-dlp
Used to scan channels and download videos.
- **macOS:** `brew install yt-dlp`
- **Linux:** `sudo apt install yt-dlp` *(or `pipx install yt-dlp` for the latest)*
- **Windows:** `winget install yt-dlp` *(or download from the [yt-dlp releases page](https://github.com/yt-dlp/yt-dlp/releases))*

### 4. A Google Gemini API key (required, free)
Get one at **[aistudio.google.com/apikey](https://aistudio.google.com/apikey)**. The free tier is generous enough to run real audits.

### 5. A Groq API key (optional, also free)
Only needed if you want Whisper as a fallback when a video has no embedded captions. Get one at **[console.groq.com/keys](https://console.groq.com/keys)**.

---

## 🚀 Setup

Pick the section for your operating system. Run the commands one at a time from a terminal.

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

Once the server is running, open **[http://localhost:8000](http://localhost:8000)** in your browser. Press `Ctrl+C` in the terminal to stop the server.

Whenever you come back to use the tool again later, you only need to:
1. `cd` into the project folder
2. Re-activate the venv (`source .venv/bin/activate` on macOS/Linux, `.venv\Scripts\Activate.ps1` on Windows)
3. Run `python main.py`

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Your Google Gemini key |
| `GEMINI_TRIAGE_MODEL` | ✅ | `gemini-3-flash-preview` | Cheap/fast model for triage + scoring |
| `GEMINI_SMART_MODEL` | ✅ | `gemini-3.1-pro-preview` | Smart model for deep analysis + reports |
| `GROQ_API_KEY` | ❌ | — | Only needed for Whisper fallback |
| `GEMINI_DELAY_SECONDS` | ❌ | `1` | Delay between Gemini calls (rate-limit safety) |
| `DELETE_VIDEOS_AFTER_TRANSCRIPT` | ❌ | `true` | Delete MP4 files after transcript extraction to save disk |
| `MAX_CONCURRENT_DOWNLOADS` | ❌ | `3` | Parallel downloads when scanning |

> 💡 **Tip:** Gemini model names change over time. If you get a "model not found" error, head to [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) for the current list and update `.env`.

---

## 🗺️ How To Use It

### Step 1 — Set up your own channel
1. Open [http://localhost:8000](http://localhost:8000)
2. Under **Your Channel**, enter your TikTok username (no `@`) and click **Scan**
3. Wait for the scan to finish (it polls and shows live progress)
4. From the channel dashboard, select the videos you want to use as your "style baseline" and run **Self Audit**
5. When scoring finishes, click **Generate Report** — this also creates your `style_profile.md`

### Step 2 — Analyse a competitor
1. Back on the home page, enter a competitor's username under **Competitor Analysis** and **Scan**
2. Open the competitor's dashboard and select the videos you want to analyse
3. Pick *your* channel as the **style profile** to compare against
4. Click **Run Competitor Analysis** — Triage stage filters first, then deep analysis runs on what passes
5. Click **Generate Report** for the final write-up

All reports are saved as Markdown under `data/channels/<username>/reports/` and are also viewable in-browser.

---

## 📂 Project Layout

```
tiktok-auditor/
├── main.py                  # FastAPI app + routes
├── requirements.txt
├── env.example
├── data/
│   ├── prompts/             # Editable Gemini prompt templates
│   └── channels/            # Per-channel scans, transcripts, scores, reports
├── services/
│   ├── tiktok.py            # yt-dlp wrapper (channel scanning)
│   ├── transcriber.py       # ffmpeg subs + Groq Whisper fallback
│   ├── gemini_client.py     # Gemini API wrapper (two model tiers)
│   ├── analyser.py          # Triage / score / analyse pipelines
│   └── reporter.py          # Report generation
├── models/schemas.py        # Pydantic models
├── templates/               # Jinja2 HTML templates
└── static/                  # CSS / JS / assets
```

The Gemini prompts in `data/prompts/` are plain text — **edit them** to tune the auditor's behaviour to your niche.

---

## 🛠️ Troubleshooting

**`ModuleNotFoundError: No module named 'dotenv'`**
Your venv isn't active or deps weren't installed. Run `source .venv/bin/activate` then `pip install -r requirements.txt`.

**`yt-dlp: command not found`**
Install yt-dlp via your package manager (see Prerequisites). On macOS the binary should land at `/opt/homebrew/bin/yt-dlp`.

**Scan finds 0 videos**
TikTok occasionally rate-limits or rotates its scraping defences. Update yt-dlp (`brew upgrade yt-dlp`) — it ships fixes constantly.

**Gemini errors about an unknown model**
The model IDs in `env.example` will drift over time. Check [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) and update `GEMINI_TRIAGE_MODEL` / `GEMINI_SMART_MODEL` in `.env`.

**A video has no transcript**
TikTok auto-captions aren't always present. Add a `GROQ_API_KEY` to enable the Whisper fallback (free tier).

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
