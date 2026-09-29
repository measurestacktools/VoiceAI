# VoiceAI — Talk with AI

VoiceAI is a polished, ready-to-run **AI voice assistant**. Tap the orb and speak — your voice is transcribed by Groq, answered by a Groq chat model, and spoken back to you by your browser. With a full text transcript of everything said.

No database. No frontend framework. No fake microphone tricks. Just a clean FastAPI backend + a premium voice-command interface + your own Groq API key.

## Features

- Big animated voice orb with four honest states: idle, listening, thinking, speaking
- Real speech-to-text via Groq Whisper (`whisper-large-v3-turbo`)
- Real answers via a Groq chat model (`openai/gpt-oss-120b`)
- Premium spoken replies via Groq Orpheus TTS (`canopylabs/orpheus-v1-english`, 6 voices) — same API key, no extra setup
- Per-answer 🔊 play button (audio generated on demand), pause/resume, stop, replay, voice picker
- Automatic fallback to your browser's free built-in voices if premium TTS is unavailable
- Live transcript: every user message and AI answer shown as cards with timestamps
- Replay last answer, stop speaking anytime, auto-speak toggle
- Keyboard fallback: full text input when no microphone is available
- Current-session conversation history (server-side, bounded), one-click clear
- Microphone permission handling with plain-language guidance
- Settings panel: paste your Groq key in the UI, verified instantly, kept only in server memory
- Responsive desktop + mobile, graceful degradation everywhere

## Requirements

