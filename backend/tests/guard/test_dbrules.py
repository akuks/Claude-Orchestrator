"""co-guard database rules (dbrules.py): per-function tables.

Cases ported from jspark-forge tests/blocklist.test.js ('sql-destroy',
'db-write') and tests/dbguard.test.js, plus the Phase 2 spec additions.
"""

import ast
import pathlib

import pytest

from app.guard import dbrules as db
from app.guard.dbrules import Finding


# ---------------------------------------------------------------------------
# sql_strip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,keeps,drops", [
    ("select 'drop table x'", "select", "drop"),
    ("select 'it''s; drop table x'", "select", "drop"),
    ('select "drop" from t', "from t", "drop"),
    ("select `delete` from t", "from t", "delete"),
    ("select 1 -- drop table x", "select 1", "drop"),
    ("select 1 /* truncate t */ from t", "from t", "truncate"),
    ("select $$drop table x$$", "select", "drop"),
    ("select $fn$ delete from t $fn$ as body", "as body", "delete"),
    ("/*!50000 INSERT INTO t VALUES (1) */", "INSERT INTO t", "/*!"),
    ("select 'unterminated drop table", "select", "drop"),
    ("select 1 /* never closed drop table x", "select 1", "drop"),
])
def test_sql_strip(text, keeps, drops):
    out = db.sql_strip(text)
    assert keeps in out
    assert drops not in out


def test_sql_strip_keeps_semicolons_outside_strings():
    assert db.sql_strip("select ';'; insert into t values (1)").count(";") == 1


def test_sql_strip_dollar_variable_is_not_a_quote():
    # "$1" is no dollar quote (a tag cannot start with a digit): the text after it stays
    assert "insert" in db.sql_strip("select $1; insert into t values (2)")


# ---------------------------------------------------------------------------
# sql_destructive
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "DROP TABLE users", "drop database shop", "DROP SCHEMA public CASCADE;", "TRUNCATE orders",
    "truncate table orders", 'TRUNCATE "orders"', "delete from users;", "select 1; DELETE FROM logs",
    "DELETE FROM sessions", "drop   \n table x", "DELETE FROM t; SELECT 1 WHERE 1=1",
])
def test_sql_destructive_positive(text):
    assert db.sql_destructive(text)


@pytest.mark.parametrize("text", [
    "DELETE FROM users WHERE id = 3", "SELECT * FROM users", "drop index idx on t", "create table t(id int)",
    "select 'drop table x' -- truncate", "select 1 /* delete from t */", "select $$DROP TABLE x$$",
    "select 'delete from t'", "update t set a = 1", "drop view v", "",
])
def test_sql_destructive_negative(text):
    assert not db.sql_destructive(text, "pg")


# ---------------------------------------------------------------------------
# sql_writes (forge stmtWrites, READ_PRAGMA, META_WRITE)
# ---------------------------------------------------------------------------
SQL_WRITES = [
    "INSERT INTO users VALUES (1)", "UPDATE users SET admin = true WHERE id = 1",
    "update users as u set name = 'x' where u.id = 1", "insert t values (1)", "DELETE users WHERE id = 1",
    "ALTER TABLE orders ADD COLUMN note text", "create table x (id int)", "SELECT * INTO backup FROM users",
    "vacuum full", "refresh materialized view sales_mv",
    "WITH d AS (DELETE FROM t WHERE id = 1 RETURNING *) SELECT * FROM d",
    "EXPLAIN ANALYZE UPDATE t SET a = 1 WHERE id = 2", "select 1; insert into t values (2)",
    "/*!50000 INSERT INTO t VALUES (1) */", "$SQL", "EXEC sp_purge",
    "select setval('s', 1)", "select set_config('a.b', 'c', false)", "select pg_terminate_backend(42)",
    "select * from dblink('x', 'y')", "select cron.schedule('x')", "select nextval('s')",
    "\\gexec", "select 1 \\gexec", "\\i migrate.sql", "\\ir x.sql", "\\! rm -rf /", "\\include x.sql",
    ".read x.sql", ".import data.csv t", ".shell ls", "\\copy t from 'x.csv'", "\\copy t from stdin",
    "pragma journal_mode = delete", "pragma writable_schema = 1", "set global max_connections = 10",
    "start slave", "copy t from stdin", "copy t to '/tmp/x'", "grant all on t to bob", "call proc()",
    "merge into t using s on (1=1)", "TABLE t; drop table x", "lock table t",
]
SQL_READS = [
    "SELECT * FROM users", "select 1; select 2", "WITH x AS (SELECT 1) SELECT * FROM x",
    "EXPLAIN SELECT * FROM users", "EXPLAIN UPDATE t SET a = 1", "EXPLAIN ANALYZE SELECT * FROM t",
    "SHOW TABLES", "DESCRIBE orders", "desc t", "show create table t", "\\dt", "\\d users",
    "select * from notes where note = 'call me (now); update x set a = 1'", "-- create table x\nselect 1",
    "/* drop it later */ select 1", "select * from t for update", "select * from t for no key update",
    "values (1), (2)", "TABLE users", "\\copy t to stdout", "copy t to stdout", "pragma table_info(t)",
    "pragma main.index_list(t)", "pragma journal_mode", "begin; select 1; commit", "start transaction",
    "set search_path = app", "use shop", "select $$insert into t$$", "", "  ;  ", "(select 1)",
]


@pytest.mark.parametrize("text", SQL_WRITES)
def test_sql_writes_positive(text):
    assert db.sql_writes(text)


@pytest.mark.parametrize("text", SQL_READS)
def test_sql_writes_negative(text):
    assert not db.sql_writes(text, "pg")


# ---------------------------------------------------------------------------
# redis / mongo
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("words,writes", [
    (["FLUSHALL"], True), (["set", "k", "v"], True), (["del", "k"], True), (["EXPIRE", "k", "1"], True),
    (["config", "set", "x", "y"], True), (["GET", "k"], False), (["hgetall", "k"], False),
    (["config", "get", "maxmemory"], False), (["CLIENT", "LIST"], False), ([], False), (["", " "], False),
])
def test_redis_writes(words, writes):
    assert db.redis_writes(words) is writes


@pytest.mark.parametrize("text,writes", [
    ("db.users.insertOne({a: 1})", True), ("db.users.deleteMany({a: 1})", True),
    ("db.users.findAndModify({query: {a: 1}})", True), ("db.users.drop()", True),
    ("db.x.aggregate([{$out: 'copy'}])", True), ("load('x.js')", True), ("db.runCommand({x: 1})", True),
    ("db.users.find({a: 1})", False), ("db.users.countDocuments()", False),
    ("db.x.aggregate([{$match: {}}])", False),
])
def test_mongo_writes(text, writes):
    assert db.mongo_writes(text) is writes


