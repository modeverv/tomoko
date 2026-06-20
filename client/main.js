const statusEl = document.querySelector("#status");
const debugEl = document.querySelector("#debug");
const transcriptEl = document.querySelector("#transcript");
const timelineItemsEl = document.querySelector("#timeline-items");
const connectButton = document.querySelector("#connect");
const stopButton = document.querySelector("#stop-audio");
const outputSelect = document.querySelector("#audio-output");

let ws = null;
let audioContext = null;
let playbackTime = 0;
let timelineSequence = 0;
let acceptingAudio = true;
const activeAudioSources = new Set();

async function populateAudioOutputs() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  outputSelect.replaceChildren(
    ...devices
      .filter((device) => device.kind === "audiooutput")
      .map((device) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label || "Audio output";
        return option;
      }),
  );
}

async function connect() {
  ws = new WebSocket(`${location.origin.replace("http", "ws")}/ws`);
  ws.binaryType = "arraybuffer";
  ws.addEventListener("open", async () => {
    statusEl.textContent = "connected";
    appendTimelineItem("system", "connected");
    console.log("[tomoko:client] ws_open");
    audioContext = new AudioContext({ sampleRate: 16000 });
    await audioContext.audioWorklet.addModule("/client/audio-worklet.js");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const source = audioContext.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audioContext, "tomoko-mic");
    node.port.onmessage = (event) => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(event.data);
    };
    source.connect(node);
    console.log("[tomoko:client] mic_stream_started");
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
    statusEl.textContent = "disconnected";
    appendTimelineItem("system", "disconnected");
    console.log("[tomoko:client] ws_close");
  });
}

async function playAudioChunk(arrayBuffer) {
  if (!audioContext) audioContext = new AudioContext();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(audioContext.destination);
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

populateAudioOutputs();

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
