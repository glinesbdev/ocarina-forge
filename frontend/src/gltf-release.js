/**
 * GLTFLoader keeps the JSON document, the binary chunk, and a dependency
 * cache on the parser. The scene the viewer keeps does not need them once
 * the meshes and textures have been built.
 */
export function releaseGltfDocument(gltf) {
  const parser = gltf && gltf.parser;
  if (!parser) return false;
  parser.json = null;
  if (parser.cache && typeof parser.cache.removeAll === "function") parser.cache.removeAll();
  parser.associations = null;
  gltf.parser = null;
  return true;
}
