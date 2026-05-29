const statusEl = document.querySelector("#status");
const vadStateEl = document.querySelector("#vad-state");
const attentionModeEl = document.querySelector("#attention-mode");
const latencyEl = document.querySelector("#latency");
const bytesEl = document.querySelector("#bytes");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
const recordNoiseButton = document.querySelector("#record-noise");
const recordReadButton = document.querySelector("#record-read");
const nextReadPromptButton = document.querySelector("#next-read-prompt");
const replyTextEl = document.querySelector("#reply-text");
const emotionEl = document.querySelector("#emotion");
const tomokoImageEl = document.querySelector("#tomoko-image");
const readPromptTextEl = document.querySelector("#read-prompt-text");
const debugResultTextEl = document.querySelector("#debug-result-text");
const candidateResultTextEl = document.querySelector("#candidate-result-text");
const transcriptLogEntriesEl = document.querySelector("#transcript-log-entries");
const partialTranscriptEl = document.querySelector("#partial-transcript");
const finalTranscriptEl = document.querySelector("#final-transcript");
const replyStreamEl = document.querySelector("#reply-stream");
const contextSummaryEl = document.querySelector("#context-summary");
const shortMemoryStatusEl = document.querySelector("#short-memory-status");
const shortMemoryNotesEl = document.querySelector("#short-memory-notes");

let audioContext = null;
let micStream = null;
let workletNode = null;
let sourceNode = null;
let sinkNode = null;
let websocket = null;
let bytesSent = 0;
let nextPlaybackTime = 0;
let currentAudioTurnId = null;
let playbackSources = [];
let nextPlaybackChunkId = 1;
let recordingTimer = null;
let activeDebugRecording = null;
let readPromptIndex = 0;
let activeTomokoLogEntry = null;
const MAX_TRANSCRIPT_LOG_ENTRIES = 80;

const READ_PROMPTS = [
  "正直ログを見ながら喋る感じ、ウィスパーがまともに拾えている感じが全くないのですが。",
  "トモコ、今日の予定を確認して、あとで少しだけ話しかけて。",
  "画面に文字が表示されてから、音声が出るまでが少し長く感じます。",
  "小さいノイズでココココと出るなら、先にゲートした方が良さそうです。",
];

const CANDIDATE_EVENT_TYPES = new Set([
  "initiative_fetch_requested",
  "initiative_skipped",
  "initiative_llm_judge_requested",
  "initiative_reply_requested",
  "arrival_fetch_requested",
  "arrival_skipped",
  "arrival_wait_silent",
  "arrival_subtle_react",
  "arrival_reply_requested",
  "candidate_command_failed",
]);

function setStatus(value) {
  statusEl.textContent = value;
}

function sendPlaybackEvent(type, entry) {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) {
    return;
  }
  websocket.send(
    JSON.stringify({
      type,
      turn_id: entry.turnId,
      chunk_id: entry.chunkId,
      scheduled_audio_time: entry.scheduledAudioTime,
      sent_audio_time: audioContext?.currentTime ?? null,
      audio_context_time: audioContext?.currentTime ?? null,
      performance_now_ms: performance.now(),
    }),
  );
}

function formatCandidateEvent(event) {
  const parts = [event.type];
  if (event.reason) {
    parts.push(`reason=${event.reason}`);
  }
  if (event.gate_reason) {
    parts.push(`gate=${event.gate_reason}`);
  }
  if (event.candidate_id) {
    parts.push(`id=${shortId(event.candidate_id)}`);
  }
  if (event.arrival_candidate_id) {
    parts.push(`arrival=${shortId(event.arrival_candidate_id)}`);
  }
  if (event.policy?.decision) {
    parts.push(`policy=${event.policy.decision}`);
  }
  if (typeof event.policy?.score === "number") {
    parts.push(`score=${event.policy.score.toFixed(3)}`);
  }
  if (typeof event.policy?.threshold === "number") {
    parts.push(`threshold=${event.policy.threshold.toFixed(3)}`);
  }
  return parts.join(" / ");
}

function shortId(value) {
  return String(value).slice(0, 8);
}

function trimTranscriptEntries() {
  while (transcriptLogEntriesEl.children.length > MAX_TRANSCRIPT_LOG_ENTRIES) {
    transcriptLogEntriesEl.lastElementChild.remove();
  }
}

