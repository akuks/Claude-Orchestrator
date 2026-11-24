"""Phase 6c Task 1: finding and safely reading SQL migration files."""

import os
import shutil

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
    _w(tmp_path, "migrations/3.sql", "z")  # a small later file is not read either: no gaps in the middle
    c = files.collect(str(tmp_path))
    assert [f[0] for f in _names(c, "migrations")] == ["migrations/1.sql"]
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


def test_undo_u_is_case_sensitive(tmp_path):
    for n in ("U1__a.sql", "u2__b.sql"):
        _w(tmp_path, f"migrations/{n}")
    assert [f[0] for f in _names(files.collect(str(tmp_path)), "migrations")] == ["migrations/u2__b.sql"]


def test_folder_swapped_for_link_after_listing_is_not_followed(tmp_path, monkeypatch):
    proj, out = tmp_path / "proj", tmp_path / "outside"
    _w(proj, "migrations/sub/1.sql", "create table fine (id int);")
    _w(proj, "migrations/0.sql", "create table zero (id int);")
    _w(out, "1.sql", "create table LEAKED_SECRET (id int);")

    def swap():
        shutil.rmtree(proj / "migrations" / "sub")
        os.symlink(out, proj / "migrations" / "sub")

    monkeypatch.setattr(files, "_after_list", swap)
    c = files.collect(str(proj))
    got = [f for r in c.roots for f in r.files]
    assert [g[0] for g in got] == ["migrations/0.sql"]
    assert not any("LEAKED" in g[1] for g in got)


def test_root_swapped_for_link_after_listing_is_not_followed(tmp_path, monkeypatch):
    proj, out = tmp_path / "proj", tmp_path / "outside"
    _w(proj, "migrations/1.sql", "create table fine (id int);")
    _w(out, "1.sql", "create table LEAKED_SECRET (id int);")

    def swap():
        shutil.rmtree(proj / "migrations")
        os.symlink(out, proj / "migrations")

    monkeypatch.setattr(files, "_after_list", swap)
    assert [f for r in files.collect(str(proj)).roots for f in r.files] == []


def test_changed_size_is_rescanned_once(tmp_path, monkeypatch):
    p = _w(tmp_path, "migrations/1.sql", "create table a (id int);")
    calls = []

    def grow():
        calls.append(1)
        if len(calls) == 1:
            p.write_text("create table a (id int);\ncreate table b (id int);")

    monkeypatch.setattr(files, "_after_list", grow)
    c = files.collect(str(tmp_path))
    assert "table b" in _names(c, "migrations")[0][1] and c.limits == []


def test_still_changing_file_is_read_and_named(tmp_path, monkeypatch):
    p = _w(tmp_path, "migrations/1.sql", "x")
    n = []

    def grow():
        n.append(1)
        p.write_text("create table a (id int);" + " " * len(n))

    monkeypatch.setattr(files, "_after_list", grow)
    c = files.collect(str(tmp_path))
    assert len(_names(c, "migrations")) == 1
    assert "migrations/1.sql changed while it was being read." in c.limits


def test_project_folder_is_not_reopened_by_path(tmp_path, monkeypatch):
    proj, out = tmp_path / "proj", tmp_path / "outside"
    _w(proj, "migrations/1.sql", "create table fine (id int);")
    _w(out, "migrations/1.sql", "create table LEAKED (id int);")

    def swap():
        os.rename(proj, tmp_path / "moved")
        os.symlink(out, proj)

    monkeypatch.setattr(files, "_after_list", swap)
    c = files.collect(str(proj))
    assert [f[1] for f in _names(c, "migrations")] == ["create table fine (id int);"]


def test_total_limit_holds_when_files_grow_after_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_BYTES", 5000)
    paths = [_w(tmp_path, f"migrations/{i}.sql", "x") for i in range(40)]

    def grow():
        for p in paths:
            p.write_text("-- " + "y" * 3000)

    monkeypatch.setattr(files, "_after_list", lambda: None)
    sc = files.scan(str(tmp_path))
    grow()
    try:
        res = files.read(sc, accept_changed=True)
    finally:
        sc.close()
    total = sum(len(t) for r in res.roots for _, t in r.files)
    assert total <= 5000 and files.LIMIT_BYTES in res.limits
    # through collect (rescan + accept) the same
    for p in paths:
        p.write_text("x")
    monkeypatch.setattr(files, "_after_list", grow)
    c = files.collect(str(tmp_path))
    assert sum(len(t) for r in c.roots for _, t in r.files) <= 5000 and files.LIMIT_BYTES in c.limits
