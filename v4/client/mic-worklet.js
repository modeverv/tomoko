// マイクの音を、そのままメインスレッドへ渡す。判断はしない。
class MicProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("mic", MicProcessor);