function prependLogEntry(mode, metaParts, textValue = "") {
  const entry = document.createElement("div");
  entry.className = "transcript-entry";
  entry.dataset.mode = mode;

  const meta = document.createElement("span");
  meta.textContent = metaParts.join(" / ");

  const text = document.createElement("p");
  text.textContent = textValue;

  entry.append(meta, text);
  transcriptLogEntriesEl.prepend(entry);
  trimTranscriptEntries();

  return { entry, text };
}

function appendTranscriptEntry(event) {
  const parts = [
    new Date().toLocaleTimeString("ja-JP", { hour12: false }),
    event.attention_mode || "ambient",
    event.participation_mode || "observer",
  ];
  if (event.conversation_session_id) {
    parts.push(`session=${shortId(event.conversation_session_id)}`);
  }
  prependLogEntry(event.participation_mode || "observer", parts, event.text || "");
}

function formatContextSummary(event) {
  const counts = event.included_counts || event.source_counts || {};
  const countText = Object.entries(counts)
    .map(([key, value]) => `${key}:${value}`)
    .join(", ");
  const skipped = Array.isArray(event.skipped_sources)
    ? event.skipped_sources.join(",")
    : "";
  const elapsed =
    typeof event.build_elapsed_ms === "number"
      ? `${event.build_elapsed_ms.toFixed(1)}ms`
      : "--";
  return [
    `depth=${event.depth || "--"}`,
    `counts=${countText || "-"}`,
    `skipped=${skipped || "-"}`,
    `short=${event.short_memory_notes_count ?? 0}`,
    `elapsed=${elapsed}`,
  ].join(" / ");
}

function renderShortMemorySnapshot(event) {
  const notes = Array.isArray(event.notes) ? event.notes : [];
  shortMemoryStatusEl.textContent = `turn=${event.current_turn ?? "-"} / notes=${notes.length}`;
  shortMemoryNotesEl.replaceChildren(
    ...notes.map((note) => {
      const item = document.createElement("div");
      item.className = "short-memory-note";

      const meta = document.createElement("span");
      meta.textContent = [
        note.status || "accepted",
        note.kind || "working_context",
        `ttl=${note.remaining_turns ?? "-"}`,
      ].join(" / ");

      const text = document.createElement("p");
      text.textContent = note.text || "";

      item.append(meta, text);
      return item;
    }),
  );
}

function appendTomokoReplyDelta(delta) {
  if (!delta) {
    return;
  }
  if (activeTomokoLogEntry === null) {
    activeTomokoLogEntry = prependLogEntry("tomoko", [
      new Date().toLocaleTimeString("ja-JP", { hour12: false }),
      "tomoko",
      "reply_text",
    ]);
  }
  activeTomokoLogEntry.text.textContent += delta;
}

