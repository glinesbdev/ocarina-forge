import assert from "node:assert/strict";
import test from "node:test";

import { patchWebgpuBptc } from "./webgpu-bptc-plugin.js";

test("the three r170 getFormat switch gains a BC7 case with the correct WebGPU name", () => {
  const source = `case RGBA_S3TC_DXT5_Format:
				formatGPU = ( colorSpace === SRGBColorSpace ) ? GPUTextureFormat.BC3RGBAUnormSRGB : GPUTextureFormat.BC3RGBAUnorm;
				break;

			case RGB_ETC2_Format:`;
  const next = patchWebgpuBptc(source);
  assert.match(next, /case RGBA_BPTC_Format:/);
  assert.match(next, /bc7-rgba-unorm-srgb/);
  assert.doesNotMatch(next, /"bc7-rgba-srgb"/);
  assert.equal(patchWebgpuBptc(next), next);
});

test("the r170 BC7 sRGB enum typo is corrected", () => {
  const source = "BC7RGBAUnormSRGB: 'bc7-rgba-srgb',";
  assert.equal(patchWebgpuBptc(source), "BC7RGBAUnormSRGB: 'bc7-rgba-unorm-srgb',");
});