- Python 3.10 or newer
- A free Groq API key (takes ~2 minutes)
- Internet connection (the STT + AI calls go to Groq's API)
- A browser with microphone access for voice input (see Browser compatibility).
  Typing works everywhere, no microphone needed.

## Installation

```bash
cd VoiceAI
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

Run the automated test suite (needs no API key; only the key-verification test needs internet, and it skips itself offline):

```bash
pip install -r requirements-test.txt
pytest tests/ -q
```

## Creating a Groq API key (free)

1. Go to **https://console.groq.com/keys**
2. Sign up / log in (free tier is enough for this project)
3. Click **Create API Key**
4. Copy the key — it starts with `gsk_...`

## Configuration (pick either option)

**Option A — Settings panel (easiest, no code):**

1. Run the app and open http://127.0.0.1:8002
2. Click **Settings** (top right)
3. Paste your key, click **Save key** — it's verified against Groq instantly

The key is kept only in the server's memory: never written to disk, never logged, never stored in the browser, never shown again. It clears when the server restarts. Use **Remove** in Settings to forget it at any time.

**Option B — `.env` file (permanent):**

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Then open `.env` and paste your key:

```
GROQ_API_KEY=gsk_paste_your_key_here
```

Optional voice settings (defaults work out of the box):

```
TTS_MODEL=canopylabs/orpheus-v1-english
TTS_VOICE=autumn
```

> Models used: STT `whisper-large-v3-turbo` and chat `openai/gpt-oss-120b`, both verified live against the Groq API. If Groq renames models and you see a "model was not found" error, check https://console.groq.com/docs/models and update `STT_MODEL` / `GROQ_MODEL`.

## Running

```bash
uvicorn app:app --reload --port 8002
```

Then open **http://127.0.0.1:8002** in your browser.

- Homepage: `GET /`
- Health check: `GET /api/status` (key state + models)
- Transcribe: `POST /api/transcribe` (multipart `audio`)
- Chat: `POST /api/chat` (JSON `{"text": "..."}`)
- Clear history: `DELETE /api/history`
- Save key: `POST /api/key` · Remove key: `DELETE /api/key`

## Browser microphone permissions

1. Click/tap the orb — the browser asks for microphone permission
2. Click **Allow**
3. Speak, then tap the orb again to stop (or just keep talking — tap when done)

If permission was denied earlier: click the lock/tune icon in the address bar → Site settings → Microphone → Allow, then reload. The app explains all of this inline if anything goes wrong, and typing always works as a fallback.

> Microphone capture requires a **secure context**: `http://localhost` / `http://127.0.0.1` counts as secure, so local use works fine. Over a network, serve via HTTPS.

## How STT → LLM → TTS works

```
You speak + tap the orb
   │  Browser records audio (MediaRecorder → WebM/Opus, or M4A on Safari)
   ▼
POST /api/transcribe  (audio stays in server memory, never saved to disk)
   │  Validated (type, size ≤ 10MB, non-empty) → Groq Whisper STT
   ▼  transcript text
POST /api/chat  (transcript + last 8 conversation turns)
   │  Groq chat model answers concisely (spoken-style, ≤ 400 tokens)
   ▼  answer text
Browser speechSynthesis speaks it aloud (your OS voices, free) + transcript cards
```

## Voice output (premium TTS + fallback)

Each AI answer shows a 🔊 button — audio is generated **on demand** (tap to play),
so nothing is spent until you actually listen:

1. `POST /api/speak` sends the answer text to Groq Orpheus TTS using your existing key
2. Long answers are split into sentence-aware pieces (Orpheus caps input at 200
   characters) and joined into a single WAV on the server
3. Generated audio is **cached in server memory** for the session — replaying the
   same answer costs nothing extra (watch for the 🔊 replay button)
4. The browser plays it back with full pause/resume/stop controls

**One-time approval:** Groq requires accepting the Orpheus model terms once per
account. Open https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
and accept, then premium voices work. **Until then (or if premium ever fails),
VoiceAI automatically falls back to your browser's free built-in voices** — the
app tells you which voice is playing and everything keeps working.

**Cost:** Orpheus English is billed at $22 per 1M characters. A typical spoken
answer (300–800 characters) costs roughly $0.007–$0.018. STT/chat usage is
separate and tiny by comparison. Turn off **Auto-speak** and use the per-answer
🔊 button to spend only on what you replay. The voice picker (Autumn, Diana,
Hannah, Austin, Daniel, Troy) is remembered in your browser only — it's a
preference, not a secret.

## Browser compatibility

- **Chrome / Edge (desktop + Android):** full support — mic recording (WebM/Opus), all voices
- **Safari (macOS / iOS):** supported — recording falls back to M4A; `speechSynthesis` voices depend on the OS; `getUserMedia` needs HTTPS outside localhost
- **Firefox:** mic + chat work; TTS voice availability varies by OS
- **No mic / denied / headless:** the app says so plainly and typing covers everything except recording
- No universal-support claims: if `MediaRecorder` or `speechSynthesis` is missing, the affected buttons disable or explain instead of failing silently

## Troubleshooting

| Problem | What to do |
|---|---|
| `No API key configured` | Click **Settings** and paste your key, or set up `.env`. |
| `That key was rejected by Groq` | Re-copy from https://console.groq.com/keys (no extra spaces). |
| `Microphone access was denied` | Address-bar lock icon → Site settings → Microphone → Allow → reload. |
| `No microphone was found` | Use the text box below the conversation instead. |
| `I couldn't hear any speech` | Speak louder/closer, reduce noise, hold a full second before stopping. |
| `Recording is too large` | Keep messages under ~1 minute (limit is 10MB). |
| `Groq could not process that recording` | Re-record in a quiet room; very short clips often fail. |
| `Groq rate limit reached` | Wait ~1 minute and retry. |
| `Model ... was not found` | Groq renamed a model — check https://console.groq.com/docs/models. |
| Premium voice says "one-time approval" | Open the playground link in the message, accept the Orpheus terms once — browser-voice fallback covers you meanwhile. |
| No voice output | Check the auto-speak toggle is on; some browsers need a click before audio plays; pick a voice in OS settings. |

## Security

- Your `.env` file contains a **secret API key**. Never share it, never commit it. `.gitignore` excludes `.env` and all audio extensions.
- The Settings key lives only in server memory: never on disk, never in logs, never in browser storage, never echoed back in any API response.
- Recordings are never written to disk and filenames are never executed — used as an API label only.
- AI output is inserted with `textContent` (never `innerHTML`); oversized uploads are rejected before being read into memory.
- If a key ever leaks, delete it at https://console.groq.com/keys and create a new one.

## Limitations

- Conversation history is per server run (last 8 turns; cleared on restart or via Clear)
- Answers are capped (~400 tokens) to stay speakable — ask follow-ups for more depth
- STT accuracy depends on mic quality, noise, and accent; short clips (< 1s) often fail
- Browser voices vary by OS; voice choice follows your system defaults
- Single user at a time (local app design — history is shared in the server process)

## Current models used

| Step | Model | Source |
|---|---|---|
| Speech-to-text | `whisper-large-v3-turbo` | Groq (verified in current docs) |
| Chat | `openai/gpt-oss-120b` | Groq (verified live via API) |
| Text-to-speech | `canopylabs/orpheus-v1-english` (+ browser Web Speech fallback) | Groq, same key (~$22/1M chars) |

Groq also offers its own (Orpheus) TTS models — see `GROQ_MODEL`/`STT_MODEL` in `.env.example` and https://console.groq.com/docs/text-to-speech if you ever want server-side voices instead.

## Customization

- **Chat model:** `GROQ_MODEL` in `.env` (e.g. `openai/gpt-oss-20b` for faster/cheaper)
- **STT model:** `STT_MODEL` in `.env` (`whisper-large-v3` for max accuracy)
- **Premium voice:** `TTS_VOICE` in `.env` (`autumn`, `diana`, `hannah`, `austin`, `daniel`, `troy`); the in-app picker overrides it per browser
- **Answer length:** `max_tokens=400` in `api_chat`
- **Memory:** `HISTORY_TURNS = 8` in `app.py`
- **Audio cap:** `MAX_AUDIO_MB` in `.env`
- **Personality:** `SYSTEM_PROMPT` in `app.py` (keep it speakable — no tables/code fences)
- **Theme:** CSS variables at the top of `static/styles.css`; orb states via `.orb[data-state=...]`