# ---------------------------------------------------------------------------
# hosts and dev patterns
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("host", [
    "localhost", "LOCALHOST", "127.0.0.1", "127.0.0.2", "::1", "[::1]:5432", "0.0.0.0", "app.localhost",
    "/var/run/postgresql", "'localhost'", "", ".", "(local)", "(localdb)\\x", "tcp:localhost",
    "localhost:5432",
])
def test_host_local_positive(host):
    assert db.host_local(host)


@pytest.mark.parametrize("host", [
    "prod.example.com", "10.0.0.5", "127.evil.com", "/cloudsql/proj:us-central1:main", "$DB_HOST",
    "${PGHOST}", "`h`", "%H%", "localhost.evil.com", "db",
])
def test_host_local_negative(host):
    assert not db.host_local(host)


@pytest.mark.parametrize("patterns,host,dev", [
    (["host:*.dev.internal"], "a.dev.internal", True),
    (["host:*.dev.internal"], "A.DEV.INTERNAL:5432", True),
    (["host:*.dev.internal"], "dev.internal", False),
    (["host:*.dev.internal"], "prod.internal", False),
    (["host: *.db.example.com"], "dev.db.example.com", True),
    (["dev.internal"], "dev.internal", True),
    (["dev.internal"], "x.dev.internal", False),
    (["mcp:prod.example.com"], "prod.example.com", False),
    (["host:*.dev.internal"], "$DEV_HOST", False),
    ([], "a.dev.internal", False),
    ("host:*", "a", False),  # not a list: nothing is dev
    ([None, 3, "  ", "host:*.dev.internal"], "a.dev.internal", True),
])
def test_dev_host(patterns, host, dev):
    assert db.dev_host(patterns, host) is dev


def test_dev_host_glob_is_linear():
    import time
    t = time.monotonic()
    assert not db.dev_host(["host:" + "*a" * 200 + "b"], "a" * 250)
    assert time.monotonic() - t < 0.5


@pytest.mark.parametrize("patterns,server,dev", [
    (["claude_ai_Supabase"], "claude_ai_Supabase", True),
    (["mcp:claude_ai_Supabase"], "claude_ai_supabase", True),
    (["supabase"], "claude_ai_Supabase", False),
    (["mcp:pg-dev*"], "pg-dev-eu", True),
    (["mcp:pg-dev*"], "my-pg-dev", False),
    (["mcp:local_*"], "local_postgres", True),
    (["mcp:local_*"], "postgres_local", False),
    (["dev"], "pg_dev", False),
    (["pg_dev"], "pg_dev", True),
    (["host:postgres"], "postgres", False),
])
def test_dev_server(patterns, server, dev):
    assert db.dev_server(patterns, server) is dev


@pytest.mark.parametrize("p,ok", [
    ("host:*.dev.internal", True), ("mcp:pg-dev*", True), ("mcp:claude_ai_Supabase", True), ("staging-db", True),
    ("HOST: *.x", True), ("*.dev.example.com", False), ("mcp:*_dev", False), ("host:", False), ("mcp: ", False),
    ("", False), ("  ", False), ("x" * 501, False), (None, False), (7, False),
])
def test_check_dev_pattern(p, ok):
    err = db.check_dev_pattern(p)
    assert (err is None) is ok
    if not ok:
        assert isinstance(err, str) and err


def test_check_dev_pattern_messages():
    assert "host:" in db.check_dev_pattern("*.dev.example.com")
    assert "prefix ending in *" in db.check_dev_pattern("mcp:*_dev")


# ---------------------------------------------------------------------------
# client_finding
# ---------------------------------------------------------------------------
def cf(argv, **kw):
    return db.client_finding(argv[0], argv[1:], **kw)


@pytest.mark.parametrize("argv,kw,want", [
    # sql-destroy ignores the target
    (["psql", "-c", "DROP TABLE users"], {}, Finding(True, False)),
    (["mysql", "-e", "drop database shop"], {}, Finding(True, False)),
    (["sqlite3", "app.db", "DELETE FROM sessions"], {}, Finding(True, False)),
    (["psql", "-h", "localhost", "-c", "DELETE FROM t"], {}, Finding(True, False)),
    (["psql", "mydb"], {"stdin": "literal", "feed": "DROP SCHEMA public CASCADE;"}, Finding(True, False)),
    (["psql", "-h", "db.example.com", "-c", "TRUNCATE orders"], {}, Finding(True, True)),
    (["duckdb", "x.db", "-c", "drop table t"], {}, Finding(True, False)),
    (["clickhouse-client", "--query=TRUNCATE TABLE t"], {}, Finding(True, False)),
    (["sqlcmd", "-Q", "DROP TABLE t"], {}, Finding(True, False)),
    (["cockroach", "sql", "--execute", "drop database d"], {}, Finding(True, False)),
    (["mysql", "-etruncate t"], {}, Finding(True, False)),
    # not destructive
    (["psql", "-c", "select 'drop table x' -- truncate"], {}, Finding(False, False)),
    (["psql", "-c", "DELETE FROM users WHERE id = 3"], {}, Finding(False, False)),
    (["psql", "-f", "drop_everything.sql"], {}, Finding(False, False)),
    (["mongosh", "--eval", "db.dropDatabase(); drop table x"], {}, Finding(False, False)),
    (["redis-cli", "FLUSHALL"], {}, Finding(False, False)),
])
def test_client_finding_destroy(argv, kw, want):
    assert cf(argv, **kw) == want


