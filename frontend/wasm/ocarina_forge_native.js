/* @ts-self-types="./ocarina_forge_native.d.ts" */

/**
 * Packed Land_* meshes plus heightmap tiles, built from `/api/ground/surface`.
 */
export class WasmGroundSurface {
    __destroy_into_raw() {
        const ptr = this.__wbg_ptr;
        this.__wbg_ptr = 0;
        WasmGroundSurfaceFinalization.unregister(this);
        return ptr;
    }
    free() {
        const ptr = this.__destroy_into_raw();
        wasm.__wbg_wasmgroundsurface_free(ptr, 0);
    }
    /**
     * @returns {number}
     */
    memoryBytes() {
        const ret = wasm.wasmgroundsurface_memoryBytes(this.__wbg_ptr);
        return ret >>> 0;
    }
    /**
     * @param {Uint8Array | number[]} data
     */
    constructor(data) {
        const ptr0 = passArray8ToWasm0(data, wasm.__wbindgen_malloc);
        const len0 = WASM_VECTOR_LEN;
        const ret = wasm.wasmgroundsurface_new(ptr0, len0);
        if (ret[2]) {
            throw takeFromExternrefTable0(ret[1]);
        }
        this.__wbg_ptr = ret[0];
        WasmGroundSurfaceFinalization.register(this, this.__wbg_ptr, this);
        return this;
    }
    /**
     * @param {number} x
     * @param {number} z
     * @returns {number}
     */
    sampleHeightmap(x, z) {
        const ret = wasm.wasmgroundsurface_sampleHeightmap(this.__wbg_ptr, x, z);
        return ret;
    }
    /**
     * @param {number} x
     * @param {number} z
     * @param {number} exclude_lo
     * @param {number} exclude_hi
     * @returns {number}
     */
    sampleMesh(x, z, exclude_lo, exclude_hi) {
        const ret = wasm.wasmgroundsurface_sampleMesh(this.__wbg_ptr, x, z, exclude_lo, exclude_hi);
        return ret;
    }
    /**
     * Composed walkable height at (x, z). `exclude_lo/hi` are the low and
     * high 32 bits of the actor Hash to skip, or both 0 to skip none.
     * @param {number} x
     * @param {number} z
     * @param {number} exclude_lo
     * @param {number} exclude_hi
     * @returns {number}
     */
    sample(x, z, exclude_lo, exclude_hi) {
        const ret = wasm.wasmgroundsurface_sample(this.__wbg_ptr, x, z, exclude_lo, exclude_hi);
        return ret;
    }
    /**
     * @returns {number}
     */
    get tileCount() {
        const ret = wasm.wasmgroundsurface_tileCount(this.__wbg_ptr);
        return ret >>> 0;
    }
    /**
     * @returns {number}
     */
    get triangleCount() {
        const ret = wasm.wasmgroundsurface_triangleCount(this.__wbg_ptr);
        return ret >>> 0;
    }
    /**
     * @returns {number}
     */
    get vertexCount() {
        const ret = wasm.wasmgroundsurface_vertexCount(this.__wbg_ptr);
        return ret >>> 0;
    }
}
if (Symbol.dispose) WasmGroundSurface.prototype[Symbol.dispose] = WasmGroundSurface.prototype.free;

export class WasmMeshIndex {
    __destroy_into_raw() {
        const ptr = this.__wbg_ptr;
        this.__wbg_ptr = 0;
        WasmMeshIndexFinalization.unregister(this);
        return ptr;
    }
    free() {
        const ptr = this.__destroy_into_raw();
        wasm.__wbg_wasmmeshindex_free(ptr, 0);
    }
    /**
     * @param {number} step
     * @param {Float64Array} triangles
     */
    constructor(step, triangles) {
        const ret = wasm.wasmmeshindex_new(step, triangles);
        this.__wbg_ptr = ret;
        WasmMeshIndexFinalization.register(this, this.__wbg_ptr, this);
        return this;
    }
    /**
     * Sample every XZ pair in `points` (length `2 * N`). Missing hits are NaN.
     * @param {Float64Array} points
     * @returns {Float64Array}
     */
    sampleMany(points) {
        const ret = wasm.wasmmeshindex_sampleMany(this.__wbg_ptr, points);
        return ret;
    }
    /**
     * @param {number} x
     * @param {number} z
     * @returns {number}
     */
    sampleOne(x, z) {
        const ret = wasm.wasmmeshindex_sampleOne(this.__wbg_ptr, x, z);
        return ret;
    }
    /**
     * Sample `points[2*start .. 2*end]` and return `end - start` heights.
     * The worker pool uses this so each worker only copies its own slice.
     * @param {Float64Array} points
     * @param {number} start
     * @param {number} end
     * @returns {Float64Array}
     */
    sampleRange(points, start, end) {
        const ret = wasm.wasmmeshindex_sampleRange(this.__wbg_ptr, points, start, end);
        return ret;
    }
    /**
     * @returns {number}
     */
    get triangleCount() {
        const ret = wasm.wasmmeshindex_triangleCount(this.__wbg_ptr);
        return ret >>> 0;
    }
}
if (Symbol.dispose) WasmMeshIndex.prototype[Symbol.dispose] = WasmMeshIndex.prototype.free;

