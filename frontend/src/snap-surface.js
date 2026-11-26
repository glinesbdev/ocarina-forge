import * as THREE from "three";

const raycaster = new THREE.Raycaster();
const downward = new THREE.Vector3(0, -1, 0);

function raycastMesh(mesh, found) {
  if (mesh.isBatchedMesh) {
    THREE.BatchedMesh.prototype.raycast.call(mesh, raycaster, found);
    return;
  }
  if (mesh.isInstancedMesh) {
    THREE.InstancedMesh.prototype.raycast.call(mesh, raycaster, found);
    return;
  }
  THREE.Mesh.prototype.raycast.call(mesh, raycaster, found);
}

function skipMesh(mesh) {
  if (!mesh || mesh.visible === false || !mesh.isMesh || !mesh.geometry) return true;
  const data = mesh.userData || {};
  if (data.subTerrain || data.forgeEffectGizmo || data.snapProxy || data.previewWater) return true;
  const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
  if (material && material.userData && material.userData.banc === "water") return true;
  return false;
}

/**
 * Downward column through static meshes, InstancedMesh previews, and
 * BatchedMesh instances. Terrain proxies override `raycast` with a no-op,
 * so this calls the mesh prototype and still hits the map geometry.
 * `accept(hit)` false drops that hit (the actor being dragged, or a proxy).
 */
export function columnHits(roots, x, y, z, accept) {
  const originY = Number.isFinite(y) ? y : 0;
  raycaster.ray.origin.set(x, originY + 80, z);
  raycaster.ray.direction.copy(downward);
  raycaster.near = 0;
  raycaster.far = Infinity;
  const hits = [];
  const visit = (object) => {
    if (!object || object.visible === false) return;
    if (object.isMesh && !skipMesh(object)) {
      const found = [];
      try {
        raycastMesh(object, found);
      } catch {
        // A broken instance matrix should not blank the rest of the column.
      }
      for (const hit of found) {
        if (!hit.point || !Number.isFinite(hit.point.y)) continue;
        hit.object = object;
        if (accept && accept(hit) === false) continue;
        hits.push(hit);
      }
    }
    const children = object.children || [];
    for (let i = 0; i < children.length; i += 1) visit(children[i]);
  };
  for (const root of roots || []) visit(root);
  return hits;
}
