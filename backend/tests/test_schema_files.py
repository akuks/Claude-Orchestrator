"""Phase 6c Task 1: finding and safely reading SQL migration files."""

import os

from app.schema import files


def _w(base, rel, text="select 1;"):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _names(c, root):
    return [r for r in c.roots if r.root == root][0].files


def test_numeric_aware_order(tmp_path):
    for n in ("10_x.sql", "2_x.sql", "1_x.sql"):
        _w(tmp_path, f"migrations/{n}")
    c = files.collect(str(tmp_path))
    assert [f[0] for f in _names(c, "migrations")] == ["migrations/1_x.sql", "migrations/2_x.sql", "migrations/10_x.sql"]


def test_down_and_undo_skipped(tmp_path):
    for n in ("1_a.sql", "1_a.down.sql", "2_b_down.sql", "3_c.undo.sql", "4_d_undo.sql", "U5__e.sql", "V5__e.sql", "notes.txt"):
        _w(tmp_path, f"db/migrations/{n}")
    c = files.collect(str(tmp_path))
    assert [f[0].split("/")[-1] for f in _names(c, "db/migrations")] == ["1_a.sql", "V5__e.sql"]


def test_prisma_layout(tmp_path):
    _w(tmp_path, "prisma/migrations/20240101_init/migration.sql", "create table a (id int);")
    _w(tmp_path, "prisma/migrations/20240101_init/other.sql")
    _w(tmp_path, "prisma/migrations/migration_lock.toml")
    c = files.collect(str(tmp_path))
    assert [f[0] for f in _names(c, "prisma/migrations")] == ["prisma/migrations/20240101_init/migration.sql"]


def test_four_roots_kept_separate(tmp_path):
    for r in files.ROOTS:
        _w(tmp_path, f"{r}/1_a.sql" if not r.startswith("prisma") else f"{r}/1_a/migration.sql")
    c = files.collect(str(tmp_path))
    assert [r.root for r in c.roots] == list(files.ROOTS)
    assert all(len(r.files) == 1 for r in c.roots)


def test_nothing_found(tmp_path):
    assert files.collect(str(tmp_path)).roots == []
    assert files.collect(str(tmp_path / "gone")).roots == []


def test_file_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILES", 3)
    for i in range(5):
        _w(tmp_path, f"migrations/{i}.sql")
    c = files.collect(str(tmp_path))
    assert len(_names(c, "migrations")) == 3 and files.LIMIT_FILES in c.limits
    assert "5,000" in files.LIMIT_FILES


def test_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_BYTES", 100)
    _w(tmp_path, "migrations/1.sql", "x" * 60)
    _w(tmp_path, "migrations/2.sql", "y" * 60)
    c = files.collect(str(tmp_path))
    assert len(_names(c, "migrations")) == 1 and files.LIMIT_BYTES in c.limits
    assert "5 MB" in files.LIMIT_BYTES


def test_links_skipped(tmp_path):
    out = tmp_path / "outside"
    proj = tmp_path / "proj"
    _w(out, "secret.sql", "create table leaked (id int);")
    _w(proj, "migrations/1_a.sql")
    os.symlink(out / "secret.sql", proj / "migrations/2_link.sql")
    os.symlink(out, proj / "migrations/sub")
    _w(out, "db/1.sql")
    os.symlink(out, proj / "supabase")  # supabase itself a link to a folder holding migrations/
    (out / "migrations").mkdir(exist_ok=True)
    _w(out, "migrations/1.sql", "create table leaked2 (id int);")
    os.symlink(out / "migrations", proj / "prisma_link")
    c = files.collect(str(proj))
    got = [f for r in c.roots for f in r.files]
    assert [g[0] for g in got] == ["migrations/1_a.sql"]
    assert all("leaked" not in g[1] for g in got)


def test_linked_root_skipped(tmp_path):
    out = tmp_path / "outside"
    proj = tmp_path / "proj"
    _w(out, "1.sql", "create table leaked (id int);")
    proj.mkdir()
    os.symlink(out, proj / "migrations")
    (proj / "db").mkdir()
    os.symlink(out.parent, proj / "db" / "migrations")  # a link to the parent holding the project too
    assert files.collect(str(proj)).roots == []


def test_linked_parent_of_root_skipped(tmp_path):
    out = tmp_path / "outside"
    proj = tmp_path / "proj"
    _w(out, "migrations/1.sql", "create table leaked (id int);")
    proj.mkdir()
    os.symlink(out, proj / "supabase")
    assert files.collect(str(proj)).roots == []


def test_signature_changes_with_file(tmp_path):
    p = _w(tmp_path, "migrations/1.sql", "a")
    s1 = files.collect(str(tmp_path)).signature
    p.write_text("abc")
    assert files.collect(str(tmp_path)).signature != s1