/**
 * Decode a linear (already deswizzled) surface to RGBA.
 * @param {string} codec
 * @param {number} width
 * @param {number} height
 * @param {Uint8Array | number[]} linear
 * @param {number} block_w
 * @param {number} block_h
 * @returns {Uint8Array | undefined}
 */
export function decodeLinear(codec, width, height, linear, block_w, block_h) {
    const val3 = +width;
    const val4 = +height;
    const val5 = +block_w;
    const val6 = +block_h;
    let ptr0, len0, ptr1, len1;
    try {
        ptr0 = passStringToWasm0(codec, wasm.__wbindgen_malloc, wasm.__wbindgen_realloc);
        len0 = WASM_VECTOR_LEN;
        ptr1 = passArray8ToWasm0(linear, wasm.__wbindgen_malloc);
        len1 = WASM_VECTOR_LEN;
    } catch (e) {
        if (ptr1) { wasm.__wbindgen_free(ptr1, len1 * 1, 1); }
        if (ptr0) { wasm.__wbindgen_free(ptr0, len0 * 1, 1); }
        throw e;
    }
    const ret = wasm.decodeLinear(ptr0, len0, val3, val4, ptr1, len1, val5, val6);
    let v7;
    if (ret[0] !== 0) {
        v7 = getArrayU8FromWasm0(ret[0], ret[1]).slice();
        wasm.__wbindgen_free(ret[0], ret[1] * 1, 1);
    }
    return v7;
}

/**
 * Deswizzle and decode a block-linear surface to RGBA in one pass.
 * @param {string} codec
 * @param {number} width
 * @param {number} height
 * @param {number} block_w
 * @param {number} block_h
 * @param {number} bytes_per
 * @param {number} tile
 * @param {number} log2
 * @param {Uint8Array | number[]} raw
 * @param {Uint8Array | number[]} channels
 * @returns {Uint8Array | undefined}
 */
export function decodeSwizzle(codec, width, height, block_w, block_h, bytes_per, tile, log2, raw, channels) {
    const val4 = +width;
    const val5 = +height;
    const val6 = +block_w;
    const val7 = +block_h;
    const val8 = +bytes_per;
    const val9 = +tile;
    const val10 = +log2;
    let ptr0, len0, ptr1, len1, ptr2, len2;
    try {
        ptr0 = passStringToWasm0(codec, wasm.__wbindgen_malloc, wasm.__wbindgen_realloc);
        len0 = WASM_VECTOR_LEN;
        ptr1 = passArray8ToWasm0(raw, wasm.__wbindgen_malloc);
        len1 = WASM_VECTOR_LEN;
        ptr2 = passArray8ToWasm0(channels, wasm.__wbindgen_malloc);
        len2 = WASM_VECTOR_LEN;
    } catch (e) {
        if (ptr2) { wasm.__wbindgen_free(ptr2, len2 * 1, 1); }
        if (ptr1) { wasm.__wbindgen_free(ptr1, len1 * 1, 1); }
        if (ptr0) { wasm.__wbindgen_free(ptr0, len0 * 1, 1); }
        throw e;
    }
    const ret = wasm.decodeSwizzle(ptr0, len0, val4, val5, val6, val7, val8, val9, val10, ptr1, len1, ptr2, len2);
    let v11;
    if (ret[0] !== 0) {
        v11 = getArrayU8FromWasm0(ret[0], ret[1]).slice();
        wasm.__wbindgen_free(ret[0], ret[1] * 1, 1);
    }
    return v11;
}

/**
 * Deswizzle Tegra/Switch block-linear bytes into a linear surface.
 * @param {number} width
 * @param {number} height
 * @param {number} block_w
 * @param {number} block_h
 * @param {number} bytes_per
 * @param {number} tile_mode
 * @param {number} log2
 * @param {Uint8Array | number[]} data
 * @returns {Uint8Array}
 */
