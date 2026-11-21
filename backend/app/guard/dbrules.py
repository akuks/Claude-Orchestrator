"""co-guard database rules: ``sql-destroy`` and ``db-write``.

Python port of the database guard in jspark-forge ``scripts/lib/blocklist.js``
(MIT, same owner): sqlStrip, stmtWrites, READ_PRAGMA, META_WRITE, the client
specs, clientArgs, clientTarget, connInfo, noteDbEnv, literalFeed, dbCliWrite,
DB_SERVER and mcpDbWrite (inventory F32/F33), hardened in Task 1 fix round 1
(Rulings P4-P7). Nothing is ever executed or connected to.

RULING R1: stdlib only, no app imports. This module is copied next to
``rules.py`` and ``shell.py`` into the co-guard plugin and loaded like
``shell.py`` (package import or sibling file).

``sql-destroy`` (DROP TABLE/DATABASE/SCHEMA/OWNED/EXTENSION/TYPE/MATERIALIZED
VIEW, any DROP ... CASCADE, TRUNCATE, a DELETE without its own WHERE) is read
from the SQL a client actually receives: ``-c``/``-e``/``--query``/``--eval``
values, positional SQL (sqlite3/duckdb), literal text piped in, and literal
heredoc/here-string bodies (a quoted heredoc, or one with no ``$`` or
backtick). It ignores the target, so it fires on local databases too. Ruling
P4 backstops: the bodies of strings and dollar quotes after DO / AS /
EXECUTE, and every literal of a text that uses ``\\gexec``, are scanned as SQL
too. A plain EXPLAIN runs nothing.

SQL is lexed the way the client's dialect lexes it, and (Ruling P7) where the
dialect leaves room - backslash escapes, ``$`` as a plain character, ``#``
comments, nested comments - several lexings are tried and their results OR-ed.
An unterminated quote, dollar tag or comment is unknown SQL, so a write. pg:
dollar quotes (only where ``$`` does not continue an identifier), ``E''``
backslash escapes, nested comments, plus a ``standard_conforming_strings=off``
lexing. mysql: no dollar quotes, backslash escapes in ``'``/``"`` strings (and
a ``NO_BACKSLASH_ESCAPES`` lexing), ``#`` and ``-- `` comments, ``/*! */``
runs. Anything else (other clients, MCP servers of unknown dialect): every
lexing.

``db-write`` is any statement that is not a read, against a target that is
neither local nor dev. Unknown SQL (a script file, a stdin redirect, a pipe
from a non-literal stage, an unterminated literal) is a write. Local:
localhost, 127.x, ::1, 0.0.0.0, ``*.localhost``, a Unix socket directory (not
``/cloudsql/``), a file database. Unknown means production: a variable,
``service=``/``PGSERVICE``, ``--login-path``/``--defaults-file``, a ``\\c`` /
``\\connect`` / mysql ``connect``/``\\r`` inside the SQL, a duckdb ATTACH of a
non-file, a command run on another machine (``ssh host CMD``), and (Ruling P7)
any option the parser does not know - an unknown option letter or an
ambiguous long-option prefix also makes every positional count as SQL.
Options are parsed getopt-style: short clusters (``-Atc SQL``), attached
values (``-hprod``, ``-dpostgres://x``) and unambiguous long prefixes
(``--ho=prod``).

Shell escapes inside a client (psql ``\\!``, ``\\o|\\g|\\w |cmd``, ``-o |cmd``,
``\\copy``/``COPY ... PROGRAM 'cmd'``, sqlite/duckdb ``.shell``/``.system``/
``.once |cmd``, mysql ``system``/``\\!``/``pager``/``--pager``) are returned
in ``Finding.shell`` for the caller to check as Bash commands, and count as a
write on a target that is not local.

Dev patterns (forge ``database dev:`` syntax): ``host:<glob>`` (database
hosts, ``*`` only), ``mcp:<server>`` or ``mcp:<prefix>*`` (MCP servers), or
a bare exact host or server name. A bare name with ``*`` and a pattern that is
only wildcards are rejected by :func:`check_dev_pattern` and ignored if they
reach a policy (Ruling P5).

MCP: a server is a database when its ``environment`` is set (``dev`` or
``prod``) or its prefix or name matches ``DB_SERVER``. ``db-write`` follows
forge ``mcpDbWrite`` and is skipped for ``dev`` servers and ``mcp:`` dev
patterns - an explicit ``prod`` beats any dev pattern (Ruling P5).
``sql-destroy`` checks the SQL fields of database servers' inputs in every
environment, and (Ruling P4) the sql/statement/query fields of any tool whose
name says it runs SQL (sql, execute, statement, run, raw, query; not search).

Seatbelt limits (co-guard sees command text, not what runs)
---------------------------------------------------------
* Clients run inside another program: ``docker exec|run``, ``docker compose
  exec``, ``kubectl exec``, ``podman exec``, ``ssh`` with a script on disk,
  ``make``/``npm run``/scripts that call a client.
* SQL the server runs on its own: functions, procedures and triggers that
  already exist, ``dblink``/``dblink_exec``/foreign tables (a write through
  them to another server counts against the target named on the command
  line), ``PREPARE``d statements run later, ``pg_cron`` jobs.
* SQL built at run time: ``format()``/string concatenation outside
  ``\\gexec``, ``psql`` variables (``:x``), shell variables in SQL text
  (``drop $T x`` is a write when not local, never a destroy).
* MongoDB JavaScript written to hide a method name (``eval``, string
  concatenation, computed property names, ``Function``), beyond the bracket
  and ``.call``/``.apply`` forms that are caught.
* Files a client reads (``-f``, ``\\i``, ``source``, ``.read``) are never
  opened: they are unknown SQL (a write when not local, never a destroy).
* A dev pattern relaxes whatever host name the command names (DNS is not
  resolved), and env vars an existing script or Makefile reads are not seen.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass

__all__ = [
    "Finding", "SQL_CLIENTS", "DB_CLIENT_SPECS", "DB_ENV", "DB_ENV_NAMES", "sql_strip", "sql_destructive",
    "sql_writes", "redis_writes", "mongo_writes", "host_local", "dev_host", "dev_server", "check_dev_pattern",
    "client_finding", "cli_write", "mcp_finding", "db_env_name", "shell_escapes",
]

_F = re.I | re.A


@dataclass(frozen=True)
class Finding:
    destroy: bool = False
    write: bool = False
    shell: tuple = ()  # shell commands the client would run (escapes), for the caller to check


_NONE = Finding()

# ---------------------------------------------------------------------------
# Lexing
# ---------------------------------------------------------------------------
_DOLLAR = re.compile(r"\$([^\W\d]\w*)?\$")


def _ident_char(c):
    return c.isalnum() or c in "_$"


class _Lexed:
    __slots__ = ("out", "kept", "ok", "bodies")

    def __init__(self, out, kept, ok, bodies):
        self.out = out  # same length as the input; literals and comments blanked
        self.kept = kept  # same, but quoted identifiers keep their text ("setval" -> setval)
        self.ok = ok  # False: an unterminated quote, dollar tag or comment
        self.bodies = bodies  # [(start, end, content)] of string and dollar-quote literals


def _lex(s, dollar=True, bs="e", hash_=False, nest=False, dash_space=False, dq_string=False):
    """One pass. bs: "none" | "e" (only E'' strings) | "single" (every '' string)
    | "all" ('' and "" strings). dq_string: "..." is a string (mysql), not an
    identifier."""
    n = len(s)
    out, kept = list(s), list(s)
    bodies = []
    ok = True
    exec_open = 0  # inside /*! ... */ (MySQL runs it)

    def blank(a, b, keep_ends=None):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
                kept[k] = " "
        if keep_ends:
            out[a] = kept[a] = keep_ends
            if b - 1 > a:
                out[b - 1] = kept[b - 1] = keep_ends

    i = 0
    while i < n:
        c = s[i]
        nx = s[i + 1] if i + 1 < n else ""
        if c == "-" and nx == "-" and (not dash_space or i + 2 >= n or s[i + 2] in " \t\r\n\f\v"):
            k = s.find("\n", i)
            k = n if k < 0 else k
            blank(i, k)
            i = k
            continue
        if c == "#" and hash_:
            k = s.find("\n", i)
            k = n if k < 0 else k
            blank(i, k)
            i = k
            continue
        if c == "*" and nx == "/" and exec_open:
            blank(i, i + 2)
            exec_open -= 1
            i += 2
            continue
        if c == "/" and nx == "*":
            if i + 2 < n and s[i + 2] == "!":
                j = i + 3
                while j < n and "0" <= s[j] <= "9":
                    j += 1
                blank(i, j)
                exec_open += 1
                i = j
                continue
            depth, j = 1, i + 2
            while j < n and depth:
                if nest and s.startswith("/*", j):
                    depth += 1
                    j += 2
                elif s.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                ok = False
            blank(i, j)
            i = j
            continue
        if c == "'" or (c == '"' and dq_string):
            esc = bs in ("all", "single") if c == "'" else bs == "all"
            if c == "'" and bs == "e" and i > 0 and s[i - 1] in "eE" and (i < 2 or not _ident_char(s[i - 2])):
                esc = True
            j, buf = i + 1, []
            closed = False
            while j < n:
                ch = s[j]
                if esc and ch == "\\" and j + 1 < n:
                    buf.append(s[j + 1])
                    j += 2
                    continue
                if ch == c:
                    if j + 1 < n and s[j + 1] == c:
                        buf.append(c)
                        j += 2
                        continue
                    closed = True
                    break
                buf.append(ch)
                j += 1
            end = j + 1 if closed else n
            if not closed:
                ok = False
            bodies.append((i, end, "".join(buf)))
            blank(i, end, "'")
            i = end
            continue
        if c in ('"', "`"):
            k = i + 1
            while True:
                k = s.find(c, k)
                if k < 0 or k + 1 >= n or s[k + 1] != c:
                    break
                k += 2
            end = n if k < 0 else k + 1
            if k < 0:
                ok = False
            content = s[i + 1:end - 1].replace(c + c, c)
            blank(i, end, '"')
            for off, ch in enumerate(content[: max(0, end - i - 2)]):
                kept[i + 1 + off] = ch if ch != "\n" else "\n"
            kept[i] = kept[end - 1] = " "
            i = end
            continue
        if c == "$" and dollar and (i == 0 or not _ident_char(s[i - 1])):
            m = _DOLLAR.match(s, i)
            if m:
                tag = m.group(0)
                k = s.find(tag, i + len(tag))
                end = n if k < 0 else k + len(tag)
                if k < 0:
                    ok = False
                bodies.append((i, end, s[i + len(tag):(k if k >= 0 else n)]))
                blank(i, end, "'")
                i = end
                continue
        i += 1
    if exec_open:
        ok = False
    return _Lexed("".join(out), "".join(kept), ok, bodies)


_PG_STD = dict(dollar=True, bs="e", nest=True)
_LEXINGS = {
    "pg": (_PG_STD, dict(dollar=True, bs="single", nest=True)),
    "mysql": (dict(dollar=False, bs="all", hash_=True, dash_space=True, dq_string=True),
              dict(dollar=False, bs="none", hash_=True, dash_space=True, dq_string=True)),
    None: (dict(dollar=True, bs="e"), dict(dollar=True, bs="all"), dict(dollar=False, bs="none"),
           dict(dollar=True, bs="e", hash_=True)),
}


@functools.lru_cache(maxsize=64)
def _lexings_of(s, dialect):
    return tuple(_lex(s, **kw) for kw in _LEXINGS.get(dialect, _LEXINGS[None]))


def _lexings(text, dialect):
    return _lexings_of(str(text), dialect if dialect in _LEXINGS else None)


def sql_strip(text, dialect="pg"):
    """SQL with string literals, quoted identifiers and comments blanked out
    (same length as the input). MySQL runs ``/*! ... */``, so its text is kept."""
    return _lexings(text, dialect)[0].out


def _spans(out):
    start = 0
    for k, ch in enumerate(out):
        if ch == ";":
            yield start, k
            start = k + 1
    yield start, len(out)


# ---------------------------------------------------------------------------
# sql-destroy
# ---------------------------------------------------------------------------
_DROP = re.compile(r"\bdrop\s+(table|database|schema|owned|extension|type|materialized\s+view)\b", _F)
_DROP_WORD = re.compile(r"\bdrop\b", _F)
_CASCADE = re.compile(r"\bcascade\b", _F)
_TRUNCATE = re.compile(r"\btruncate\s+(table\s+)?[\"'`\w]", _F)
_NOT_DELETE_BEFORE = re.compile(r"\b(on|for|after|before|of|or|grant|revoke|instead)\s*$|,\s*$", _F)
_NOT_DELETE_AFTER = re.compile(r"\s*(on\b|,|\)|$)", _F)
_EXPLAIN = re.compile(r"^[\s(]*explain\b", _F)
_ANALYZE = re.compile(r"\banaly[sz]e\b", _F)
_BODY_AFTER = re.compile(r"(\b(do|as|execute)|\\set\s+\w+|\bdblink(_exec)?\s*\([^()]*)\s*$", _F)
_GEXEC = re.compile(r"\\gexec(?![a-z_])", _F)


_DELETE_TOKENS = re.compile(r"[()]|\b(delete|where|returning)\b", _F)


def _unbounded_delete(stmt):
    """Some DELETE has no WHERE of its own (at its own paren depth, before its
    RETURNING or the end of its group). One pass."""
    depth, pending = 0, {}
    for m in _DELETE_TOKENS.finditer(stmt):
        tok = m.group(0).lower()
        if tok == "(":
            depth += 1
        elif tok == ")":
            if pending.pop(depth, 0):
                return True
            depth = max(0, depth - 1)
        elif tok == "delete":
            if _NOT_DELETE_BEFORE.search(stmt[max(0, m.start() - 40):m.start()]) or \
                    _NOT_DELETE_AFTER.match(stmt, m.end()):
                continue  # ON DELETE CASCADE, GRANT DELETE, AFTER DELETE trigger ...
            pending[depth] = pending.get(depth, 0) + 1
        elif tok == "where":
            pending.pop(depth, None)
        elif tok == "returning" and pending.get(depth):
            return True
    return any(pending.values())


def _stmt_destructive(stmt):
    if _EXPLAIN.match(stmt) and not _ANALYZE.search(stmt):
        return False  # plain EXPLAIN runs nothing
    if _DROP.search(stmt) or _TRUNCATE.search(stmt):
        return True
    m = _DROP_WORD.search(stmt)
    if m and _CASCADE.search(stmt, m.end()):
        return True
    return _unbounded_delete(stmt)


def _destructive(text, dialect, depth):
    if depth > 4:
        return False
    for lx in _lexings(text, dialect):
        if any(_stmt_destructive(lx.out[a:b]) for a, b in _spans(lx.out)):
            return True
        gexec = bool(_GEXEC.search(lx.out))
        for start, _end, content in lx.bodies:  # Ruling P4 backstops
            if (gexec or _BODY_AFTER.search(lx.out[max(0, start - 40):start])) and \
                    _destructive(content, dialect, depth + 1):
                return True
    return False


def sql_destructive(text, dialect=None):
    """DROP TABLE/DATABASE/SCHEMA/..., DROP ... CASCADE, TRUNCATE, or a DELETE
    without its own WHERE, in any lexing of ``text`` (Rulings P4, P7)."""
    return _destructive(str(text), dialect, 0)


# ---------------------------------------------------------------------------
# sql_writes
# ---------------------------------------------------------------------------
# Words that make a read statement (SELECT, WITH, VALUES, EXPLAIN ANALYZE) change data: SELECT ... INTO, a
# data-modifying CTE, a second statement without a semicolon (T-SQL), and functions with side effects.
_SQL_WRITE_WORD = re.compile(
    r"\b(insert|update|delete|merge|upsert|into|create|alter|drop|truncate|grant|revoke|exec|execute|call)\b", _F)
_SQL_SIDE_EFFECT = re.compile(
    r"\b(setval|nextval|set_config|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|"
    r"pg_switch_wal|pg_promote|pg_drop_replication_slot|pg_create_\w+|pg_file_\w+|pg_logical_emit_message|"
    r"lo_unlink|lo_import|lo_export|lo_create|lo_from_bytea|lo_put|lo_truncate|query_to_xml\w*|"
    r"cursor_to_xml\w*|dblink\w*|cron\.\w+)\s*\(", _F)
_SQL_READ_FIRST = frozenset({"show", "describe", "desc", "help", "use", "begin", "commit", "rollback", "end",
                             "savepoint", "release", "abort", "listen", "unlisten", "fetch", "close", "discard"})
_READ_PRAGMA = re.compile(
    r"^pragma\s+(\w+\.)?(table_info|table_xinfo|table_list|index_list|index_info|index_xinfo|foreign_key_list|"
    r"foreign_key_check|database_list|compile_options|collation_list|function_list|module_list|pragma_list|"
    r"integrity_check|quick_check|user_version|schema_version|application_id|page_count|page_size|"
    r"freelist_count|data_version|journal_mode|encoding|foreign_keys)\b[^=]*$", _F)
# psql / mysql meta-commands that run a file, a shell, generated SQL, or write their output to a pipe.
_META_WRITE = re.compile(
    r"\\(gexec|ir?|include(_relative)?|!|\.|lo_import|lo_unlink|setenv|(o|out|g|gx|w|write)\s*\|)(?![a-z_])", _F)
_LEAD = re.compile(r"^[\s(]+")
_FIRST = re.compile(r"^[a-z_]+", _F)
_FOR_LOCK = re.compile(r"\bfor\s+(no\s+key\s+)?(update|share)\b", _F)


def _stmt_writes(stmt, kept=""):
    t = _LEAD.sub("", stmt)
    if not t:
        return False
    if _META_WRITE.search(t) or _SQL_SIDE_EFFECT.search(kept):
        return True
    if t[0] in "\\.":  # a meta / dot-command runs to the end of its line; SQL may follow on the next
        line, _nl, rest = t.partition("\n")
        if t[0] == "\\":
            meta = bool(re.match(r"\\copy\b", line, _F)) and (
                not re.search(r"\bto\b", line, _F) or bool(re.search(r"\bfrom\s+(stdin|pstdin|'')", line, _F))
                or bool(re.search(r"\bprogram\b", line, _F)))
        else:  # sqlite / duckdb dot-commands
            meta = bool(re.match(r"\.(read|import|restore|load|shell|system|clone|once|output|excel|save)\b", line, _F))
        return meta or _stmt_writes(rest)
    m = _FIRST.match(t)
    if not m:
        return True  # not a statement we can read ("$SQL"): unknown, so held
    k = m.group(0).lower()
    body = _FOR_LOCK.sub(" ", t)
    if k in ("select", "with", "values", "table"):
        return bool(_SQL_WRITE_WORD.search(body) or _SQL_SIDE_EFFECT.search(body))
    if k == "explain":  # plain EXPLAIN runs nothing
        return bool(_ANALYZE.search(t)) and bool(_SQL_WRITE_WORD.search(body) or _SQL_SIDE_EFFECT.search(body))
    if k in _SQL_READ_FIRST:
        return False
    if k == "start":
        return not re.match(r"start\s+transaction\b", t, _F)
    if k == "set":
        return bool(re.match(r"set\s+(global|persist|persist_only|password|@@global|@@persist)\b", t, _F))
    if k == "pragma":
        return not _READ_PRAGMA.match(t)
    if k == "copy":
        return (not re.search(r"\bto\s+stdout\b", t, _F) or bool(re.search(r"\bfrom\s+(stdin|program)\b", t, _F))
                or bool(re.search(r"\bprogram\b", t, _F)))
    return True


def sql_writes(text, dialect=None):
    """Any statement that is not a read, in any lexing of ``text``; an
    unterminated literal is unknown, so a write (Ruling P7)."""
    for lx in _lexings(text, dialect):
        if not lx.ok:
            return True
        if any(_stmt_writes(lx.out[a:b], lx.kept[a:b]) for a, b in _spans(lx.out)):
            return True
    return False


# ---------------------------------------------------------------------------
# Shell escapes and connection changes inside client SQL
# ---------------------------------------------------------------------------
_PG_BANG = re.compile(r"\\!", re.A)
_PG_PIPE = re.compile(r"\\(o|out|g|gx|w|write)[ \t]*\|", _F)
_PROGRAM = re.compile(r"\bprogram\s*$", _F)


def _rest_of_line(raw, k):
    e = raw.find("\n", k)
    return raw[k:] if e < 0 else raw[k:e]


def shell_escapes(text, family):
    """Shell commands a client runs from its SQL text: family "pg", "mysql"
    or "lite"."""
    raw = str(text)
    lx = _lexings(raw, "mysql" if family == "mysql" else "pg")[0]
    out, cmds = lx.out, []
    if family == "pg":
        for m in _PG_BANG.finditer(out):
            cmds.append(_rest_of_line(raw, m.end()))
        for m in _PG_PIPE.finditer(out):
            cmds.append(_rest_of_line(raw, m.end()))
    if family in ("pg", None):
        for start, _end, content in lx.bodies:
            if _PROGRAM.search(out[max(0, start - 20):start]):
                cmds.append(content)
    if family == "mysql":
        for a, b in _spans(out):
            st = out[a:b]
            for m in re.finditer(r"(?:^|\n)[ \t(]*(system|pager|\\!|\\P)(?=[ \t]|$)", st, re.I):
                cmds.append(_rest_of_line(raw, a + m.end()))
    if family == "lite":
        for m in re.finditer(r"(?m)^[ \t]*\.(shell|system)\b", out):
            cmds.append(_rest_of_line(raw, m.end()))
        for m in re.finditer(r"(?m)^[ \t]*\.(once|output|excel)\b[ \t]*['\"]?[ \t]*\|", raw):
            cmds.append(_rest_of_line(raw, m.end()).rstrip().rstrip("'\""))
    return [c.strip() for c in cmds if c.strip()]


def _retargets(text, family):
    """The SQL text connects somewhere else: psql ``\\c``/``\\connect``, mysql
    ``connect``/``\\r``, duckdb ATTACH of a non-file."""
    raw = str(text)
    lx = _lexings(raw, "mysql" if family == "mysql" else "pg")[0]
    out = lx.out
    if family == "pg" and (re.search(r"\\c(onnect)?[ \t]+\S", out) or re.search(r"\bdblink\w*\s*\(", out, re.I)):
        return True  # \c otherdb, dblink('host=prod', ...): another server
    if family == "mysql" and re.search(r"(?:^|[;\n])[ \t]*(connect\b|\\r\b)", out, re.I):
        return True
    if family == "duckdb":
        for start, _end, content in lx.bodies:
            if re.search(r"\battach\s+(database\s+)?(if\s+not\s+exists\s+)?$", out[max(0, start - 40):start], re.I) \
                    and ":" in content and not re.match(r"[A-Za-z]:[\\/]", content):
                return True
    return False


# ---------------------------------------------------------------------------
# MongoDB / Redis
# ---------------------------------------------------------------------------
_MONGO_METHODS = (r"insert\w*|update\w*|delete\w*|drop\w*|remove\w*|replaceOne|bulkWrite|save|create\w*|"
                  r"rename\w*|findOneAnd\w+|findAndModify|mapReduce|runCommand|adminCommand|grant\w*|revoke\w*|"
                  r"shardCollection|compact|initialize\w*BulkOp")
_MONGO_WRITE = re.compile(
    r"\.\s*(" + _MONGO_METHODS + r")\s*(\(|\.\s*(call|apply|bind)\b)"
    r"|\[\s*['\"`]\s*(" + _MONGO_METHODS + r")\s*['\"`]\s*\]"
    r"|\$(out|merge)\b|\b(load|eval|Function)\s*\(", re.A)


def mongo_writes(text):
    return bool(_MONGO_WRITE.search(str(text)))


_REDIS_READ = frozenset((
    "get mget hget hgetall hmget hkeys hvals hlen hexists hstrlen hscan keys scan sscan zscan exists ttl pttl "
    "type info ping dbsize lrange llen lindex smembers sismember smismember scard srandmember zrange zrangebyscore "
    "zrevrange zrevrangebyscore zrangebylex zcard zscore zmscore zrank zrevrank zcount zlexcount strlen getrange "
    "getbit bitcount bitpos xrange xrevrange xlen xinfo time echo lastsave role randomkey pfcount geopos geodist "
    "geohash geosearch json.get json.type json.mget ft.search ft.info memory subscribe psubscribe ssubscribe "
    "unsubscribe punsubscribe sunsubscribe monitor").split())
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


_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/?]*@)?(\[[^\]]+\]|[^/?]*)([^?]*)(\?.*)?$", re.I | re.S)


def _url_hosts(u):
    """Hosts a URL names (authority plus ?host= / ?hostaddr= parameters),
    or None when ``u`` is no URL. URL-decoded; ports dropped."""
    from urllib.parse import unquote
    m = _URL.match(str(u))
    if not m:
        return None
    h = m.group(1)
    hosts = [h] if h.startswith("[") else [re.sub(r":\d*$", "", x) for x in h.split(",")]
    hosts = [unquote(x) for x in hosts]
    for pm in re.finditer(r"[?&](host|hostaddr|server)=([^&#]*)", m.group(3) or "", re.I):
        hosts += [unquote(x) for x in pm.group(2).split(",")]
    return hosts


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
    if not v.replace("*", "").strip(" ."):
        return f'"{x}" matches every database; name the dev hosts or servers'
    if not kind and "*" in v:
        return f'"{x}": a pattern needs host:<pattern> (database hosts) or mcp:<prefix>* (MCP servers)'
    if kind == "mcp" and "*" in v[:-1]:
        return f'"{x}": mcp: takes an exact server name or a prefix ending in * (host: is for host patterns)'
    return None


def _patterns(patterns):
    if not isinstance(patterns, (list, tuple)):
        return []
    return [p.strip() for p in patterns if isinstance(p, str) and p.strip() and check_dev_pattern(p) is None]


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
# Client specs
# ---------------------------------------------------------------------------
def _w(text):
    return tuple(text.split())


def _spec(**kw):
    base = {"sql": (), "file": (), "host": (), "conn": (), "value": (), "unknown": (), "flags": (),
            "optval": (), "pipe": (), "pager": (), "subcmds": (), "uri_positional": False,
            "positional_sql": False, "mongo": False, "redis": False, "restore": False, "at_host": False,
            "file_db": False, "single_dash": False, "dialect": None, "family": None, "read_modes": (),
            "free_long": False}
    base.update(kw)
    return base


_PG = _spec(
    sql=_w("-c --command"), file=_w("-f --file"), host=_w("-h --host"), conn=_w("-d --dbname"),
    value=_w("-p --port -U --username -v --set --variable -L --log-file -P --pset -F --field-separator "
             "-R --record-separator -T --table-attr"),
    pipe=_w("-o --output"),
    flags=_w("-a --echo-all -A --no-align -b --echo-errors -e --echo-queries -E --echo-hidden -H --html -l --list "
             "-n --no-readline -q --quiet -s --single-step -S --single-line -t --tuples-only -w --no-password "
             "-W --password -x --expanded -X --no-psqlrc -z --field-separator-zero -0 --record-separator-zero "
             "-1 --single-transaction -V --version -? --help --csv"),
    uri_positional=True, dialect="pg", family="pg")
_PGCLI = _spec(
    host=_w("-h --host"), conn=_w("-d --dbname"), unknown=_w("-D --dsn --ssh-tunnel"),
    value=_w("-p --port -U --username --user --row-limit --prompt --prompt-dsn --log-file --pgclirc --application-name"),
    flags=_w("-W --password -w --no-password --single-connection -v --version --list -l --list-dsn --less-chatty "
             "--auto-vertical-output --warn --help --ping"),
    uri_positional=True, dialect="pg", family="pg")
_MY = _spec(
    sql=_w("-e --execute --init-command"), host=_w("-h --host"),
    unknown=_w("--login-path --defaults-file --defaults-extra-file --defaults-group-suffix"),
    value=_w("-u --user -P --port -D --database -S --socket --protocol --ssl-mode --ssl-ca --ssl-capath --ssl-cert "
             "--ssl-key --ssl-cipher --ssl-crl --ssl-crlpath --tls-version --tls-ciphersuites --default-character-set "
             "--character-sets-dir --connect-timeout --max-allowed-packet --net-buffer-length --prompt --tee "
             "--delimiter --plugin-dir --default-auth --server-public-key-path --select-limit --max-join-size "
             "--histignore --load-data-local-dir --compression-algorithms --zstd-compression-level --bind-address "
             "--dns-srv-name --network-namespace --ssl-fips-mode --oci-config-file"),
    optval=_w("-p --password -# --debug --local-infile"),
    pager=_w("--pager"),
    flags=_w("-B --batch -N --skip-column-names -s --silent -t --table -v --verbose -E --vertical -r --raw -f --force "
             "-n --unbuffered -q --quick -A --no-auto-rehash --auto-rehash -C --compress -X --xml -H --html "
             "-b --no-beep -T --debug-info -U --safe-updates --i-am-a-dummy -V --version -? -I --help -c --comments "
             "-L --line-numbers --column-names --column-type-info -G --named-commands -i --ignore-spaces -j --syslog "
             "-o --one-database -W --pipe -w --wait --binary-as-hex --binary-mode --show-warnings --sigint-ignore "
             "--no-defaults --print-defaults --ssl --reconnect --secure-auth --enable-cleartext-plugin "
             "--get-server-public-key --system-command --no-pager --no-tee"),
    dialect="mysql", family="mysql")
_MYCLI = dict(_MY, uri_positional=True,
              value=_MY["value"] + _w("-R -l --logfile --myclirc --auth-plugin"),
              flags=_MY["flags"] + _w("--csv --warn --no-warn --auto-vertical-output --list-dsn"))
_MYSQLSH = dict(_MY, file=_w("-f --file"), conn=_w("--uri"), uri_positional=True, at_host=True,
                value=_MY["value"] + _w("--schema --dbuser --log-level --save-passwords --name-cache"),
                flags=_MY["flags"] + _w("--sql --sqlx --sqlc --js --javascript --py --python --tabbed --interactive "
                                        "--quiet-start --no-wizard --mysqlx -mx --mysql -mc --json --no-name-cache"))
_CH = _spec(
    sql=_w("-q --query"), file=_w("--queries-file"), host=_w("-h --host"),
    value=_w("--port -u --user --password -d --database -f --format -C --config-file --connection --history_file"),
    flags=_w("-n --multiquery -m --multiline -t --time --stacktrace -s --secure --vertical -E --echo --progress "
             "--no-warnings --ignore-error -V --version --help"),
    subcmds=("client", "local"), free_long=True)
_COCKROACH = _spec(
    sql=_w("-e --execute"), file=_w("-f --file"), host=_w("--host"), conn=_w("--url"),
    value=_w("--port -p --user -u --database -d --certs-dir --format --set --watch --cluster-name"),
    flags=_w("--insecure --echo-sql --safe-updates --no-line-editor --debug-sql-cli --embedded --read-only"),
    subcmds=("sql", "demo"), dialect="pg", family="pg")
_SQLCMD = _spec(
    sql=_w("-Q -q"), file=_w("-i"), host=_w("-S"),
    value=_w("-U -P -d -o -s -w -h -t -l -v -f -H -a -c -m -y -Y -z -Z -K -V"),
    optval=_w("-r -p -k"),
    flags=_w("-E -b -e -I -X -x -C -N -R -W -j -u -G -? -M -A -L"))
_LITE = _spec(
    sql=_w("-cmd"), file=_w("-init"), single_dash=True, positional_sql=True, file_db=True, family="lite",
    value=_w("-separator -newline -nullvalue -vfs -maxsize -mmap -lookaside -pagecache -heap -escape -nonce"),
    flags=_w("-append -ascii -bail -batch -box -column -csv -deserialize -echo -header -noheader -html -interactive "
             "-json -line -list -markdown -memtrace -nofollow -quote -readonly -safe -stats -table -tabs -version "
             "-help -zip -multiplex -utf8 -no-rowid-in-view -unsafe-testing"))
_DUCK = dict(_LITE, sql=_w("-c -cmd -s"), file=_w("-init -f"), family="duckdb",
             value=_w("-separator -newline -nullvalue -storage-version"),
             flags=_w("-batch -bail -csv -json -jsonlines -line -list -markdown -box -column -table -header -noheader "
                      "-echo -readonly -unsigned -version -help -safe -ui -no-stdin -ascii -html -quote -tabs "
                      "-latex -interactive"))
_MONGO = _spec(
    sql=_w("--eval"), file=_w("-f --file"), host=_w("--host"), mongo=True, uri_positional=True,
    value=_w("--port -u --username -p --password --authenticationDatabase --authenticationMechanism "
             "--gssapiServiceName --tlsCAFile --tlsCertificateKeyFile --tlsCertificateKeyFilePassword --tlsCRLFile "
             "--tlsCertificateSelector --tlsDisabledProtocols --apiVersion --awsAccessKeyId --awsSecretAccessKey "
             "--awsSessionToken --awsIamSessionToken --keyVaultNamespace --kmsURL --cryptSharedLibPath "
             "--oidcFlows --oidcRedirectUri --browser --jsContext --sspiHostnameCanonicalization"),
    flags=_w("--quiet --nodb --norc --shell --tls --tlsAllowInvalidCertificates --tlsAllowInvalidHostnames "
             "--tlsFIPSMode --verbose --version --help -h --apiStrict --apiDeprecationErrors --retryWrites --ipv6 "
             "--oidcDumpTokens --oidcTrustedEndpoint --oidcNoNonce --build-info --json"))
_REDIS = _spec(
    host=_w("-h"), conn=_w("-u"), redis=True, file=_w("--eval"), unknown=_w("--cluster"),
    value=_w("-p -a -n -s -r -i -d -D -t --user --pass --sni --cacert --cacertdir --cert --key --tls-ciphers "
             "--tls-ciphersuites --pattern --count --quoted-pattern --pipe-timeout --rdb --functions-rdb "
             "--intrinsic-latency --lru-test --memkeys-samples"),
    flags=_w("-x -c -e -2 -3 --raw --no-raw --quoted-input --csv --json --quoted-json --tls --insecure "
             "--no-auth-warning --askpass --verbose --version --help --show-pushes --latency --latency-history "
             "--latency-dist --replica --scan --bigkeys --memkeys --hotkeys --keystats --stat --pipe --ldb "
             "--ldb-sync-mode"),
    read_modes=_w("--scan --bigkeys --memkeys --hotkeys --keystats --stat --latency --latency-history "
                  "--latency-dist --replica --rdb --functions-rdb --intrinsic-latency --lru-test"))
_PG_RESTORE = _spec(
    host=_w("-h --host"), conn=_w("-d --dbname"), restore=True,
    value=_w("-p --port -U --username -f --file -j --jobs -n --schema -N --exclude-schema -t --table -I --index "
             "-P --function -T --trigger -L --use-list -S --superuser --role -F --format --section "
             "--transaction-size"),
    flags=_w("-a --data-only -c --clean -C --create -e --exit-on-error -l --list -O --no-owner -s --schema-only "
             "-x --no-privileges -1 --single-transaction -v --verbose -w --no-password -W --password --if-exists "
             "--no-comments --no-tablespaces --disable-triggers --enable-row-security --no-data-for-failed-tables "
             "--no-security-labels --no-subscriptions --no-publications --strict-names "
             "--use-set-session-authorization -V --version -? --help"))

DB_CLIENT_SPECS = {
    "psql": _PG, "usql": _PG, "pgcli": _PGCLI, "pg_restore": _PG_RESTORE,
    "mysql": _MY, "mariadb": _MY, "mycli": _MYCLI, "mysqlsh": _MYSQLSH,
    "clickhouse-client": dict(_CH, subcmds=()), "clickhouse": _CH,
    "cockroach": _COCKROACH, "sqlcmd": _SQLCMD,
    "sqlite3": _LITE, "sqlite": _LITE,
    # duckdb: a file database, but "md:" / "motherduck:" or an ATTACH of a non-file is remote
    "duckdb": _DUCK,
    "mongosh": _MONGO, "mongo": _MONGO,
    "redis-cli": _REDIS, "valkey-cli": _REDIS,
}
# Clients that take SQL (sql-destroy is read from their SQL text). Forge's set plus pgcli/usql/mycli.
SQL_CLIENTS = frozenset({"psql", "pgcli", "usql", "mysql", "mariadb", "mycli", "mysqlsh", "sqlite3", "sqlite",
                         "sqlcmd", "clickhouse-client", "clickhouse", "duckdb", "cockroach"})
_GENERIC_CONN = ("--url", "--uri", "--db-url", "--connection-string", "-connectionstring")
DB_ENV_NAMES = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGDATABASE", "MYSQL_HOST", "DATABASE_URL", "MONGODB_URI",
                "POSTGRES_URL")
DB_ENV = re.compile(r"^(" + "|".join(DB_ENV_NAMES) + r")$", re.I)
_MY_PREFIXES = ("--skip-", "--enable-", "--disable-", "--loose-", "--maximum-")


def db_env_name(name):
    """The canonical (upper-case) name when ``name`` is a database env var, else None."""
    return str(name).upper() if isinstance(name, str) and DB_ENV.match(name) else None


_CATS = ("sql", "file", "host", "conn", "unknown", "pipe", "pager", "value", "optval", "flags")


def _opt_table(spec):
    table = spec.get("_table")
    if table is None:
        table = {}
        for cat in _CATS:
            for o in spec[cat]:
                table.setdefault(o, cat)
        for o in _GENERIC_CONN:
            table.setdefault(o, "conn")
        spec["_table"] = table
    return table


class _Args:
    __slots__ = ("sql", "file", "hosts", "conn", "positional", "other", "unknown", "uncertain", "shell", "modes")

    def __init__(self):
        self.sql, self.hosts, self.conn, self.positional, self.other, self.shell, self.modes = [], [], [], [], [], [], []
        self.file = self.unknown = self.uncertain = False


def _take(r, cat, opt, val):
    if cat == "sql":
        r.sql.append(val)
    elif cat == "file":
        r.file = True
    elif cat == "host":
        r.hosts.append(val)
    elif cat == "conn":
        r.conn.append(val)
    elif cat == "unknown":
        r.unknown = True
    elif cat == "pipe":
        if val.lstrip().startswith("|"):
            r.shell.append(val.lstrip()[1:])
    elif cat == "pager":
        if val:
            r.shell.append(val)


def _resolve_long(table, name, spec):
    if name in table:
        return name, table[name]
    hits = [o for o in table if o.startswith("--") and o.startswith(name)]
    if len(hits) == 1:
        return hits[0], table[hits[0]]
    if spec["family"] == "mysql" and name.startswith(_MY_PREFIXES):
        return name, "flags"
    return None, None


def _client_args(spec, args):
    r = _Args()
    table = _opt_table(spec)
    args = [str(a) for a in args]
    i, n, end_opts = 0, len(args), False

    def next_value(strict):  # strict: SQL / file names may start with "-"
        nonlocal i
        nx = args[i + 1] if i + 1 < n else None
        if nx is None or (not strict and nx.startswith("-") and len(nx) > 1):
            return None
        i += 1
        return nx

    while i < n:
        a = args[i]
        if end_opts or a == "-" or not a.startswith("-"):
            r.positional.append(a)
            i += 1
            continue
        if a == "--":
            end_opts = True
            i += 1
            continue
        if spec["single_dash"] or a.startswith("--"):
            name, eq, val = a.partition("=")
            if spec["single_dash"] and name.startswith("--"):
                name = name[1:]
            opt, cat = (name, table.get(name)) if spec["single_dash"] else _resolve_long(table, name, spec)
            if cat is None:
                if not (eq and spec["free_long"]):
                    r.uncertain = True
                r.other.append(a)
            elif cat in ("flags", "optval") or (cat == "pager" and not eq):
                r.other.append(opt)
            elif eq:
                _take(r, cat, opt, val)
            else:
                v = next_value(cat in ("sql", "file", "pipe"))
                if v is not None:
                    _take(r, cat, opt, v)
            if opt in spec["read_modes"]:
                r.modes.append(opt)
            i += 1
            continue
        # a short-option cluster, getopt style
        j = 1
        while j < len(a):
            o = "-" + a[j]
            cat = table.get(o)
            if cat is None:
                r.uncertain = True
                r.other.append(a)
                break
            if cat == "flags":
                r.other.append(o)
                j += 1
                continue
            rest = a[j + 1:]
            if cat == "optval":
                r.other.append(o)
                break
            if rest:
                _take(r, cat, o, rest)
            else:
                v = next_value(cat in ("sql", "file", "pipe"))
                if v is not None:
                    _take(r, cat, o, v)
            break
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
    parts = str(v).split(",")
    for k, part in enumerate(parts):
        if not (k and re.fullmatch(r"\s*\d+\s*", part)):  # "host,1433": a port ("-h 0" is a host)
            t.hosts.append(part)


_CONN_KEY = re.compile(
    r"(?:^|[\s;])(host|hostaddr|server|data source|address|addr)\s*=\s*"
    r"(?:'((?:[^'\\]|\\.)*)'|\"([^\"]*)\"|([^\s;'\"]+)|(?=[\s;]|$))", re.I)


def _conn_info(t, v, spec, as_uri):
    """A connection string, URL or conninfo ("host=db dbname=x",
    "Server=db;...", "service=prod"), or a database name."""
    s = str(v)
    if re.match(r"(md|motherduck):", s, re.I):
        t.hosts.append("motherduck.com")
        return
    hs = _url_hosts(s)
    if hs is not None:
        for h in hs:
            _add_hosts(t, h)
        return
    found = False
    if re.search(r"(^|[\s;])service\s*=", s, re.I):
        t.unknown = True
        found = True
    for m in _CONN_KEY.finditer(s):
        val = next((g for g in m.groups()[1:] if g is not None), None)
        if val is None:
            t.unknown = True
        else:
            _add_hosts(t, val.replace("\\'", "'"))
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


def _client_target(spec, p, env, extra_conn=()):
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
                hs = _url_hosts(v)
                for h in hs or ():
                    _add_hosts(t, h)
    for h in p.hosts:
        _add_hosts(t, h)
    for c in list(p.conn) + list(extra_conn):
        _conn_info(t, c, spec, True)
    if spec["redis"]:
        cands = []
    elif spec["positional_sql"]:
        cands = p.positional[:1]
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
                   remote=False, piped=False):
    """What a database client stage does: ``Finding(destroy, write, shell)``.

    ``name``: the command name (basename, lower-case); ``args``: its
    arguments. ``prefix_env``: the stage's own ``VAR=value`` prefixes;
    ``line_env``: database variables set earlier in the line or inherited
    (None values are unknown). ``stdin``: ``"none"``, ``"literal"`` (``feed``
    is the text the client reads) or ``"unknown"``. ``remote``: the stage runs
    on another machine (``ssh host CMD``), so no database is local.
    ``piped``: some ``|`` feeds the line the client is on, maybe through a
    compound command the parser flattened - with no inline SQL, its stdin is
    unknown."""
    spec = DB_CLIENT_SPECS.get(str(name))
    if spec is None:
        return _NONE
    p = _client_args(spec, args)
    if spec["subcmds"]:
        if not p.positional or p.positional[0].lower() not in spec["subcmds"]:
            return _NONE  # cockroach start, clickhouse server ...: no client
        sub = p.positional.pop(0).lower()
        if sub == "local":
            spec = dict(spec, file_db=True)
    texts = list(p.sql)
    if spec["positional_sql"]:
        texts += p.positional[1:]
    if p.uncertain:  # Ruling P7: an option we do not know: every positional may be SQL
        texts += [x for x in p.positional if x not in texts]
    unknown_in = stdin == "unknown"
    if stdin == "literal" and isinstance(feed, str):
        texts.append(feed)
    elif stdin == "literal":
        unknown_in = True
    inline = bool(p.sql) or (spec["positional_sql"] and len(p.positional) > 1) or (
        spec["redis"] and bool(p.positional))
    if piped and stdin == "none" and not inline and not p.modes:
        unknown_in = True
    family, dialect = spec["family"], spec["dialect"]
    is_sql = name in SQL_CLIENTS
    shell = list(p.shell)
    retarget = p.uncertain
    if is_sql:
        for x in texts:
            shell += shell_escapes(x, "lite" if family in ("lite", "duckdb") else family)
            retarget = retarget or _retargets(x, family)
    destroy = is_sql and any(sql_destructive(x, dialect) for x in texts)
    write = _client_write(spec, p, texts, unknown_in, _env(line_env, prefix_env), patterns, remote or retarget,
                          bool(shell))
    return Finding(destroy, write, tuple(shell))


def _client_write(spec, p, texts, unknown_in, env, patterns, unknown_target, escapes):
    t = _client_target(spec, p, env)
    if unknown_target:
        t.unknown = True
    far = [h for h in t.hosts if not host_local(h)]
    if not t.unknown and not far:
        return False  # local
    if not t.unknown and all(dev_host(patterns, h) for h in far):
        return False  # every host is a dev host
    if escapes:
        return True
    if spec["restore"]:
        return bool(p.conn)  # pg_restore -d writes; without -d it prints a script
    if p.file:
        return True  # a script file: unknown content
    if spec["mongo"] and any(re.search(r"\.js$", a, re.I) for a in p.positional):
        return True
    if spec["redis"]:
        if p.modes and not p.positional and not unknown_in:
            return False  # --scan, --bigkeys, --stat ...: read modes
        return (unknown_in or any(o in ("-x", "--pipe") for o in p.other) or redis_writes(p.positional)
                or any(redis_writes(line.split()) for x in texts for line in re.split(r"\r?\n", x)))
    if unknown_in and not p.sql:
        return True  # a redirect, heredoc or pipe: unknown content (-c / -e ignore stdin)
    if spec["mongo"]:
        return any(mongo_writes(x) for x in texts)
    return any(sql_writes(x, spec["dialect"]) for x in texts)


# ---------------------------------------------------------------------------
# ORM / migration CLIs
# ---------------------------------------------------------------------------
_DB_WRAPPERS = frozenset({"npx", "pnpx", "bunx", "pnpm", "yarn", "npm", "bun", "dlx", "exec"})
_WRAPPER_VALUE = frozenset({"-p", "--package", "-c", "--call", "--cache", "--registry", "--prefix", "-w",
                            "--workspace", "--dir", "-C", "--filter", "--cwd"})
_CLI_VALUE = {"prisma": frozenset({"--schema"}),
              "supabase": frozenset({"--workdir", "--project-ref", "--profile", "--password", "-p", "--db-url",
                                     "--schema", "-s", "--include-roles", "--network-id", "-o", "--output",
                                     "--status", "--version"})}
_PRISMA_WRITES = (("db", "push"), ("db", "execute"), ("migrate", "deploy"), ("migrate", "resolve"),
                  ("migrate", "reset"))


def _tool_base(word):
    b = re.split(r"[\\/]", str(word))[-1].lower()
    b = b[:-4] if b.endswith(".exe") else b
    return re.sub(r"@[^@]*$", "", b) if not b.startswith("@") else b  # prisma@5 -> prisma


def _pairs(pos, wanted):
    return any((pos[k], pos[k + 1]) in wanted for k in range(len(pos) - 1))


def _cli_target(name, args):
    """(tool, host) when a prisma / supabase command writes a database that
    is not known to be local, else None (forge ``dbCliWrite``)."""
    cmd, args = _tool_base(name), [str(a) for a in args]
    if cmd in _DB_WRAPPERS:
        idx, k = None, 0
        while k < len(args):
            a = args[k]
            if a in ("-c", "--call") and k + 1 < len(args):  # npx -c 'prisma db push'
                words = args[k + 1].split()
                return _cli_target(words[0], words[1:]) if words else None
            if a in _WRAPPER_VALUE:
                k += 2
                continue
            if not a.startswith("-") and _tool_base(a) in ("prisma", "supabase"):
                idx = k
                break
            k += 1
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
        pos.append(a.lower())
    # prisma reads its database from the schema or .env: never known to be dev. "migrate dev" uses a dev database.
    if cmd == "prisma":
        return ("prisma", None) if _pairs(pos, _PRISMA_WRITES) else None
    db_url = None
    for k, a in enumerate(args):
        if a == "--db-url":
            db_url = args[k + 1] if k + 1 < len(args) else ""
        elif a.startswith("--db-url="):
            db_url = a[9:]
    hs = None if db_url is None else (_url_hosts(db_url) or [db_url])
    remote_url = db_url is not None and not all(host_local(h) for h in hs)
    url_h = None if hs is None else ",".join(hs)
    local_only = "--local" in args or (db_url is not None and not remote_url)
    to_remote = "--linked" in args or remote_url
    held = ("supabase", url_h if remote_url and "--linked" not in args else None)
    if _pairs(pos, {("db", "push"), ("migration", "repair")}):  # remote unless told otherwise
        return None if local_only else held
    if _pairs(pos, {("db", "reset"), ("migration", "up")}):  # local unless told otherwise
        return held if to_remote else None
    return None


def cli_write(name, args, patterns=()):
    """prisma db push|db execute|migrate deploy|migrate resolve|migrate reset,
    supabase db push|migration repair (remote unless --local), supabase db
    reset|migration up with --linked or a remote --db-url; also through
    npx/pnpx/bunx/pnpm/yarn/npm/bun (dlx/exec, -p/--package). A --db-url whose
    every host is a dev host is not a write."""
    hit = _cli_target(name, args)
    if hit is None:
        return False
    host = hit[1]
    return not (host and all(dev_host(patterns, h) for h in host.split(",")))


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
_SQL_FIELD_WORDS = frozenset("sql query queries statement statements command commands script q eval code "
                             "cypher".split())
_DESTROY_TOOL_WORDS = ("sql", "execute", "statement", "run", "raw", "query")
_DESTROY_FIELD = re.compile(r"^(sql|statements?|query|queries)$", re.I)
_MONGO_READ_CMDS = frozenset(w.lower() for w in (
    "ping buildInfo serverStatus hello isMaster listCollections listDatabases dbStats collStats count find "
    "distinct explain connectionStatus hostInfo getLog getParameter listIndexes whatsmyuri getCmdLineOpts top "
    "dataSize features validate replSetGetStatus").split())
_ENVIRONMENTS = ("dev", "prod", "unknown")
_MAX_DEPTH = 64


def _tool_words(name):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return [w for w in re.split(r"[^a-z0-9.]+", s) if w]


def _strings(v, out, depth=0):
    if depth > _MAX_DEPTH:
        return False
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, dict):
        return all(_strings(x, out, depth + 1) for x in v.values())
    elif isinstance(v, list):
        return all(_strings(x, out, depth + 1) for x in v)
    return True


def _sql_field(key):
    return isinstance(key, str) and any(w in _SQL_FIELD_WORDS for w in _tool_words(key))


def _field_texts(v, out=None, depth=0, keyp=_sql_field):
    """(texts, complete): the strings under SQL-named keys at any depth."""
    out = [] if out is None else out
    if depth > _MAX_DEPTH:
        return out, False
    items = v.items() if isinstance(v, dict) else (enumerate(v) if isinstance(v, list) else ())
    complete = True
    for k, x in items:
        if keyp(k):
            complete = _strings(x, out, depth + 1) and complete
        elif isinstance(x, (dict, list)):
            complete = _field_texts(x, out, depth + 1, keyp)[1] and complete
    return out, complete


def _server_info(servers, server):
    if not isinstance(servers, dict):
        return {}
    info = servers.get(server.lower())
    return info if isinstance(info, dict) else {}


def _mcp_dialect(server, label):
    s = f"{server} {label}"
    if re.search(r"supabase|postgres|pgsql|neon|cockroach|(^|[^a-z])pg([^a-z]|$)", s, re.I):
        return "pg"
    if re.search(r"mysql|maria|planetscale", s, re.I):
        return "mysql"
    return None


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
    words = _tool_words(tool)
    dialect = _mcp_dialect(server, label)
    ti = tool_input if isinstance(tool_input, (dict, list)) else {}
    migration = tool.lower().replace("-", "_") == "apply_migration"
    is_db = env in ("dev", "prod") or bool(DB_SERVER.search(server)) or bool(label and DB_SERVER.search(label))
    destroy = False
    # Ruling P4: a tool that says it runs SQL, on any server
    if any(w in tool.lower() for w in _DESTROY_TOOL_WORDS) and "search" not in tool.lower():
        texts, _c = _field_texts(ti, keyp=lambda k: isinstance(k, str) and bool(_DESTROY_FIELD.match(k)))
        destroy = any(sql_destructive(x, dialect) for x in texts)
    if not is_db and not migration:
        return Finding(destroy, False)
    texts, complete = _field_texts(ti)
    if not texts and any(w in _STRONG_RUN for w in words):
        _strings(ti, texts)  # a runner without a SQL-named field: every string may be its SQL
    destroy = destroy or any(sql_destructive(x, dialect) for x in texts)
    write = False
    if env == "prod" or (env != "dev" and not dev_server(patterns, server)):  # Ruling P5: prod beats dev patterns
        write = _mcp_write(server, words, ti, texts, complete, dialect)
    return Finding(destroy, write)


def _mongo_command_reads(ti):
    """Every command document is a read command (ping, buildInfo, ...)."""
    found = []
    for k, v in (ti.items() if isinstance(ti, dict) else ()):
        if _sql_field(k) and isinstance(v, dict) and v:
            found.append(next(iter(v)).lower() in _MONGO_READ_CMDS and not re.search(
                r"\$(out|merge)\b", json.dumps(v, default=str)))
    return bool(found) and all(found)


def _mcp_write(server, words, ti, texts, complete, dialect):
    if any(w in _TOOL_WRITE for w in words):
        return True
    if "create" in words and any(w in _DB_OBJECTS for w in words):
        return True
    try:
        text = json.dumps(ti, default=str)
    except (TypeError, ValueError, RecursionError):
        return True
    if re.search(r'"\$(out|merge)"\s*:', text):
        return True
    if not any(w in _RUN_WORDS for w in words):
        return False
    if not complete:
        return True  # nested deeper than we read: unknown
    mongo = bool(re.search(r"mongo", server, re.I))
    if mongo and _mongo_command_reads(ti):
        return False
    if not texts:
        return any(w in _STRONG_RUN for w in words)  # it runs something we cannot see: held
    if re.search(r"redis|valkey|upstash", server, re.I):
        return any(redis_writes(x.split()) for x in texts)
    if mongo:
        return any(mongo_writes(x) for x in texts) or bool(
            re.search(r'"(insert|update|delete|drop\w*|findAndModify|create\w*|renameCollection)"\s*:', text))
    return any(sql_writes(x, dialect) for x in texts)
