export class LengthPrefixedFrameReader {
  constructor(maxFrameBytes, onFrame) {
    if (!Number.isInteger(maxFrameBytes) || maxFrameBytes < 1) {
      throw new RangeError("maxFrameBytes must be a positive integer");
    }
    if (typeof onFrame !== "function") {
      throw new TypeError("onFrame must be a function");
    }
    this.maxFrameBytes = maxFrameBytes;
    this.onFrame = onFrame;
    this.header = Buffer.alloc(4);
    this.headerBytes = 0;
    this.frame = null;
    this.frameBytes = 0;
  }

  push(chunk) {
    let offset = 0;
    while (offset < chunk.length) {
      if (this.frame === null) {
        const headerBytes = Math.min(this.header.length - this.headerBytes, chunk.length - offset);
        chunk.copy(this.header, this.headerBytes, offset, offset + headerBytes);
        this.headerBytes += headerBytes;
        offset += headerBytes;
        if (this.headerBytes < this.header.length) continue;

        const frameBytes = this.header.readUInt32LE(0);
        if (frameBytes <= 0 || frameBytes > this.maxFrameBytes) {
          throw new RangeError(`frame size ${frameBytes} is not usable`);
        }
        this.frame = Buffer.allocUnsafe(frameBytes);
        this.frameBytes = 0;
      }

      const bodyBytes = Math.min(this.frame.length - this.frameBytes, chunk.length - offset);
      chunk.copy(this.frame, this.frameBytes, offset, offset + bodyBytes);
      this.frameBytes += bodyBytes;
      offset += bodyBytes;

      if (this.frameBytes === this.frame.length) {
        const completeFrame = this.frame;
        this.frame = null;
        this.frameBytes = 0;
        this.headerBytes = 0;
        this.onFrame(completeFrame);
      }
    }
  }
}
