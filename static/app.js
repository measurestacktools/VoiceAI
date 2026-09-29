/* VoiceAI frontend — vanilla JS. Mic -> Groq STT -> Groq chat -> browser TTS. */
(() => {
  const $ = (id) => document.getElementById(id);
  const apiStatus = $("apiStatus"), statusText = $("statusText");
  const settingsBtn = $("settingsBtn"), settingsModal = $("settingsModal");
  const settingsClose = $("settingsClose"), keyInput = $("keyInput");
  const keySave = $("keySave"), keySpinner = $("keySpinner"), keyRemove = $("keyRemove");
  const keyState = $("keyState"), keyError = $("keyError"), keyOk = $("keyOk");
  const orbBtn = $("orbBtn"), stageTitle = $("stageTitle"), stageSub = $("stageSub");
  const stageError = $("stageError");
  const replayBtn = $("replayBtn"), stopBtn = $("stopBtn"), autoSpeak = $("autoSpeak");
  const pauseBtn = $("pauseBtn"), voiceSel = $("voiceSel");
  const messages = $("messages"), convoEmpty = $("convoEmpty"), modelTag = $("modelTag");
  const clearBtn = $("clearBtn"), composer = $("composer"), textInput = $("textInput");
  const chatError = $("chatError");

  let keySource = null;
  let statusTimer = null;
  let recorder = null;
  let audioChunks = [];
  let recording = false;
  let busy = false;            // a transcribe/chat round is in flight
  let lastAnswer = "";
  let lastAiCard = null;
  let stream = null;

  const hasTts = ("speechSynthesis" in window) && ("SpeechSynthesisUtterance" in window);
  const hasMic = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)
    && (typeof MediaRecorder !== "undefined");

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }
  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* ---------- orb state machine ---------- */
  const STATES = {
    idle: ["Tap to speak", "Ready when you are"],
    listening: ["Listening…", "Speak naturally — tap again to stop"],
    processing: ["Thinking…", "Transcribing and answering"],
    speaking: ["Speaking…", "Tap stop to interrupt"],
  };
  function setState(name, sub) {
    orbBtn.dataset.state = name;
    orbBtn.setAttribute("aria-label",
      name === "idle" ? "Tap to speak" :
      name === "listening" ? "Listening. Tap to stop recording" :
      name === "processing" ? "Processing your message" : "Speaking the answer");
    stageTitle.textContent = STATES[name][0];
    stageSub.textContent = (typeof sub === "string" && sub) ? sub : STATES[name][1];
  }
  function setStageError(msg) {
    if (!msg) { hide(stageError); stageError.textContent = ""; return; }
    stageError.textContent = msg; show(stageError);
  }
  function setChatError(msg) {
    if (!msg) { hide(chatError); chatError.textContent = ""; return; }
    chatError.textContent = msg; show(chatError);
  }

  /* ---------- status ---------- */
  function pillText(configured) {
    const narrow = window.innerWidth < 560;
    if (configured) return "API connected";
    return narrow ? "API key missing" : "API key missing — open Settings";
  }
  async function loadStatus() {
    apiStatus.classList.add("checking");
    apiStatus.classList.remove("ok", "bad");
    try {
      const r = await fetch("/api/status");
      const d = await r.json();
      apiStatus.classList.remove("checking");
      keySource = d.source || null;
      if (d.configured) {
        apiStatus.classList.add("ok");
        statusText.textContent = pillText(true);
        statusText.title = d.message || "";
      } else {
        apiStatus.classList.add("bad");
        statusText.textContent = pillText(false);
        statusText.title = d.message || "Add your Groq API key via Settings or .env";
      }
      renderKeyState(d);
    } catch {
      apiStatus.classList.remove("checking");
      apiStatus.classList.add("bad");
      statusText.textContent = "Server unreachable";
    }
  }
  window.addEventListener("resize", () => {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(loadStatus, 250);
  });

  /* ---------- settings modal ---------- */
  function renderKeyState(d) {
    if (!d) return;
    keyState.classList.remove("on", "off");
    if (d.configured) {
      keyState.classList.add("on");
      const where = d.source === "settings"
        ? "Key active — entered via Settings (this session only)."
        : "Key active — loaded from your .env file.";
      keyState.textContent = "✓ " + where + " Chat: " + (d.model || "ready") + ".";
    } else {
      keyState.classList.add("off");
      keyState.textContent = "✕ No API key configured yet.";
    }
    keyRemove.disabled = keySource !== "settings";
    keyRemove.title = keySource === "settings"
      ? "Forget the key entered via Settings"
      : "There is no Settings key to remove (the .env key, if any, stays)";
  }
  function setKeyError(msg) {
    if (!msg) { hide(keyError); keyError.textContent = ""; return; }
    keyError.textContent = msg; show(keyError);
  }
  function setKeyOk(msg) {
    if (!msg) { hide(keyOk); keyOk.textContent = ""; return; }
    keyOk.textContent = msg; show(keyOk);
  }
  function openSettings() {
    setKeyError(""); setKeyOk(""); keyInput.value = "";
    show(settingsModal);
    loadStatus();
    setTimeout(() => keyInput.focus(), 50);
  }
  function closeSettings() { hide(settingsModal); settingsBtn.focus(); }
  settingsBtn.addEventListener("click", openSettings);
  settingsClose.addEventListener("click", closeSettings);
  settingsModal.addEventListener("click", (e) => { if (e.target === settingsModal) closeSettings(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !settingsModal.hidden) closeSettings();
  });
  keyInput.addEventListener("input", () => { setKeyError(""); setKeyOk(""); });
  $("keyForm").addEventListener("submit", (e) => { e.preventDefault(); keySave.click(); });
  keySave.addEventListener("click", async () => {
    const key = keyInput.value.trim();
    setKeyError(""); setKeyOk("");
    if (!key) { setKeyError("Please paste your Groq API key first."); keyInput.focus(); return; }
    keySave.disabled = true; show(keySpinner);
    try {
      const res = await fetch("/api/key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setKeyError(data.error || ("Could not save the key (HTTP " + res.status + ").")); return; }
      keyInput.value = "";
      setKeyOk("✓ " + (data.message || "Key saved."));
      await loadStatus();
    } catch {
      setKeyError("Could not reach the server. Make sure the app is running and try again.");
    } finally {
      keySave.disabled = false; hide(keySpinner);
    }
  });
  keyRemove.addEventListener("click", async () => {
    setKeyError(""); setKeyOk("");
    try {
      const res = await fetch("/api/key", { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      setKeyOk("✓ " + (data.message || "Key removed."));
      await loadStatus();
    } catch {
      setKeyError("Could not reach the server. Try again.");
    }
  });

  /* ---------- conversation UI ---------- */
  function now() {
    const d = new Date();
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  function addMessage(kind, text) {
    if (convoEmpty && !convoEmpty.hidden) hide(convoEmpty);
    const div = document.createElement("div");
    const label = document.createElement("span");
    label.className = "who";
    const body = document.createElement("div");
    body.className = "body";
    if (kind === "user") {
      div.className = "msg user";
      label.textContent = "You · " + now();
      body.textContent = text;
    } else if (kind === "error") {
      div.className = "msg msg-error";
      body.textContent = text;
    } else {
      div.className = "msg ai";
      label.textContent = "VoiceAI · " + now();
      const play = document.createElement("button");
      play.type = "button";
      play.className = "play-one";
      play.setAttribute("aria-label", "Play this answer aloud");
      play.title = "Play this answer aloud";
      play.textContent = "🔊";
      play.addEventListener("click", () => speak(text, div));
      label.appendChild(document.createTextNode(" "));
      label.appendChild(play);
      body.textContent = text;
    }
    div.appendChild(label);
    div.appendChild(body);
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  /* ---------- voice output: browser speechSynthesis only ---------- */
  function markIdle() {
    if (lastAiCard) lastAiCard.classList.remove("speaking-now");
    stopBtn.disabled = true;
    pauseBtn.disabled = true;
    pauseBtn.textContent = "⏸ Pause";
    if (!busy && !recording) setState("idle");
  }
  function stopAllAudio() {
    try { speechSynthesis.cancel(); } catch {}
    markIdle();
  }
  function getSavedVoiceURI() {
    try { return localStorage.getItem("voiceai_voice") || ""; } catch { return ""; }
  }
  function pickVoice(uri) {
    try {
      const voices = speechSynthesis.getVoices() || [];
      if (uri) {
        const match = voices.find((v) => v.voiceURI === uri);
        if (match) return match;
      }
      const en = voices.filter((v) => v.lang && v.lang.toLowerCase().startsWith("en"));
      return (en.find((v) => v.default) || en[0] || voices[0]) || null;
    } catch {
      return null;
    }
  }
  function populateVoices() {
    if (!voiceSel) return;
    let voices = [];
    try { voices = speechSynthesis.getVoices() || []; } catch { voices = []; }
    const en = voices.filter((v) => v.lang && v.lang.toLowerCase().startsWith("en"));
    voiceSel.innerHTML = "";
    en.forEach((v) => {
      const o = document.createElement("option");
      o.value = v.voiceURI;
      o.textContent = v.name + " (" + v.lang + ")";
      voiceSel.appendChild(o);
    });
    const saved = getSavedVoiceURI();
    if (saved && en.some((v) => v.voiceURI === saved)) {
      voiceSel.value = saved;
    } else if (en.length) {
      const def = pickVoice("");
      voiceSel.value = (def && def.voiceURI) || en[0].voiceURI;
      try { localStorage.setItem("voiceai_voice", voiceSel.value); } catch {}
    }
    if (!voiceSel.options.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "System default";
      voiceSel.appendChild(o);
    }
  }
  if (voiceSel) {
    voiceSel.addEventListener("change", () => {
      try { localStorage.setItem("voiceai_voice", voiceSel.value); } catch {}
    });
  }
  try {
    if (hasTts && typeof speechSynthesis.addEventListener === "function") {
      speechSynthesis.addEventListener("voiceschanged", populateVoices);
    } else if (hasTts) {
      speechSynthesis.onvoiceschanged = populateVoices;
    }
  } catch {}
  function speak(text, card) {
    if (!text) return;
    if (!hasTts) {
      setChatError("This browser has no text-to-speech voices. You can still read the answers above.");
      setState("idle");
      return;
    }
    stopAllAudio();
    try {
      const u = new SpeechSynthesisUtterance(text);
      const saved = voiceSel ? voiceSel.value : getSavedVoiceURI();
      const v = pickVoice(saved);
      if (v) u.voice = v;
      u.rate = 1.0;
      u.onstart = () => {
        setState("speaking");
        stopBtn.disabled = false;
        pauseBtn.disabled = false;
        if (card) { card.classList.add("speaking-now"); lastAiCard = card; }
      };
      const done = () => { markIdle(); };
      u.onend = done;
      u.onerror = done;
      speechSynthesis.speak(u);
    } catch {
      setChatError("Speech output failed in this browser. The written answer is above.");
      setState("idle");
    }
  }
  stopBtn.addEventListener("click", stopAllAudio);
  pauseBtn.addEventListener("click", () => {
    try {
      if (speechSynthesis.speaking && !speechSynthesis.paused) {
        speechSynthesis.pause();
        pauseBtn.textContent = "▶ Resume";
      } else if (speechSynthesis.paused) {
        speechSynthesis.resume();
        pauseBtn.textContent = "⏸ Pause";
      }
    } catch {}
  });
  replayBtn.addEventListener("click", () => {
    if (!lastAnswer) return;
    const card = lastAiCard && document.contains(lastAiCard) ? lastAiCard : null;
    speak(lastAnswer, card);
  });

  /* ---------- chat ---------- */
  async function askText(text) {
    setChatError("");
    setStageError("");
    const q = (text || "").trim();
    if (!q) { setChatError("Please speak or type a message first."); return; }
    addMessage("user", q);
    busy = true;
    setState("processing");
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: q }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        addMessage("error", data.error || ("Request failed (HTTP " + res.status + "). Please try again."));
        setState("idle");
        return;
      }
      lastAnswer = data.answer || "";
      const card = addMessage("ai", lastAnswer);
      lastAiCard = card;
      replayBtn.disabled = !lastAnswer;
      if (data.model) {
        modelTag.textContent = "◈ " + data.model;
        modelTag.hidden = false;
      }
      if (autoSpeak.checked && lastAnswer) speak(lastAnswer, card);
      else setState("idle");
    } catch {
      addMessage("error", "Could not reach the server. Make sure the app is running and try again.");
      setState("idle");
    } finally {
      busy = false;
    }
  }
  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const v = textInput.value;
    textInput.value = "";
    askText(v);
  });

  clearBtn.addEventListener("click", async () => {
    try { await fetch("/api/history", { method: "DELETE" }); } catch {}
    messages.querySelectorAll(".msg").forEach((m) => m.remove());
    show(convoEmpty);
    lastAnswer = "";
    lastAiCard = null;
    replayBtn.disabled = true;
    modelTag.hidden = true;
    setChatError("");
  });

  /* ---------- microphone ---------- */
  function pickMime() {
    if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
    const cands = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    for (const c of cands) {
      try { if (MediaRecorder.isTypeSupported(c)) return c; } catch {}
    }
    return "";
  }
  async function startRecording() {
    setStageError("");
    setChatError("");
    if (recording || busy) return;
    if (!hasMic) {
      setStageError("Microphone recording isn't available in this browser. Type your message below instead — everything else works the same.");
      textInput.focus();
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const name = (err && err.name) || "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        setStageError("Microphone access was denied. Allow the microphone in your browser's site settings and try again — or type below instead.");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        setStageError("No microphone was found on this device. Type your message below instead.");
      } else {
        setStageError("Could not start the microphone (" + (err && err.message ? err.message : "unknown error") + "). Type below instead.");
      }
      textInput.focus();
      return;
    }
    try {
      audioChunks = [];
      const mime = pickMime();
      recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) audioChunks.push(e.data); };
      recorder.onstop = onRecordStop;
      recorder.start();
      recording = true;
      setState("listening");
    } catch (err) {
      setStageError("Recording failed to start. Type your message below instead.");
      stopStream();
    }
  }
  function stopStream() {
    if (stream) {
      try { stream.getTracks().forEach((t) => t.stop()); } catch {}
      stream = null;
    }
  }
  function stopRecording() {
    if (!recording || !recorder) return;
    recording = false;
    try {
      if (recorder.state !== "inactive") recorder.stop();
    } catch {
      onRecordStop();
    }
    stopStream();
  }
  async function onRecordStop() {
    if (!audioChunks.length) {
      setStageError("Nothing was recorded. Tap the orb and speak for at least a second.");
      setState("idle");
      return;
    }
    const type = (recorder && recorder.mimeType) || "audio/webm";
    const blob = new Blob(audioChunks, { type: type.split(";")[0] });
    audioChunks = [];
    await sendAudio(blob);
  }
  async function sendAudio(blob) {
    busy = true;
    setState("processing");
    try {
      const ext = (blob.type || "").includes("mp4") ? "recording.m4a" : "recording.webm";
      const form = new FormData();
      form.append("audio", blob, ext);
      const res = await fetch("/api/transcribe", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        addMessage("error", data.error || ("Transcription failed (HTTP " + res.status + "). Please try again."));
        setState("idle");
        return;
      }
      await askText(data.transcript || "");
    } catch {
      addMessage("error", "Could not reach the server. Make sure the app is running and try again.");
      setState("idle");
    } finally {
      busy = false;
    }
  }
  orbBtn.addEventListener("click", () => {
    if (busy) return;
    stopAllAudio();
    if (recording) stopRecording();
    else startRecording();
  });

  /* ---------- init ---------- */
  if (!hasTts) {
    autoSpeak.checked = false;
    autoSpeak.disabled = true;
  }
  populateVoices();
  setState("idle");
  loadStatus();
})();
