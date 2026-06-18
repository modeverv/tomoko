class TomokoMicProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) {
      this.port.postMessage(input.slice(0).buffer, [input.slice(0).buffer]);
    }
    return true;
  }
}

registerProcessor("tomoko-mic", TomokoMicProcessor);
