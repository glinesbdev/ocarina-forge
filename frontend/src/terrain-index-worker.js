import { BufferAttribute, BufferGeometry } from "three";
import { MeshBVH } from "three-mesh-bvh";

self.onmessage = ({ data: { position, index } }) => {
  try {
    const geometry = new BufferGeometry().setAttribute("position", new BufferAttribute(position, 3));
    if (index) geometry.setIndex(new BufferAttribute(index, 1));
    const tree = new MeshBVH(geometry, { indirect: true });
    const { roots, indirectBuffer } = MeshBVH.serialize(tree, { cloneBuffers: false });
    self.postMessage({ roots, indirectBuffer }, [...roots, indirectBuffer.buffer]);
  } catch (error) {
    self.postMessage({ error: String(error) });
  }
};