DB_WRITE_POS = [
    (["psql", "-h", "db.prod.example.com", "-c", "INSERT INTO users VALUES (1)"], {}),
    (["psql", "postgres://app:pw@db.example.com:5432/app", "-c", "UPDATE users SET a = 1 WHERE id = 1"], {}),
    (["mysql", "-h", "10.0.0.5", "-u", "root", "-e", "ALTER TABLE orders ADD COLUMN note text"], {}),
    (["mysql", "--host=shop.rds.amazonaws.com", "-e", "insert into t values (1)"], {}),
    (["mysql", "-hprod.example.com", "-e", "insert into t values (1)"], {}),
    (["psql", "-c", "delete from sessions where id = 3"], {"prefix_env": {"PGHOST": "prod.example.com"}}),
    (["psql", "-c", "insert into t values (1)"], {"line_env": {"PGHOST": "prod.example.com"}}),
    (["psql", "-c", "insert into t values (1)"], {"prefix_env": {"PGSERVICE": "prod"}}),
    (["psql", "-c", "insert into t values (1)"], {"prefix_env": {"PGHOST": None}}),
    (["psql", "-c", "insert into t values (1)"], {"prefix_env": {"DATABASE_URL": "postgres://db.x/app"}}),
    (["psql", "-h", "prod.example.com", "app"], {"stdin": "literal", "feed": "INSERT INTO t VALUES (1);"}),
    (["psql", "-h", "$DB_HOST", "-c", "create table x (id int)"], {}),
    (["psql", "$DATABASE_URL", "-c", "update t set a = 1 where id = 2"], {}),
    (["psql", "service=prod", "-c", "insert into t values (1)"], {}),
    (["mysql", "--login-path=prod", "-e", "insert into t values (1)"], {}),
    (["psql", "-h", "127.evil.com", "-c", "insert into t values (1)"], {}),
    (["psql", "-h", "/cloudsql/proj:us-central1:main", "-c", "insert into t values (1)"], {}),
    (["psql", "-h", "prod.example.com", "-f", "migrate.sql"], {}),
    (["psql", "-h", "prod.example.com", "--file=migrate.sql", "app"], {}),
    (["psql", "-h", "prod.example.com", "app"], {"stdin": "unknown"}),
    (["psql", "-h", "prod.example.com", "app"], {"stdin": "literal", "feed": None}),
    (["psql", "-h", "prod.example.com", "-c", "$SQL"], {}),
    (["sqlcmd", "-S", "prod-sql.database.windows.net", "-Q", "UPDATE t SET a = 1 WHERE id = 2"], {}),
    (["sqlcmd", "-S", "prod.example.com", "-Q", "EXEC sp_purge"], {}),
    (["mongosh", "mongodb+srv://cluster0.mongodb.net/app", "--eval", "db.users.insertOne({a: 1})"], {}),
    (["mongosh", "prod.example.com:27017/app", "--eval", "db.users.deleteMany({a: 1})"], {}),
    (["mongosh", "--host", "prod.example.com", "app", "migrate.js"], {}),
    (["pg_restore", "-h", "prod.example.com", "-d", "app", "dump.backup"], {}),
    (["redis-cli", "-h", "prod.example.com", "FLUSHALL"], {}),
    (["redis-cli", "-h", "prod.example.com", "set", "k", "v"], {}),
    (["redis-cli", "-h", "prod.example.com"], {"stdin": "literal", "feed": "FLUSHALL"}),
    (["valkey-cli", "-u", "redis://cache.example.com", "DEL", "k"], {}),
    (["clickhouse-client", "-h", "ch.example.com", "--query", "INSERT INTO t VALUES (1)"], {}),
    (["cockroach", "sql", "--url", "postgresql://root@crdb.example.com:26257", "-e", "insert into t values (1)"], {}),
    (["duckdb", "md:my_db", "-c", "insert into t values (1)"], {}),
    (["mysqlsh", "root@prod.example.com", "-e", "insert into t values (1)"], {}),
    (["psql", "-h", "localhost", "-c", "insert into t values (1)"], {"remote": True}),
    (["psql", "-h", "a.dev.internal,prod.internal", "-c", "insert into t values (1)"],
     {"patterns": ["host:*.dev.internal"]}),
    (["psql", "-h", "prod.internal", "-c", "insert into t values (1)"],
     {"prefix_env": {"PGHOST": "a.dev.internal"}, "patterns": ["host:*.dev.internal"]}),
]
DB_WRITE_NEG = [
    (["psql", "-h", "prod.example.com", "-c", "SELECT * FROM users"], {}),
    (["psql", "-h", "prod.example.com", "-c", "select 1; select 2"], {}),
    (["psql", "-h", "prod.example.com", "-c", "\\dt"], {}),
    (["psql", "-h", "prod.example.com", "-c", "\\copy t to stdout"], {}),
    (["psql", "-h", "prod.example.com", "app"], {"stdin": "literal", "feed": "select 1"}),
    (["mysql", "-h", "10.0.0.5", "-e", "SHOW TABLES"], {}),
    (["psql", "-h", "localhost", "-c", "INSERT INTO t VALUES (1)"], {}),
    (["psql", "-h", "127.0.0.1", "-c", "UPDATE t SET a = 1 WHERE id = 2"], {}),
    (["psql", "-h", "::1", "-c", "insert into t values (1)"], {}),
    (["psql", "-h", "/var/run/postgresql", "-c", "insert into t values (1)"], {}),
    (["psql", "mydb", "-c", "insert into t values (1)"], {}),
    (["psql", "-c", "insert into t values (1)"], {"line_env": {"PGHOST": "localhost"}}),
    (["mysql", "-e", "update t set a = 1 where id = 2"], {}),
    (["sqlite3", "app.db", "INSERT INTO t VALUES (1)"], {}),
    (["sqlite3", "app.db", "INSERT INTO t VALUES (1)"], {"prefix_env": {"DATABASE_URL": "postgres://prod/x"}}),
    (["sqlite3", "app.db"], {"stdin": "unknown"}),
    (["mongosh", "mongodb://localhost:27017/app", "--eval", "db.users.insertOne({a: 1})"], {}),
    (["mongosh", "mongodb+srv://cluster0.mongodb.net/app", "--eval", "db.users.find()"], {}),
    (["mongosh", "app", "--eval", "db.users.deleteMany({a: 1})"], {}),
    (["redis-cli", "-h", "prod.example.com", "GET", "k"], {}),
    (["redis-cli", "-h", "localhost", "FLUSHALL"], {}),
    (["pg_restore", "-l", "dump.backup"], {}),
    (["pg_restore", "-d", "app", "dump.backup"], {}),
    (["pg_restore", "-h", "prod.example.com", "-f", "out.sql", "dump.backup"], {}),
    (["psql", "$PROD_DB", "-c", "select 1"], {}),
    (["psql", "-h", "dev.db.example.com", "-c", "insert into t values (1)"],
     {"patterns": ["host:*.db.example.com"]}),
    (["psql", "-h", "dev.internal", "-c", "insert into t values (1)"], {"patterns": ["dev.internal"]}),
    (["psql", "-h", "b.dev.internal", "-c", "insert into t values (1)"],
     {"prefix_env": {"PGHOST": "a.dev.internal"}, "patterns": ["host:*.dev.internal"]}),
    (["psql", "-h", "prod.example.com"], {}),  # interactive, nothing to read
    (["notaclient", "-h", "prod", "-c", "insert"], {}),
]


@pytest.mark.parametrize("argv,kw", DB_WRITE_POS)
def test_client_finding_write_positive(argv, kw):
    assert cf(argv, **kw).write


@pytest.mark.parametrize("argv,kw", DB_WRITE_NEG)
def test_client_finding_write_negative(argv, kw):
    assert not cf(argv, **kw).write


