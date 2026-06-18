class TomokoMicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunkFrames = Math.round(sampleRate * 0.032);
    this.buffer = new Float32Array(this.chunkFrames);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }

    const channel = input[0];
    if (!channel) {
      return true;
    }

    let readOffset = 0;
    while (readOffset < channel.length) {
      const writable = Math.min(this.chunkFrames - this.offset, channel.length - readOffset);
      this.buffer.set(channel.subarray(readOffset, readOffset + writable), this.offset);
      this.offset += writable;
      readOffset += writable;

      if (this.offset === this.chunkFrames) {
        const chunk = this.buffer;
        this.port.postMessage(chunk.buffer, [chunk.buffer]);
        this.buffer = new Float32Array(this.chunkFrames);
        this.offset = 0;
      }
    }

    return true;
  }
}

registerProcessor("tomoko-mic-processor", TomokoMicProcessor);
