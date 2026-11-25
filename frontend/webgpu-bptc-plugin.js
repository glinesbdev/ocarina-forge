/**
 * three r170's WebGPU getFormat() maps DXT, ETC2, and ASTC, but not BC7
 * (RGBA_BPTC_Format). The same build also misspells the sRGB BC7 enum as
 * "bc7-rgba-srgb"; the WebGPU name is "bc7-rgba-unorm-srgb".
 */
const NEEDLE = `case RGBA_S3TC_DXT5_Format:
				formatGPU = ( colorSpace === SRGBColorSpace ) ? GPUTextureFormat.BC3RGBAUnormSRGB : GPUTextureFormat.BC3RGBAUnorm;
				break;

			case RGB_ETC2_Format:`;

const PATCHED = `case RGBA_S3TC_DXT5_Format:
				formatGPU = ( colorSpace === SRGBColorSpace ) ? GPUTextureFormat.BC3RGBAUnormSRGB : GPUTextureFormat.BC3RGBAUnorm;
				break;

			case RGBA_BPTC_Format:
				formatGPU = ( colorSpace === SRGBColorSpace ) ? "bc7-rgba-unorm-srgb" : "bc7-rgba-unorm";
				break;

			case RGB_ETC2_Format:`;

const TYPO = "BC7RGBAUnormSRGB: 'bc7-rgba-srgb'";
const TYPO_FIXED = "BC7RGBAUnormSRGB: 'bc7-rgba-unorm-srgb'";

export function patchWebgpuBptc(code) {
  if (!code) return code;
  let next = code;
  if (next.includes(TYPO)) next = next.replaceAll(TYPO, TYPO_FIXED);
  if (!next.includes("case RGBA_BPTC_Format:") && next.includes(NEEDLE)) {
    next = next.replace(NEEDLE, PATCHED);
  }
  return next;
}

export function webgpuBptcPlugin() {
  return {
    name: "webgpu-bptc",
    enforce: "pre",
    transform(code, id) {
      const file = id.split("?")[0].replaceAll("\\", "/");
      if (!file.includes("/three/") && !file.endsWith("/three.webgpu.js") && !file.endsWith("WebGPUTextureUtils.js")) {
        return null;
      }
      const next = patchWebgpuBptc(code);
      if (next === code) return null;
      return { code: next, map: null };
    },
  };
}
