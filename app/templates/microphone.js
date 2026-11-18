// The input AudioContext runs at 16 kHz and mixes meeting audio down to mono.
class MeetingMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.packet = new ArrayBuffer(640); // 320 PCM16 samples = 20 ms.
    this.samples = new DataView(this.packet);
    this.position = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      const clipped = Math.max(-1, Math.min(1, sample));
      const pcm = Math.round(clipped * (clipped < 0 ? 32768 : 32767));
      this.samples.setInt16(this.position * 2, pcm, true);
      if (++this.position === 320) {
        this.port.postMessage(this.packet, [this.packet]);
        this.packet = new ArrayBuffer(640);
        this.samples = new DataView(this.packet);
        this.position = 0;
      }
    }
    // Leave the output buffers silent: this node only captures microphone audio.
    return true;
  }
}

registerProcessor("meeting-microphone", MeetingMicrophone);
