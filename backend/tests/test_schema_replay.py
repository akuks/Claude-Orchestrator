"""Phase 6c Task 1: replaying migration SQL into tables."""

from app.schema.replay import replay, split_statements


def run(*texts):
    return replay([(f"m/{i + 1}.sql", t) for i, t in enumerate(texts)])


def cols(t):
    return [c.name for c in t.columns]


def test_create_inline_pk_and_fk():
    s = run("CREATE TABLE users (id int PRIMARY KEY, name text NOT NULL);\n"
            "CREATE TABLE posts (id int PRIMARY KEY, user_id int REFERENCES users(id), body text);")
    u, p = s.tables["users"], s.tables["posts"]
    assert [(c.name, c.type, c.nullable, c.primary_key) for c in u.columns] == [
        ("id", "int", False, True), ("name", "text", False, False)]
    assert [(lk.columns, lk.to_table, lk.to_columns) for lk in p.links] == [(["user_id"], "users", ["id"])]
    assert u.defined_at == "m/1.sql:1" and p.defined_at == "m/1.sql:2"
    assert p.columns[1].nullable is True


def test_table_level_pk_and_fk():
    s = run("CREATE TABLE a (x int, y int, CONSTRAINT a_pk PRIMARY KEY (x, y),\n"
            " FOREIGN KEY (y) REFERENCES public.b (id), CONSTRAINT f2 FOREIGN KEY (x) REFERENCES c(id));")
    a = s.tables["a"]
    assert [c.primary_key for c in a.columns] == [True, True]
    assert [(lk.columns, lk.to_table, lk.to_columns) for lk in a.links] == [(["y"], "b", ["id"]), (["x"], "c", ["id"])]


def test_alter_columns():
    s = run("CREATE TABLE a (id int PRIMARY KEY, old text, gone int);",
            "ALTER TABLE a ADD COLUMN extra int NOT NULL;\nALTER TABLE a DROP COLUMN gone;\n"
            "ALTER TABLE a RENAME COLUMN old TO fresh;")
    a = s.tables["a"]
    assert cols(a) == ["id", "fresh", "extra"]
    assert a.columns[2].nullable is False and a.columns[2].defined_at == "m/2.sql:1"
    assert s.not_understood_count == 0


def test_alter_constraints():
    s = run("CREATE TABLE a (id int, b int);",
            "ALTER TABLE a ADD PRIMARY KEY (id);\n"
            "ALTER TABLE a ADD CONSTRAINT fk_b FOREIGN KEY (b) REFERENCES t(id);\n")
    assert s.tables["a"].columns[0].primary_key and len(s.tables["a"].links) == 1
    s = run("CREATE TABLE a (id int, b int);",
            "ALTER TABLE a ADD CONSTRAINT fk_b FOREIGN KEY (b) REFERENCES t(id);\n"
            "ALTER TABLE a ADD CONSTRAINT a_pkey PRIMARY KEY (id);",
            "ALTER TABLE a DROP CONSTRAINT fk_b;\nALTER TABLE a DROP CONSTRAINT a_pkey;")
    assert s.tables["a"].links == [] and not s.tables["a"].columns[0].primary_key


def test_drop_and_rename_table():
    s = run("CREATE TABLE a (id int); CREATE TABLE b (id int, a_id int REFERENCES a(id)); CREATE TABLE c (id int);",
            "ALTER TABLE a RENAME TO a2;\nDROP TABLE c;")
    assert sorted(s.tables) == ["a2", "b"]
    assert s.tables["b"].links[0].to_table == "a2"
    s = run("CREATE TABLE a (id int); CREATE TABLE d (id int);", "RENAME TABLE a TO z, d TO e;")
    assert sorted(s.tables) == ["e", "z"]
    s = run("CREATE TABLE a (id int);", "DROP TABLE IF EXISTS a, nope CASCADE;")
    assert s.tables == {}


