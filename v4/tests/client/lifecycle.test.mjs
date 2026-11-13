// MOCK: browser APIs only; no microphone, browser, network, or external packages.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../client/main.js", import.meta.url), "utf8");
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function rig(options = {}) {
  const h = { streams: [], contexts: [], sockets: [], worklets: [], timers: new Map(), errors: [], captureCalls: 0 };
  const elements = { toggle: { textContent: "始める", disabled: false }, state: { textContent: "止まっています" }, log: { prepend() {} } };
  h.elements = elements;
  h.media = () => { const track = { stopped: 0, stop() { this.stopped++; } }; const stream = { track, getTracks: () => [track] }; h.streams.push(stream); return stream; };
  let constructors = 0, timerId = 0;
  const connect = { connect(target) { return target; } };
  class Context {
    constructor({ sampleRate }) {
      if (++constructors === options.failConstructor) throw new Error("MOCK constructor");
      this.sampleRate = sampleRate; this.currentTime = 0; this.state = "suspended";
      this.closed = 0; this.resumed = 0; this.sources = []; this.destination = {};
      this.audioWorklet = { addModule: () => options.failModule ? Promise.reject(new Error("MOCK module")) : Promise.resolve() };
      h.contexts.push(this);
    }
    resume() { this.resumed++; this.state = "running"; return options.failResume ? Promise.reject(new Error("MOCK resume")) : Promise.resolve(); }
    close() { this.closed++; this.state = "closed"; return Promise.resolve(); }
    createMediaStreamSource() { return connect; }
    createGain() { return { ...connect, gain: { value: 1 } }; }
    createBuffer(_channels, length, rate) { return { duration: length / rate, copyToChannel() {} }; }
    createBufferSource() {
      const s = { connect() {}, start() {}, stop() { this.stopped = true; } };
      this.sources.push(s); return s;
    }
  }
  class Socket {
    static OPEN = 1;
    constructor() { this.readyState = 0; this.sent = []; this.closes = 0; h.sockets.push(this); }
    send(data) { assert.equal(this.readyState, 1); this.sent.push(data); }
    close() { this.closes++; this.readyState = 3; }
    open() { this.readyState = 1; this.onopen?.({}); }
  }
  class Worklet {
    constructor() { this.port = { onmessage: null }; h.worklets.push(this); }
    connect(target) { return target; }
  }
  const context = vm.createContext({
    navigator: { mediaDevices: { getUserMedia: () => {
      h.captureCalls++;
      if (options.denied) return Promise.reject(Object.assign(new Error("MOCK denied"), { name: "NotAllowedError" }));
      return options.permission?.(h) ?? Promise.resolve(h.media());
    } } },
    document: { getElementById: (id) => elements[id], createElement: () => ({}) },
    AudioContext: Context, AudioWorkletNode: Worklet, WebSocket: Socket,
    location: { protocol: "http:", host: "localhost:8700" },
    setInterval(fn) { h.timers.set(++timerId, fn); return timerId; },
    clearInterval(id) { h.timers.delete(id); },
    console: { warn() {} }, DataView, Float32Array, ArrayBuffer,
  });
  vm.runInContext(source, context);
  h.click = async () => { if (!elements.toggle.disabled) try { await elements.toggle.onclick(); } catch (e) { h.errors.push(e); } await flush(); };
  h.stop = () => vm.runInContext("stop()", context);
  return h;
}
function packet() { const b = new ArrayBuffer(24); new DataView(b).setUint32(0, 0, true); return b; }
function cleaned(h) { assert(h.streams.every((s) => s.track.stopped === 1)); assert(h.contexts.every((c) => c.closed === 1)); assert.equal(h.timers.size, 0); assert.equal(h.errors.length, 0); }

