// Capture diagnostics for the modules Three creates for real draws and
// warm-up. Reuse those modules instead of compiling another copy of the WGSL.
export function observeWgsl(backend) {
  const failures = [];
  if (backend?.isWebGPUBackend !== true) return () => failures;
  const create = backend.createProgram.bind(backend);
  backend.createProgram = (program) => {
    create(program);
    const module = backend.get(program).module?.module;
    if (!module?.getCompilationInfo) return;
    void module.getCompilationInfo().then(({ messages }) => {
      for (const { type, message, lineNum, linePos } of messages) {
        if (type !== "error" || failures.length >= 32) continue;
        failures.push({ stage: program.stage, program: program.id, message, line: lineNum, column: linePos });
      }
    }).catch(() => {});
  };
  return () => failures.slice();
}
