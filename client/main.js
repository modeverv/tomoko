const statusEl = document.querySelector("#status");
const vadStateEl = document.querySelector("#vad-state");
const latencyEl = document.querySelector("#latency");
const bytesEl = document.querySelector("#bytes");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
const replyTextEl = document.querySelector("#reply-text");
const emotionEl = document.querySelector("#emotion");
const tomokoImageEl = document.querySelector("#tomoko-image");

let audioContext = null;
let micStream = null;
let workletNode = null;
let sourceNode = null;
let sinkNode = null;
let websocket = null;
let bytesSent = 0;
let nextPlaybackTime = 0;

function setStatus(value) {
  statusEl.textContent = value;
}

function handleJsonEvent(data) {
  const event = JSON.parse(data);
  if (event.type === "state") {
    vadStateEl.textContent = event.state;
    if (event.state === "idle" && statusEl.textContent.startsWith("participation:")) {
      // Keep participation status visible until the next speech starts
    } else {
      setStatus(event.state);
    }
    return;
  }
  if (event.type === "participation") {
    setStatus(`participation:${event.mode}`);
    // Clear reply text when new speech starts
    replyTextEl.textContent = "";
  }
  if (event.type === "emotion") {
    emotionEl.textContent = event.value;
    if (event.image) {
      tomokoImageEl.src = event.image;
    }
  }
  if (event.type === "reply_text") {
    replyTextEl.textContent += event.delta;
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
  source.buffer = audioBuffer;
  source.connect(audioContext.destination);

  const startAt = Math.max(audioContext.currentTime + 0.03, nextPlaybackTime);
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
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    },
  });

  websocket = new WebSocket(websocketUrl());
  websocket.binaryType = "arraybuffer";
  websocket.addEventListener("open", () => {
    setStatus("connected");
    stopButton.disabled = false;
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
  websocket?.close();
  await audioContext?.close();

  audioContext = null;
  micStream = null;
  workletNode = null;
  sourceNode = null;
  sinkNode = null;
  websocket = null;
  nextPlaybackTime = 0;
  stopButton.disabled = true;
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