def test_client_finding_env_names_are_filtered():
    assert not cf(["psql", "-c", "insert into t values (1)"], prefix_env={"OTHER": "prod.example.com"}).write
    assert not cf(["psql", "-c", "insert into t values (1)"], prefix_env="junk", line_env=None).write


# ---------------------------------------------------------------------------
# cli_write (prisma / supabase)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["prisma", "db", "push"], ["prisma", "db", "execute", "--file", "x.sql"], ["prisma", "migrate", "deploy"],
    ["prisma", "migrate", "resolve", "--applied", "x"], ["prisma", "migrate", "reset"],
    ["prisma", "--schema", "s.prisma", "db", "push"],
    ["npx", "prisma", "db", "push", "--accept-data-loss"], ["npx", "prisma", "migrate", "deploy"],
    ["pnpm", "dlx", "prisma", "migrate", "reset", "--force"], ["bunx", "prisma", "db", "push"],
    ["pnpx", "prisma", "db", "push"], ["yarn", "prisma", "db", "push"], ["npm", "exec", "prisma", "db", "push"],
    ["bun", "x", "prisma", "db", "push"], ["npx", "-y", "prisma@5.2.0", "db", "push"],
    ["./node_modules/.bin/prisma", "db", "push"],
    ["supabase", "db", "push"], ["supabase", "migration", "repair", "--status", "applied", "1"],
    ["supabase", "db", "reset", "--linked"], ["supabase", "migration", "up", "--linked"],
    ["supabase", "db", "reset", "--db-url", "postgres://u@db.example.com/x"],
    ["supabase", "db", "push", "--db-url=postgres://u@db.example.com/x"],
    ["npx", "supabase", "db", "push"],
])
def test_cli_write_positive(argv):
    assert db.cli_write(argv[0], argv[1:])


@pytest.mark.parametrize("argv,patterns", [
    (["prisma", "migrate", "dev"], []), (["npx", "prisma", "generate"], []), (["prisma", "studio"], []),
    (["prisma", "db", "pull"], []), (["supabase", "db", "reset"], []), (["supabase", "db", "push", "--local"], []),
    (["supabase", "db", "diff"], []), (["supabase", "start"], []),
    (["supabase", "db", "push", "--db-url", "postgres://u@localhost:54322/x"], []),
    (["supabase", "db", "push", "--db-url", "postgres://u@db.dev.internal/x"], ["host:*.dev.internal"]),
    (["npm", "install", "prisma"], []), (["npx", "tsc"], []), (["git", "push"], []),
])
def test_cli_write_negative(argv, patterns):
    assert not db.cli_write(argv[0], argv[1:], patterns)


# ---------------------------------------------------------------------------
# mcp_finding (forge tests/dbguard.test.js)
# ---------------------------------------------------------------------------
SUPA = "mcp__claude_ai_Supabase__"
MCP_WRITES = [
    (f"{SUPA}apply_migration", {"project_id": "p", "name": "add_x", "query": "create table x (id int)"}),
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "UPDATE users SET admin = true WHERE id = 1"}),
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "select 1; insert into t values (1)"}),
    (f"{SUPA}merge_branch", {"branch_id": "b"}),
    (f"{SUPA}reset_branch", {"branch_id": "b"}),
    (f"{SUPA}restore_project", {"project_id": "p"}),
    ("mcp__postgres__query", {"sql": "INSERT INTO t VALUES (1)"}),
    ("mcp__plugin_db_mysql__execute", {"query": "ALTER TABLE t ADD c int"}),
    ("mcp__mongodb__insert-many", {"database": "app", "collection": "users", "documents": [{"a": 1}]}),
    (f"{SUPA}execute_sql", {"query": "SELECT * INTO backup FROM users"}),
    (f"{SUPA}execute_sql", {"query": "vacuum full"}),
    (f"{SUPA}execute_sql", {"query": "WITH d AS (DELETE FROM t WHERE id = 1 RETURNING *) SELECT * FROM d"}),
    (f"{SUPA}execute_sql", {"query": "select set_config('a.b', 'c', false); select setval('s', 1)"}),
    ("mcp__mssql__execute_query", {"query": "EXEC sp_purge"}),
    ("mcp__dynamodb__put_item", {"TableName": "t", "Item": {"id": {"S": "1"}}}),
    ("mcp__dynamodb__update_item", {"TableName": "t", "Key": {"id": {"S": "1"}}}),
    ("mcp__dynamodb__delete_item", {"TableName": "t", "Key": {"id": {"S": "1"}}}),
    ("mcp__dynamodb__execute_statement", {"Statement": "UPDATE t SET a = 1 WHERE id = '1'"}),
    ("mcp__redis__set", {"key": "k", "value": "v"}),
    ("mcp__redis__del", {"key": "k"}),
    ("mcp__redis__flushall", {}),
    ("mcp__redis__execute_command", {"command": "FLUSHALL"}),
    ("mcp__mongodb__updateOne", {"collection": "users", "filter": {"a": 1}, "update": {"$set": {"b": 2}}}),
    ("mcp__mongodb__findOneAndUpdate", {"collection": "users", "filter": {"a": 1}, "update": {"$set": {"b": 2}}}),
    ("mcp__mongodb__find_and_modify", {"collection": "users", "query": {"a": 1}}),
    ("mcp__mongodb__delete-many", {"collection": "users", "filter": {"a": 1}}),
    ("mcp__mongodb__aggregate", {"collection": "users", "pipeline": [{"$match": {}}, {"$out": "copy"}]}),
    ("mcp__postgres__create_table", {"name": "t"}),
    ("mcp__postgres__run_sql", {}),  # runs something we cannot see
    ("mcp__notes_srv__apply_migration", {"sql": "select 1"}),  # apply_migration on any server
]
MCP_READS = [
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "SELECT * FROM users"}),
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "WITH x AS (SELECT 1 a) SELECT a FROM x"}),
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "EXPLAIN SELECT * FROM users"}),
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "show server_version"}),
    (f"{SUPA}execute_sql", {"project_id": "p", "query": "select 1; select 2"}),
    (f"{SUPA}list_tables", {"project_id": "p"}),
    (f"{SUPA}list_migrations", {"project_id": "p"}),
    (f"{SUPA}get_project", {"id": "p"}),
    ("mcp__postgres__query", {"sql": "describe users"}),
    ("mcp__mongodb__find", {"database": "app", "collection": "users"}),
    ("mcp__github__create_issue", {"title": "x", "body": "we should insert into the users table"}),
    ("mcp__slack__post_message", {"text": "ship the plan, then update users set a = 1"}),
    (f"{SUPA}execute_sql", {"query": "show create table t"}),
    (f"{SUPA}execute_sql", {"query": "select * from notes where note = 'call me (now); update x set a = 1'"}),
    (f"{SUPA}execute_sql", {"query": "-- create table x\nselect 1"}),
    (f"{SUPA}execute_sql", {"query": "/* insert later */ SELECT count(*) FROM users"}),
    (f"{SUPA}execute_sql", {"query": "select * from t for update"}),
    ("mcp__dynamodb__get_item", {"TableName": "t", "Key": {"id": {"S": "1"}}}),
    ("mcp__dynamodb__query", {"TableName": "t", "KeyConditionExpression": "id = :v"}),
    ("mcp__dynamodb__scan", {"TableName": "t"}),
    ("mcp__dynamodb__execute_statement", {"Statement": "SELECT * FROM t WHERE id = '1'"}),
    ("mcp__redis__get", {"key": "k"}),
    ("mcp__redis__hgetall", {"key": "k"}),
    ("mcp__redis__execute_command", {"command": "GET k"}),
    ("mcp__mongodb__aggregate", {"collection": "users", "pipeline": [{"$match": {"a": 1}}]}),
    ("mcp__mongodb__count", {"collection": "users"}),
    ("not_an_mcp_tool", {"sql": "insert into t values (1)"}),
]


