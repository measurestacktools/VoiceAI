"""VoiceAI — AI voice assistant: speak -> Groq STT -> Groq LLM -> browser TTS.

Flow:
  Browser records audio (MediaRecorder) and POSTs it to /api/transcribe.
  The server validates it and transcribes it with Groq (Whisper), in memory.
  The transcript is POSTed to /api/chat, answered by a Groq chat model.
  The browser speaks the answer with the built-in Web Speech API (free,
  no extra key). Nothing but the two Groq calls leaves the machine.

Docs verified live 2026:
- STT endpoint (OpenAI-compatible): https://api.groq.com/openai/v1/audio/transcriptions
- STT model: whisper-large-v3-turbo (best speed/price; whisper-large-v3 = most accurate)
- Audio formats: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm; max 25MB
- Chat: client.chat.completions.create(model, messages, temperature, max_tokens)
- Chat model: openai/gpt-oss-120b (verified live; Llama 3.x no longer served)
- TTS: browser-native Web Speech API (no Groq TTS dependency, zero cost)
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("voiceai")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()
try:
    MAX_AUDIO_MB = float(os.getenv("MAX_AUDIO_MB", "10"))
except ValueError:
    MAX_AUDIO_MB = 10.0
if not MAX_AUDIO_MB > 0:
    MAX_AUDIO_MB = 10.0
if MAX_AUDIO_MB > 25:
    MAX_AUDIO_MB = 25.0  # Groq STT hard limit
MAX_AUDIO_BYTES = int(MAX_AUDIO_MB * 1024 * 1024)

# Matches Groq's documented STT formats (+ common browser container aliases).
ALLOWED_EXT = {".webm", ".mp4", ".m4a", ".mpeg", ".mpga", ".mp3", ".ogg", ".oga", ".wav", ".flac"}
ALLOWED_MIME = {
    "audio/webm", "audio/mp4", "audio/x-m4a", "audio/m4a", "audio/mpeg",
    "audio/mp3", "audio/ogg", "audio/wav", "audio/x-wav", "audio/wave",
    "audio/flac", "audio/x-flac", "video/webm",
}

HISTORY_TURNS = 8  # previous Q/A pairs kept; bounds request size

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="VoiceAI", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Serve the app icon so browsers never 404 on /favicon.ico."""
    return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


# In-memory API key entered via the Settings panel in the UI.
# - Takes precedence over the .env key for this server process only.
# - Never written to disk, never logged, never sent back to the browser.
# - Cleared when the server restarts (use .env for a permanent key).
_session_key: str | None = None

# Current-session conversation history (in memory only, cleared on restart).
_history: list[dict] = []


class KeyPayload(BaseModel):
    key: str = ""


class ChatPayload(BaseModel):
    text: str = ""


def _effective_key() -> str:
    return (_session_key or GROQ_API_KEY).strip()


def _key_source() -> str | None:
    if _session_key:
        return "settings"
    if GROQ_API_KEY:
        return "env"
    return None


def _verify_key(key: str) -> None:
    client = OpenAI(api_key=key, base_url=GROQ_BASE_URL, timeout=15.0)
    client.models.list()