test("permission denial is visible and can be retried manually", async () => {
  const h = rig({ denied: true }); await h.click();
  assert.match(h.elements.state.textContent, /許可/); assert.equal(h.errors.length, 0);
  assert.equal(h.elements.toggle.disabled, false); assert.equal(h.sockets.length, 0);
  await h.click(); assert.equal(h.captureCalls, 2);
});
for (const [name, options] of [["second context", { failConstructor: 2 }], ["worklet module", { failModule: true }], ["resume", { failResume: true }]]) {
  test(`${name} failure releases every acquired resource`, async () => {
    const h = rig(options); await h.click(); cleaned(h);
    assert.match(h.elements.state.textContent, /できません/); assert.equal(h.elements.toggle.textContent, "始める");
    assert.equal(h.sockets.length, 0);
  });
}
test("repeated starts during permission wait acquire only one stream", async () => {
  const gate = deferred(); const h = rig({ permission: () => gate.promise });
  const first = h.click(); await flush(); const second = h.click(); gate.resolve(h.media()); await Promise.all([first, second]);
  assert.equal(h.captureCalls, 1); assert.equal(h.contexts.length, 2); assert.equal(h.sockets.length, 1);
  assert.equal(h.errors.length, 0);
});
test("listening starts only after open and suspended contexts are resumed", async () => {
  const h = rig(); await h.click();
  assert.match(h.elements.state.textContent, /接続/); assert.notEqual(h.elements.state.textContent, "聞いています");
  assert(h.contexts.every((c) => c.resumed === 1)); assert.equal(h.timers.size, 0);
  h.sockets[0].open(); assert.equal(h.elements.state.textContent, "聞いています"); assert.equal(h.timers.size, 1);
});
test("connection failure cleans up without automatic retry", async () => {
  const h = rig(); await h.click(); h.sockets[0].onerror?.({}); await flush();
  cleaned(h); assert.match(h.elements.state.textContent, /接続.*できません/); assert.equal(h.sockets.length, 1);
  await h.click(); assert.equal(h.sockets.length, 2); h.sockets[1].open();
  assert.equal(h.elements.state.textContent, "聞いています");
});
test("remote close releases resources and permits manual restart", async () => {
  const h = rig(); await h.click(); const old = h.sockets[0]; old.open(); old.readyState = 3; old.onclose({ wasClean: false }); await flush();
  cleaned(h); await h.click(); assert.equal(h.sockets.length, 2); assert.equal(h.errors.length, 0);
});
test("stale socket, audio, worklet and timer callbacks cannot touch a restarted session", async () => {
  const h = rig(); await h.click(); const old = h.sockets[0]; old.open();
  const callbacks = { open: old.onopen, close: old.onclose, error: old.onerror, message: old.onmessage,
    mic: h.worklets[0].port.onmessage, timer: [...h.timers.values()][0] };
  await h.click(); cleaned(h); await h.click(); const next = h.sockets[1]; next.open();
  for (const [key, event] of [["open", {}], ["mic", { data: new Float32Array(4) }], ["message", { data: packet() }], ["timer", {}], ["close", {}], ["error", {}]]) {
    assert.doesNotThrow(() => callbacks[key]?.(event));
  }
  assert.equal(next.closes, 0); assert.equal(next.sent.length, 0); assert.equal(h.timers.size, 1);
  assert(h.contexts.slice(2).every((c) => c.closed === 0 && c.sources.length === 0));
  assert.equal(h.streams[1].track.stopped, 0);
});
test("stopping while connecting ignores a late open", async () => {
  const h = rig(); await h.click(); const lateOpen = h.sockets[0].onopen;
  await h.click(); cleaned(h); lateOpen({}); assert.equal(h.timers.size, 0);
  await h.click(); assert.equal(h.sockets.length, 2);
});
test("a permission result arriving after stop is disposed without replacing a new session", async () => {
  const gate = deferred(); const h = rig({ permission: (r) => r.captureCalls === 1 ? gate.promise : undefined });
  const pending = h.click(); await flush(); h.stop(); await h.click(); const late = h.media(); gate.resolve(late); await pending;
  assert.equal(late.track.stopped, 1); assert.equal(h.streams[0].track.stopped, 0);
  assert.equal(h.sockets.length, 1); h.sockets[0].open(); assert.equal(h.elements.state.textContent, "聞いています");
});
test("audio resume is requested before waiting for microphone permission", async () => {
  const gate = deferred(); const h = rig({ permission: () => gate.promise }); const pending = h.click(); await flush();
  const resumed = h.contexts.length === 2 && h.contexts.every((c) => c.resumed === 1);
  gate.resolve(h.media()); await pending; assert(resumed);
});
test("the live connection sends mic data and preserves playback generation stops", async () => {
  const h = rig(); await h.click(); const socket = h.sockets[0], mic = h.worklets[0].port.onmessage;
  mic({ data: new Float32Array(4) }); assert.equal(socket.sent.length, 0);
  socket.open(); mic({ data: new Float32Array(4) }); assert.equal(socket.sent.length, 1);
  socket.onmessage({ data: packet() }); const output = h.contexts[1]; assert.equal(output.sources.length, 1);
  socket.onmessage({ data: JSON.stringify({ type: "stop", generation: 0 }) }); assert(output.sources[0].stopped);
  socket.onmessage({ data: packet() }); assert.equal(output.sources.length, 1);
  await h.click(); cleaned(h); await h.click(); h.sockets[1].open(); h.sockets[1].onmessage({ data: packet() });
  assert.equal(h.contexts[3].sources.length, 1);
});
