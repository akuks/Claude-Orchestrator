"""Step dependencies of a workflow (phase 7b): pure functions over the stages list.

A stage may carry ``"after": [i, ...]``, indexes of EARLIER stages it waits for. A missing
``after`` means "the stage just before me" (stage 0 waits for nothing); ``[]`` starts at once.
``max_parallel`` (1-4, default 2) is stored as a key on stage 0 (see get/set_max_parallel).
"""

DEFAULT_MAX_PARALLEL = 2
MAX_PARALLEL_WORDS = "Run at most 1 to 4 steps at once."


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def deps(stages: list[dict], i: int) -> list[int]:
    """Indexes the stage waits for, ascending. Unusable entries (not an earlier step) are ignored."""
    after = (stages[i] or {}).get("after")
    if after is None:
        return [i - 1] if i > 0 else []
    if not isinstance(after, list):
        return []
    return sorted({j for j in after if _int(j) and 0 <= j < i})


def ready(stages: list[dict], done: set[int], started: set[int]) -> list[int]:
    """Stages not yet started whose every wait is done, lowest number first."""
    return [i for i in range(len(stages))
            if i not in done and i not in started and all(j in done for j in deps(stages, i))]


def dependants(stages: list[dict], i: int) -> set[int]:
    """Every later stage that waits for `i`, directly or through other steps."""
    out: set[int] = set()
    for k in range(i + 1, len(stages)):
        if i in deps(stages, k) or out & set(deps(stages, k)):
            out.add(k)
    return out


def depth(stages: list[dict]) -> list[int]:
    """Longest chain of waits leading to each stage (0 for stages that start at once)."""
    d: list[int] = []
    for i in range(len(stages)):
        d.append(max((d[j] + 1 for j in deps(stages, i)), default=0))
    return d


def get_max_parallel(stages: list[dict]) -> int:
    v = stages[0].get("max_parallel") if stages else None
    return v if _int(v) and 1 <= v <= 4 else DEFAULT_MAX_PARALLEL


def set_max_parallel(stages: list[dict], value: int | None) -> None:
    """Store on stage 0 (no column: no migration); None or the default removes the key."""
    if not stages:
        return
    for st in stages[1:]:
        st.pop("max_parallel", None)
    if value is None or value == DEFAULT_MAX_PARALLEL:
        stages[0].pop("max_parallel", None)
    else:
        stages[0]["max_parallel"] = value


def validate(stages: list[dict], max_parallel) -> list[str]:
    """Every problem as a plain sentence (steps numbered from 1); [] when fine."""
    out: list[str] = []
    if max_parallel is not None and not (_int(max_parallel) and 1 <= max_parallel <= 4):
        out.append(MAX_PARALLEL_WORDS)
    for i, st in enumerate(stages):
        after = (st or {}).get("after")
        n = i + 1
        if after is None:
            continue
        if not isinstance(after, list):
            out.append(f"Step {n}: the steps it waits for must be a list.")
            continue
        seen: set[int] = set()
        for j in after:
            if not _int(j):
                out.append(f"Step {n} waits for something that is not a step number.")
            elif j == i:
                out.append(f"Step {n} waits for itself.")
            elif j < 0 or j >= len(stages):
                out.append(f"Step {n} waits for step {j + 1}, which does not exist.")
            elif j > i:
                out.append(f"Step {n} waits for step {j + 1}, which comes later.")
            elif j in seen:
                out.append(f"Step {n} lists step {j + 1} twice.")
            if _int(j):
                seen.add(j)
    return out
