"""One-model profile, a short scene walk, and the private cache."""

from pathlib import Path

import ocarina_forge.formats.bfres as bfres
import ocarina_forge.models as models
from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import build_fixture
from ocarina_forge.profile import _run_pass, profile_dump


def test_one_model_profile_writes_cprofile_and_uses_a_private_cache(tmp_path):
    out = tmp_path / "one.prof"
    report = profile_dump(
        Dump(build_fixture(tmp_path / "dump")),
        model=["Npc_Kokiri_F_01"],
        cprofile=str(out),
        log=lambda *_args: None,
    )
    assert out.is_file() and out.stat().st_size > 0
    assert report["cachePrivate"] is True
    assert report["cacheKey"] == "models-48"
    assert report["cacheKept"] is False
    assert not Path(report["cache"]).exists()
    assert report["bc7"] in {"rust", "python"}
    assert report["pixels"] in {"rust", "python"}
    assert report["mesh"] in {"rust", "python"}
    assert "ocarina-forge-profile-" in report["cache"]
    cold = report["models"]["cold"][0]
    warm = report["models"]["warm"][0]
    assert cold["cache"] == "miss"
    assert cold["stages"]["bfres"] > 0
    assert "vertices" in cold["stages"]
    assert "indices" in cold["stages"]
    assert warm["cache"] == "hit"
    assert models.parse is bfres.parse


def test_jobs_converts_two_models_in_a_pool(tmp_path):
    root = build_fixture(tmp_path / "dump")
    rows = _run_pass(str(root), ["No_Such_A", "No_Such_B"], 2, lambda *_args: None, "cold")
    assert {row["name"] for row in rows} == {"No_Such_A", "No_Such_B"}
    assert all(row["status"] == "box" for row in rows)


def test_limit_stops_each_scene_and_jobs_can_run(tmp_path):
    report = profile_dump(
        Dump(build_fixture(tmp_path / "dump")),
        limit=1,
        jobs=2,
        log=lambda *_args: None,
    )
    assert report["kokiri"]
    assert all(scene["profiled"] <= 1 for scene in report["kokiri"])
    assert all(len(scene["cold"]) <= 1 for scene in report["kokiri"])
    assert "HyruleCastle" in report["missing"]


def test_keep_cache_leaves_the_private_directory(tmp_path):
    report = profile_dump(
        Dump(build_fixture(tmp_path / "dump")),
        model=["Npc_Kokiri_F_01"],
        keep_cache=True,
        log=lambda *_args: None,
    )
    kept = Path(report["cache"])
    assert report["cacheKept"] is True
    assert kept.is_dir()
    assert "profile-tmp" in kept.as_posix()
    import shutil

    shutil.rmtree(kept, ignore_errors=True)


def test_a_changed_file_is_not_served_from_the_read_cache(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    first_path = "Model/Npc_Kokiri_F.Npc_Kokiri_F_01.bfres.zs"
    second_path = "Model/Obj_Tree.Obj_Tree.bfres.zs"
    original = dump.read_file(first_path)[0]
    other = dump.read_file(second_path)[0]
    assert original != other
    target = dump.romfs / first_path
    target.write_bytes((dump.romfs / second_path).read_bytes())
    assert dump.read_file(first_path)[0] == other


def test_markers_stay_boxes_without_a_search(tmp_path):
    dump = Dump(build_fixture(tmp_path / "dump"))
    air = models.describe_actor(dump, "FldObj_AirWall_A")
    loc = models.describe_actor(dump, "EftLoc_Smoke")
    assert air["status"] == "box" and air["reason"].startswith("Marker")
    assert loc["status"] == "box" and "Tried" not in loc["reason"]
    missing = models.describe_actor(dump, "Npc_Kokiri_M_01")
    assert "No model" in missing["reason"]


def test_cprofile_without_one_model_is_rejected(tmp_path):
    try:
        profile_dump(
            Dump(build_fixture(tmp_path / "dump")),
            cprofile=str(tmp_path / "no.prof"),
            log=lambda *_args: None,
        )
    except SystemExit as exc:
        assert "exactly one" in str(exc)
    else:
        raise AssertionError("cprofile without --model should fail")