def test_default_schemas():
    s = run("CREATE TABLE public.a (id int); CREATE TABLE dbo.b (id int); CREATE TABLE audit.c (id int);",
            "ALTER TABLE public.a ADD COLUMN x int; ALTER TABLE dbo.b ADD x int;")
    assert sorted(s.tables) == ["a", "audit.c", "b"]
    assert cols(s.tables["a"]) == ["id", "x"] and cols(s.tables["b"]) == ["id", "x"]


def test_dollar_bodies_dont_split():
    s = run("CREATE FUNCTION f() RETURNS trigger AS $body$ BEGIN NEW.x = 1; RETURN NEW; END; $body$ LANGUAGE plpgsql;\n"
            "CREATE TABLE after_fn (id int);")
    assert list(s.tables) == ["after_fn"] and s.not_understood_count == 0
    assert s.tables["after_fn"].defined_at == "m/1.sql:2"
    assert [x[1] for x in split_statements("select ';'; -- a;b\n/* ; */ select 2")] == [1, 2]


def test_mysql_backticks():
    s = run("CREATE TABLE `orders` (`id` int NOT NULL, `note` varchar(10), PRIMARY KEY (`id`)) ENGINE=InnoDB;")
    o = s.tables["orders"]
    assert cols(o) == ["id", "note"] and o.columns[0].primary_key and o.columns[1].nullable


def test_tsql_brackets():
    s = run("CREATE TABLE [dbo].[Orders] ([Id] int NOT NULL, [Note] nvarchar(10) NULL);\nGO\n"
            "ALTER TABLE [dbo].[Orders] ADD [Extra] int;")
    assert cols(s.tables["Orders"]) == ["Id", "Note", "Extra"] and s.not_understood_count == 0


def test_garbage_is_counted_with_line():
    s = run("CREATE TABLE a (id int);\n\nthis is not sql at all;\nCREATE TABLE b (id int);")
    assert s.not_understood_count == 1 and s.not_understood_samples == ["m/1.sql:3"]
    assert sorted(s.tables) == ["a", "b"]


def test_samples_capped_at_5():
    s = run(";\n".join(["nonsense words here"] * 8))
    assert s.not_understood_count == 8 and len(s.not_understood_samples) == 5


def test_do_block_marks_uncertain():
    s = run("CREATE TABLE a (id int);\nDO $$ BEGIN\n IF NOT EXISTS (select 1) THEN CREATE TABLE z (id int); END IF;\n"
            "ALTER TABLE a ADD COLUMN q int; END $$;")
    assert s.tables["z"].uncertain and s.tables["a"].uncertain and not s.tables["a"].columns[0].primary_key
    assert s.not_understood_count == 0


def test_other_statements_ignored():
    s = run("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE INDEX i ON a (id); INSERT INTO a VALUES (1);"
            " GRANT ALL ON a TO bob; SET search_path = x; COMMENT ON TABLE a IS 'x';")
    assert s.tables == {} and s.not_understood_count == 0


def test_if_not_exists_keeps_first():
    s = run("CREATE TABLE a (id int);", "CREATE TABLE IF NOT EXISTS a (id int, other int);")
    assert cols(s.tables["a"]) == ["id"]


def test_deadline():
    import time

    import pytest

    with pytest.raises(TimeoutError):
        replay([("m/1.sql", "CREATE TABLE a (id int);")], deadline=time.monotonic() - 1)


def test_hostile_go_input_is_fast():
    import time

    t0 = time.monotonic()
    out = split_statements(",\nGO\n" * 70_000)  # 0.4 MB
    assert time.monotonic() - t0 < 1.0
    s = run(",\nGO\n" * 70_000)
    assert s.not_understood_count == 1  # one over-long statement, never parsed
    assert [x[1] for x in split_statements("a;\nGO\nb;")] == [1, 3] and len(out) == 1


def test_scanner_checks_deadline():
    import time

    import pytest

    with pytest.raises(TimeoutError):
        split_statements("select 1;\n" * 200_000, deadline=time.monotonic() - 1)


