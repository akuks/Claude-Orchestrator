import asyncio
import socket

import pytest

from app.gates import preflight as pf

GB = 1024**3


def chk(lines):
    return asyncio.run(pf.check(lines))


def test_parse_forms():
    assert pf.parse("Free Memory 4 GB").kind == "memory"
    assert pf.parse("free memory 1.5 GB").value == int(1.5 * GB)
    assert pf.parse("free memory 512 MB").value == 512 * 1024**2
    assert pf.parse("port 5432 OPEN") == pf.Check("port", 5432, "open")
    assert pf.parse("container db running").value == "db"


@pytest.mark.parametrize("line", ["free memory 4.25 GB", "free memory 1.5 MB", "port 70000 open", "port 0 free",
                                  "container -x running", "disk 4 GB", "port  80 open", "free memory 0 GB"])
def test_parse_rejects(line):
    with pytest.raises(ValueError):
        pf.parse(line)


def test_memory(monkeypatch):
    monkeypatch.setattr(pf, "_free_bytes", lambda: int(2.1 * GB))
    assert chk(["free memory 4 GB"]) == "free memory is 2.1 GB, needs 4 GB"
    assert chk(["free memory 2 GB"]) is None
    monkeypatch.setattr(pf, "_free_bytes", lambda: None)
    assert chk(["free memory 1 GB"]) == "could not check free memory"


def test_port(monkeypatch):
    monkeypatch.setattr(pf, "_port_open", lambda p: True)
    assert chk(["port 3000 open"]) is None
    assert chk(["port 3000 free"]) == "port 3000 is in use"
    monkeypatch.setattr(pf, "_port_open", lambda p: False)
    assert chk(["port 5432 open"]) == "port 5432 is not open"
    assert chk(["port 5432 free"]) is None


def test_container(monkeypatch):
    monkeypatch.setattr(pf, "_container_running", lambda n: True)
    assert chk(["container db running"]) is None
    monkeypatch.setattr(pf, "_container_running", lambda n: False)
    assert chk(["container db running"]) == "container db is not running"
    monkeypatch.setattr(pf, "_container_running", lambda n: None)
    assert chk(["container db running"]) is not None


def test_docker_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(pf.subprocess, "run", boom)
    assert pf._container_running("db") is None
    assert chk(["container db running"]) is not None


def test_first_unmet_wins(monkeypatch):
    monkeypatch.setattr(pf, "_port_open", lambda p: False)
    assert chk(["port 1 free", "port 2 open", "port 3 open"]) == "port 2 is not open"


def test_real_port():
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    sk.listen()
    port = sk.getsockname()[1]
    assert pf._port_open(port) is True
    sk.close()
    assert pf._port_open(port) is False


def test_free_bytes_real():
    v = pf._free_bytes()
    assert v is None or v > 0
