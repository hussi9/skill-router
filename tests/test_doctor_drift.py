"""Live-install drift: the hooks run a separate copy of the code; doctor must say when it is stale."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor  # type: ignore[import-not-found]  # noqa: E402


def _tree(root: Path, files: dict[str, str]) -> Path:
    (root / "scripts").mkdir(parents=True)
    for name, text in files.items():
        (root / "scripts" / name).write_text(text)
    return root


def test_same_tree_is_in_sync(tmp_path):
    a = _tree(tmp_path / "a", {"router.py": "x", "doctor.py": "y"})
    b = _tree(tmp_path / "b", {"router.py": "x", "doctor.py": "y"})
    assert doctor.drift(a, b) == []


def test_missing_file_is_drift(tmp_path):
    a = _tree(tmp_path / "a", {"router.py": "import helper\n", "helper.py": "n",
                               "learn.py": "y"})
    b = _tree(tmp_path / "b", {"router.py": "import helper\n", "learn.py": "CHANGED"})
    assert doctor.drift(a, b) == ["scripts/helper.py", "scripts/learn.py"]


def test_only_hook_scripts_and_their_imports_are_compared(tmp_path):
    a = _tree(tmp_path / "a", {"router.py": "import helper\n", "helper.py": "h",
                               "doctor.py": "new", "benchmark_x.py": "new"})
    b = _tree(tmp_path / "b", {"router.py": "import helper\n", "helper.py": "h",
                               "doctor.py": "old"})
    assert doctor.hook_modules(a) == ["helper.py", "router.py"]
    assert doctor.drift(a, b) == []


def test_symlinked_live_dir_is_in_sync(tmp_path):
    a = _tree(tmp_path / "a", {"router.py": "x"})
    link = tmp_path / "link"
    link.symlink_to(a)
    assert doctor.drift(a, link) == []


def test_live_install_dir_handles_home_and_wrappers(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "HOME", tmp_path)
    settings = {"hooks": {"UserPromptSubmit": [{"hooks": [
        {"command": "FOO=1 python3 $HOME/skills/skill-router/scripts/router.py --hook"}]}]}}
    assert doctor.live_install_dir(settings) == tmp_path / "skills" / "skill-router"
    assert doctor.live_install_dir({}) is None


def test_tilde_only_expands_for_current_user(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "HOME", tmp_path)
    assert doctor._expand_home("~/x") == f"{tmp_path}/x"
    assert doctor._expand_home("~") == str(tmp_path)
    assert doctor._expand_home("~other/x") == "~other/x"
    assert doctor._expand_home("$HOMEDIR/x") == "$HOMEDIR/x"


def _run_check(tmp_path, monkeypatch, capsys, n_stale):
    imports = "".join(f"import f{i}\n" for i in range(n_stale))
    live = _tree(tmp_path / "live", {"router.py": imports, **{f"f{i}.py": "old" for i in range(n_stale)}})
    repo = _tree(tmp_path / "repo", {"router.py": imports, **{f"f{i}.py": "new" for i in range(n_stale)}})
    monkeypatch.setattr(doctor, "HOME", tmp_path)
    monkeypatch.setattr(doctor, "ROOT", repo)
    monkeypatch.setattr(doctor, "load_settings", lambda: {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"command": f"python3 {live}/scripts/router.py"}]}]}})
    r = doctor.Report(False)
    doctor.check_live_drift(r)
    return r, capsys.readouterr().out


def test_check_live_drift_truncates_and_never_suggests_delete(tmp_path, monkeypatch, capsys):
    r, out = _run_check(tmp_path, monkeypatch, capsys, 8)
    assert r.failed == 1
    assert "[FAIL] live install in sync" in out
    assert "(+3 more)" in out and "f4.py" in out and "f5.py" not in out
    assert "rsync -avn ~/repo/scripts/ ~/live/scripts/" in out
    assert "rsync -a ~/repo/scripts/ ~/live/scripts/ && python3 ~/live/scripts/install_hooks.py" in out
    assert "--delete" not in out


def test_gate_mode_warns_instead_of_failing_on_drift(tmp_path, monkeypatch, capsys):
    live = _tree(tmp_path / "live", {"router.py": "old"})
    repo = _tree(tmp_path / "repo", {"router.py": "new"})
    monkeypatch.setattr(doctor, "HOME", tmp_path)
    monkeypatch.setattr(doctor, "ROOT", repo)
    monkeypatch.setattr(doctor, "load_settings", lambda: {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"command": f"python3 {live}/scripts/router.py"}]}]}})
    r = doctor.Report(False, gate=True)
    doctor.check_live_drift(r)
    out = capsys.readouterr().out
    assert r.failed == 0 and "[warn] live install in sync" in out and "FAIL" not in out
