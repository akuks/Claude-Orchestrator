"""Phase 6c: has the project a user interface? (recommend.detect)"""

import json
import os

import pytest

from app.recommend.detect import detect, valid_brand


def _pkg(d, deps, key="dependencies"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps({key: {n: "1" for n in deps}}))


@pytest.mark.parametrize("dep", ["react", "next", "vue", "nuxt", "svelte", "@sveltejs/kit", "@angular/core",
                                 "solid-js", "preact", "astro", "vite"])
def test_each_dependency(tmp_path, dep):
    _pkg(tmp_path, [dep], key="devDependencies")
    r = detect(str(tmp_path))
    assert r.has_ui and r.react == (dep in ("react", "next", "preact"))


def test_plain_dependency_is_not_ui(tmp_path):
    _pkg(tmp_path, ["express", "lodash"])
    assert not detect(str(tmp_path)).has_ui


@pytest.mark.parametrize("ending", [".html", ".tsx", ".jsx", ".vue", ".svelte"])
@pytest.mark.parametrize("where", ["", "src", "frontend", "frontend/src"])
def test_file_endings_in_each_place(tmp_path, ending, where):
    d = tmp_path / where if where else tmp_path
    d.mkdir(parents=True, exist_ok=True)
    (d / f"page{ending}").write_text("x")
    r = detect(str(tmp_path))
    assert r.has_ui and not r.react


def test_deeper_files_do_not_count(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.tsx").write_text("x")
    assert not detect(str(tmp_path)).has_ui


def test_frontend_package_json(tmp_path):
    _pkg(tmp_path / "frontend", ["react"])
    r = detect(str(tmp_path))
    assert r.has_ui and r.react


def test_big_or_invalid_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "1"}, "pad": "' + "x" * (1024 * 1024) + '"}')
    assert not detect(str(tmp_path)).has_ui
    (tmp_path / "package.json").write_text("{not json")
    assert not detect(str(tmp_path)).has_ui
    (tmp_path / "package.json").write_text("[1, 2]")
    assert not detect(str(tmp_path)).has_ui


def test_linked_package_json_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _pkg(outside, ["react"])
    proj = tmp_path / "proj"
    proj.mkdir()
    os.symlink(outside / "package.json", proj / "package.json")
    assert not detect(str(proj)).has_ui


def test_linked_ui_file_and_linked_folder_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.tsx").write_text("x")
    proj = tmp_path / "proj"
    proj.mkdir()
    os.symlink(outside / "a.tsx", proj / "a.tsx")
    os.symlink(outside, proj / "src")
    assert not detect(str(proj)).has_ui


def test_missing_folder_and_none(tmp_path):
    for d in (None, "", str(tmp_path / "gone")):
        r = detect(d)
        assert (r.has_ui, r.react, r.brand_skill_found) == (False, False, False)


def test_brand_skill_found(tmp_path):
    assert not detect(str(tmp_path)).brand_skill_found
    for rel in (".claude/skills/brand/SKILL.md", "skills/brand/SKILL.md"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True)
        p.write_text("x")
        assert detect(str(tmp_path)).brand_skill_found
        p.unlink()


def test_valid_brand():
    assert valid_brand("acme:brand-1.x_y")
    for bad in ("a b", "a\nb", 'a"b', "", "x" * 121, None, 5):
        assert not valid_brand(bad)