def _friendly_groq_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, AuthenticationError):
        return 401, (
            "Your Groq API key was rejected. Replace it via Settings (top right) "
            "or check GROQ_API_KEY in your .env file (no extra spaces or quotes), "
            "then try again. Get a free key at https://console.groq.com/keys"
        )
    if isinstance(exc, RateLimitError):
        return 429, (
            "Groq rate limit reached (too many requests). "
            "Wait about a minute and try again. Details: "
            "https://console.groq.com/docs/rate-limits"
        )
    if isinstance(exc, APIConnectionError):
        return 503, (
            "Could not reach the Groq API. Check your internet connection and "
            "try again. If Groq is having an outage, wait a few minutes."
        )
    if isinstance(exc, APIStatusError):
        status = exc.status_code or 502
        detail = ""
        try:
            detail = str(exc.response.json())[:400]
        except Exception:
            detail = str(exc)[:400]
        if status == 400 and ("audio" in detail.lower() or "file" in detail.lower()):
            return 400, (
                "Groq could not process that recording (unsupported or corrupt audio). "
                "Try speaking closer to the microphone in a quiet room and record again."
            )
        if status == 413:
            return 413, (
                "The recording was too large for Groq to process. "
                "Please record a shorter message and try again."
            )
        if status in (498, 499):
            return 503, (
                "Groq is temporarily at capacity and did not process the request. "
                "Wait a minute and try again — you will not be charged for this."
            )
        if status == 404:
            return 502, (
                "A Groq model was not found. It may have been renamed — check "
                "https://console.groq.com/docs/models for current model names "
                "and update STT_MODEL / GROQ_MODEL in your .env file."
            )
        return status, f"Groq API error (HTTP {status}). Details: {detail}"
    return 500, f"Unexpected server error: {str(exc)[:300]}"


SYSTEM_PROMPT = (
    "You are a helpful voice assistant. Answer clearly and naturally, as if "
    "speaking to the user out loud. Keep responses concise enough to be spoken "
    "(a few short sentences or bullets). Do not use markdown tables, code "
    "fences, or complex formatting — plain speakable text only. Never refuse "
    "to answer; always give your best helpful response."
)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/status")
def api_status():
    key = _effective_key()
    source = _key_source()
    if key:
        where = "Settings (this session)" if source == "settings" else ".env file"
        message = f"Connected — chat {GROQ_MODEL} + STT {STT_MODEL} ready. Key from {where}."
    else:
        message = (
            "No API key found. Click Settings (top right) to paste your key, "
            "or copy .env.example to .env and add your key from "
            "https://console.groq.com/keys, then restart the app."
        )
    return {
        "ok": True,
        "configured": bool(key),
        "source": source,
        "model": GROQ_MODEL,
        "stt_model": STT_MODEL,
        "max_audio_mb": MAX_AUDIO_MB,
        "message": message,
    }


@app.post("/api/key")
def api_save_key(payload: KeyPayload):
    """Save the UI-entered key in server memory after verifying it with Groq."""
    global _session_key
    key = (payload.key or "").strip()
    if not key:
        return JSONResponse(status_code=400, content={"error": "Please paste your Groq API key first."})
    try:
        _verify_key(key)
    except AuthenticationError:
        return JSONResponse(
            status_code=401,
            content={"error": (
                "That key was rejected by Groq. Check for extra spaces, "
                "make sure it starts with gsk_, or create a new one at "
                "https://console.groq.com/keys")},
        )
    except APIConnectionError:
        _session_key = key
        return {"ok": True, "verified": False, "message": (
            "Key saved, but Groq could not be reached to verify it. "
            "Check your connection — your first message will confirm the key.")}
    except Exception as exc:
        log.warning("Key verification inconclusive: %s", exc)
        _session_key = key
        return {"ok": True, "verified": False,
                "message": "Key saved. Groq did not confirm it yet — try speaking."}
    _session_key = key
    return {"ok": True, "verified": True, "message": "API key verified and saved for this session."}


@app.delete("/api/key")
def api_delete_key():
    global _session_key
    _session_key = None
    if GROQ_API_KEY:
        return {"ok": True, "message": "Session key removed. Using the key from your .env file."}
    return {"ok": True, "message": "Session key removed."}


