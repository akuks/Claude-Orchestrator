"""co-guard database rules: ``sql-destroy`` and ``db-write``.

Python port of the database guard in jspark-forge ``scripts/lib/blocklist.js``
(MIT, same owner): sqlStrip, stmtWrites, READ_PRAGMA, META_WRITE, the client
specs, clientArgs, clientTarget, connInfo, noteDbEnv, literalFeed, dbCliWrite,
DB_SERVER and mcpDbWrite (inventory F32/F33). Nothing is ever executed or
connected to.

RULING R1: stdlib only, no app imports. This module is copied next to
``rules.py`` and ``shell.py`` into the co-guard plugin and loaded like
``shell.py`` (package import or sibling file).

``sql-destroy`` (DROP TABLE/DATABASE/SCHEMA, TRUNCATE, DELETE without WHERE)
is read from the SQL a client actually receives, after :func:`sql_strip` has
blanked string literals, quoted identifiers and comments: ``-c``/``-e``/
``--query``/``--eval`` values, positional SQL (sqlite3/duckdb), literal text
piped in (``echo``/``printf``), and heredoc/here-string bodies that are
literal (a quoted heredoc, or one with no ``$`` or backtick). It ignores the
target, so it fires on local databases too.

``db-write`` is any statement that is not a read, against a target that is
neither local nor dev. Unknown SQL (a script file, a stdin redirect, a pipe
from a non-literal stage) is a write. Local: localhost, 127.x, ::1, 0.0.0.0,
``*.localhost``, a Unix socket directory (not ``/cloudsql/``), a file
database. A variable, ``service=``/``PGSERVICE`` or ``--login-path`` is
unknown, which means production. Commands run on another machine
(``ssh host CMD``) have no local database.

Dev patterns (forge ``database dev:`` syntax): ``host:<glob>`` (database
hosts, ``*`` only), ``mcp:<server>`` or ``mcp:<prefix>*`` (MCP servers), or
a bare exact host or server name. A bare name with ``*`` is rejected by
:func:`check_dev_pattern`.

MCP: a server is a database when its ``environment`` is set (``dev`` or
``prod``) or its name matches ``DB_SERVER``. ``db-write`` follows forge
``mcpDbWrite`` and is skipped for ``dev`` servers and ``mcp:`` dev patterns.
``sql-destroy`` checks only the SQL fields of database servers' inputs, in
every environment.

Seatbelt limits: SQL inside stored procedures, ``DO`` blocks and other
dollar-quoted bodies is not read (it is blanked like a string), files the
client reads are never opened, and a dev pattern relaxes whatever host name
the command names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

__all__ = [
    "Finding", "SQL_CLIENTS", "DB_CLIENT_SPECS", "DB_ENV", "sql_strip", "sql_destructive", "sql_writes",
    "redis_writes", "mongo_writes", "host_local", "dev_host", "dev_server", "check_dev_pattern",
    "client_finding", "cli_write", "mcp_finding", "db_env_name",
]

_F = re.I | re.A


@dataclass(frozen=True)
class Finding:
    destroy: bool = False
    write: bool = False


_NONE = Finding()

# ---------------------------------------------------------------------------
# SQL text
# ---------------------------------------------------------------------------
_DOLLAR = re.compile(r"\$([A-Za-z_]\w{0,62})?\$", re.A)


def sql_strip(text):
    """SQL with string literals, quoted identifiers and comments blanked out.
    MySQL runs ``/*! ... */``, so its text is kept. One pass."""
    s = str(text)
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        nx = s[i + 1] if i + 1 < n else ""
        if c == "-" and nx == "-":
            k = s.find("\n", i)
            i = n if k < 0 else k
            continue
        if c == "/" and nx == "*":
            if i + 2 < n and s[i + 2] == "!":
                out.append(" ")
                i += 3
                while i < n and "0" <= s[i] <= "9":
                    i += 1
                continue
            k = s.find("*/", i + 2)
            out.append(" ")
            i = n if k < 0 else k + 2
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(" '' ")
            i = j + 1
            continue
        if c in ('"', "`"):
            k = s.find(c, i + 1)
            out.append(" q ")
            i = n if k < 0 else k + 1
            continue
        if c == "$":
            m = _DOLLAR.match(s, i)
            if m:
                tag = m.group(0)
                k = s.find(tag, i + len(tag))
                out.append(" '' ")
                i = n if k < 0 else k + len(tag)
                continue
        out.append(c)
        i += 1
    return "".join(out)


_DROP = re.compile(r"\bdrop\s+(table|database|schema)\b", _F)
_TRUNCATE = re.compile(r"\btruncate\s+(table\s+)?[\"'`\w]", _F)
_DELETE_FROM = re.compile(r"\bdelete\s+from\b", _F)
_WHERE = re.compile(r"\bwhere\b", _F)


def sql_destructive(text):
    """DROP TABLE/DATABASE/SCHEMA, TRUNCATE, or DELETE FROM without WHERE,
    in the SQL left after :func:`sql_strip`."""
    for stmt in sql_strip(text).split(";"):
        if _DROP.search(stmt) or _TRUNCATE.search(stmt):
            return True
        if _DELETE_FROM.search(stmt) and not _WHERE.search(stmt):
            return True
    return False


# Words that make a read statement (SELECT, WITH, VALUES, EXPLAIN ANALYZE) change data: SELECT ... INTO, a
# data-modifying CTE, a second statement without a semicolon (T-SQL), and functions with side effects.
_SQL_WRITE_WORD = re.compile(
    r"\b(insert|update|delete|merge|upsert|into|create|alter|drop|truncate|grant|revoke|exec|execute|call)\b", _F)
_SQL_SIDE_EFFECT = re.compile(
    r"\b(setval|nextval|set_config|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|"
    r"pg_switch_wal|pg_promote|pg_drop_replication_slot|pg_create_\w+|pg_file_\w+|lo_unlink|lo_import|lo_export|"
    r"lo_create|dblink\w*|cron\.\w+)\s*\(", _F)
_SQL_READ_FIRST = frozenset({"show", "describe", "desc", "help", "use", "begin", "commit", "rollback", "end",
                             "savepoint", "release", "abort", "listen", "unlisten", "fetch", "close", "discard"})
_READ_PRAGMA = re.compile(
    r"^pragma\s+(\w+\.)?(table_info|table_xinfo|table_list|index_list|index_info|index_xinfo|foreign_key_list|"
    r"foreign_key_check|database_list|compile_options|collation_list|function_list|module_list|pragma_list|"
    r"integrity_check|quick_check|user_version|schema_version|application_id|page_count|page_size|"
    r"freelist_count|data_version|journal_mode|encoding|foreign_keys)\b[^=]*$", _F)
# psql / mysql meta-commands that run a file, a shell or generated SQL.
_META_WRITE = re.compile(r"\\(gexec|ir?|include(_relative)?|!|\.|lo_import|lo_unlink)(?![a-z_])", _F)
_LEAD = re.compile(r"^[\s(]+")
_FIRST = re.compile(r"^[a-z_]+", _F)
_FOR_LOCK = re.compile(r"\bfor\s+(no\s+key\s+)?(update|share)\b", _F)


def _stmt_writes(stmt):
    t = _LEAD.sub("", stmt)
    if not t:
        return False
    if _META_WRITE.search(t):
        return True
    if t[0] == "\\":
        return bool(re.match(r"\\copy\b", t, _F)) and (
            not re.search(r"\bto\b", t, _F) or bool(re.search(r"\bfrom\s+(stdin|pstdin|'')", t, _F)))
    if t[0] == ".":  # sqlite / duckdb dot-commands
        return bool(re.match(r"\.(read|import|restore|load|shell|system|clone)\b", t, _F))
    m = _FIRST.match(t)
    if not m:
        return True  # not a statement we can read ("$SQL"): unknown, so held
    k = m.group(0).lower()
    body = _FOR_LOCK.sub(" ", t)
    if k in ("select", "with", "values", "table"):
        return bool(_SQL_WRITE_WORD.search(body) or _SQL_SIDE_EFFECT.search(body))
    if k == "explain":  # plain EXPLAIN runs nothing
        return bool(re.search(r"\banaly[sz]e\b", t, _F)) and bool(
            _SQL_WRITE_WORD.search(body) or _SQL_SIDE_EFFECT.search(body))
    if k in _SQL_READ_FIRST:
        return False
    if k == "start":
        return not re.match(r"start\s+transaction\b", t, _F)
    if k == "set":
        return bool(re.match(r"set\s+(global|persist|persist_only|password|@@global|@@persist)\b", t, _F))
    if k == "pragma":
        return not _READ_PRAGMA.match(t)
    if k == "copy":
        return not re.search(r"\bto\s+stdout\b", t, _F) or bool(re.search(r"\bfrom\s+(stdin|program)\b", t, _F))
    return True


def sql_writes(text):
    """Any statement in ``text`` that is not a read."""
    return any(_stmt_writes(s) for s in sql_strip(text).split(";"))


_MONGO_WRITE = re.compile(
    r"\.\s*(insert\w*|update\w*|delete\w*|drop\w*|remove\w*|replaceOne|bulkWrite|save|create\w*|rename\w*|"
    r"findOneAnd\w+|findAndModify|runCommand|adminCommand|grant\w*|revoke\w*|shardCollection|compact|"
    r"initialize\w*BulkOp)\s*\(|\$(out|merge)\b|\bload\s*\(", re.A)


def mongo_writes(text):
    return bool(_MONGO_WRITE.search(str(text)))


_REDIS_READ = frozenset((
    "get mget hget hgetall hmget hkeys hvals hlen hexists hstrlen hscan keys scan sscan zscan exists ttl pttl "
    "type info ping dbsize lrange llen lindex smembers sismember smismember scard srandmember zrange zrangebyscore "
    "zrevrange zrevrangebyscore zrangebylex zcard zscore zmscore zrank zrevrank zcount zlexcount strlen getrange "
    "getbit bitcount bitpos xrange xrevrange xlen xinfo time echo lastsave role randomkey pfcount geopos geodist "
    "geohash geosearch json.get json.type json.mget ft.search ft.info memory").split())
_REDIS_READ_SUB = re.compile(
    r"^(config|slowlog|client|cluster|object|command|acl|latency|pubsub)\s+(get|list|info|nodes|slots|shards|myid|"
    r"encoding|freq|idletime|refcount|count|docs|whoami|getuser|users|cat|latest|history|len|channels|numsub|"
    r"numpat|getname|id)\b", _F)


def redis_writes(words):
    """A redis command (its words) that is not a read."""
    w = [str(x).strip() for x in words]
    w = [x for x in w if x]
    if not w:
        return False
    return w[0].lower() not in _REDIS_READ and not _REDIS_READ_SUB.match(" ".join(w[:2]))


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------
def _is_var(s):  # $VAR, ${VAR}, `cmd`, %VAR%: unknown, so production
    return bool(re.search(r"[$%`]", str(s)))


def _unquote(h):
    return re.sub(r"^['\"]|['\"]$", "", str(h).strip())


def _host_name(h):
    """A host as written ('tcp:db,1433', '[::1]:5432', '(localdb)\\x') reduced to the name."""
    s = _unquote(h).lower()
    if s.startswith("tcp:"):
        s = s[4:]
    s = s.split("\\")[0]
    m = re.fullmatch(r"\[(.+)\](:\d+)?", s)
    if m:
        return m.group(1)
    if s.count(":") == 1:
        s = s.split(":")[0]
    return s


def host_local(h):
    """localhost, 127.x, ::1, 0.0.0.0, *.localhost, a Unix socket directory
    (not /cloudsql/) or an empty host."""
    raw = _unquote(h)
    if _is_var(raw):
        return False
    if raw.startswith("/"):
        return not re.search(r"/cloudsql/", raw, re.I)
    s = _host_name(raw)
    return (s in ("", ".", "localhost", "(local)", "(localdb)", "::1", "0.0.0.0")
            or bool(re.fullmatch(r"127\.\d{1,3}\.\d{1,3}\.\d{1,3}", s, re.A)) or s.endswith(".localhost"))


_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/?]*@)?(\[[^\]]+\]|[^/?]*)", re.I)


def _url_host(u):
    m = _URL.match(str(u))
    if not m:
        return None
    h = m.group(1)
    # postgres://a,b:5433/db lists hosts; keep the commas, drop the ports (an IPv6 [..] stays whole)
    return h if h.startswith("[") else ",".join(re.sub(r":\d*$", "", x) for x in h.split(","))


# ---------------------------------------------------------------------------
# Dev patterns
# ---------------------------------------------------------------------------
_KIND = re.compile(r"^(host|mcp)\s*:", re.I)
DEV_PATTERN_MAX = 500


def _split_pattern(p):
    p = str(p).strip()
    m = _KIND.match(p)
    if m:
        return m.group(1).lower(), p[m.end():].strip()
    return None, p


def check_dev_pattern(p):
    """None when ``p`` is a valid dev database pattern, else an error message."""
    if not isinstance(p, str):
        return "a dev database pattern must be text"
    x = p.strip()
    if len(x) > DEV_PATTERN_MAX:
        return f"a dev database pattern is at most {DEV_PATTERN_MAX} characters"
    kind, v = _split_pattern(x)
    if not v:
        return f'"{x}" names nothing'
    if not kind and "*" in v:
        return f'"{x}": a pattern needs host:<pattern> (database hosts) or mcp:<prefix>* (MCP servers)'
    if kind == "mcp" and "*" in v[:-1]:
        return f'"{x}": mcp: takes an exact server name or a prefix ending in * (host: is for host patterns)'
    return None


def _patterns(patterns):
    if not isinstance(patterns, (list, tuple)):
        return []
    return [p.strip() for p in patterns if isinstance(p, str) and p.strip()]


def _glob(pattern, name):
    """``*``-only glob, case-insensitive, linear-time (no regex backtracking)."""
    p, s = pattern.lower(), name.lower()
    i = j = 0
    star, mark = -1, 0
    while j < len(s):
        if i < len(p) and p[i] != "*" and p[i] == s[j]:
            i += 1
            j += 1
        elif i < len(p) and p[i] == "*":
            star, mark = i, j
            i += 1
        elif star >= 0:
            i = star + 1
            mark += 1
            j = mark
        else:
            return False
    while i < len(p) and p[i] == "*":
        i += 1
    return i == len(p)


def dev_host(patterns, h):
    """A host is dev when a ``host:<glob>`` pattern or a bare exact name matches its name."""
    if h is None or _is_var(h):
        return False
    name = _host_name(h)
    if not name:
        return False
    for p in _patterns(patterns):
        kind, v = _split_pattern(p)
        if kind == "host":
            if v and _glob(v, name):
                return True
        elif kind is None and v.lower() == name:
            return True
    return False


def dev_server(patterns, name):
    """An MCP server is dev when ``mcp:<name>`` or a bare pattern names it
    exactly, or ``mcp:<prefix>*`` starts it."""
    s = str(name).lower()
    for p in _patterns(patterns):
        kind, v = _split_pattern(p)
        if kind == "host":
            continue
        v = v.lower()
        if kind == "mcp" and v.endswith("*"):
            if s.startswith(v[:-1]):
                return True
        elif v == s:
            return True
    return False


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
def _spec(**kw):
    base = {"sql": (), "file": (), "host": (), "conn": (), "value": (), "unknown": (), "uri_positional": False,
            "positional_sql": False, "ci": False, "mongo": False, "redis": False, "restore": False,
            "at_host": False, "file_db": False}
    base.update(kw)
    return base


_PG = _spec(sql=("-c", "--command"), file=("-f", "--file"), host=("-h", "--host"), conn=("-d", "--dbname"),
            value=("-p", "--port", "-U", "--username", "-v", "--set", "--variable", "-o", "--output", "-L",
                   "--log-file", "-P", "--pset", "-F", "--field-separator", "-R", "--record-separator", "-T",
                   "--table-attr"), uri_positional=True)
_MY = _spec(sql=("-e", "--execute"), host=("-h", "--host"), unknown=("--login-path",),
            value=("-u", "--user", "-P", "--port", "-D", "--database", "-S", "--socket", "--defaults-file",
                   "--defaults-extra-file", "--protocol"))
_CH = _spec(sql=("-q", "--query"), file=("--queries-file",), host=("-h", "--host"),
            value=("--port", "-u", "--user", "--password", "-d", "--database"))
_MONGO = _spec(sql=("--eval",), file=("-f", "--file"), host=("--host",), mongo=True, uri_positional=True,
               value=("--port", "-u", "--username", "-p", "--password", "--authenticationDatabase", "--tlsCAFile",
                      "--tlsCertificateKeyFile", "--apiVersion"))
_REDIS = _spec(host=("-h",), conn=("-u",), redis=True,
               value=("-p", "-a", "-n", "--user", "--pass", "-s", "-r", "-i", "-d"))
_LITE = _spec(sql=("-cmd",), file=("-init",), positional_sql=True, file_db=True,
              value=("-separator", "-newline", "-nullvalue", "-vfs", "-maxsize", "-mmap", "-lookaside",
                     "-pagecache"))

DB_CLIENT_SPECS = {
    "psql": _PG, "pgcli": _PG, "usql": _PG,
    "pg_restore": _spec(host=("-h", "--host"), conn=("-d", "--dbname"), restore=True,
                        value=("-p", "--port", "-U", "--username", "-f", "--file", "-j", "--jobs", "-n", "--schema",
                               "-t", "--table", "-I", "--index", "-P", "--function", "-T", "--trigger", "-L",
                               "--use-list", "-S", "--superuser", "--role", "-F", "--format")),
    "mysql": _MY, "mariadb": _MY, "mycli": dict(_MY, uri_positional=True),
    "mysqlsh": dict(_MY, file=("-f", "--file"), uri_positional=True, at_host=True),
    "clickhouse-client": _CH, "clickhouse": _CH,
    "cockroach": _spec(sql=("-e", "--execute"), file=("-f", "--file"), host=("--host",), conn=("--url",),
                       value=("--port", "-p", "--user", "-u", "--database", "-d", "--certs-dir")),
    "sqlcmd": _spec(sql=("-Q", "-q"), file=("-i",), host=("-S",),
                    value=("-U", "-P", "-d", "-o", "-s", "-w", "-h", "-t", "-l", "-v", "-f")),
    "sqlite3": _LITE, "sqlite": _LITE,
    # duckdb: a file database, but "md:" / "motherduck:" is remote
    "duckdb": dict(_LITE, sql=("-c", "-cmd"), file=("-init", "-f")),
    "mongosh": _MONGO, "mongo": _MONGO,
    "redis-cli": _REDIS, "valkey-cli": _REDIS,
}
# Clients that take SQL (sql-destroy is read from their SQL text). Forge's set plus pgcli/usql/mycli, which
# take the same SQL (forge left them out of its raw-line check).
SQL_CLIENTS = frozenset({"psql", "pgcli", "usql", "mysql", "mariadb", "mycli", "mysqlsh", "sqlite3", "sqlite",
                         "sqlcmd", "clickhouse-client", "clickhouse", "duckdb", "cockroach"})
_GENERIC_CONN = ("--url", "--uri", "--db-url", "--connection-string", "-connectionstring")
DB_ENV = re.compile(r"^(PGHOST|PGHOSTADDR|PGSERVICE|MYSQL_HOST|DATABASE_URL|MONGODB_URI|POSTGRES_URL)$", re.I)
_LONG = re.compile(r"^(--[\w-]+)=(.*)$", re.S | re.A)


def db_env_name(name):
    """The canonical (upper-case) name when ``name`` is a database env var, else None."""
    return str(name).upper() if isinstance(name, str) and DB_ENV.match(name) else None


class _Args:
    __slots__ = ("sql", "file", "hosts", "conn", "positional", "other", "unknown")

    def __init__(self):
        self.sql, self.hosts, self.conn, self.positional, self.other = [], [], [], [], []
        self.file = False
        self.unknown = False


def _client_args(spec, args):
    r = _Args()
    args = [str(a) for a in args]
    i = 0
    while i < len(args):
        a = args[i]
        long = _LONG.match(a)
        opt = long.group(1) if long else (a.lower() if spec["ci"] else a)

        def opt_value():  # SQL may start with "-" ("-- a comment")
            nonlocal i
            if long:
                return long.group(2)
            i += 1
            return args[i] if i < len(args) else None

        def plain_value():  # "-p" without a value must not swallow the next option
            nonlocal i
            if long:
                return long.group(2)
            nx = args[i + 1] if i + 1 < len(args) else None
            if nx is None or (nx.startswith("-") and len(nx) > 1):
                return None
            i += 1
            return nx

        if opt in spec["sql"]:
            v = opt_value()
            if v is not None:
                r.sql.append(v)
        elif opt in spec["file"]:
            opt_value()
            r.file = True
        elif opt in spec["host"]:
            v = plain_value()
            if v is not None:
                r.hosts.append(v)
        elif opt in spec["conn"] or opt.lower() in _GENERIC_CONN:
            v = plain_value()
            if v is not None:
                r.conn.append(v)
        elif opt in spec["unknown"]:
            r.unknown = True
            plain_value()
        elif opt in spec["value"]:
            plain_value()
        elif not spec["ci"] and re.match(r"-[a-zA-Z].", a) and not a.startswith("--"):  # -e"..." -hprod
            o = a[:2]
            if o in spec["sql"]:
                r.sql.append(a[2:])
            elif o in spec["host"]:
                r.hosts.append(a[2:])
            else:
                r.other.append(a)
        elif a.startswith("-") and len(a) > 1:
            r.other.append(a)
        else:
            r.positional.append(a)
        i += 1
    return r


class _Target:
    __slots__ = ("hosts", "unknown")

    def __init__(self, unknown=False):
        self.hosts = []
        self.unknown = unknown


def _add_hosts(t, v):
    if v is None or _is_var(v):
        t.unknown = True
        if v is not None:
            t.hosts.append(str(v))
        return
    for part in str(v).split(","):
        if not re.fullmatch(r"\s*\d+\s*", part):  # "host,1433": a port
            t.hosts.append(part)


_CONN_KEY = re.compile(r"(?:^|[\s;])(?:host|hostaddr|server|data source|address|addr)\s*=\s*([^\s;'\"]+)", re.I)


def _conn_info(t, v, spec, as_uri):
    """A connection string, URL or conninfo ("host=db dbname=x",
    "Server=db;...", "service=prod"), or a database name."""
    s = str(v)
    if re.match(r"(md|motherduck):", s, re.I):
        t.hosts.append("motherduck.com")
        return
    u = _url_host(s)
    if u is not None:
        _add_hosts(t, u)
        return
    found = False
    if re.search(r"(^|[\s;])service\s*=", s, re.I):
        t.unknown = True
        found = True
    for m in _CONN_KEY.finditer(s):
        _add_hosts(t, m.group(1))
        found = True
    if found or not as_uri:
        return
    if _is_var(s):
        t.unknown = True
        return
    if spec["mongo"]:
        m = re.match(r"([^\s/:@]+)(?::\d+)?/", s) or re.fullmatch(r"([^\s/:@]+):\d+", s)
        if m:
            _add_hosts(t, m.group(1))
    elif spec["at_host"]:
        m = re.search(r"@([^\s/:@]+)", s)
        if m:
            _add_hosts(t, m.group(1))


def _client_target(spec, p, env):
    t = _Target(p.unknown)
    if not spec["file_db"]:  # a file database never connects to a server named in the env
        for k, v in env.items():
            if k == "PGSERVICE":
                t.unknown = True
            elif k in ("PGHOST", "PGHOSTADDR", "MYSQL_HOST"):
                _add_hosts(t, v)
            elif v is None or _is_var(v):
                t.unknown = True
            else:
                u = _url_host(v)
                if u is not None:
                    _add_hosts(t, u)
    for h in p.hosts:
        _add_hosts(t, h)
    for c in p.conn:
        _conn_info(t, c, spec, True)
    if spec["redis"]:
        cands = []
    elif spec["positional_sql"]:
        cands = [] if spec["ci"] else p.positional[:1]
    else:
        cands = p.positional
    for c in cands:
        _conn_info(t, c, spec, spec["uri_positional"])
    return t


def _env(line_env, prefix_env):
    env = {}
    for src in (line_env, prefix_env):
        if isinstance(src, dict):
            for k, v in src.items():
                name = db_env_name(k)
                if name:
                    env[name] = None if v is None else str(v)
    return env


def client_finding(name, args, *, prefix_env=None, line_env=None, stdin="none", feed=None, patterns=(),
                   remote=False):
    """What a database client stage does: ``Finding(destroy, write)``.

    ``name``: the command name (basename, lower-case); ``args``: its
    arguments. ``prefix_env``: the stage's own ``VAR=value`` prefixes;
    ``line_env``: database variables exported earlier in the line (None
    values are unknown). ``stdin``: ``"none"``, ``"literal"`` (``feed`` is
    the text the client reads) or ``"unknown"``. ``remote``: the stage runs
    on another machine (``ssh host CMD``), so no database is local."""
    spec = DB_CLIENT_SPECS.get(str(name))
    if spec is None:
        return _NONE
    p = _client_args(spec, args)
    texts = list(p.sql)
    if spec["positional_sql"]:
        texts += p.positional[0 if spec["ci"] else 1:]
    unknown_in = stdin == "unknown"
    if stdin == "literal" and isinstance(feed, str):
        texts.append(feed)
    elif stdin == "literal":
        unknown_in = True
    destroy = name in SQL_CLIENTS and any(sql_destructive(x) for x in texts)
    return Finding(destroy, _client_write(spec, p, texts, unknown_in, _env(line_env, prefix_env), patterns, remote))


def _client_write(spec, p, texts, unknown_in, env, patterns, remote):
    t = _client_target(spec, p, env)
    if remote:
        t.unknown = True
    far = [h for h in t.hosts if not host_local(h)]
    if not t.unknown and not far:
        return False  # local
    if not t.unknown and all(dev_host(patterns, h) for h in far):
        return False  # every host is a dev host
    if spec["restore"]:
        return bool(p.conn)  # pg_restore -d writes; without -d it prints a script
    if p.file:
        return True  # a script file: unknown content
    if spec["mongo"] and any(re.search(r"\.js$", a, re.I) for a in p.positional):
        return True
    if spec["redis"]:
        return (unknown_in or any(o in ("-x", "--pipe") for o in p.other) or redis_writes(p.positional)
                or any(redis_writes(line.split()) for x in texts for line in re.split(r"\r?\n", x)))
    if unknown_in and not p.sql:
        return True  # a redirect, heredoc or pipe: unknown content (-c / -e ignore stdin)
    if spec["mongo"]:
        return any(mongo_writes(x) for x in texts)
    return any(sql_writes(x) for x in texts)


# ---------------------------------------------------------------------------
# ORM / migration CLIs
# ---------------------------------------------------------------------------
_DB_WRAPPERS = frozenset({"npx", "pnpx", "bunx", "pnpm", "yarn", "npm", "bun", "dlx", "exec"})
_CLI_VALUE = {"prisma": frozenset({"--schema"}),
              "supabase": frozenset({"--workdir", "--project-ref", "--profile", "--password", "-p"})}


def _tool_base(word):
    b = re.split(r"[\\/]", str(word))[-1].lower()
    b = b[:-4] if b.endswith(".exe") else b
    return re.sub(r"@[^@]*$", "", b) if not b.startswith("@") else b  # prisma@5 -> prisma


def _cli_target(name, args):
    """(tool, host) when a prisma / supabase command writes a database that
    is not known to be local, else None (forge ``dbCliWrite``)."""
    cmd, args = _tool_base(name), [str(a) for a in args]
    if cmd in _DB_WRAPPERS:
        idx = next((k for k, a in enumerate(args) if _tool_base(a) in ("prisma", "supabase")), None)
        if idx is None:
            return None
        cmd, args = _tool_base(args[idx]), args[idx + 1:]
    if cmd not in ("prisma", "supabase"):
        return None
    pos, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            skip = a in _CLI_VALUE[cmd]
            continue
        pos.append(a)
    sub = f"{pos[0] if pos else ''} {pos[1] if len(pos) > 1 else ''}"
    # prisma reads its database from the schema or .env: never known to be dev. "migrate dev" uses a dev database.
    if cmd == "prisma":
        return ("prisma", None) if sub in ("db push", "db execute", "migrate deploy", "migrate resolve",
                                           "migrate reset") else None
    db_url = None
    for k, a in enumerate(args):
        if a == "--db-url":
            db_url = args[k + 1] if k + 1 < len(args) else ""
        elif a.startswith("--db-url="):
            db_url = a[9:]
    url_h = None if db_url is None else (_url_host(db_url) if _url_host(db_url) is not None else db_url)
    remote_url = db_url is not None and not host_local(url_h)
    local_only = "--local" in args or (db_url is not None and not remote_url)
    to_remote = "--linked" in args or remote_url
    held = ("supabase", url_h if remote_url and "--linked" not in args else None)
    if sub in ("db push", "migration repair"):  # remote unless told otherwise
        return None if local_only else held
    if sub in ("db reset", "migration up"):  # local unless told otherwise
        return held if to_remote else None
    return None


def cli_write(name, args, patterns=()):
    """prisma db push|db execute|migrate deploy|migrate resolve|migrate reset,
    supabase db push|migration repair (remote unless --local), supabase db
    reset|migration up with --linked or a remote --db-url; also through
    npx/pnpx/bunx/pnpm/yarn/npm/bun (dlx/exec). A --db-url host that is a
    dev host is not a write."""
    hit = _cli_target(name, args)
    if hit is None:
        return False
    host = hit[1]
    return not (host and dev_host(patterns, host))


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------
DB_SERVER = re.compile(
    r"supabase|postgres|pgsql|mysql|maria|sqlite|mongo|database|neon|planetscale|turso|cockroach|clickhouse|duckdb|"
    r"snowflake|bigquery|dynamo|redis|valkey|firebase|firestore|prisma|cassandra|oracle|mssql|sqlserver|libsql|"
    r"upstash|xata|convex|(^|[^a-z])(db|sql|pg)([^a-z]|$)", re.I)
_TOOL_WRITE = frozenset((
    "insert update delete del drop put set write remove upsert truncate rename replace bulk flush flushall "
    "flushdb incr incrby incrbyfloat decr decrby expire expireat pexpire persist lpush rpush lpushx rpushx lpop "
    "rpop lset ltrim lrem linsert sadd srem spop smove zadd zrem zincrby zpopmin zpopmax hset hsetnx hmset hdel "
    "hincrby mset msetnx setex setnx psetex append getdel getset xadd xdel xtrim unlink restore reset rebase merge "
    "migrate apply modify import load alter grant revoke push pop save patch add").split())
_DB_OBJECTS = frozenset(("table tables collection collections index indexes database schema view user role "
                         "function procedure trigger type extension sequence policy keyspace").split())
_RUN_WORDS = frozenset("sql query execute exec run command statement eval script raw cypher".split())
_STRONG_RUN = frozenset("sql execute exec run command statement eval script raw".split())
_SQL_FIELD = re.compile(r"^(sql|query|queries|statement|statements|command|commands|script|q|eval|code|cypher)$",
                        re.I)
_ENVIRONMENTS = ("dev", "prod", "unknown")


def _tool_words(name):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return [w for w in re.split(r"[^a-z0-9.]+", s) if w]


def _field_texts(v, out=None, depth=0):
    out = [] if out is None else out
    if depth > 6:
        return out
    items = v.items() if isinstance(v, dict) else (enumerate(v) if isinstance(v, list) else ())
    for k, x in items:
        named = isinstance(k, str) and _SQL_FIELD.match(k)
        if named and isinstance(x, str):
            out.append(x)
        elif named and isinstance(x, list):
            out += [y for y in x if isinstance(y, str)]
        elif isinstance(x, (dict, list)):
            _field_texts(x, out, depth + 1)
    return out


def _server_info(servers, server):
    if not isinstance(servers, dict):
        return {}
    info = servers.get(server.lower())
    return info if isinstance(info, dict) else {}


def mcp_finding(tool_name, tool_input, servers=None, patterns=()):
    """``Finding(destroy, write)`` for an ``mcp__<server>__<tool>`` call.
    ``servers``: policy ``mcp_servers`` ({lower-cased server prefix: {name,
    environment}})."""
    m = re.match(r"^mcp__(.+?)__(.+)$", str(tool_name).strip(), re.S)
    if not m:
        return _NONE
    server, tool = m.group(1), m.group(2)
    info = _server_info(servers, server)
    env = info.get("environment")
    env = env.strip().lower() if isinstance(env, str) else None
    env = env if env in _ENVIRONMENTS else None
    label = info.get("name") if isinstance(info.get("name"), str) else ""
    migration = tool.lower().replace("-", "_") == "apply_migration"
    is_db = env in ("dev", "prod") or bool(DB_SERVER.search(server)) or bool(label and DB_SERVER.search(label))
    if not is_db and not migration:
        return _NONE
    ti = tool_input if isinstance(tool_input, (dict, list)) else {}
    texts = _field_texts(ti)
    destroy = any(sql_destructive(x) for x in texts)
    write = False
    if env != "dev" and not dev_server(patterns, server):
        write = _mcp_write(server, _tool_words(tool), ti, texts)
    return Finding(destroy, write)


def _mcp_write(server, words, ti, texts):
    if any(w in _TOOL_WRITE for w in words):
        return True
    if "create" in words and any(w in _DB_OBJECTS for w in words):
        return True
    try:
        text = json.dumps(ti, default=str)
    except (TypeError, ValueError, RecursionError):
        text = ""
    if re.search(r'"\$(out|merge)"\s*:', text):
        return True
    if not any(w in _RUN_WORDS for w in words):
        return False
    if not texts:
        return any(w in _STRONG_RUN for w in words)  # it runs something we cannot see: held
    if re.search(r"redis|valkey|upstash", server, re.I):
        return any(redis_writes(x.split()) for x in texts)
    if re.search(r"mongo", server, re.I):
        return any(mongo_writes(x) for x in texts) or bool(
            re.search(r'"(insert|update|delete|drop\w*|findAndModify|create\w*|renameCollection)"\s*:', text))
    return any(sql_writes(x) for x in texts)