@pytest.mark.parametrize("name,inp", MCP_WRITES)
def test_mcp_finding_write(name, inp):
    assert db.mcp_finding(name, inp, {}, []).write


@pytest.mark.parametrize("name,inp", MCP_READS)
def test_mcp_finding_read(name, inp):
    assert db.mcp_finding(name, inp, {}, []) == Finding(False, False)


@pytest.mark.parametrize("name,inp,servers,patterns,want", [
    ("mcp__postgres__query", {"sql": "DROP TABLE x"}, {}, [], Finding(True, True)),  # evaluate drops the write
    ("mcp__db__execute", {"query": "delete from audit"}, {}, [], Finding(True, True)),
    # destroy runs in every environment, also for dev servers and dev patterns
    ("mcp__postgres__query", {"sql": "DROP TABLE x"}, {"postgres": {"environment": "dev"}}, [], Finding(True, False)),
    ("mcp__postgres__execute", {"sql": "truncate t"}, {}, ["postgres"], Finding(True, False)),
    # only SQL fields, only database servers
    ("mcp__notes__save", {"note": "DROP TABLE users"}, {}, [], Finding(False, False)),
    ("mcp__postgres__query", {"note": "DROP TABLE users", "sql": "select 1"}, {}, [], Finding(False, False)),
    ("mcp__postgres__query", {"sql": "select 'drop table x'"}, {}, [], Finding(False, False)),
    ("mcp__postgres__query", {"a": {"b": [{"query": "drop table x"}]}}, {}, [], Finding(True, True)),
    # environment marks a database server
    ("mcp__acme__run_query", {"query": "insert into t values (1)"}, {}, [], Finding(False, False)),
    ("mcp__acme__run_query", {"query": "insert into t values (1)"}, {"acme": {"environment": "prod"}}, [],
     Finding(False, True)),
    ("mcp__acme__run_query", {"query": "drop table t"}, {"acme": {"environment": "prod"}}, [],
     Finding(True, True)),
    ("mcp__Acme__run_query", {"query": "insert into t values (1)"}, {"acme": {"environment": "dev"}}, [],
     Finding(False, False)),
    ("mcp__acme__run_query", {"query": "drop table t"}, {"acme": {"environment": "dev"}}, [], Finding(True, False)),
    ("mcp__acme__run_query", {"query": "insert into t values (1)"}, {"acme": {"environment": "unknown"}}, [],
     Finding(False, False)),
    ("mcp__postgres__query", {"sql": "insert into t values (1)"}, {"postgres": {"environment": "unknown"}}, [],
     Finding(False, True)),
    ("mcp__postgres__query", {"sql": "insert into t values (1)"}, {"postgres": {"environment": "dev"}}, [],
     Finding(False, False)),
    ("mcp__postgres__query", {"sql": "insert into t values (1)"}, {"postgres": {"environment": "bogus"}}, [],
     Finding(False, True)),
    ("mcp__postgres__query", {"sql": "insert into t values (1)"}, "junk", [], Finding(False, True)),
    # dev patterns
    ("mcp__pg-dev-eu__query", {"sql": "insert into t values (1)"}, {}, ["mcp:pg-dev*"], Finding(False, False)),
    ("mcp__pg_local__query", {"sql": "insert into t values (1)"}, {}, ["mcp:pg_*"], Finding(False, False)),
    (f"{SUPA}apply_migration", {"query": "create table x (id int)"}, {}, ["claude_ai_Supabase"],
     Finding(False, False)),
    (f"{SUPA}apply_migration", {"query": "create table x (id int)"}, {}, ["supabase"], Finding(False, True)),
    ("mcp__postgres__query", {"sql": "insert into t values (1)"}, {}, ["host:postgres"], Finding(False, True)),
])
def test_mcp_finding_table(name, inp, servers, patterns, want):
    assert db.mcp_finding(name, inp, servers, patterns) == want


def test_mcp_finding_odd_inputs():
    assert db.mcp_finding("mcp__postgres__query", "not a dict", {}, []) == Finding(False, False)
    deep = {"sql": "drop table x"}
    for _ in range(50):
        deep = {"a": deep}
    assert db.mcp_finding("mcp__postgres__query", deep, {}, []) == Finding(True, True)  # read at any depth
    for _ in range(80):
        deep = {"a": deep}
    assert db.mcp_finding("mcp__postgres__query", deep, {}, []) == Finding(False, True)  # too deep: unknown


# ---------------------------------------------------------------------------
# R1: stdlib only
# ---------------------------------------------------------------------------
def test_dbrules_is_stdlib_only():
    import sys
    src = pathlib.Path(db.__file__).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            names = [node.module]
        else:
            continue
        for n in names:
            assert n.split(".")[0] in sys.stdlib_module_names or n == "__future__", n



