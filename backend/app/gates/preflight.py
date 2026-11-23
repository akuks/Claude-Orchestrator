"""Pre-flight checks (F115): fixed-form lines checked before a stage starts."""

import asyncio
import re
import socket
import subprocess
import sys
from dataclasses import dataclass

_MEM = re.compile(r"^free memory (\d+(?:\.\d)?|\d+) (GB|MB)$", re.I)
_CONT = re.compile(r"^container ([A-Za-z0-9][A-Za-z0-9_.-]{0,62}) running$", re.I)

FORMS = "free memory <n> GB|MB, port <n> open|free, container <name> running"


@dataclass(frozen=True)
class Check:
    kind: str  # memory | port | container
    value: object  # memory: bytes needed; port: int; container: name
    want: str  # memory: display of the need ("4 GB"); port: open|free; container: running


def parse(line: str) -> Check:
    if not isinstance(line, str):
        raise ValueError("a pre-flight line must be text")
    text = line.strip()
    m = _MEM.match(text)
    if m:
        num, unit = m.group(1), m.group(2).upper()
        if unit == "MB" and "." in num:
            raise ValueError(f"pre-flight '{text}': MB must be a whole number")
        n = float(num)
        if n <= 0:
            raise ValueError(f"pre-flight '{text}': memory must be above 0")
        size = int(n * (1024**3 if unit == "GB" else 1024**2))
        return Check("memory", size, f"{num} {unit}")
    m = re.match(r"^port (\d+) (open|free)$", text, re.I)
    if m:
        port = int(m.group(1))
        if not 1 <= port <= 65535:
            raise ValueError(f"pre-flight '{text}': port must be 1 to 65535")
        return Check("port", port, m.group(2).lower())
    m = _CONT.match(text)
    if m:
        return Check("container", m.group(1), "running")
    raise ValueError(f"pre-flight '{text}' is not one of: {FORMS}")


def _free_bytes() -> int | None:
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as f:
                for ln in f:
                    if ln.startswith("MemAvailable:"):
                        return int(ln.split()[1]) * 1024
            return None
        if sys.platform == "darwin":
            out = subprocess.run(
                ["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=5, check=True
            ).stdout
            m = re.search(r"page size of (\d+) bytes", out)
            if not m:
                return None
            size = int(m.group(1))
            pages = 0
            for key in ("Pages free", "Pages inactive", "Pages speculative"):
                mm = re.search(rf"^{key}:\s+(\d+)", out, re.M)
                if not mm:
                    return None
                pages += int(mm.group(1))
            return pages * size
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _port_open(port: int) -> bool:
    for host, fam in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as sk:
                sk.settimeout(1)
                sk.connect((host, port))
                return True
        except OSError:
            continue
    return False


def _container_running(name: str) -> bool | None:
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{name}$", "--filter", "status=running",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return name in r.stdout.split()


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


async def check(lines: list[str]) -> str | None:
    """None when every line is met; otherwise the first unmet reason in plain words."""
    for line in lines or []:
        c = parse(line)
        if c.kind == "memory":
            free = await asyncio.to_thread(_free_bytes)
            if free is None:
                return "could not check free memory"
            if free < c.value:
                return f"free memory is {_gb(free)}, needs {c.want}"
        elif c.kind == "port":
            is_open = await asyncio.to_thread(_port_open, c.value)
            if c.want == "open" and not is_open:
                return f"port {c.value} is not open"
            if c.want == "free" and is_open:
                return f"port {c.value} is in use"
        else:
            up = await asyncio.to_thread(_container_running, c.value)
            if up is None:
                return f"could not check container {c.value}"
            if not up:
                return f"container {c.value} is not running"
    return None
