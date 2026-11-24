"""The connected-tool launcher (app/mcp_launch.py), run as a real subprocess the
way the --mcp-config entry runs it: ``<python> -I <launcher> <folder> -- <cmd> ...``.
The "tool" is a tiny script these tests write; no real MCP server runs."""

import json
import os
import subprocess
import sys
from pathlib import Path

from app import mcp_manager

LAUNCHER = mcp_manager.launcher_path()

# Prints where it runs, its env and argv, and whether a planted module was imported.
TOOL = """import json, os, sys
print(json.dumps({"cwd": os.getcwd(), "env": dict(os.environ), "argv": sys.argv[1:],
                  "planted": "planted_mod" in sys.modules or "sitecustomize_planted" in os.environ}))
"""


def _tool(tmp_path) -> Path:
    d = tmp_path / "tools"
    d.mkdir(exist_ok=True)
    f = d / "tool.py"
    f.write_text(TOOL)
    return f


def _launch(folder, command, *args, cwd=None, env=None, timeout=20):
    return subprocess.run([sys.executable, "-I", LAUNCHER, str(folder), "--", command, *args],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _neutral(tmp_path) -> Path:
    d = tmp_path / "neutral"
    d.mkdir(mode=0o700)
    return d


def test_launcher_is_in_the_app_code_tree():
    p = Path(LAUNCHER)
    assert p.is_absolute() and p.name == "mcp_launch.py" and p.is_file()
    assert p.parent == Path(mcp_manager.__file__).resolve().parent


def test_cwd_pwd_and_env(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    neutral = _neutral(tmp_path)
    env = dict(os.environ, INIT_CWD=str(project), OLDPWD=str(project), PWD=str(project), KEEP_ME="yes")
    r = _launch(neutral, sys.executable, "-I", str(_tool(tmp_path)), cwd=project, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert Path(out["cwd"]).resolve() == neutral.resolve()
    assert out["env"]["PWD"] == str(neutral)
    assert "INIT_CWD" not in out["env"] and "OLDPWD" not in out["env"]
    assert out["env"]["KEEP_ME"] == "yes"  # nothing else changes


def test_args_pass_through_unchanged(tmp_path):
    neutral = _neutral(tmp_path)
    args = ["a b", "--", "-x", "", "--flag=1 2", "$HOME", "*"]
    r = _launch(neutral, sys.executable, "-I", str(_tool(tmp_path)), *args, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["argv"] == args


def test_bare_name_is_found_on_path(tmp_path):
    neutral = _neutral(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "fake-tool"
    exe.write_text(f"#!{sys.executable} -I\n" + TOOL)
    exe.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    r = _launch(neutral, "fake-tool", "x", env=env, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["argv"] == ["x"] and Path(out["cwd"]).resolve() == neutral.resolve()


def test_missing_folder_exits_127_with_one_line(tmp_path):
    r = _launch(tmp_path / "gone", sys.executable, "-c", "print('ran')", cwd=tmp_path)
    assert r.returncode == 127
    assert r.stdout == ""
    lines = r.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("mcp_launch: ") and "Traceback" not in r.stderr


def test_folder_that_is_a_file_exits_127(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    r = _launch(f, sys.executable, "-c", "print('ran')", cwd=tmp_path)
    assert r.returncode == 127 and r.stdout == "" and len(r.stderr.splitlines()) == 1


def test_missing_command_exits_127_with_one_line(tmp_path):
    neutral = _neutral(tmp_path)
    r = _launch(neutral, "co-no-such-tool-xyz", "a", cwd=tmp_path)
    assert r.returncode == 127
    lines = r.stderr.splitlines()
    assert len(lines) == 1 and "co-no-such-tool-xyz" in lines[0] and "Traceback" not in r.stderr


def test_bad_usage_exits_127(tmp_path):
    for argv in ([], [str(tmp_path)], [str(tmp_path), "x", "y"], [str(tmp_path), "--"]):
        r = subprocess.run([sys.executable, "-I", LAUNCHER, *argv], capture_output=True, text=True, timeout=20)
        assert r.returncode == 127 and len(r.stderr.splitlines()) == 1 and "Traceback" not in r.stderr


def test_project_modules_are_not_imported(tmp_path):
    """A sitecustomize.py, or a module named like one the launcher imports (os,
    sys), in the project folder (the launcher's starting cwd) is never run."""
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "PLANTED"
    planted = f"open({str(marker)!r}, 'a').write('x')\n"
    for name in ("sitecustomize.py", "usercustomize.py", "os.py", "sys.py", "mcp_launch.py", "planted_mod.py"):
        (project / name).write_text(planted)
    neutral = _neutral(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(project), PYTHONSTARTUP=str(project / "sitecustomize.py"))
    r = _launch(neutral, sys.executable, "-I", str(_tool(tmp_path)), cwd=project, env=env)
    assert r.returncode == 0, r.stderr
    assert not marker.exists()
    assert json.loads(r.stdout)["planted"] is False


def test_main_is_importable_and_callable_shape():
    from app import mcp_launch

    assert callable(mcp_launch.main)
    src = Path(LAUNCHER).read_text()
    # stdlib only
    imports = [ln.split()[1] for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert set(imports) <= {"os", "sys"}
