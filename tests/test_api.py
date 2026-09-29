"""API validation tests (no Groq key needed — error/validation paths only).

Run:  pytest tests/ -q
Live Groq tests (real transcription + chat) are done separately; see README.
"""
import io
import math
import os
import struct
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

import app as m


def _client():
    m._session_key = None
    m._history = []
    return TestClient(m.app)


def _tone_wav(seconds=1, freq=440):
    """Tiny valid WAV (sine tone) built with the stdlib — no speech in it."""
    rate = 16000
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(n):
            v = int(9000 * math.sin(2 * math.pi * freq * i / rate))
            w.writeframes(struct.pack("<h", v))
    buf.seek(0)
    return buf.getvalue()


def _groq_reachable() -> bool:
    try:
        httpx.get("https://api.groq.com/openai/v1/models", timeout=5.0)
        return True
    except Exception:
        return False


def test_home_status_assets():
    c = _client()
    r = c.get("/")
    assert r.status_code == 200 and "VoiceAI" in r.text
    d = c.get("/api/status").json()
    assert d["configured"] is False and d["model"] == "openai/gpt-oss-120b"
    assert d["stt_model"] == "whisper-large-v3-turbo"
    for p in ("/static/styles.css", "/static/app.js"):
        assert c.get(p).status_code == 200


def test_key_validation():
    c = _client()
    assert c.post("/api/key", json={"key": ""}).status_code == 400
    if not _groq_reachable():
        pytest.skip("no network: live key-verification test skipped")
    assert c.post("/api/key", json={"key": "gsk_fake_key_for_tests"}).status_code == 401
    assert c.get("/api/status").json()["configured"] is False
    assert c.delete("/api/key").status_code == 200


def test_transcribe_validation():
    c = _client()
    assert c.post("/api/transcribe").status_code in (400, 422)
    r = c.post("/api/transcribe", files={"audio": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 400 and "Unsupported audio" in r.json()["error"]
    r = c.post("/api/transcribe", files={"audio": ("empty.webm", b"", "audio/webm")})
    assert r.status_code == 400
    r = c.post("/api/transcribe", files={"audio": ("tiny.webm", b"\x1a\x45" * 100, "audio/webm")})
    assert r.status_code == 400 and "too short" in r.json()["error"]
    r = c.post("/api/transcribe",
               files={"audio": ("big.webm", os.urandom(11 * 1024 * 1024), "audio/webm")})
    assert r.status_code == 413
    # Valid container, real tone, no key -> reaches the key gate (proves validation passed).
    r = c.post("/api/transcribe", files={"audio": ("tone.wav", _tone_wav(), "audio/wav")})
    assert r.status_code == 401 and "API key" in r.json()["error"]


def test_chat_validation():
    c = _client()
    assert c.post("/api/chat", json={"text": ""}).status_code == 400
    assert c.post("/api/chat", json={"text": "x" * 2001}).status_code == 400
    r = c.post("/api/chat", json={"text": "Hello?"})
    assert r.status_code == 401 and "API key" in r.json()["error"]


def test_history_clear():
    c = _client()
    assert c.delete("/api/history").status_code == 200


def test_speak_validation():
    from app import _concat_wavs, _split_tts_chunks
    c = _client()
    assert c.post("/api/speak", json={"text": ""}).status_code == 400
    assert c.post("/api/speak", json={"text": "x" * 2001}).status_code == 400
    r = c.post("/api/speak", json={"text": "Hello", "voice": "mickey"})
    assert r.status_code == 400 and "Unknown voice" in r.json()["error"]
    r = c.post("/api/speak", json={"text": "Hello there"})
    assert r.status_code == 401 and "API key" in r.json()["error"]

    # chunking: long text splits into <=190-char sentence-aware pieces
    long_text = ("The harbor master announced the regatta schedule for July. "
                 "Every vessel must register before June the first. " * 6)
    chunks = _split_tts_chunks(long_text)
    assert len(chunks) > 1
    assert all(len(x) <= 190 for x in chunks)
    assert _split_tts_chunks("") == []
    assert _split_tts_chunks("Hi.") == ["Hi."]

    # wav concat: two tones join into one valid wav with summed frames
    import wave as wavmod
    a, b = _tone_wav(seconds=1), _tone_wav(seconds=1, freq=660)
    joined = _concat_wavs([a, b])
    assert joined[:4] == b"RIFF" and joined[8:12] == b"WAVE"
    with wavmod.open(io.BytesIO(joined), "rb") as w:
        n = w.getnframes()
    with wavmod.open(io.BytesIO(a), "rb") as w:
        assert n == 2 * w.getnframes()
    assert _concat_wavs([a]) == a
