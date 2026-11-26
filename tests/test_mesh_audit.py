"""The scene mesh audit distinguishes absent, unsupported and invalid model output."""

from types import SimpleNamespace

from ocarina_forge import meshaudit, server
from ocarina_forge.local_api import editor_client
from ocarina_forge.formats.gltf import Primitive, join_glb, split_glb, to_glb


def test_mesh_audit_distinguishes_generation_failures_and_grouped_placements(tmp_path, monkeypatch):
    good = tmp_path / "good.glb"
    good.write_bytes(to_glb([Primitive([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [0, 1, 2])]))
    document, binary = split_glb(good.read_bytes())
    document["scenes"][0]["nodes"] = []
    detached = tmp_path / "detached.glb"
    detached.write_bytes(join_glb(document, binary))
    monkeypatch.setattr(meshaudit, "_cached_glb", {"good": good, "detached": detached}.get)
    monkeypatch.setattr(meshaudit, "shared_model_path", lambda _: tmp_path / "shared.glb")
    metadata = {
        "Land_Valid": {"status": "mesh", "digest": "good", "triangles": 1},
        "Land_Partial": {"status": "mesh", "digest": "good", "triangles": 1,
                         "reason": "1 shapes use FSHP flag 0x10 and were not decoded"},
        "Land_Detached": {"status": "mesh", "digest": "detached", "triangles": 1},
        "Land_Bridge": {"status": "box", "source": "Model/Bridge.bfres.zs",
                        "reason": "geometry format not decoded (FSHP flag 0x10)"},
        "Land_Missing": {"status": "box", "reason": "No model for Land_Missing."},
        "Obj_Logic": {"status": "box", "reason": "No mesh."},
    }
    visited = []

    def describe(_dump, name, *, convert=True):
        assert convert is False
        visited.append(name)
        if name == "Land_Cold":
            raise meshaudit.CacheMiss()
        return metadata[name]

    monkeypatch.setattr(meshaudit, "describe_actor", describe)
    banc = "Banc/Test/Test_Child_Dynamic.bcett.byml.zs"
    companion = banc.replace("Dynamic", "Static")
    dump = SimpleNamespace(list_scenes=lambda: [{"path": banc}, {"path": companion}])
    views = {banc: ["Land_Bridge", "Land_Valid", "Land_Partial", "Obj_Logic", "Land_Cold"],
             companion: ["Land_Bridge", "Land_Detached", "Land_Missing", "EftLoc_Marker"]}

    def view(bank):
        return {"actors": [{"gyaml": name, "id": f"{bank}-{i}"} for i, name in enumerate(views[bank])]}

    result = meshaudit.audit_scene_meshes(dump, banc, view=view)
    assert result["counts"] == dict(unsupported=1, ready=1, partial=1, invalid=1, missing=1, no_mesh=1, uncached=1)
    assert visited.count("Land_Bridge") == 1
    bridge = next(row for row in result["models"] if row["gyaml"] == "Land_Bridge")
    assert bridge["source"] == "Model/Bridge.bfres.zs"
    assert [actor["banc"] for actor in bridge["actors"]] == [banc, companion]
    text = meshaudit.render_text(result)
    assert "Land_Bridge (2 placements): unsupported geometry" in text
    assert "no drawable triangles" in text


def test_mesh_audit_endpoint_routes_scene_to_validator(monkeypatch):
    fixture = object()
    monkeypatch.setattr(server, "_open_dump", lambda: fixture)

    def audit(dump, banc, **_):
        assert dump is fixture
        return {"banc": banc, "counts": {}, "models": []}

    monkeypatch.setattr(meshaudit, "audit_scene_meshes", audit)
    reply = editor_client(server.app).get("/api/scene/mesh-audit", params={"banc": "sample"})
    assert reply.status_code == 200
    assert reply.json()["banc"] == "sample"