export function deswizzle(width, height, block_w, block_h, bytes_per, tile_mode, log2, data) {
    const val2 = +width;
    const val3 = +height;
    const val4 = +block_w;
    const val5 = +block_h;
    const val6 = +bytes_per;
    const val7 = +tile_mode;
    const val8 = +log2;
    const ptr0 = passArray8ToWasm0(data, wasm.__wbindgen_malloc);
    const len0 = WASM_VECTOR_LEN;
    const ret = wasm.deswizzle(val2, val3, val4, val5, val6, val7, val8, ptr0, len0);
    var v9 = getArrayU8FromWasm0(ret[0], ret[1]).slice();
    wasm.__wbindgen_free(ret[0], ret[1] * 1, 1);
    return v9;
}
function __wbg_get_imports() {
    const import0 = {
        __proto__: null,
        __wbg___wbindgen_throw_4c88d8bf264fd984: function(arg0, arg1) {
            throw new Error(getStringFromWasm0(arg0, arg1));
        },
        __wbg_length_5a8bb8abe1e54e15: function(arg0) {
            const ret = arg0.length;
            return ret;
        },
        __wbg_new_with_length_07edd8a7019b8c30: function(arg0) {
            const ret = new Float64Array(arg0 >>> 0);
            return ret;
        },
        __wbg_prototypesetcall_a366c038a3918afa: function(arg0, arg1, arg2) {
            Float64Array.prototype.set.call(getArrayF64FromWasm0(arg0, arg1), arg2);
        },
        __wbg_set_59dc36dce85e2698: function(arg0, arg1, arg2) {
            arg0.set(getArrayF64FromWasm0(arg1, arg2));
        },
        __wbindgen_generic_0000000000000001: function(arg0, arg1) {
            // Cast intrinsic for `Ref(String) -> Externref`.
            const ret = getStringFromWasm0(arg0, arg1);
            return ret;
        },
        __wbindgen_init_externref_table: function() {
            const table = wasm.__wbindgen_externrefs;
            table.set(0, undefined);
            table.set(1024, undefined);
            table.set(1025, null);
            table.set(1026, true);
            table.set(1027, false);
        },
    };
    return {
        __proto__: null,
        "./ocarina_forge_native_bg.js": import0,
    };
}

const WasmGroundSurfaceFinalization = (typeof FinalizationRegistry === 'undefined')
    ? { register: () => {}, unregister: () => {} }
    : new FinalizationRegistry(ptr => wasm.__wbg_wasmgroundsurface_free(ptr, 1));
const WasmMeshIndexFinalization = (typeof FinalizationRegistry === 'undefined')
    ? { register: () => {}, unregister: () => {} }
    : new FinalizationRegistry(ptr => wasm.__wbg_wasmmeshindex_free(ptr, 1));

function getArrayF64FromWasm0(ptr, len) {
    ptr = ptr >>> 0;
    return getFloat64ArrayMemory0().subarray(ptr / 8, ptr / 8 + len);
}

function getArrayU8FromWasm0(ptr, len) {
    ptr = ptr >>> 0;
    return getUint8ArrayMemory0().subarray(ptr / 1, ptr / 1 + len);
}

let cachedFloat64ArrayMemory0 = null;
function getFloat64ArrayMemory0() {
    if (cachedFloat64ArrayMemory0 === null || cachedFloat64ArrayMemory0.byteLength === 0) {
        cachedFloat64ArrayMemory0 = new Float64Array(wasm.memory.buffer);
    }
    return cachedFloat64ArrayMemory0;
}

function getStringFromWasm0(ptr, len) {
    ptr = ptr >>> 0;
    return decodeText(getUint8ArrayMemory0().subarray(ptr, ptr + len));
}

let cachedUint8ArrayMemory0 = null;
function getUint8ArrayMemory0() {
    if (cachedUint8ArrayMemory0 === null || cachedUint8ArrayMemory0.byteLength === 0) {
        cachedUint8ArrayMemory0 = new Uint8Array(wasm.memory.buffer);
    }
    return cachedUint8ArrayMemory0;
}

function passArray8ToWasm0(arg, malloc) {
    const ptr = malloc(arg.length * 1, 1) >>> 0;
    getUint8ArrayMemory0().set(arg, ptr / 1);
    WASM_VECTOR_LEN = arg.length;
    return ptr;
}

function passStringToWasm0(arg, malloc, realloc) {
    if (realloc === undefined) {
        const buf = cachedTextEncoder.encode(arg);
        const ptr = malloc(buf.length, 1) >>> 0;
        getUint8ArrayMemory0().subarray(ptr, ptr + buf.length).set(buf);
        WASM_VECTOR_LEN = buf.length;
        return ptr;
    }

    let len = arg.length;
    let ptr = malloc(len, 1) >>> 0;

    const mem = getUint8ArrayMemory0();

    let offset = 0;

    for (; offset < len; offset++) {
        const code = arg.charCodeAt(offset);
        if (code > 0x7F) break;
        mem[ptr + offset] = code;
    }
    if (offset !== len) {
        if (offset !== 0) {
            arg = arg.slice(offset);
        }
        ptr = realloc(ptr, len, len = offset + arg.length * 3, 1) >>> 0;
        const view = getUint8ArrayMemory0().subarray(ptr + offset, ptr + len);
        const ret = cachedTextEncoder.encodeInto(arg, view);

        offset += ret.written;
        ptr = realloc(ptr, len, offset, 1) >>> 0;
    }

    WASM_VECTOR_LEN = offset;
    return ptr;
}