function handleJsonEvent(data) {
  const event = JSON.parse(data);
  if (CANDIDATE_EVENT_TYPES.has(event.type)) {
    candidateResultTextEl.textContent = formatCandidateEvent(event);
  }
  if (event.type === "transcript_partial") {
    partialTranscriptEl.textContent = event.text || "--";
    return;
  }
  if (event.type === "transcript_final") {
    finalTranscriptEl.textContent = event.text || "--";
    appendTranscriptEntry(event);
    return;
  }
  if (event.type === "context_snapshot") {
    contextSummaryEl.textContent = formatContextSummary(event);
    return;
  }
  if (event.type === "short_memory_extraction") {
    const elapsed =
      typeof event.elapsed_ms === "number" ? `${event.elapsed_ms.toFixed(1)}ms` : "--";
    shortMemoryStatusEl.textContent = [
      event.status || "--",
      `turn=${event.turn ?? "-"}`,
      `backend=${event.backend || "-"}`,
      `source=${event.source || "-"}`,
      `decision=${event.decision || "-"}`,
      `proposals=${event.proposal_count ?? "-"}`,
      `elapsed=${elapsed}`,
    ].join(" / ");
    return;
  }
  if (event.type === "short_memory_snapshot") {
    renderShortMemorySnapshot(event);
    return;
  }
  if (event.type === "debug_recording_started") {
    activeDebugRecording = event.recording_id;
    debugResultTextEl.textContent = `${event.kind}: recording`;
    updateDebugButtons();
    return;
  }
  if (event.type === "debug_recording_saved") {
    activeDebugRecording = null;
    clearRecordingTimer();
    const transcript = event.transcript ? ` / ${event.transcript}` : "";
    const elapsed = event.stt_elapsed_ms ? ` / ${event.stt_elapsed_ms}ms` : "";
    debugResultTextEl.textContent = `${event.kind}: ${event.rms_db}dB${elapsed}${transcript}`;
    updateDebugButtons();
    return;
  }
  if (event.type === "debug_recording_error") {
    activeDebugRecording = null;
    clearRecordingTimer();
    debugResultTextEl.textContent = event.error;
    updateDebugButtons();
    return;
  }
  if (event.type === "audio_start") {
    if (currentAudioTurnId !== event.turn_id) {
      stopPlayback(null);
    }
    currentAudioTurnId = event.turn_id;
    return;
  }
  if (event.type === "audio_end") {
    if (currentAudioTurnId === event.turn_id) {
      currentAudioTurnId = null;
    }
    return;
  }
  if (event.type === "audio_control") {
    stopPlayback(event.turn_id ?? null);
    return;
  }
  if (event.type === "state") {
    vadStateEl.textContent = event.state;
    if (event.state === "idle" && statusEl.textContent.startsWith("participation:")) {
      // Keep participation status visible until the next speech starts
    } else {
      setStatus(event.state);
    }
    return;
  }
  if (event.type === "attention") {
    attentionModeEl.textContent = event.mode;
    return;
  }
  if (event.type === "participation") {
    setStatus(`participation:${event.mode}`);
    // Clear reply text when new speech starts
    replyTextEl.textContent = "";
    replyStreamEl.textContent = "";
    activeTomokoLogEntry = null;
  }
  if (event.type === "emotion") {
    emotionEl.textContent = event.value;
    if (event.image) {
      tomokoImageEl.src = event.image;
    }
  }
  if (event.type === "reply_text") {
    replyTextEl.textContent += event.delta;
    replyStreamEl.textContent =
      replyStreamEl.textContent === "--"
        ? event.delta
        : replyStreamEl.textContent + event.delta;
    appendTomokoReplyDelta(event.delta);
  }
  if (event.type === "reply_done") {
    activeTomokoLogEntry = null;
  }
}

function selectedReadPrompt() {
  return READ_PROMPTS[readPromptIndex % READ_PROMPTS.length];
}

function renderReadPrompt() {
  readPromptTextEl.textContent = selectedReadPrompt();
}

function clearRecordingTimer() {
  if (recordingTimer !== null) {
    clearTimeout(recordingTimer);
    recordingTimer = null;
  }
}

function updateDebugButtons() {
  const connected = websocket?.readyState === WebSocket.OPEN;
  const recording = activeDebugRecording !== null;
  recordNoiseButton.disabled = !connected || recording;
  recordReadButton.disabled = !connected || recording;
}

function sendJsonEvent(payload) {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) {
    return false;
  }
  websocket.send(JSON.stringify(payload));
  return true;
}

function startDebugRecording(kind, durationMs, expectedText = null) {
  if (
    !sendJsonEvent({
      type: "debug_recording_start",
      kind,
      duration_ms: durationMs,
      expected_text: expectedText,
    })
  ) {
    return;
  }
  activeDebugRecording = "pending";
  debugResultTextEl.textContent = `${kind}: recording`;
  updateDebugButtons();
  clearRecordingTimer();
  recordingTimer = setTimeout(() => {
    sendJsonEvent({ type: "debug_recording_stop" });
  }, durationMs + 120);
}

function stopPlayback(turnId) {
  const shouldStop = (entry) => turnId === null || entry.turnId === turnId;
  const remaining = [];
  for (const entry of playbackSources) {
    if (!shouldStop(entry)) {
      remaining.push(entry);
      continue;
    }
    try {
      clearTimeout(entry.startedTimer);
      entry.source.stop();
    } catch {
      // Already stopped/ended sources are safe to ignore.
    }
  }
  playbackSources = remaining;
  if (!turnId || currentAudioTurnId === turnId) {
    currentAudioTurnId = null;
  }
  if (audioContext) {
    nextPlaybackTime = audioContext.currentTime;
  } else {
    nextPlaybackTime = 0;
  }
}