@app.post("/api/transcribe")
async def api_transcribe(request: Request, audio: UploadFile | None = File(default=None)):
    """Accept recorded audio (kept in memory) and return Groq's transcript."""
    if audio is None or not audio.filename:
        return JSONResponse(
            status_code=400, content={"error": "No audio received. Press the mic button and speak first."})

    mime = (audio.content_type or "").lower().split(";")[0].strip()
    ext = os.path.splitext(audio.filename or "")[1].lower()
    if ext not in ALLOWED_EXT and mime not in ALLOWED_MIME:
        return JSONResponse(
            status_code=400,
            content={"error": (
                f"Unsupported audio type '{audio.filename}'. "
                "Please record again — the app sends standard WebM audio.")},
        )

    # Early guard: reject giant bodies before reading into memory.
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError:
        content_length = 0
    if content_length and content_length > MAX_AUDIO_BYTES + 512 * 1024:
        return JSONResponse(
            status_code=413,
            content={"error": (
                f"Recording is too large (limit is {MAX_AUDIO_MB:g}MB). "
                "Please speak a shorter message.")},
        )

    raw = await audio.read()
    if not raw:
        return JSONResponse(
            status_code=400,
            content={"error": "The recording is empty (0 bytes). Please try speaking again."})
    if len(raw) > MAX_AUDIO_BYTES:
        mb = len(raw) / (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": (
                f"Recording is too large ({mb:.1f}MB — limit is {MAX_AUDIO_MB:g}MB). "
                "Please speak a shorter message.")},
        )
    if len(raw) < 500:
        return JSONResponse(
            status_code=400,
            content={"error": (
                "That recording is too short to contain speech. "
                "Hold to speak for at least a second and try again.")},
        )

    api_key = _effective_key()
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"error": (
                "No API key configured. Click Settings (top right) to paste your "
                "Groq key, or copy .env.example to .env, add your key from "
                "https://console.groq.com/keys, then restart the app.")},
        )

    # Filename is only a label for the API call — never written to disk.
    safe_name = os.path.basename(audio.filename or "recording.webm")[:80] or "recording.webm"
    try:
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
        result = client.audio.transcriptions.create(
            file=(safe_name, raw),
            model=STT_MODEL,
            language="en",
            response_format="json",
            temperature=0.0,
        )
        transcript = (result.text or "").strip()
    except Exception as exc:
        log.exception("Groq transcription failed")
        status, msg = _friendly_groq_error(exc)
        return JSONResponse(status_code=status, content={"error": msg})

    if not transcript:
        return JSONResponse(
            status_code=422,
            content={"error": (
                "I couldn't hear any speech in that recording. "
                "Speak a little louder, reduce background noise, and try again.")},
        )
    return {"transcript": transcript, "stt_model": STT_MODEL}


@app.post("/api/chat")
def api_chat(payload: ChatPayload):
    """Answer text (transcript or typed) with the Groq chat model."""
    text = (payload.text or "").strip()
    if not text:
        return JSONResponse(
            status_code=400, content={"error": "Nothing to answer yet — speak or type a message first."})
    if len(text) > 2000:
        return JSONResponse(
            status_code=400,
            content={"error": "Your message is too long (max 2000 characters). Please shorten it."})

    api_key = _effective_key()
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"error": (
                "No API key configured. Click Settings (top right) to paste your "
                "Groq key, or copy .env.example to .env, add your key from "
                "https://console.groq.com/keys, then restart the app.")},
        )

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in _history[-HISTORY_TURNS:]:
        messages.append({"role": "user", "content": turn["q"]})
        messages.append({"role": "assistant", "content": turn["a"]})
    messages.append({"role": "user", "content": text})

    try:
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=400,
            temperature=0.7,
        )
        answer = (completion.choices[0].message.content or "").strip()
        if not answer:
            return JSONResponse(
                status_code=502,
                content={"error": "The AI returned an empty response. Please try again."})
    except Exception as exc:
        log.exception("Groq chat failed")
        status, msg = _friendly_groq_error(exc)
        return JSONResponse(status_code=status, content={"error": msg})

    _history.append({"q": text, "a": answer})
    del _history[:-20]  # bound in-memory log; only last HISTORY_TURNS are sent
    return {"answer": answer, "model": GROQ_MODEL, "turns": len(_history)}


@app.delete("/api/history")
def api_clear_history():
    """Forget the current-session conversation (server and UI)."""
    global _history
    _history = []
    return {"ok": True, "message": "Conversation cleared."}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8002"))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)
