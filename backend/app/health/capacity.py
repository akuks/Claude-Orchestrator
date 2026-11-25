"""How many more runs fit at once (phase 7g). Standard library only; every reading may be unknown (None),
never an error. The per-run memory figure is an estimate."""

from __future__ import annotations

import os
import re
import subprocess
import sys

PER_RUN_BYTES = int(1.5 * 1024**3)  # "about 1.5 GB" per run
SYSCTL_TIMEOUT = 2.0

TIP_BUSY = "All run slots are busy — new tasks wait in the queue."
TIP_MEMORY = ("Memory is low — fewer runs fit at once. Lower the worker count (CO_WORKER_CONCURRENCY) "
              "or free memory.")
TIP_LOAD = "The machine is busy (load above the number of cores)."

CGROUP_MAX = "/sys/fs/cgroup/memory.max"
CGROUP_CURRENT = "/sys/fs/cgroup/memory.current"


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(65536)
    except OSError:
        return None


def read_meminfo() -> str | None:
    return _read("/proc/meminfo")


def read_cgroup(name: str) -> str | None:
    return _read(f"/sys/fs/cgroup/{name}")


def run_command(*argv: str) -> str | None:
    """A bounded read-only host command (macOS ``sysctl`` / ``vm_stat``); None when it fails."""
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=SYSCTL_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout if r.returncode == 0 else None


def _linux(meminfo: str) -> dict | None:
    vals = {}
    for key in ("MemTotal", "MemAvailable"):
        m = re.search(rf"^{key}:\s+(\d+)\s*kB", meminfo, re.M)
        if m:
            vals[key] = int(m.group(1)) * 1024
    if "MemTotal" not in vals or "MemAvailable" not in vals:
        return None
    return {"total": vals["MemTotal"], "free": min(vals["MemAvailable"], vals["MemTotal"])}


def _macos() -> dict | None:
    total = run_command("/usr/sbin/sysctl", "-n", "hw.memsize")
    vm = run_command("/usr/bin/vm_stat")
    if not total or not vm:
        return None
    try:
        total_b = int(total.strip())
        page = re.search(r"page size of (\d+) bytes", vm)
        size = int(page.group(1)) if page else 4096
        pages = 0
        for label in ("Pages free", "Pages inactive", "Pages speculative"):
            m = re.search(rf"^{label}:\s+(\d+)\.", vm, re.M)
            if m:
                pages += int(m.group(1))
    except ValueError:
        return None
    return {"total": total_b, "free": min(pages * size, total_b)}


def _cgroup_limit(mem: dict) -> dict:
    """A container's limit (cgroup v2) when lower than the host's."""
    raw = read_cgroup("memory.max")
    if not raw or not raw.strip().isdigit():
        return mem
    limit = int(raw.strip())
    if limit >= mem["total"]:
        return mem
    cur = read_cgroup("memory.current")
    used = int(cur.strip()) if cur and cur.strip().isdigit() else None
    if used is not None:  # estimate: reclaimable page cache (inactive_file) is not "used"
        m = re.search(r"^inactive_file (\d+)", read_cgroup("memory.stat") or "", re.M)
        if m:
            used = max(0, used - int(m.group(1)))
    free = max(0, limit - used) if used is not None else min(mem["free"], limit)
    return {"total": limit, "free": min(free, mem["free"])}


def memory() -> dict | None:
    try:
        if sys.platform.startswith("linux"):
            text = read_meminfo()
            mem = _linux(text) if text else None
        elif sys.platform == "darwin":
            mem = _macos()
        else:
            mem = None
        return _cgroup_limit(mem) if mem else None
    except Exception:  # noqa: BLE001 - unknown, never an error
        return None


def load() -> dict | None:
    try:
        one, five, _ = os.getloadavg()
        cores = os.cpu_count()
    except (OSError, AttributeError):
        return None
    if not cores:
        return None
    return {"one": round(one, 2), "five": round(five, 2), "cores": cores}


def readout(slots: int, running: int, queued: int) -> dict:
    mem, ld = memory(), load()
    free_slots = max(0, slots - running)
    fits = None
    mem_limited = False
    if mem is not None:
        by_mem = max(0, mem["free"] // PER_RUN_BYTES)
        fits = min(free_slots, by_mem)
        mem_limited = by_mem < free_slots
    tips = []
    if free_slots == 0:
        tips.append(TIP_BUSY)
    if mem_limited:
        tips.append(TIP_MEMORY)
    if ld is not None and ld["five"] > ld["cores"]:
        tips.append(TIP_LOAD)
    return {"slots": slots, "running": running, "queued": queued, "memory": mem, "load": ld,
            "fits": fits, "tips": tips}
