# Comparing a Bancwright build with After the Ocarina

The goal is the same romfs After the Ocarina's own build writes. This machine has no dump, so the check runs on yours.

`examples/after-the-ocarina` is the project at `m8-navi-rumors` commit `ad60e27`. The attached archive was the parent, `72c844a`. This environment could not clone `glinesbdev/after-the-ocarina` (the git remote returned 403), so the one commit after the archive was applied from the Origin patch: the tapestry, the Zora starter's `ExceptionalBindType` 1, Mido's Show before Complete, and `Defeat_Ganon` Complete on the boot hook. If your checkout is newer, import it first so the two builds read the same YAML.

From the ocarina-forge checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Only when your checkout is not ad60e27. This rewrites examples/after-the-ocarina.
python scripts/import_ato.py ~/Work/after-the-ocarina examples/after-the-ocarina

# config.toml in the After the Ocarina checkout must point romfs (or pristine_romfs)
# at the 1.1.1 dump. The script runs that checkout's scripts/build.py --no-install
# with ~/Work/after-the-ocarina/.venv/bin/python when the virtualenv exists.
# Without it, the current interpreter is used and the script says to install
# that checkout's requirements.txt. The build writes build/romfs there.
python scripts/compare_romfs.py --ato ~/Work/after-the-ocarina
```

`compare_romfs.py` then builds `examples/after-the-ocarina` into `examples/after-the-ocarina/build/romfs` from the same romfs and diffs the two trees.

- Exit 0: every file is byte-identical.
- Exit 3: the only differences are explained. A compressed file can differ while the unpacked payload matches, and a BYML, event flow, or MSBT can differ in bytes while the parsed value matches.
- Exit 1: a file is missing or the parsed value differs. The line names the path.

You can also build the two trees yourself and point the diff at them:

```bash
cd ~/Work/after-the-ocarina
python scripts/build.py --no-install

cd ~/Work/ocarina-forge
python scripts/compare_romfs.py --ato ~/Work/after-the-ocarina --skip-ato-build
```

`--project` selects a different Bancwright project. `--romfs` overrides the dump only for the Bancwright half; After the Ocarina's `scripts/build.py` still reads its `config.toml`. Use the same dump for both.

`Defeat_Ganon` Complete on the first epilogue load is still a hypothesis in After the Ocarina's own notes. The build emits it. It has not been re-checked in game here.

What the engine builds, and what is still an advanced patch with no editor, is in [project-format.md](project-format.md).