async function playAudioChunk(arrayBuffer) {
  if (!audioContext) {
    return;
  }
  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
  const source = audioContext.createBufferSource();
  const turnId = currentAudioTurnId;
  source.buffer = audioBuffer;
  source.connect(audioContext.destination);

  const startAt = Math.max(audioContext.currentTime + 0.03, nextPlaybackTime);
  const startedDelayMs = Math.max(0, (startAt - audioContext.currentTime) * 1000);
  const entry = {
    source,
    turnId,
    chunkId: nextPlaybackChunkId,
    scheduledAudioTime: startAt,
    startedTimer: setTimeout(() => {
      sendPlaybackEvent("playback_started", entry);
    }, startedDelayMs),
  };
  nextPlaybackChunkId += 1;
  playbackSources.push(entry);
  source.addEventListener("ended", () => {
    clearTimeout(entry.startedTimer);
    sendPlaybackEvent("playback_ended", entry);
    playbackSources = playbackSources.filter((item) => item !== entry);
  });
  source.start(startAt);
  nextPlaybackTime = startAt + audioBuffer.duration;
}

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

async function startSession() {
  startButton.disabled = true;
  setStatus("starting");
  latencyEl.textContent = "-";

  audioContext = new AudioContext({ sampleRate: 16000 });
  await audioContext.audioWorklet.addModule("/client/audio-worklet.js");

  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
    },
  });

  websocket = new WebSocket(websocketUrl());
  websocket.binaryType = "arraybuffer";
  websocket.addEventListener("open", () => {
    setStatus("connected");
    stopButton.disabled = false;
    updateDebugButtons();
  });
  websocket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      handleJsonEvent(event.data);
      return;
    }
    playAudioChunk(event.data).catch((error) => {
      console.error(error);
      setStatus("audio-error");
    });
  });
  websocket.addEventListener("close", () => {
    setStatus("stopped");
    stopButton.disabled = true;
    startButton.disabled = false;
    activeDebugRecording = null;
    clearRecordingTimer();
    updateDebugButtons();
  });

  sourceNode = audioContext.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioContext, "tomoko-mic-processor");
  sinkNode = audioContext.createGain();
  sinkNode.gain.value = 0;
  workletNode.port.addEventListener("message", (event) => {
    const buffer = event.data;
    if (websocket.readyState !== WebSocket.OPEN) {
      return;
    }
    websocket.send(buffer);
    bytesSent += buffer.byteLength;
    bytesEl.textContent = `${bytesSent}`;
  });
  workletNode.port.start();
  sourceNode.connect(workletNode);
  workletNode.connect(sinkNode).connect(audioContext.destination);
}

async function stopSession() {
  workletNode?.disconnect();
  sourceNode?.disconnect();
  sinkNode?.disconnect();
  micStream?.getTracks().forEach((track) => track.stop());
  sendJsonEvent({ type: "client_stop" });
  websocket?.close();
  await audioContext?.close();

  audioContext = null;
  micStream = null;
  workletNode = null;
  sourceNode = null;
  sinkNode = null;
  websocket = null;
  nextPlaybackTime = 0;
  currentAudioTurnId = null;
  playbackSources = [];
  nextPlaybackChunkId = 1;
  activeDebugRecording = null;
  clearRecordingTimer();
  stopButton.disabled = true;
  updateDebugButtons();
}

startButton.addEventListener("click", () => {
  startSession().catch((error) => {
    console.error(error);
    setStatus("error");
    startButton.disabled = false;
  });
});

stopButton.addEventListener("click", () => {
  stopSession().catch((error) => {
    console.error(error);
    setStatus("error");
  });
});

recordNoiseButton.addEventListener("click", () => {
  startDebugRecording("noise", 1000);
});

recordReadButton.addEventListener("click", () => {
  startDebugRecording("read_aloud", 5000, selectedReadPrompt());
});

nextReadPromptButton.addEventListener("click", () => {
  readPromptIndex += 1;
  renderReadPrompt();
});

renderReadPrompt();
updateDebugButtons();
