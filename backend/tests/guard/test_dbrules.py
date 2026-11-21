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
    assert not db.sql_destructive(text)


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
    assert not db.sql_writes(text)


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
    assert db.mcp_finding("mcp__postgres__query", deep, {}, []) == Finding(False, False)  # beyond depth 6


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
