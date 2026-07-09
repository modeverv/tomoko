const statusEl = document.querySelector("#status");
const debugEl = document.querySelector("#debug");
const transcriptEl = document.querySelector("#transcript");
const timelineItemsEl = document.querySelector("#timeline-items");
const connectButton = document.querySelector("#connect");
const stopButton = document.querySelector("#stop-audio");
const inputSelect = document.querySelector("#audio-input");
const outputSelect = document.querySelector("#audio-output");
const playbackElement = document.querySelector("#playback-output");

let ws = null;
let audioContext = null;
let audioWorkletReady = false;
let playbackTime = 0;
let timelineSequence = 0;
let acceptingAudio = true;
let playbackGain = null;
let playbackDestination = null;
let microphoneStream = null;
let microphoneSource = null;
let microphoneNode = null;
let microphoneKeepAlive = null;
const REPLACE_FADE_SEC = 0.08;
const REPLACE_SILENCE_GAP_SEC = 0.15;
const activeAudioSources = new Set();

async function populateAudioDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputDevices = devices.filter((device) => device.kind === "audioinput");
  const outputDevices = devices.filter((device) => device.kind === "audiooutput");
  updateDeviceSelect(inputSelect, inputDevices, "Default microphone", "Microphone");
  updateDeviceSelect(outputSelect, outputDevices, "Default speaker", "Speaker");
}

function updateDeviceSelect(select, devices, defaultLabel, fallbackLabel) {
  if (!select) return;
  const previousValue = select.value;
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = defaultLabel;
  const options = devices
    .filter((device) => device.deviceId && device.deviceId !== "default")
    .map((device, index) => {
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || `${fallbackLabel} ${index + 1}`;
      return option;
    });
  select.replaceChildren(defaultOption, ...options);
  if ([...select.options].some((option) => option.value === previousValue)) {
    select.value = previousValue;
  }
}

function selectedAudioInputConstraints() {
  const deviceId = inputSelect?.value || "";
  if (!deviceId) return { audio: true };
  return { audio: { deviceId: { exact: deviceId } } };
}

async function connect() {
  if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return;
  await populateAudioDevices();
  await ensureAudioContext();
  await preparePlaybackOutput();
  ws = new WebSocket(`${location.origin.replace("http", "ws")}/ws`);
  ws.binaryType = "arraybuffer";
  ws.addEventListener("open", async () => {
    statusEl.textContent = "connected";
    appendTimelineItem("system", "connected");
    console.log("[tomoko:client] ws_open");
    try {
      await startMicrophoneCapture();
    } catch (error) {
      statusEl.textContent = "microphone error";
      appendTimelineItem("system", "microphone error");
      console.log("[tomoko:client] mic_stream_error", error);
    }
  });
  ws.addEventListener("message", (event) => {
    if (event.data instanceof ArrayBuffer) {
      console.log("[tomoko:client] audio_chunk", { bytes: event.data.byteLength });
      if (!acceptingAudio) return;
      playAudioChunk(event.data);
      return;
    }
    if (typeof event.data !== "string") return;
    const payload = JSON.parse(event.data);
    console.log("[tomoko:client] event", payload);
    if (payload.type === "transcript") {
      transcriptEl.textContent = payload.text;
    }
    if (payload.type === "transcript" && payload.is_final) {
      const text = (payload.text || "").trim();
      if (text) appendTimelineItem("stt", text);
    }
    if (payload.type === "model_delta") transcriptEl.textContent += payload.text_delta;
    if (payload.type === "model_complete") transcriptEl.textContent = payload.text;
    if (payload.type === "tts_result") {
      appendTimelineItem(
        "tts",
        payload.text || "(blank)",
        `${payload.audio_chunks ?? 0} chunks / ${payload.audio_bytes ?? 0} bytes`,
      );
    }
    if (payload.type === "speech_order") {
      if (payload.mode === "stop") {
        stopLocalPlayback();
      } else if (payload.mode === "replace_current") {
        fadeOutAndCutPlayback();
        acceptingAudio = true;
      } else {
        acceptingAudio = true;
      }
      appendTimelineItem(
        "order",
        payload.text || payload.mode || "(blank)",
        `${payload.mode || "unknown"} / priority=${payload.priority ?? 0}`,
      );
    }
    if (payload.type === "backchannel") acceptingAudio = true;
    debugEl.textContent = payload.type;
  });
  ws.addEventListener("close", () => {
    stopMicrophoneCapture();
    statusEl.textContent = "disconnected";
    appendTimelineItem("system", "disconnected");
    console.log("[tomoko:client] ws_close");
  });
}

async function ensureAudioContext() {
  if (!audioContext) audioContext = new AudioContext({ sampleRate: 16000 });
  if (audioContext.state === "suspended") await audioContext.resume();
  if (!audioWorkletReady) {
    await audioContext.audioWorklet.addModule("/client/audio-worklet.js");
    audioWorkletReady = true;
  }
  return audioContext;
}

async function startMicrophoneCapture() {
  const context = await ensureAudioContext();
  const nextStream = await navigator.mediaDevices.getUserMedia(selectedAudioInputConstraints());
  const nextSource = context.createMediaStreamSource(nextStream);
  const nextNode = new AudioWorkletNode(context, "tomoko-mic");
  const nextKeepAlive = context.createGain();
  nextKeepAlive.gain.value = 0;
  nextNode.port.onmessage = (event) => {
    if (ws?.readyState === WebSocket.OPEN) ws.send(event.data);
  };
  nextSource.connect(nextNode);
  nextNode.connect(nextKeepAlive).connect(context.destination);
  stopMicrophoneCapture();
  microphoneStream = nextStream;
  microphoneSource = nextSource;
  microphoneNode = nextNode;
  microphoneKeepAlive = nextKeepAlive;
  await populateAudioDevices();
  console.log("[tomoko:client] mic_stream_started", {
    deviceId: inputSelect?.value || "default",
  });
}

