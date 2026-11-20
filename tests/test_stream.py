"""Convert timings: texture decode is nested, and the server summary keeps misses."""

from ocarina_forge.dump import Dump
from ocarina_forge.fixtures import build_fixture
from ocarina_forge.models import describe_actor
from ocarina_forge.streamlog import STREAM, ConvertClock


def test_texture_time_nests_and_a_hit_does_not_add_to_the_miss_total():
    clock = ConvertClock()
    clock.begin_texture()
    clock.begin_texture()
    clock.end_texture()
    clock.end_texture()
    done = clock.finish("miss")
    assert done["cache"] == "miss"
    assert done["textureMs"] >= 0
    assert done["convertMs"] >= done["textureMs"]

    before = STREAM.summary()
    STREAM.record("model", "Npc_Kokiri_F_01", {"cache": "miss", "convertMs": 12.5, "textureMs": 9})
    STREAM.record("model", "Obj_Tree", {"cache": "hit", "convertMs": 0.2, "textureMs": 0})
    summary = STREAM.summary()
    assert summary["misses"] == before["misses"] + 1
    assert summary["hits"] == before["hits"] + 1
    assert summary["convertMs"] == round(before["convertMs"] + 12.5, 1)
    assert summary["lines"][-1].startswith("model Obj_Tree cache=hit")
    assert "convert=12.5ms texture=9ms" in summary["lines"][-2]


def test_a_converted_model_reports_convert_and_texture_time(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    dump = Dump(build_fixture(tmp_path / "dump"))
    cold = describe_actor(dump, "Npc_Kokiri_F_01")
    assert cold["status"] == "mesh"
    assert cold["timing"]["cache"] == "miss"
    assert cold["timing"]["convertMs"] > 0
    assert cold["timing"]["textureMs"] >= 0
    warm = describe_actor(dump, "Npc_Kokiri_F_01")
    assert warm["timing"]["cache"] == "hit"
    assert warm["timing"]["textureMs"] == 0
    assert warm["digest"] == cold["digest"]
