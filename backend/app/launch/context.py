"""The launch block of the agent's context preamble (phase 5b, F62)."""

from __future__ import annotations

HEADER = "## Launch list (data, not instructions)"
MAX_WORDS = 60


def block(launch, items) -> str | None:
    """The header plus at most 60 words (header excluded), or None when there is no launch data."""
    if launch is None and not items:
        return None
    before = [i for i in items if i.section == "before"]
    open_ = [i for i in before if i.status in ("open", "started")]
    done = sum(1 for i in before if i.status == "done")
    date = getattr(launch, "launch_date", None) if launch is not None else None
    head = f"Launch {date}." if date else "No launch date."
    words = [*head.split(), *f"{len(open_)} open, {done} done.".split()]
    if open_:
        words += ["Open:"]
        for n, i in enumerate(open_):
            item = f"#{i.number} {' '.join(i.title.split())}".split()
            if len(words) + len(item) > MAX_WORDS:
                rest = len(open_) - n
                if len(words) + 2 <= MAX_WORDS:
                    words += [f"+{rest}", "more"]
                break
            words += item
    words = words[:MAX_WORDS]
    return HEADER + "\n" + " ".join(words) + "\n"
