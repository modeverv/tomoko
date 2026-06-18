const statusEl = document.querySelector("#status");
const debugEl = document.querySelector("#debug");
const transcriptEl = document.querySelector("#transcript");
const connectButton = document.querySelector("#connect");
const stopButton = document.querySelector("#stop-audio");
const outputSelect = document.querySelector("#audio-output");

let ws = null;
let audioContext = null;
let playbackTime = 0;

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
    audioContext = new AudioContext({ sampleRate: 16000 });
    await audioContext.audioWorklet.addModule("/client/audio-worklet.js");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const source = audioContext.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audioContext, "tomoko-mic");
    node.port.onmessage = (event) => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(event.data);
    };
    source.connect(node);
  });
  ws.addEventListener("message", (event) => {
    if (event.data instanceof ArrayBuffer) {
      playAudioChunk(event.data);
      return;
    }
    if (typeof event.data !== "string") return;
    const payload = JSON.parse(event.data);
    if (payload.type === "transcript") transcriptEl.textContent = payload.text;
    if (payload.type === "model_delta") transcriptEl.textContent += payload.text_delta;
    if (payload.type === "model_complete") transcriptEl.textContent = payload.text;
    debugEl.textContent = payload.type;
  });
  ws.addEventListener("close", () => {
    statusEl.textContent = "disconnected";
  });
}

async function playAudioChunk(arrayBuffer) {
  if (!audioContext) audioContext = new AudioContext();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(audioContext.destination);
  const startAt = Math.max(audioContext.currentTime, playbackTime);
  source.start(startAt);
  playbackTime = startAt + audioBuffer.duration;
}

connectButton.addEventListener("click", connect);
stopButton.addEventListener("click", () => {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "audio_control", command: "stop" }));
  }
});

populateAudioOutputs();
