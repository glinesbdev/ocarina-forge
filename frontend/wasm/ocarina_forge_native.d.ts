/* tslint:disable */
/* eslint-disable */

/**
 * Packed Land_* meshes plus heightmap tiles, built from `/api/ground/surface`.
 */
export class WasmGroundSurface {
    free(): void;
    [Symbol.dispose](): void;
    memoryBytes(): number;
    constructor(data: Uint8Array | number[]);
    sampleHeightmap(x: number, z: number): number;
    sampleMesh(x: number, z: number, exclude_lo: number, exclude_hi: number): number;
    /**
     * Composed walkable height at (x, z). `exclude_lo/hi` are the low and
     * high 32 bits of the actor Hash to skip, or both 0 to skip none.
     */
    sample(x: number, z: number, exclude_lo: number, exclude_hi: number): number;
    readonly tileCount: number;
    readonly triangleCount: number;
    readonly vertexCount: number;
}

export class WasmMeshIndex {
    free(): void;
    [Symbol.dispose](): void;
    constructor(step: number, triangles: Float64Array);
    /**
     * Sample every XZ pair in `points` (length `2 * N`). Missing hits are NaN.
     */
    sampleMany(points: Float64Array): Float64Array;
    sampleOne(x: number, z: number): number;
    /**
     * Sample `points[2*start .. 2*end]` and return `end - start` heights.
     * The worker pool uses this so each worker only copies its own slice.
     */
    sampleRange(points: Float64Array, start: number, end: number): Float64Array;
    readonly triangleCount: number;
}

/**
 * Decode a linear (already deswizzled) surface to RGBA.
 */
export function decodeLinear(codec: string, width: number, height: number, linear: Uint8Array | number[], block_w: number, block_h: number): Uint8Array | undefined;

/**
 * Deswizzle and decode a block-linear surface to RGBA in one pass.
 */
export function decodeSwizzle(codec: string, width: number, height: number, block_w: number, block_h: number, bytes_per: number, tile: number, log2: number, raw: Uint8Array | number[], channels: Uint8Array | number[]): Uint8Array | undefined;

/**
 * Deswizzle Tegra/Switch block-linear bytes into a linear surface.
 */
export function deswizzle(width: number, height: number, block_w: number, block_h: number, bytes_per: number, tile_mode: number, log2: number, data: Uint8Array | number[]): Uint8Array;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_wasmgroundsurface_free: (a: number, b: number) => void;
    readonly __wbg_wasmmeshindex_free: (a: number, b: number) => void;
    readonly decodeLinear: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number) => [number, number];
    readonly decodeSwizzle: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: number, j: number, k: number, l: number, m: number) => [number, number];
    readonly deswizzle: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: number) => [number, number];
    readonly wasmgroundsurface_memoryBytes: (a: number) => number;
    readonly wasmgroundsurface_new: (a: number, b: number) => [number, number, number];
    readonly wasmgroundsurface_sample: (a: number, b: number, c: number, d: number, e: number) => number;
    readonly wasmgroundsurface_sampleHeightmap: (a: number, b: number, c: number) => number;
    readonly wasmgroundsurface_sampleMesh: (a: number, b: number, c: number, d: number, e: number) => number;
    readonly wasmgroundsurface_tileCount: (a: number) => number;
    readonly wasmgroundsurface_triangleCount: (a: number) => number;
    readonly wasmgroundsurface_vertexCount: (a: number) => number;
    readonly wasmmeshindex_new: (a: number, b: any) => number;
    readonly wasmmeshindex_sampleMany: (a: number, b: any) => any;
    readonly wasmmeshindex_sampleOne: (a: number, b: number, c: number) => number;
    readonly wasmmeshindex_sampleRange: (a: number, b: any, c: number, d: number) => any;
    readonly wasmmeshindex_triangleCount: (a: number) => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