# ===========================================================================
# Task 1 fix round 1 (review findings C1-C3, I1-I4, M1-M5; Rulings P4-P7)
# ===========================================================================
# C1: short-option clusters, attached values, long-option prefixes; P7 unknown options
@pytest.mark.parametrize("argv,want", [
    (["psql", "-h", "prod", "-Atc", "DELETE FROM users"], Finding(True, True)),
    (["psql", "-h", "prod", "-qc", "DROP TABLE users"], Finding(True, True)),
    (["psql", "-h", "prod", "-Xqc", "insert into t values(1)"], Finding(False, True)),
    (["psql", "-Xh", "prod", "-c", "insert into t values(1)"], Finding(False, True)),
    (["psql", "-qh", "prod", "-c", "insert into t values(1)"], Finding(False, True)),
    (["psql", "-h", "localhost", "-1c", "drop table x"], Finding(True, False)),
    (["psql", "--ho=prod", "-c", "insert into t values(1)"], Finding(False, True)),
    (["psql", "-h", "prod", "--comm=insert into t values(1)"], Finding(False, True)),
    (["psql", "-h", "localhost", "--comm=drop table x"], Finding(True, False)),
    (["psql", "-dpostgres://prod/db", "-c", "insert into t values(1)"], Finding(False, True)),
    (["psql", "-dhost=prod", "-c", "insert into t values(1)"], Finding(False, True)),
    (["psql", "-h", "prod", "-fx.sql"], Finding(False, True)),
    (["psql", "-h", "prod", "-f-"], Finding(False, True)),
    (["psql", "-h", "localhost", "-c", "insert into t values(1)", "-hprod"], Finding(False, True)),
    (["psql", "-c", "insert into t values(1)", "--", "postgres://prod/db"], Finding(False, True)),
    (["mysql", "-h", "prod", "-Be", "DELETE FROM users"], Finding(True, True)),
    (["mysql", "-h", "prod", "-NBe", "insert into t values(1)"], Finding(False, True)),
    (["mysql", "-h", "prod", "--exec=insert into t values(1)"], Finding(False, True)),
    (["mysql", "-h", "prod", "-psecret", "-e", "insert into t values(1)"], Finding(False, True)),
    (["clickhouse-client", "-h", "prod", "-nq", "insert into t values(1)"], Finding(False, True)),
    (["clickhouse", "client", "-h", "prod", "-q", "insert into t values(1)"], Finding(False, True)),
    (["sqlcmd", "-Sprod", "-Q", "insert into t values(1)"], Finding(False, True)),
    (["sqlite3", "-bail", "app.db", "drop table t"], Finding(True, False)),
    (["sqlite3", "--readonly", "app.db", "drop table t"], Finding(True, False)),
    # P7: an option we do not know makes the target unknown and every positional SQL
    (["psql", "-Z", "-h", "localhost", "insert into t values(1)"], Finding(False, True)),
    (["psql", "--frobnicate", "drop table x"], Finding(True, True)),
    (["pgcli", "-h", "localhost", "-e", "insert into t values(1)"], Finding(False, True)),
])
def test_fix1_option_parsing(argv, want):
    assert cf(argv) == want


@pytest.mark.parametrize("argv", [
    ["psql", "-h", "prod", "-At", "-c", "select 1"], ["psql", "-Atc", "select 1", "-h", "prod"],
    ["mysql", "-h", "prod", "-NBe", "select 1"], ["psql", "-h", "prod", "--csv", "-c", "select 1"],
    ["mysql", "-h", "prod", "--skip-column-names", "-e", "show tables"], ["mysql", "-p", "-h", "prod", "-e", "select 1"],
    ["clickhouse-client", "-h", "prod", "--max_threads=8", "-q", "select 1"],
    ["cockroach", "sql", "--insecure", "--host", "prod", "-e", "select 1"], ["cockroach", "start", "--insecure"],
    ["redis-cli", "-h", "prod", "--scan", "--pattern", "x*"], ["redis-cli", "-h", "prod", "--bigkeys"],
    ["redis-cli", "-h", "prod", "SUBSCRIBE", "ch"], ["redis-cli", "-h", "prod", "MONITOR"],
    ["psql", "-h", "0", "-c", "select 1"],
])
def test_fix1_reads_stay_free(argv):
    assert not cf(argv).write


@pytest.mark.parametrize("argv", [["psql", "-h", "0", "-c", "insert into t values(1)"],
                                  ["psql", "-h", "2130706433", "-c", "insert into t values(1)"]])
def test_fix1_numeric_hosts_are_hosts(argv):
    assert cf(argv).write


# C2: lexings
@pytest.mark.parametrize("text,dialect", [
    ("select a$b$c; drop table x; select $b$", "pg"),
    ("select a$b$c; drop table x; select $b$ $b$", None),
    ("select 1 as a$b$; DROP TABLE users; select $b$ $b$", "pg"),
    ("select 1 as $x$; DROP TABLE users", "mysql"),
    ("select 1 as $x$; DROP TABLE users", None),
    ("select 'a\\'; drop table x; -- '", "mysql"),  # NO_BACKSLASH_ESCAPES lexing
    ("select '\\''; DROP TABLE users; select 'x'", None),
    ("select 1 # it's\n; DROP TABLE users", "mysql"),
    ("select 1 # it's\n; DROP TABLE users", None),
    ("/*!50000DROP TABLE x*/", "mysql"),
    ("/*!DROP*/ TABLE x", "mysql"),
    ("DELETE FROM t#where", "mysql"),
    ("select 1 /* /* nested */ drop table x */", "mysql"),  # MySQL does not nest comments
])
def test_fix1_lexings_destroy(text, dialect):
    assert db.sql_destructive(text, dialect)


@pytest.mark.parametrize("text,dialect", [
    ("select E'\\''; insert into t values(1); select 'x'", "pg"),
    ("select a$b$c; insert into t values(1); select $b$ $b$", "pg"),
    ("select 1 as x$y$; insert into t values(1); select $y$ $y$", "pg"),
    ("select $x$ $x$, $1$y$; insert into t values(1); --$y$", None),
    ("select x$a$ from t; delete from t where id=1; select $a$", "pg"),
    ("select a$b$c; insert into t values(1); select $b$", "mysql"),
    ("select 1 # '\n; insert into t values(1)", "mysql"),
    ('select "a\\""; insert into t values(1); select ""', "mysql"),
    ("select '\\''; insert into t values(1); select '", "mysql"),  # unterminated in one lexing
    ("select 'a\\'b'", "mysql"),  # P7: unterminated under NO_BACKSLASH_ESCAPES, so unknown
    ("select 'never closed", "pg"), ("select $q$ never closed", "pg"), ("select /* never closed", "pg"),
    ('select "setval"(1)', "pg"), ("select pg_catalog.setval('s', 1)", "pg"),
    ("select query_to_xml('delete from t', true, true, '')", "pg"), ("select lo_from_bytea(0, 'x')", "pg"),
    ("select pg_logical_emit_message(true,'x','y')", "pg"), ("select lo_import('/etc/passwd')", "pg"),
    ("select pg_reload_conf ()", "pg"),
])
def test_fix1_lexings_write(text, dialect):
    assert db.sql_writes(text, dialect)


