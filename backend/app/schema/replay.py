"""Replay SQL migration files into a table list with sqlglot (never runs anything).

Files are split statement by statement by a small scanner (it knows comments, quotes, backticks and Postgres
``$tag$`` bodies and keeps line numbers), then each statement is parsed on its own so one bad statement does not
lose the file. Dialect: a statement with backticks is tried as MySQL first, one with [bracket] names as T-SQL
first, anything else as Postgres first; the other dialects are the fallback. A statement that no dialect reads
is counted as not understood (never guessed). Changes made inside DO blocks and function bodies mark the table
uncertain.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

logging.getLogger("sqlglot").setLevel(logging.ERROR)

NO_SCHEMA = {"public", "dbo"}
SAMPLES = 5
MAX_STATEMENT = 256 * 1024  # a longer statement is counted as not understood, never parsed
CHECK_EVERY = 64 * 1024
_GO = re.compile(r"[ \t]*GO[ \t]*(?:\r?\n|$)", re.I)
_DOLLAR = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")
_BODY = re.compile(r"^\s*(do|create\s+(or\s+replace\s+)?(function|procedure|trigger)|begin|declare)\b", re.I)
_BODY_TABLE = re.compile(
    r"\b(create|alter|drop)\s+(?:temp\w*\s+|unlogged\s+)?table\s+(?:only\s+)?(?:if\s+(?:not\s+)?exists\s+)?"
    r"((?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])(?:\s*\.\s*(?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\]))*)", re.I)
_CMD_TABLE = re.compile(r"^\s*(create|alter|drop)\s+(temp\w*\s+|unlogged\s+)?table\b", re.I)
_RENAME = re.compile(r"^\s*rename\s+table\s+(.*)$", re.I | re.S)
_PAIR = re.compile(r"\s*((?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])(?:\s*\.\s*(?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\]))*)"
                   r"\s+to\s+((?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\])(?:\s*\.\s*(?:[\w$]+|\"[^\"]+\"|`[^`]+`|\[[^\]]+\]))*)\s*,?",
                   re.I)
_BRACKET = re.compile(r"\[[A-Za-z_][\w ]*\]")


@dataclass
class Column:
    name: str
    type: str = ""
    nullable: bool = True
    primary_key: bool = False
    defined_at: str = ""


@dataclass
class Link:
    columns: list[str]
    to_table: str
    to_columns: list[str]
    defined_at: str
    name: str | None = None


@dataclass
class Table:
    name: str
    defined_at: str
    file: str
    uncertain: bool = False
    columns: list[Column] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    pk_name: str | None = None


@dataclass
class RootSchema:
    tables: dict[str, Table] = field(default_factory=dict)
    not_understood_count: int = 0
    not_understood_samples: list[str] = field(default_factory=list)


def split_statements(text: str, deadline: float | None = None):
    """Yield (statement_text, first_line). Comments are dropped (newlines kept); quotes and $tag$ bodies stay whole.

    Linear time: the last significant character is tracked, never re-joined. The deadline is checked every
    ``CHECK_EVERY`` characters; over it, TimeoutError."""
    out: list[tuple[str, int]] = []
    buf: list[str] = []
    line = 1
    start_line = None
    last = ""  # last non-space character of the statement so far
    i, n = 0, len(text)
    at_line_start = True
    next_check = CHECK_EVERY

    def flush():
        nonlocal buf, start_line, last
        s = "".join(buf).strip()
        if s and start_line is not None:
            out.append((s, start_line))
        buf, start_line, last = [], None, ""

    def put(chunk: str):
        nonlocal start_line, last
        t = chunk.rstrip()
        if t:
            if start_line is None:
                start_line = line
            last = t[-1]
        buf.append(chunk)

    while i < n:
        if i >= next_check:
            next_check = i + CHECK_EVERY
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("schema replay over budget")
        c = text[i]
        if at_line_start:
            m = _GO.match(text, i)
            if m and last not in ("(", ","):
                flush()
                i = m.end()
                line += 1
                continue
            at_line_start = False
        if c == "\n":
            line += 1
            at_line_start = True
            buf.append(c)
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += text.count("\n", i, j)
            buf.append(" " + "\n" * text.count("\n", i, j))
            i = j
        elif c in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == c:
                    if j + 1 < n and text[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            chunk = text[i:j + 1]
            put(chunk)
            line += chunk.count("\n")
            i = j + 1
        elif c == "$" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")) and (m := _DOLLAR.match(text, i)):
            tag = m.group(0)
            j = text.find(tag, m.end())
            j = n if j < 0 else j + len(tag)
            chunk = text[i:j]
            put(chunk)
            line += chunk.count("\n")
            i = j
        elif c == ";":
            flush()
            i += 1
        else:
            put(c)
            i += 1
    flush()
    return out


def _dialects(sql: str) -> list[str]:
    if "`" in sql:
        return ["mysql", "postgres", "tsql"]
    if _BRACKET.search(sql):
        return ["tsql", "postgres", "mysql"]
    return ["postgres", "mysql", "tsql"]


def _parse(sql: str, deadline: float | None = None):
    """(expression, dialect) from the first dialect that reads it, or None."""
    for d in _dialects(sql):
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("schema replay over budget")
        try:
            got = sqlglot.parse(sql, read=d)
        except (SqlglotError, RecursionError):
            continue
        if len(got) == 1 and got[0] is not None:
            return got[0], d
    return None


def _ident(node) -> str:
    return node.name if node is not None else ""


def table_key(schema: str | None, name: str) -> str:
    if schema and schema.lower() not in NO_SCHEMA:
        return f"{schema}.{name}"
    return name


def _tname(t: exp.Table) -> str:
    return table_key(_ident(t.args.get("db")) or None, _ident(t.this))


def _raw_name(raw: str) -> str:
    parts = [re.sub(r'^["`\[]|["`\]]$', "", p.strip()) for p in raw.split(".")]
    return table_key(parts[-2] if len(parts) > 1 else None, parts[-1])


class _Replay:
    def __init__(self, deadline):
        self.s = RootSchema()
        self.deadline = deadline
        self.idx: dict[str, str] = {}  # lowercase -> table key

    # ---- table lookup
    def get(self, name: str) -> Table | None:
        k = self.idx.get(name.lower())
        return self.s.tables.get(k) if k else None

    def put(self, t: Table):
        self.s.tables[t.name] = t
        self.idx[t.name.lower()] = t.name

    def remove(self, name: str):
        k = self.idx.pop(name.lower(), None)
        if k:
            self.s.tables.pop(k, None)

    def bad(self, where: str):
        self.s.not_understood_count += 1
        if len(self.s.not_understood_samples) < SAMPLES:
            self.s.not_understood_samples.append(where)

    # ---- statement handlers
    def run(self, files):
        for rel, text in files:
            if self.deadline is not None and time.monotonic() > self.deadline:
                raise TimeoutError("schema replay over budget")
            for sql, line in split_statements(text, self.deadline):
                if self.deadline is not None and time.monotonic() > self.deadline:
                    raise TimeoutError("schema replay over budget")
                self.statement(sql, rel, f"{rel}:{line}")
        return self.s

    def statement(self, sql: str, rel: str, at: str):
        if len(sql) > MAX_STATEMENT:
            self.bad(at)
            return
        if _BODY.match(sql):
            for m in _BODY_TABLE.finditer(sql):
                name = _raw_name(m.group(2))
                t = self.get(name)
                if t is None and m.group(1).lower() == "create":
                    t = Table(name, at, rel, uncertain=True)
                    self.put(t)
                elif t is not None:
                    t.uncertain = True
            return
        if len(sql) > MAX_STATEMENT:
            self.bad(at)
            return
        parsed = _parse(sql, self.deadline)
        if parsed is None:
            self.bad(at)
            return
        node, dialect = parsed
        if isinstance(node, (exp.Column, exp.Alias, exp.Identifier, exp.Var, exp.Literal, exp.Table, exp.Paren)):
            self.bad(at)  # words that are not a statement
            return
        if isinstance(node, exp.Create) and str(node.args.get("kind") or "").upper() == "TABLE":
            self.create(node, rel, at, dialect)
        elif isinstance(node, exp.Alter) and str(node.args.get("kind") or "").upper() == "TABLE":
            self.alter(node, at, dialect)
        elif isinstance(node, exp.Drop) and str(node.args.get("kind") or "").upper() == "TABLE":
            for t in node.args.get("tables") or []:
                if isinstance(t, exp.Table):
                    self.remove(_tname(t))
        elif isinstance(node, exp.Command):
            self.command(node, sql, at)

    def command(self, node, sql: str, at: str):
        m = _RENAME.match(sql)
        if m:
            pairs = list(_PAIR.finditer(m.group(1)))
            if not pairs:
                self.bad(at)
            for p in pairs:
                self.rename_table(_raw_name(p.group(1)), _raw_name(p.group(2)))
        elif _CMD_TABLE.match(sql):
            self.bad(at)  # a table statement sqlglot only half reads: counted, not guessed

    def create(self, node, rel, at, dialect):
        target = node.this
        if isinstance(target, exp.Schema):
            tbl, items = target.this, list(target.expressions or [])
        else:
            tbl, items = target, None
        if not isinstance(tbl, exp.Table):
            self.bad(at)
            return
        name = _tname(tbl)
        old = self.get(name)
        if old is not None and node.args.get("exists"):
            return  # CREATE TABLE IF NOT EXISTS: the first one stands
        t = Table(name, at, rel)
        if old is not None:
            self.remove(name)
        self.put(t)
        if items is None:  # AS SELECT / LIKE: columns unknown
            t.uncertain = True
            return
        for it in items:
            self.item(t, it, at, dialect)

    def item(self, t: Table, it, at: str, dialect: str, cname: str | None = None):
        if isinstance(it, exp.ColumnDef):
            self.column(t, it, at, dialect)
        elif isinstance(it, exp.Constraint):
            name = _ident(it.this) or None
            for sub in it.expressions or []:
                self.item(t, sub, at, dialect, name)
        elif isinstance(it, exp.PrimaryKey):
            cols = [_ident(c.this if isinstance(c, exp.Ordered) else c) for c in it.expressions or []]
            self.set_pk(t, cols, cname)
        elif isinstance(it, exp.ForeignKey):
            ref = it.args.get("reference")
            cols = [_ident(c) for c in it.expressions or []]
            self.add_link(t, cols, ref, at, cname)

    def set_pk(self, t: Table, cols: list[str], name: str | None):
        low = {c.lower() for c in cols}
        for c in t.columns:
            if c.name.lower() in low:
                c.primary_key = True
                c.nullable = False
        t.pk_name = name

    def add_link(self, t: Table, cols: list[str], ref, at: str, name: str | None):
        if ref is None or not isinstance(ref, exp.Reference):
            return
        target = ref.this
        if isinstance(target, exp.Schema):
            tt, tcols = target.this, [_ident(c) for c in target.expressions or []]
        else:
            tt, tcols = target, []
        if isinstance(tt, exp.Table):
            t.links.append(Link(cols, _tname(tt), tcols, at, name))

    def column(self, t: Table, cd: exp.ColumnDef, at: str, dialect: str):
        col = self.build_column(t, cd, at, dialect)
        t.columns = [c for c in t.columns if c.name.lower() != col.name.lower()] + [col]

    def build_column(self, t: Table, cd: exp.ColumnDef, at: str, dialect: str) -> Column:
        name = _ident(cd.this)
        kind = cd.args.get("kind")
        typ = kind.sql(dialect=dialect).lower() if kind is not None else ""
        col = Column(name, typ, True, False, at)
        for cc in cd.args.get("constraints") or []:
            k = cc.args.get("kind") if isinstance(cc, exp.ColumnConstraint) else cc
            if isinstance(k, exp.NotNullColumnConstraint) and not k.args.get("allow_null"):
                col.nullable = False
            elif isinstance(k, exp.PrimaryKeyColumnConstraint):
                col.primary_key, col.nullable = True, False
            elif isinstance(k, exp.Reference):
                self.add_link(t, [name], k, at, None)
        return col

    def alter(self, node, at: str, dialect: str):
        tbl = node.this
        if not isinstance(tbl, exp.Table):
            return
        t = self.get(_tname(tbl))
        if t is None:
            self.bad(at)  # a change to a table we never saw: counted, not guessed
            return
        for act in node.args.get("actions") or []:
            if isinstance(act, exp.ColumnDef):
                self.column(t, act, at, dialect)
            elif isinstance(act, exp.ModifyColumn):
                self.modify_column(t, act, at, dialect)
            elif isinstance(act, exp.RenameColumn):
                self.rename_column(t, _ident(act.this), _ident(act.args.get("to")))
            elif isinstance(act, exp.AlterRename):
                new = act.this
                if isinstance(new, exp.Table):
                    self.rename_table(t.name, _tname(new))
                    t = self.get(_tname(new)) or t
                else:
                    self.bad(at)
            elif isinstance(act, exp.AddConstraint):
                for sub in act.expressions or []:
                    self.item(t, sub, at, dialect)
            elif isinstance(act, exp.Drop):
                kind = str(act.args.get("kind") or "").upper()
                if kind not in ("COLUMN", "CONSTRAINT"):
                    self.bad(at)
                    continue
                for target in act.args.get("tables") or []:
                    nm = target.name if isinstance(target, (exp.Identifier, exp.Table, exp.Column)) else ""
                    if kind == "COLUMN":
                        self.drop_column(t, nm)
                    else:
                        self.drop_constraint(t, nm)
            elif isinstance(act, exp.AlterColumn):
                self.alter_column(t, act, at, dialect)
            else:
                self.bad(at)

    def alter_column(self, t: Table, act, at: str, dialect: str):
        nm = _ident(act.this).lower()
        col = next((c for c in t.columns if c.name.lower() == nm), None)
        dtype, allow_null = act.args.get("dtype"), act.args.get("allow_null")
        if dtype is None and allow_null is None:
            self.bad(at)  # SET/DROP DEFAULT and the like
            return
        if col is None:
            self.bad(at)
            return
        if dtype is not None:
            col.type = dtype.sql(dialect=dialect).lower()
        if allow_null is not None:
            col.nullable = bool(allow_null) and not col.primary_key

    def modify_column(self, t: Table, act, at: str, dialect: str):
        """MySQL MODIFY / CHANGE: the new definition replaces the old one in place."""
        cd = act.this
        old_name = _ident(act.args.get("rename_from")) or _ident(cd.this)
        idx = next((i for i, c in enumerate(t.columns) if c.name.lower() == old_name.lower()), None)
        if idx is None:
            self.bad(at)
            return
        new = self.build_column(t, cd, at, dialect)
        if t.columns[idx].primary_key:
            new.primary_key, new.nullable = True, False
        if new.name.lower() != old_name.lower():
            self.rename_column(t, old_name, new.name)
        t.columns[idx] = new

    def drop_column(self, t: Table, nm: str):
        t.columns = [c for c in t.columns if c.name.lower() != nm.lower()]
        t.links = [lk for lk in t.links if nm.lower() not in {c.lower() for c in lk.columns}]

    def drop_constraint(self, t: Table, nm: str):
        if t.pk_name and t.pk_name.lower() == nm.lower() or nm.lower() == f"{t.name}_pkey".lower():
            for c in t.columns:
                c.primary_key = False
            t.pk_name = None
        t.links = [lk for lk in t.links if not (lk.name and lk.name.lower() == nm.lower())]

    def rename_column(self, t: Table, old: str, new: str):
        for c in t.columns:
            if c.name.lower() == old.lower():
                c.name = new
        for lk in t.links:
            lk.columns = [new if c.lower() == old.lower() else c for c in lk.columns]
        for other in self.s.tables.values():
            for lk in other.links:
                if lk.to_table.lower() == t.name.lower():
                    lk.to_columns = [new if c.lower() == old.lower() else c for c in lk.to_columns]

    def rename_table(self, old: str, new: str):
        t = self.get(old)
        if t is None:
            return
        self.remove(old)
        t.name = new
        self.put(t)
        for other in self.s.tables.values():
            for lk in other.links:
                if lk.to_table.lower() == old.lower():
                    lk.to_table = new


def replay(files: list[tuple[str, str]], deadline: float | None = None) -> RootSchema:
    """Replay (rel_path, text) files, already in order, into the tables they leave behind."""
    return _Replay(deadline).run(files)