function stopMicrophoneCapture() {
  if (microphoneNode) {
    microphoneNode.port.onmessage = null;
    microphoneNode.disconnect();
  }
  microphoneSource?.disconnect();
  microphoneKeepAlive?.disconnect();
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = null;
  microphoneSource = null;
  microphoneNode = null;
  microphoneKeepAlive = null;
}

function ensurePlaybackGain() {
  if (!audioContext) audioContext = new AudioContext({ sampleRate: 16000 });
  if (!playbackGain) {
    playbackGain = audioContext.createGain();
  }
  if (!playbackDestination) {
    playbackDestination = audioContext.createMediaStreamDestination();
    playbackGain.connect(playbackDestination);
    playbackElement.srcObject = playbackDestination.stream;
  }
  return playbackGain;
}

async function preparePlaybackOutput() {
  ensurePlaybackGain();
  await applyAudioOutputDevice();
  try {
    await playbackElement.play();
  } catch (error) {
    console.log("[tomoko:client] playback_element_start_deferred", error);
  }
}

async function applyAudioOutputDevice() {
  if (!playbackElement) return;
  const deviceId = outputSelect?.value || "";
  if (!("setSinkId" in playbackElement)) {
    console.log("[tomoko:client] audio_output_sink_unavailable");
    return;
  }
  try {
    await playbackElement.setSinkId(deviceId);
    console.log("[tomoko:client] audio_output_selected", {
      deviceId: deviceId || "default",
    });
  } catch (error) {
    debugEl.textContent = "audio_output_error";
    console.log("[tomoko:client] audio_output_select_error", error);
  }
}

function fadeOutAndCutPlayback() {
  if (!audioContext || activeAudioSources.size === 0) {
    if (audioContext) playbackTime = audioContext.currentTime;
    return;
  }
  const gain = ensurePlaybackGain();
  const now = audioContext.currentTime;
  gain.gain.cancelScheduledValues(now);
  gain.gain.setValueAtTime(gain.gain.value, now);
  gain.gain.linearRampToValueAtTime(0.0001, now + REPLACE_FADE_SEC);
  const sources = [...activeAudioSources];
  activeAudioSources.clear();
  sources.forEach((source) => {
    try {
      source.stop(now + REPLACE_FADE_SEC + 0.01);
    } catch (error) {
      console.log("[tomoko:client] audio_fade_stop_ignored", error);
    }
  });
  const resumeAt = now + REPLACE_FADE_SEC + REPLACE_SILENCE_GAP_SEC;
  gain.gain.setValueAtTime(1.0, resumeAt);
  playbackTime = resumeAt;
  appendTimelineItem("system", "audio replaced (fade)");
  console.log("[tomoko:client] audio_replace_fade", { resumeAt });
}

async function playAudioChunk(arrayBuffer) {
  if (!audioContext) audioContext = new AudioContext({ sampleRate: 16000 });
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(ensurePlaybackGain());
  activeAudioSources.add(source);
  source.onended = () => activeAudioSources.delete(source);
  const startAt = Math.max(audioContext.currentTime, playbackTime);
  source.start(startAt);
  playbackTime = startAt + audioBuffer.duration;
  console.log("[tomoko:client] audio_play", {
    durationSec: audioBuffer.duration,
    startAt,
  });
}

function stopLocalPlayback() {
  acceptingAudio = false;
  activeAudioSources.forEach((source) => {
    try {
      source.stop();
    } catch (error) {
      console.log("[tomoko:client] audio_stop_ignored", error);
    }
  });
  activeAudioSources.clear();
  if (audioContext) playbackTime = audioContext.currentTime;
  appendTimelineItem("system", "audio stopped");
  console.log("[tomoko:client] audio_stop");
}

connectButton.addEventListener("click", connect);
stopButton.addEventListener("click", () => {
  stopLocalPlayback();
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "audio_control", command: "stop" }));
  }
});
inputSelect.addEventListener("change", async () => {
  if (ws?.readyState !== WebSocket.OPEN) return;
  try {
    await startMicrophoneCapture();
  } catch (error) {
    debugEl.textContent = "audio_input_error";
    console.log("[tomoko:client] audio_input_select_error", error);
  }
});
outputSelect.addEventListener("change", () => {
  applyAudioOutputDevice();
});
if (navigator.mediaDevices?.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", populateAudioDevices);
}

populateAudioDevices();

function appendTimelineItem(kind, text, meta = "") {
  const item = document.createElement("li");
  item.className = `timeline-item timeline-item-${kind}`;
  const time = new Date().toLocaleTimeString("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  timelineSequence += 1;
  item.innerHTML = `
    <span class="timeline-kind">${escapeHtml(kind.toUpperCase())}</span>
    <span class="timeline-text">${escapeHtml(text)}</span>
    <span class="timeline-meta">${escapeHtml(`#${timelineSequence} ${time} ${meta}`)}</span>
  `;
  timelineItemsEl.prepend(item);
  while (timelineItemsEl.children.length > 80) {
    timelineItemsEl.lastElementChild.remove();
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