@pytest.mark.parametrize("text,dialect", [
    ("select E'\\'; drop table x; --'", "pg"),  # one E'' string
    ("select 1 /* /* nested */ drop table x */", "pg"),
    ("select 'C:\\temp'", "pg"), ("select $$drop table x$$", "pg"),
    ("select 'it''s; drop table x'", "mysql"), ("select `drop table x` from t", "mysql"),
])
def test_fix1_lexings_no_destroy(text, dialect):
    assert not db.sql_destructive(text, dialect)


@pytest.mark.parametrize("text,dialect", [
    ("select 1 as a$b, 2", "pg"), ("select $1, $2 from t", "pg"), ("select `insert` from t", "mysql"),
    ("select into_col from t", "mysql"), ("select * from t where a = 'drop table x'", "mysql"),
    ("select 1 # comment", "mysql"), ("select 1 -- c", "mysql"),
])
def test_fix1_lexings_reads(text, dialect):
    assert not db.sql_writes(text, dialect)


def test_fix1_sql_strip_keeps_length():
    for text in ["select 'a''b' -- x\n; drop", "select $q$x$q$, \"id\" /* c */", "/*!1 x */"]:
        assert len(db.sql_strip(text)) == len(text)


# M1 / M2 / P4: destroy shapes
@pytest.mark.parametrize("text", [
    "DELETE t FROM t", "DELETE t1, t2 FROM t1 JOIN t2", "DELETE FROM t USING u",
    "WITH d AS (select 1 where true) DELETE FROM t", "DELETE FROM t RETURNING (select 1 where true)",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "drop owned by me", "drop extension x cascade",
    "drop type x cascade", "drop materialized view x", "drop view v cascade", "drop function f() cascade",
    "TRUNCATE ONLY x", "truncate t1, t2", "explain analyze delete from t", "DELETE users",
    "DO $$ BEGIN DROP TABLE x; END $$", "DO $$ BEGIN EXECUTE $q$DROP TABLE x$q$; END $$",
    "CREATE FUNCTION f() RETURNS void AS 'DROP TABLE x' LANGUAGE sql",
    "select 'drop table x' \\gexec", "select format('drop table %I', 'x') \\gexec",
    "select $$drop table x$$ \\gexec", "select $q$x$q$; DO $$ BEGIN EXECUTE $q$TRUNCATE t$q$; END $$",
    "PREPARE p AS DELETE FROM t", "\\set x 'drop table t'",
    "select dblink_exec('host=prod', 'drop table t')",
])
def test_fix1_destroy_shapes(text):
    assert db.sql_destructive(text, "pg")


@pytest.mark.parametrize("text", [
    "explain delete from t", "EXPLAIN (costs off) DELETE FROM t",
    "create table t (a int references u on delete cascade)", "grant delete on t to bob",
    "create trigger x after delete on t for each row execute function f()",
    "DELETE FROM t WHERE id = 1 RETURNING *", "WITH d AS (DELETE FROM t WHERE id = 1 RETURNING *) SELECT 1",
    "DELETE FROM t WHERE true OR 1=1", "drop index i", "drop view v", "select 'drop table x'",
    "DO $$ BEGIN PERFORM 1; END $$",
])
def test_fix1_not_destroy_shapes(text):
    assert not db.sql_destructive(text, "pg")


# I1: shell escapes
@pytest.mark.parametrize("argv,cmd", [
    (["psql", "-h", "localhost", "-c", "\\! rm -rf ~"], "rm -rf ~"),
    (["psql", "-h", "localhost", "-c", "\\o |rm -rf ~"], "rm -rf ~"),
    (["psql", "-h", "localhost", "-c", "select 1 \\g |rm -rf ~"], "rm -rf ~"),
    (["psql", "-h", "localhost", "-c", "\\w |sh"], "sh"),
    (["psql", "-h", "localhost", "-o", "|rm -rf ~", "-c", "select 1"], "rm -rf ~"),
    (["psql", "-h", "localhost", "-c", "\\copy t to program 'rm -rf ~'"], "rm -rf ~"),
    (["psql", "-h", "localhost", "-c", "copy t from program 'rm -rf ~'"], "rm -rf ~"),
    (["psql", "-h", "localhost", "-c", "COPY t TO PROGRAM $$rm -rf ~$$"], "rm -rf ~"),
    (["sqlite3", "app.db", ".shell rm -rf ~"], "rm -rf ~"),
    (["sqlite3", "app.db", ".system rm -rf ~"], "rm -rf ~"),
    (["sqlite3", "app.db", ".once |rm -rf ~"], "rm -rf ~"),
    (["duckdb", "x.db", "-c", ".shell rm -rf ~"], "rm -rf ~"),
    (["mysql", "-h", "localhost", "-e", "system rm -rf ~"], "rm -rf ~"),
    (["mysql", "-h", "localhost", "-e", "select 1; \\! rm -rf ~"], "rm -rf ~"),
    (["mysql", "-h", "localhost", "--pager=rm -rf ~", "-e", "select 1"], "rm -rf ~"),
])
def test_fix1_shell_escapes(argv, cmd):
    f = cf(argv)
    assert cmd in f.shell
    assert not f.write  # local target


def test_fix1_shell_escape_on_a_remote_target_is_a_write():
    assert cf(["psql", "-h", "prod", "-c", "\\! ls"]).write
    assert cf(["psql", "-h", "prod", "-c", "select 1 \\g |sh"]).write
    assert not cf(["psql", "-h", "prod", "-c", "select '\\! ls'"]).shell  # inside a string


# I2: target overrides
@pytest.mark.parametrize("argv", [
    ["psql", "postgres://u:p@localhost/db?host=prod.example.com", "-c", "insert into t values(1)"],
    ["psql", "postgres:///db?host=prod.example.com", "-c", "insert into t values(1)"],
    ["psql", "postgresql://u:p@localhost/db?hostaddr=10.0.0.5", "-c", "insert into t values(1)"],
    ["psql", "host='prod'", "-c", "insert into t values(1)"],
    ["psql", "host=\"prod\"", "-c", "insert into t values(1)"],
    ["psql", "host = prod", "-c", "insert into t values(1)"],
    ["psql", "HOST=prod", "-c", "insert into t values(1)"],
    ["psql", "host=", "-c", "insert into t values(1)"],
    ["psql", "-h", "localhost", "-c", "\\c postgres://prod/db", "-c", "insert into t values(1)"],
    ["psql", "-h", "localhost", "-c", "\\connect host=prod", "-c", "insert into t values(1)"],
    ["mysql", "-h", "localhost", "-e", "connect db prod; insert into t values(1)"],
    ["mysql", "-h", "localhost", "-e", "\\r db prod\ninsert into t values(1)"],
    ["mysql", "--defaults-file=/etc/prod.cnf", "-e", "insert into t values(1)"],
    ["mysql", "--defaults-extra-file", "/etc/prod.cnf", "-e", "insert into t values(1)"],
    ["mysql", "--defaults-group-suffix=_prod", "-e", "insert into t values(1)"],
    ["duckdb", "app.db", "-c", "attach 'postgres:host=prod' as p (type postgres); insert into p.t values(1)"],
    ["psql", "-h", "localhost", "-c", "select dblink_exec('host=prod', 'insert into t values(1)')"],
])
def test_fix1_target_overrides(argv):
    assert cf(argv).write


