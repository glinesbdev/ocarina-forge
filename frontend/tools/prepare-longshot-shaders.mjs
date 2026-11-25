import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, existsSync, fstatSync, mkdtempSync, openSync, readFileSync,
  readSync, mkdirSync, renameSync, unlinkSync, writeFileSync, rmdirSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";

function shaderStore() {
  const { LONGSHOT_SHADER_STORE, LOCALAPPDATA, XDG_CACHE_HOME } = process.env;
  if (LONGSHOT_SHADER_STORE === "0") throw new Error("Longshot's shared shader store is disabled.");
  return LONGSHOT_SHADER_STORE || (process.platform === "win32"
    ? join(LOCALAPPDATA, "Longshot", "shaders")
    : join(XDG_CACHE_HOME || join(homedir(), ".cache"), "longshot", "shaders"));
}

function read(file, length, position) {
  const bytes = Buffer.alloc(length);
  for (let offset = 0; offset < length;) {
    const count = readSync(file, bytes, offset, length - offset, position + offset);
    if (!count) throw new Error("Longshot shader store ended mid-record.");
    offset += count;
  }
  return bytes;
}

function stageHash(bytes) {
  // Longshot gpu/vk/spirv_pack.h, hash_words().
  const wrap = (value) => BigInt.asUintN(64, value);
  let a = 0xcbf29ce484222325n;
  let b = 0x9e3779b97f4a7c15n ^ wrap(BigInt(bytes.length / 4) * 0xc2b2ae3d27d4eb4fn);
  for (let at = 0; at < bytes.length; at += 4) {
    const word = BigInt(bytes.readUInt32LE(at));
    a = wrap((a ^ word) * 0x100000001b3n);
    b ^= wrap(word * 0xff51afd7ed558ccdn);
    b = wrap(((b << 31n) | (b >> 33n)) * 0xc4ceb9fe1a85ec53n);
  }
  b = wrap((b ^ (b >> 33n)) * 0xff51afd7ed558ccdn);
  b = wrap((b ^ (b >> 33n)) * 0xc4ceb9fe1a85ec53n);
  b ^= b >> 33n;
  return [a, b].map((word) => word.toString(16).padStart(16, "0")).join("");
}

function readStages(store, requested, sample, visit) {
  const pack = openSync(join(store, "spirv.pack"), "r");
  try {
    const index = openSync(join(store, "spirv.idx"), "r");
    let records;
    try {
      const left = read(pack, 16, 0);
      const right = read(index, 16, 0);
      if (left.toString("ascii", 0, 4) !== "LSPK" || right.toString("ascii", 0, 4) !== "LSPI" ||
          left.readUInt32LE(4) !== 1 || !left.subarray(4).equals(right.subarray(4))) {
        throw new Error("Longshot shader pack and index have incompatible headers.");
      }
      const size = Math.floor((fstatSync(index).size - 16) / 32) * 32;
      records = read(index, size, 16);
    } finally {
      closeSync(index);
    }
    const stages = new Map();
    for (let at = 0; at < records.length; at += 32) {
      const position = records.readBigUInt64LE(at + 16);
      const words = records.readBigUInt64LE(at + 24);
      if (position < 40n || position > BigInt(Number.MAX_SAFE_INTEGER) || !words || words >= 1n << 24n) continue;
      const key = [0, 8].map((offset) => records.readBigUInt64LE(at + offset).toString(16).padStart(16, "0")).join("");
      stages.set(key, { at, position: Number(position), words: Number(words) });
    }
    const indexed = [...stages.keys()];
    const count = Math.min(sample, indexed.length);
    const selected = Array.from({ length: count }, (_, i) => indexed[Math.floor((i + 0.5) * indexed.length / count)]);
    for (const key of new Set([...requested, ...selected])) {
      visit(key, () => {
        const stage = stages.get(key);
        if (!stage) throw new Error("Stage is absent from Longshot's index.");
        const head = read(pack, 24, stage.position - 24);
        if (!head.subarray(0, 16).equals(records.subarray(stage.at, stage.at + 16)) ||
            head.readBigUInt64LE(16) !== BigInt(stage.words)) throw new Error("Stage header disagrees with the index.");
        const bytes = read(pack, stage.words * 4, stage.position);
        if (bytes.length < 20 || bytes.readUInt32LE() !== 0x07230203 || stageHash(bytes) !== key) {
          throw new Error("Stage failed Longshot's SPIR-V integrity check.");
        }
        return bytes;
      });
    }
  } finally {
    closeSync(pack);
  }
}

