"""Glob matcher for decision `touches` (stdlib only, no regex built from the pattern).

Keep in sync with the copy in app/guard/co_hook.py (co-guard runs `python3 -I` and cannot import the app);
tests/test_decisions_globmatch.py runs one case table against both.
"""
import sys

GLOB_PATTERN_MAX = 300
GLOB_WILDCARD_MAX = 30
CASE_INSENSITIVE = sys.platform in ("darwin", "win32")


def _name_match(pat, name):
    """One name against one pattern segment: ``*`` any run, ``?`` one char. No backtracking blowup: at worst
    O(len(pat) x len(name)), patterns are capped at GLOB_PATTERN_MAX chars (``**`` counts as two wildcards)."""
    p = n = 0
    star = -1
    mark = 0
    while n < len(name):
        if p < len(pat) and pat[p] != "*" and (pat[p] == "?" or pat[p] == name[n]):
            p += 1
            n += 1
        elif p < len(pat) and pat[p] == "*":
            star = p
            mark = n
            p += 1
        elif star >= 0:
            p = star + 1
            mark += 1
            n = mark
        else:
            return False
    while p < len(pat) and pat[p] == "*":
        p += 1
    return p == len(pat)


def _glob_parts(pattern, path, ci):
    if not isinstance(pattern, str) or not isinstance(path, str):
        return None
    if not pattern or len(pattern) > GLOB_PATTERN_MAX:
        return None
    if pattern.count("*") + pattern.count("?") > GLOB_WILDCARD_MAX:
        return None
    if CASE_INSENSITIVE if ci is None else ci:
        pattern, path = pattern.lower(), path.lower()
    return [s for s in pattern.split("/") if s], [s for s in path.split("/") if s]


def _glob_match(pat, parts):
    if not pat:
        return False
    # reachable[j]: the pattern segments so far can consume exactly parts[:j]
    reach = {0}
    for seg in pat:
        if seg == "**":
            if reach:
                lo = min(reach)
                reach = set(range(lo, len(parts) + 1))
            continue
        reach = {j + 1 for j in reach if j < len(parts) and _name_match(seg, parts[j])}
        if not reach:
            return False
    return len(parts) in reach


def specificity(pattern, path, ci=None):
    """0 exact path, 1 folder prefix, 2 glob match, None no match."""
    got = _glob_parts(pattern, path, ci)
    if got is None:
        return None
    pat, parts = got
    if not pat:
        return None
    if "*" not in pattern and "?" not in pattern:
        if pat == parts:
            return 0
        return 1 if len(pat) < len(parts) and parts[:len(pat)] == pat else None
    return 2 if _glob_match(pat, parts) else None


def matches(pattern, path, ci=None):
    return specificity(pattern, path, ci) is not None
