// マイクと再生だけ。判断はしない。
// 再生の規則は bench/player.py と同じ:
//   届いた断片は、前の断片の続きに置く。前が終わっていれば、すぐ鳴らす。
//   停止（世代 g）が来たら、g 以前の世代の音を止めて捨てる。以後に届く g 以前の断片も捨てる。
const IN_SR = 16000, OUT_SR = 24000;
let session = null;  // 接続と資源の所有者。会話の状態はサーバーが持つ。

const $ = (id) => document.getElementById(id);
const live = (s) => session === s && !s.closed;

function say(role, text) {
  const p = document.createElement("p");
  p.className = role;
  p.textContent = (role === "user" ? "あなた: " : "Tomoko: ") + text;
  $("log").prepend(p);
}

function onAudio(s, buffer) {
  const head = new DataView(buffer);
  const generation = head.getUint32(0, true);
  if (generation <= s.stopped) return;
  const samples = new Float32Array(buffer, 8);
  const audio = s.outContext.createBuffer(1, samples.length, OUT_SR);
  audio.copyToChannel(samples, 0);
  const source = s.outContext.createBufferSource();
  source.buffer = audio;
  source.connect(s.outContext.destination);
  const start = Math.max(s.outContext.currentTime, s.cursor);
  source.start(start);
  s.cursor = start + audio.duration;
  const item = { source, generation, start, end: s.cursor };
  s.playing.push(item);
  source.onended = () => { s.playing = s.playing.filter((x) => x !== item); };
}

function onStop(s, generation) {
  s.stopped = Math.max(s.stopped, generation);
  for (const item of s.playing) {
    if (item.generation <= generation) item.source.stop();
  }
  s.playing = s.playing.filter((x) => x.generation > generation);
  s.cursor = s.playing.reduce((m, x) => Math.max(m, x.end), 0);
}

function report(s) {
  if (!live(s) || s.socket.readyState !== WebSocket.OPEN) return;
  const now = s.outContext.currentTime;
  const started = s.playing.filter((x) => x.start <= now);
  if (started.length) {
    const generation = started[started.length - 1].generation;
    if (!s.current || s.current.generation !== generation) s.current = { generation, played: 0, last: now };
    s.current.played += now - s.current.last;
  }
  if (s.current) s.current.last = now;
  s.socket.send(JSON.stringify({
    type: "playback",
    generation: s.current ? s.current.generation : null,
    played_s: s.current ? Number(s.current.played.toFixed(3)) : 0,
  }));
}

async function start() {
  if (session) return;
  const s = { closed: false, cursor: 0, stopped: -1, playing: [], current: null };
  session = s;
  $("toggle").disabled = true;
  $("state").textContent = "マイクを準備しています";
  try {
    // resume は許可ダイアログを待つ前、開始ボタンの操作中に要求する。
    s.micContext = new AudioContext({ sampleRate: IN_SR });
    s.outContext = new AudioContext({ sampleRate: OUT_SR });
    await Promise.all([s.micContext.resume(), s.outContext.resume(),
      s.micContext.audioWorklet.addModule("mic-worklet.js")]);
    if (!live(s)) return stop(s);
    s.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    if (!live(s)) return stop(s);
    s.mic = new AudioWorkletNode(s.micContext, "mic");
    // 出力につながっていない worklet をブラウザが休止しないよう、無音で接続する。
    const silent = s.micContext.createGain();
    silent.gain.value = 0;
    s.micContext.createMediaStreamSource(s.stream).connect(s.mic).connect(silent).connect(s.micContext.destination);
    s.socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    s.socket.binaryType = "arraybuffer";
    s.socket.onmessage = (event) => {
      if (!live(s)) return;
      if (typeof event.data !== "string") return onAudio(s, event.data);
      const message = JSON.parse(event.data);
      if (message.type === "stop") onStop(s, message.generation);
      if (message.type === "text") say(message.role, message.text);
    };
    s.socket.onclose = () => { if (live(s)) stop(s, "接続が終了しました。「始める」で接続し直せます"); };
    s.socket.onerror = () => { if (live(s)) stop(s, "接続できませんでした。もう一度「始める」を押してください"); };
    s.mic.port.onmessage = (event) => {
      if (live(s) && s.socket.readyState === WebSocket.OPEN) s.socket.send(event.data.buffer);
    };
    s.socket.onopen = () => {
      if (!live(s)) return;
      s.reportTimer = setInterval(() => report(s), 100);
      $("state").textContent = "聞いています";
    };
    $("toggle").disabled = false;
    $("toggle").textContent = "止める";
    $("state").textContent = "接続しています";
  } catch (error) {
    stop(s, error.name === "NotAllowedError"
      ? "マイクの使用が許可されていません。ブラウザの設定を確認してください"
      : "開始できませんでした。マイクとブラウザの設定を確認してください");
  }
}

function stop(s = session, message = "止まっています") {
  if (!s) return;
  const owned = session === s;
  if (owned) session = null;
  s.closed = true;
  clearInterval(s.reportTimer);
  if (s.mic) s.mic.port.onmessage = null;
  if (s.socket) {
    s.socket.onopen = s.socket.onmessage = s.socket.onclose = s.socket.onerror = null;
    if (s.socket.readyState <= WebSocket.OPEN) s.socket.close();
    s.socket = null;
  }
  if (s.stream) { s.stream.getTracks().forEach((t) => t.stop()); s.stream = null; }
  for (const item of s.playing) item.source.stop();
  s.playing = [];
  for (const key of ["micContext", "outContext"]) {
    const context = s[key];
    s[key] = null;
    if (context) context.close().catch((error) => console.warn("AudioContext close failed", error));
  }
  if (owned) {
    $("toggle").disabled = false;
    $("toggle").textContent = "始める";
    $("state").textContent = message;
  }
}

$("toggle").onclick = () => (session ? stop() : start());