function importWgsl(file, translator, output, scratch) {
  const name = basename(file);
  const legacy = /^(?:nvn_)?(vs|fs|cs|vscs)_([a-f0-9]{10,16})\.wgsl$/i.exec(name);
  const role = { vs: "vertex", fs: "fragment", cs: "compute", vscs: "compute" };
  const identity = { file: name, stage: role[legacy?.[1]?.toLowerCase()] ?? null, programKey: legacy?.[2]?.toLowerCase() ?? null };
  try {
    const source = readFileSync(file);
    const hash = createHash("sha256").update(source).digest("hex");
    const destination = join(output, `direct-${hash}.wgsl`);
    const metadata = join(output, `direct-${hash}.json`);
    const cached = existsSync(destination) && existsSync(metadata);
    let shader;
    if (cached) {
      shader = JSON.parse(readFileSync(metadata, "utf8"));
    } else {
      writeFileSync(scratch, source);
      const result = spawnSync(translator, ["--inspect", scratch], { encoding: "utf8", timeout: 30000, windowsHide: true });
      if (result.error) throw result.error;
      if (result.status === 1 && result.stderr) {
        return { ...identity, status: "unsupported", reason: result.stderr.slice(0, 500).trim() };
      }
      if (result.status !== 0) throw new Error(`Inspector exited unexpectedly (${result.signal || result.status}).`);
      shader = JSON.parse(result.stdout);
    }
    if (!shader.entryPoints.length || (identity.stage && !shader.entryPoints.some((entry) => entry.stage === identity.stage))) {
      throw new Error("The shader entry points do not match the stage named in Longshot's dump.");
    }
    const constantBanks = [...source.toString("utf8").matchAll(/^\/\/ c(\d+) used (0x[0-9a-f]+|0)\s*$/gmi)]
      .map(([, bank, size]) => ({ bank: Number(bank), bytesUsed: Number(size) }));
    if (!cached) {
      renameSync(scratch, destination);
      writeFileSync(scratch, JSON.stringify({ ...shader, constantBanks }, null, 2));
      renameSync(scratch, metadata);
    }
    return { ...identity, status: cached ? "cached" : "imported", hash, ...shader, constantBanks };
  } catch (error) {
    return { ...identity, status: "error", reason: error.message };
  }
}

function prepare(selectionFile) {
  const { stages = [], sample = 0, wgsl = [], store, translator, cache } =
    JSON.parse(readFileSync(selectionFile, "utf8"));
  if (!Array.isArray(stages) || stages.some((key) => typeof key !== "string" || !/^[0-9a-f]{32}$/i.test(key)) ||
      !Array.isArray(wgsl) || wgsl.some((file) => typeof file !== "string" || !file.toLowerCase().endsWith(".wgsl")) ||
      !Number.isInteger(sample) || sample < 0 || !translator || !cache || (!stages.length && !sample && !wgsl.length)) {
    throw new Error("Provide WGSL dump files, stage hashes or a positive sample count, plus a translator and a local cache directory.");
  }
  const version = createHash("sha256").update(readFileSync(translator)).digest("hex");
  const output = join(cache, version);
  mkdirSync(output, { recursive: true });
  const scratch = mkdtempSync(join(output, ".prepare-"));
  const input = join(scratch, "stage.spv");
  const result = join(scratch, "stage.wgsl");
  const report = [];
  try {
    for (const file of wgsl) report.push(importWgsl(resolve(dirname(selectionFile), file), translator, output, result));
    if (stages.length || sample) readStages(store ?? shaderStore(), stages.map((key) => key.toLowerCase()), sample, (key, readStage) => {
      const destination = join(output, key + ".wgsl");
      if (existsSync(destination)) {
        report.push({ key, status: "cached" });
        return;
      }
      try {
        writeFileSync(input, readStage());
        const process = spawnSync(translator, [input, result], { encoding: "utf8", timeout: 30000, windowsHide: true });
        if (process.error) throw process.error;
        if (process.status === 1 && process.stderr) {
          report.push({ key, status: "unsupported", reason: process.stderr.slice(0, 500).trim() });
          return;
        }
        if (process.status !== 0) throw new Error(`Translator exited unexpectedly (${process.signal || process.status}).`);
        renameSync(result, destination);
        report.push({ key, status: "translated" });
      } catch (error) {
        report.push({ key, status: "error", reason: error.message });
      }
    });
  } finally {
    for (const file of [input, result]) if (existsSync(file)) unlinkSync(file);
    rmdirSync(scratch);
  }
  return report;
}

const selection = process.argv[2] || process.env.OCARINA_FORGE_SHADER_SELECTION;
if (selection) {
  try {
    const report = prepare(selection);
    if (process.argv[2]) {
      console.log(JSON.stringify(report, null, 2));
    } else {
      const ready = report.filter(({ status }) => ["translated", "imported", "cached"].includes(status)).length;
      console.log(`Longshot shader reference: ${ready}/${report.length} WGSL stages ready in the local cache.`);
      for (const { status, reason, file, key } of report) {
        if (status === "error") console.warn(`Longshot shader reference ${file || key}: ${reason}`);
      }
    }
  } catch (error) {
    console.error(`Longshot shader reference preparation: ${error.message}`);
    // The optional reference cache is independent of the browser renderer.
    if (process.argv[2]) process.exitCode = 1;
  }
}