def test_long_statement_not_parsed(monkeypatch):
    import app.schema.replay as r

    monkeypatch.setattr(r, "_parse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("parsed")))
    s = run("CREATE TABLE a (" + "x int," * 60_000 + "y int);")
    assert s.not_understood_count == 1 and s.tables == {}


def test_parse_checks_deadline_between_dialects():
    import time

    import pytest

    with pytest.raises(TimeoutError):
        replay([("m/1.sql", "this is not sql at all")], deadline=time.monotonic() + 0.0)


def test_mysql_change_and_modify():
    s = run("CREATE TABLE `a` (`x` int NOT NULL, `p` int PRIMARY KEY, `z` text);",
            "ALTER TABLE `a` CHANGE `x` `y` bigint NULL;\nALTER TABLE `a` MODIFY `z` varchar(5) NOT NULL;"
            "ALTER TABLE `a` MODIFY `p` bigint;")
    a = s.tables["a"]
    assert [(c.name, c.type, c.nullable, c.primary_key) for c in a.columns] == [
        ("y", "bigint", True, False), ("p", "bigint", False, True), ("z", "varchar(5)", False, False)]
    assert s.not_understood_count == 0


def test_pg_alter_column_type_and_null():
    s = run("CREATE TABLE a (x int, y int NOT NULL, z int PRIMARY KEY);",
            "ALTER TABLE a ALTER COLUMN x TYPE bigint, ALTER COLUMN x SET NOT NULL, ALTER COLUMN y DROP NOT NULL;\n"
            "ALTER TABLE a ALTER COLUMN z DROP NOT NULL;")
    a = s.tables["a"]
    assert [(c.name, c.type, c.nullable) for c in a.columns] == [
        ("x", "bigint", False), ("y", "int", True), ("z", "int", False)]
    assert s.not_understood_count == 0


def test_other_alter_actions_are_counted():
    s = run("CREATE TABLE a (x int);",
            "ALTER TABLE a ALTER COLUMN x SET DEFAULT 1;\n"
            "ALTER TABLE a ENABLE ROW LEVEL SECURITY;\n"
            "ALTER TABLE a OWNER TO bob;")
    assert s.not_understood_count == 3
    assert s.not_understood_samples == ["m/2.sql:1", "m/2.sql:2", "m/2.sql:3"]


def test_alter_on_unknown_table_is_counted():
    s = run("ALTER TABLE ghost ADD COLUMN x int;")
    assert s.not_understood_count == 1 and s.not_understood_samples == ["m/1.sql:1"]


def test_crafted_table_name_is_counted_not_a_crash():
    s = run("CREATE TABLE ok (id int);\nCREATE TABLE public. . a AS SELECT 1;\nCREATE TABLE ok2 (id int);")
    assert list(s.tables) == ["ok", "a", "ok2"] and s.tables["a"].uncertain  # read as a table of unknown columns
    assert s.not_understood_count == 0


def test_any_exception_in_a_statement_counts_that_statement(monkeypatch):
    import app.schema.replay as r

    def boom(self, *a, **k):
        raise AttributeError("x")

    monkeypatch.setattr(r._Replay, "create", boom)
    s = run("CREATE TABLE a (id int);\nALTER TABLE a ADD COLUMN x int;\nDROP TABLE a;")
    assert s.not_understood_count == 2 and s.not_understood_samples[0] == "m/1.sql:1"


def test_statement_counted_once():
    s = run("CREATE TABLE a (x int);", "ALTER TABLE a ALTER COLUMN x SET DEFAULT 1, ALTER COLUMN x SET DEFAULT 2;")
    assert s.not_understood_count == 1
    s = run("CREATE TABLE a (x int);\nALTER TABLE a ALTER COLUMN x SET DEFAULT 1; ALTER TABLE a OWNER TO b;")
    assert s.not_understood_count == 2
