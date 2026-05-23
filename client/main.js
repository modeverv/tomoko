const statusEl = document.querySelector("#status");
const latencyEl = document.querySelector("#latency");
const bytesEl = document.querySelector("#bytes");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");

let audioContext = null;
let micStream = null;
let workletNode = null;
let sourceNode = null;
let sinkNode = null;
let websocket = null;
let nextPlayTime = 0;
let bytesSent = 0;
const pendingChunks = [];

function setStatus(value) {
  statusEl.textContent = value;
}

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function enqueueSentChunk(buffer) {
  pendingChunks.push({
    byteLength: buffer.byteLength,
    sentAt: performance.now(),
  });
}

function recordEchoLatency(byteLength) {
  const sent = pendingChunks.shift();
  if (!sent || sent.byteLength !== byteLength) {
    return;
  }
  const latencyMs = Math.round(performance.now() - sent.sentAt);
  latencyEl.textContent = `${latencyMs} ms`;
}

function schedulePlayback(buffer) {
  const samples = new Float32Array(buffer);
  const audioBuffer = audioContext.createBuffer(1, samples.length, audioContext.sampleRate);
  audioBuffer.copyToChannel(samples, 0);

  const player = audioContext.createBufferSource();
  player.buffer = audioBuffer;
  player.connect(audioContext.destination);

  const now = audioContext.currentTime;
  nextPlayTime = Math.max(nextPlayTime, now + 0.02);
  player.start(nextPlayTime);
  nextPlayTime += audioBuffer.duration;
}

async function startEcho() {
  startButton.disabled = true;
  setStatus("starting");

  audioContext = new AudioContext({ sampleRate: 16000 });
  await audioContext.audioWorklet.addModule("/client/audio-worklet.js");

  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });

  websocket = new WebSocket(websocketUrl());
  websocket.binaryType = "arraybuffer";
  websocket.addEventListener("open", () => {
    setStatus("echoing");
    stopButton.disabled = false;
  });
  websocket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      return;
    }
    recordEchoLatency(event.data.byteLength);
    schedulePlayback(event.data);
  });
  websocket.addEventListener("close", () => {
    setStatus("stopped");
    stopButton.disabled = true;
    startButton.disabled = false;
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
    enqueueSentChunk(buffer);
    websocket.send(buffer);
    bytesSent += buffer.byteLength;
    bytesEl.textContent = `${bytesSent}`;
  });
  workletNode.port.start();
  sourceNode.connect(workletNode);
  workletNode.connect(sinkNode).connect(audioContext.destination);
}

async function stopEcho() {
  workletNode?.disconnect();
  sourceNode?.disconnect();
  sinkNode?.disconnect();
  micStream?.getTracks().forEach((track) => track.stop());
  websocket?.close();
  await audioContext?.close();

  audioContext = null;
  micStream = null;
  workletNode = null;
  sourceNode = null;
  sinkNode = null;
  websocket = null;
  nextPlayTime = 0;
  pendingChunks.length = 0;
  stopButton.disabled = true;
}

startButton.addEventListener("click", () => {
  startEcho().catch((error) => {
    console.error(error);
    setStatus("error");
    startButton.disabled = false;
  });
});

stopButton.addEventListener("click", () => {
  stopEcho().catch((error) => {
    console.error(error);
    setStatus("error");
  });
});