function takeFromExternrefTable0(idx) {
    const value = wasm.__wbindgen_externrefs.get(idx);
    wasm.__externref_table_dealloc(idx);
    return value;
}

let cachedTextDecoder = new TextDecoder('utf-8', { ignoreBOM: true });
cachedTextDecoder.decode();
const MAX_SAFARI_DECODE_BYTES = 2146435072;
let numBytesDecoded = 0;
function decodeText(bytes) {
    numBytesDecoded += bytes.length;
    if (numBytesDecoded >= MAX_SAFARI_DECODE_BYTES) {
        cachedTextDecoder = new TextDecoder('utf-8', { ignoreBOM: true });
        cachedTextDecoder.decode();
        numBytesDecoded = bytes.length;
    }
    return cachedTextDecoder.decode(bytes);
}

const cachedTextEncoder = new TextEncoder();

if (!('encodeInto' in cachedTextEncoder)) {
    cachedTextEncoder.encodeInto = function (arg, view) {
        const buf = cachedTextEncoder.encode(arg);
        view.set(buf);
        return {
            read: arg.length,
            written: buf.length
        };
    };
}

let WASM_VECTOR_LEN = 0;

let wasmModule, wasmInstance, wasm;
function __wbg_finalize_init(instance, module) {
    wasmInstance = instance;
    wasm = instance.exports;
    wasmModule = module;
    cachedFloat64ArrayMemory0 = null;
    cachedUint8ArrayMemory0 = null;
    wasm.__wbindgen_start();
    return wasm;
}

async function __wbg_load(module, imports) {
    if (typeof Response === 'function' && module instanceof Response) {
        if (!module.ok) {
            throw new Error(`failed to fetch Wasm: ${module.status} ${module.statusText} fetching '${module.url}'`);
        }

        if (typeof WebAssembly.instantiateStreaming === 'function') {
            try {
                return await WebAssembly.instantiateStreaming(module, imports);
            } catch (e) {
                const validResponse = expectedResponseType(module.type);

                if (validResponse && module.headers.get('Content-Type') !== 'application/wasm') {
                    console.warn("`WebAssembly.instantiateStreaming` failed because your server does not serve Wasm with `application/wasm` MIME type. Falling back to `WebAssembly.instantiate` which is slower. Original error:\n", e);

                } else { throw e; }
            }
        }

        const bytes = await module.arrayBuffer();
        return await WebAssembly.instantiate(bytes, imports);
    } else {
        const instance = await WebAssembly.instantiate(module, imports);

        if (instance instanceof WebAssembly.Instance) {
            return { instance, module };
        } else {
            return instance;
        }
    }

    function expectedResponseType(type) {
        switch (type) {
            case 'basic': case 'cors': case 'default': return true;
        }
        return false;
    }
}

function initSync(module) {
    if (wasm !== undefined) return wasm;


    if (module !== undefined) {
        if (Object.getPrototypeOf(module) === Object.prototype) {
            ({module} = module)
        } else {
            console.warn('using deprecated parameters for `initSync()`; pass a single object instead')
        }
    }

    const imports = __wbg_get_imports();
    if (!(module instanceof WebAssembly.Module)) {
        module = new WebAssembly.Module(module);
    }
    const instance = new WebAssembly.Instance(module, imports);
    return __wbg_finalize_init(instance, module);
}

let __wbg_init_promise;
async function __wbg_init(module_or_path) {
    if (wasm !== undefined) return wasm;
    if (__wbg_init_promise === undefined) {
        __wbg_init_promise = __wbg_init_once(module_or_path);
        __wbg_init_promise.catch(() => { __wbg_init_promise = undefined; });
    }
    return __wbg_init_promise;
}

async function __wbg_init_once(module_or_path) {

    if (module_or_path !== undefined) {
        if (Object.getPrototypeOf(module_or_path) === Object.prototype) {
            ({module_or_path} = module_or_path)
        } else {
            console.warn('using deprecated parameters for the initialization function; pass a single object instead')
        }
    }

    if (module_or_path === undefined) {
        module_or_path = new URL('ocarina_forge_native_bg.wasm', import.meta.url);
    }
    const imports = __wbg_get_imports();

    if (typeof module_or_path === 'string' || (typeof Request === 'function' && module_or_path instanceof Request) || (typeof URL === 'function' && module_or_path instanceof URL)) {
        module_or_path = fetch(module_or_path);
    }

    const { instance, module } = await __wbg_load(await module_or_path, imports);

    return __wbg_finalize_init(instance, module);
}

export { initSync, __wbg_init as default };