@pytest.mark.parametrize("argv", [
    ["psql", "postgresql:///db?host=/var/run/postgresql", "-c", "insert into t values(1)"],
    ["psql", "dbname=x", "-c", "insert into t values(1)"],
    ["sqlite3", "app.db", "attach '/tmp/x.db' as e; insert into e.t values(1)"],
    ["psql", "-h", "prod", "-c", "\\conninfo"],
])
def test_fix1_target_overrides_negative(argv):
    assert not cf(argv).write


# C3: a pipe the parser flattened
def test_fix1_piped_client_without_inline_sql():
    assert cf(["psql", "-h", "prod"], piped=True).write
    assert cf(["redis-cli", "-h", "prod"], piped=True).write
    assert cf(["mongosh", "mongodb://prod/db"], piped=True).write
    assert not cf(["psql", "-h", "prod", "-c", "select 1"], piped=True).write
    assert not cf(["psql", "-h", "localhost"], piped=True).write
    assert not cf(["redis-cli", "-h", "prod", "--scan"], piped=True).write


# I3: migration CLIs
@pytest.mark.parametrize("argv", [
    ["npx", "-p", "prisma", "prisma", "db", "push"], ["npx", "--package", "prisma", "prisma", "db", "push"],
    ["npx", "--package=prisma", "prisma", "db", "push"], ["npx", "-p", "prisma@5", "prisma", "db", "push"],
    ["npx", "-c", "prisma db push"], ["npx", "prisma", "--schema", "x", "db", "push"],
    ["supabase", "--db-url", "postgres://u:p@prod/db", "db", "reset"],
    ["supabase", "db", "--db-url", "postgres://u:p@prod/db", "reset"],
    ["supabase", "db", "reset", "--db-url", "$DB_URL"], ["supabase", "--workdir", "x", "db", "push"],
    ["supabase", "db", "push", "--db-url", "postgres://u@localhost,prod/x"],
])
def test_fix1_cli_write(argv):
    assert db.cli_write(argv[0], argv[1:])


@pytest.mark.parametrize("argv", [
    ["supabase", "db", "reset", "--linked=false"], ["npx", "prisma", "migrate", "dev", "--name", "x"],
    ["supabase", "--db-url", "postgres://u@localhost/db", "db", "reset"],
])
def test_fix1_cli_no_write(argv):
    assert not db.cli_write(argv[0], argv[1:])


# M2 / M3: redis, mongo
@pytest.mark.parametrize("text", [
    'db.x["drop"]()', "db.x.drop.call(db.x)", "db.x.drop.apply(db.x)", "db.dropDatabase ()",
    'eval("db.x.dr"+"op()")', "db.x.mapReduce(m, r, {out: 'y'})", 'db.getCollection("x")["deleteMany"]({})',
])
def test_fix1_mongo_writes(text):
    assert db.mongo_writes(text)


def test_fix1_mongo_run_command_reads():
    assert not db.mcp_finding("mcp__mongodb__run_command", {"command": {"ping": 1}}, {}, []).write
    assert not db.mcp_finding("mcp__mongodb__run_command", {"command": {"buildInfo": 1}}, {}, []).write
    assert not db.mcp_finding("mcp__mongodb__run_command", {"command": {"serverStatus": 1}}, {}, []).write
    assert db.mcp_finding("mcp__mongodb__run_command", {"command": {"dropDatabase": 1}}, {}, []).write
    assert db.mcp_finding("mcp__mongodb__run_command", {"command": {"aggregate": "x", "pipeline": [{"$out": "y"}]}},
                          {}, []).write


# I4 / P4: MCP sql-destroy backstop
@pytest.mark.parametrize("name,inp,destroy", [
    ("mcp__warehouse__run_query", {"sql": "TRUNCATE t"}, True),
    ("mcp__acme__query", {"statement": "DELETE FROM users"}, True),
    ("mcp__linear__query", {"query": "DELETE FROM users"}, True),
    ("mcp__acme__execute", {"queries": ["select 1", "drop table x"]}, True),
    ("mcp__acme__raw", {"sql": "drop schema s"}, True),
    ("mcp__github__search_code", {"query": "DROP TABLE users"}, False),
    ("mcp__notion__search", {"query": "drop table"}, False),
    ("mcp__context7__get-library-docs", {"query": "drop database"}, False),
    ("mcp__github__create_issue", {"body": "DROP TABLE users"}, False),
    ("mcp__acme__run_query", {"note": "DROP TABLE users"}, False),
])
def test_fix1_mcp_destroy_backstop(name, inp, destroy):
    assert db.mcp_finding(name, inp, {}, []).destroy is destroy


@pytest.mark.parametrize("inp", [
    {"input": "DROP TABLE users"}, {"text": "DROP TABLE users"}, {"sqlQuery": "DROP TABLE users"},
    {"query_text": "DROP TABLE users"}, {"query": {"text": "DROP TABLE users"}},
    {"query": ["select 1", "DROP TABLE users"]},
    {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"query": "DROP TABLE users"}}}}}}}},
])
def test_fix1_mcp_sql_fields(inp):
    f = db.mcp_finding("mcp__supabase__execute_sql", inp, {"supabase": {"environment": "prod"}}, [])
    assert f.destroy and f.write


# M5 / P5
def test_fix1_prod_beats_dev_patterns():
    prod = {"supabase": {"environment": "prod"}}
    for pats in (["supabase"], ["mcp:supa*"], ["mcp:supabase"]):
        assert db.mcp_finding("mcp__supabase__execute_sql", {"query": "insert into t values(1)"}, prod, pats).write
    assert not db.mcp_finding("mcp__supabase__execute_sql", {"query": "insert into t values(1)"}, {},
                              ["supabase"]).write


@pytest.mark.parametrize("p", ["*", "host:*", "mcp:*", "host:**", "host:*.*", "mcp:**"])
def test_fix1_wildcard_only_patterns(p):
    assert db.check_dev_pattern(p)
    assert not db.dev_host([p], "prod.example.com")
    assert not db.dev_server([p], "supabase")
