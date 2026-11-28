/**
 * Stand-in for `sharp`. The tile bake copies KTX2 bytes through and never
 * resizes an image. The real package loads a native binary at import time,
 * and a missing Windows build exits Node before the job runs.
 */
function sharp() {
  throw new Error("The tile bake does not resize images, but a transform called sharp.");
}

sharp.cache = () => {};
sharp.concurrency = () => {};

export default sharp;
