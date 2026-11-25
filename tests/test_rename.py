"""Old cache, config, and project-state paths are still read."""


def test_old_cache_directory_is_renamed(tmp_path, monkeypatch):
    monkeypatch.delenv("OCARINA_FORGE_CACHE", raising=False)
    monkeypatch.delenv("BANCWRIGHT_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "bancwright" / "models"
    old.mkdir(parents=True)
    (old / "kept.glb").write_bytes(b"glb")
    import ocarina_forge.models as models

    monkeypatch.setattr(models, "_cache_dir", None)
    found = models.cache_dir()
    assert found == tmp_path / "ocarina-forge" / "models"
    assert (found / "kept.glb").read_bytes() == b"glb"
    assert not (tmp_path / "bancwright").exists()


def test_legacy_cache_env_is_still_read(tmp_path, monkeypatch):
    monkeypatch.delenv("OCARINA_FORGE_CACHE", raising=False)
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "legacy"))
    import ocarina_forge.models as models

    monkeypatch.setattr(models, "_cache_dir", None)
    assert models.cache_dir() == tmp_path / "legacy"
    assert (tmp_path / "legacy").is_dir()


def test_new_cache_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("BANCWRIGHT_CACHE", str(tmp_path / "old"))
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "new"))
    import ocarina_forge.models as models

    monkeypatch.setattr(models, "_cache_dir", None)
    assert models.cache_dir() == tmp_path / "new"


def test_legacy_config_file_is_renamed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OCARINA_FORGE_CONFIG", raising=False)
    monkeypatch.delenv("BANCWRIGHT_CONFIG", raising=False)
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    legacy = home / ".config" / "bancwright" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("dump: /tmp/dump\n", encoding="utf-8")
    from ocarina_forge.config import config_path, load_config

    path = config_path()
    assert path == home / ".config" / "ocarina-forge" / "config.yaml"
    assert path.is_file()
    assert load_config()["dump"] == "/tmp/dump"
    assert not legacy.exists()


def test_legacy_project_state_dir_is_renamed(tmp_path):
    from ocarina_forge.locate import project_state_dir

    old = tmp_path / ".bancwright"
    old.mkdir()
    (old / "recipe-baseline.json").write_text("{}", encoding="utf-8")
    found = project_state_dir(tmp_path)
    assert found == tmp_path / ".ocarina-forge"
    assert (found / "recipe-baseline.json").is_file()
