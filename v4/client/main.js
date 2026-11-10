// マイクと再生だけ。判断はしない。
// 再生の規則は bench/player.py と同じ:
//   届いた断片は、前の断片の続きに置く。前が終わっていれば、すぐ鳴らす。
//   停止（世代 g）が来たら、g 以前の世代の音を止めて捨てる。以後に届く g 以前の断片も捨てる。
const IN_SR = 16000, OUT_SR = 24000;
let socket, micContext, outContext, stream, reportTimer;
let cursor = 0, stopped = -1, playing = [];
let current = null;  // いま鳴っている世代と、その世代を鳴らした秒数

const $ = (id) => document.getElementById(id);

function say(role, text) {
  const p = document.createElement("p");
  p.className = role;
  p.textContent = (role === "user" ? "あなた: " : "Tomoko: ") + text;
  $("log").prepend(p);
}

function onAudio(buffer) {
  const head = new DataView(buffer);
  const generation = head.getUint32(0, true);
  if (generation <= stopped) return;
  const samples = new Float32Array(buffer, 8);
  const audio = outContext.createBuffer(1, samples.length, OUT_SR);
  audio.copyToChannel(samples, 0);
  const source = outContext.createBufferSource();
  source.buffer = audio;
  source.connect(outContext.destination);
  const start = Math.max(outContext.currentTime, cursor);
  source.start(start);
  cursor = start + audio.duration;
  const item = { source, generation, start, end: cursor };
  playing.push(item);
  source.onended = () => { playing = playing.filter((x) => x !== item); };
}

function onStop(generation) {
  stopped = Math.max(stopped, generation);
  for (const item of playing) {
    if (item.generation <= generation) item.source.stop();
  }
  playing = playing.filter((x) => x.generation > generation);
  cursor = playing.reduce((m, x) => Math.max(m, x.end), 0);
}

function report() {
  const now = outContext.currentTime;
  const started = playing.filter((x) => x.start <= now);
  if (started.length) {
    const generation = started[started.length - 1].generation;
    if (!current || current.generation !== generation) current = { generation, played: 0, last: now };
    current.played += now - current.last;
  }
  if (current) current.last = now;
  socket.send(JSON.stringify({
    type: "playback",
    generation: current ? current.generation : null,
    played_s: current ? Number(current.played.toFixed(3)) : 0,
  }));
}

async function start() {
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  micContext = new AudioContext({ sampleRate: IN_SR });
  outContext = new AudioContext({ sampleRate: OUT_SR });
  await micContext.audioWorklet.addModule("mic-worklet.js");
  const mic = new AudioWorkletNode(micContext, "mic");
  // 音量 0 のノードにつないでおく。出力につながっていないと、ブラウザが worklet を呼ばなくなることがある
  const silent = micContext.createGain();
  silent.gain.value = 0;
  micContext.createMediaStreamSource(stream).connect(mic).connect(silent).connect(micContext.destination);

  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket.binaryType = "arraybuffer";
  socket.onmessage = (event) => {
    if (typeof event.data !== "string") return onAudio(event.data);
    const message = JSON.parse(event.data);
    if (message.type === "stop") onStop(message.generation);
    if (message.type === "text") say(message.role, message.text);
  };
  socket.onclose = () => stop();
  mic.port.onmessage = (event) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(event.data.buffer);
  };
  socket.onopen = () => { reportTimer = setInterval(report, 100); };
  $("toggle").textContent = "止める";
  $("state").textContent = "聞いています";
}

function stop() {
  clearInterval(reportTimer);
  if (socket && socket.readyState <= WebSocket.OPEN) socket.close();
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (micContext) micContext.close();
  if (outContext) outContext.close();
  socket = micContext = outContext = stream = null;
  cursor = 0; stopped = -1; playing = []; current = null;
  $("toggle").textContent = "始める";
  $("state").textContent = "止まっています";
}

$("toggle").onclick = () => (socket ? stop() : start());
