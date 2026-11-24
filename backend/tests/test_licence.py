import asyncio
import os
import uuid

import pytest

from app import licence

MIT = ("MIT License\n\nCopyright (c) 2020 Someone\n\nPermission is hereby granted, free of charge, to any person "
       "obtaining a copy of this software")
APACHE = "                                 Apache License\n                           Version 2.0, January 2004\n"
BSD3 = ("BSD 3-Clause License\n\nRedistribution and use in source and binary forms, with or without modification, "
        "are permitted\n3. Neither the name of the copyright holder nor the names")
GPL3 = "                    GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007\n"
AGPL = "GNU AFFERO GENERAL PUBLIC LICENSE\n Version 3, 19 November 2007"
LGPL = "GNU LESSER GENERAL PUBLIC LICENSE\n Version 2.1, February 1999"
MPL = "Mozilla Public License Version 2.0\n==================================\n\n1. Definitions"


@pytest.mark.parametrize("text,kind,ident", [
    (MIT, "permissive", "MIT"),
    (APACHE, "permissive", "Apache-2.0"),
    (BSD3, "permissive", "BSD-3-Clause"),
    (GPL3, "copyleft", "GPL-3.0"),
    (AGPL, "copyleft", "AGPL-3.0"),
    (LGPL, "copyleft", "LGPL-2.1"),
    (MPL, "copyleft", "MPL-2.0"),
    ("Copyright 2024 Acme Inc. All rights reserved.", "proprietary", "All rights reserved"),
    ("Business Source License 1.1\n", "proprietary", "BSL-1.1"),
    ("", "unknown", "unknown"),
    ("You may use this on Tuesdays if you bring cake.", "unknown", "unknown"),
    ("\x00\x01\x02\xff" * 50, "unknown", "unknown"),
])
def test_classify(text, kind, ident):
    c = licence.classify(text)
    assert c["kind"] == kind and c["id"] == ident


def test_lgpl_quoting_gpl_is_lgpl():
    assert licence.classify(LGPL + "\nthe GNU General Public License")["id"] == "LGPL-2.1"


def test_mit_mentioning_gpl_is_mit():
    assert licence.classify(MIT + "\nCompatible with the GNU General Public License.")["id"] == "MIT"


def test_dual_notice_picks_permissive():
    c = licence.classify("Licensed under either of Apache License 2.0 or MIT License at your option.")
    assert c["kind"] == "permissive" and "MIT" in c["id"] and "Apache-2.0" in c["id"]


def _run(coro):
    return asyncio.run(coro)


def test_no_file_is_none(tmp_path):
    r = _run(licence.for_directory(str(tmp_path)))
    assert r["kind"] == "none" and r["file"] is None


def test_missing_directory_is_none(tmp_path):
    assert _run(licence.for_directory(str(tmp_path / "nope"))) is None


def test_reads_licence_file_any_case(tmp_path):
    (tmp_path / "license.txt").write_text(MIT)
    r = _run(licence.for_directory(str(tmp_path)))
    assert r["id"] == "MIT" and r["file"] == "license.txt" and r["hash"] and r["checked_at"]


def test_copying_file(tmp_path):
    (tmp_path / "COPYING").write_text(GPL3)
    assert (_run(licence.for_directory(str(tmp_path))))["kind"] == "copyleft"


def test_symlink_is_never_read(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text(MIT)
    d = tmp_path / "proj"
    d.mkdir()
    os.symlink(secret, d / "LICENSE")
    r = _run(licence.for_directory(str(d)))
    assert r["kind"] == "unknown" and r["hash"] is None


def test_fifo_is_never_read(tmp_path):
    os.mkfifo(tmp_path / "LICENSE")
    r = _run(licence.for_directory(str(tmp_path)))  # must return, not block
    assert r["kind"] == "unknown" and r["hash"] is None


def test_large_file_only_first_64k_classified(tmp_path):
    (tmp_path / "LICENSE").write_bytes(b"x" * (64 * 1024) + MIT.encode())
    assert (_run(licence.for_directory(str(tmp_path))))["kind"] == "unknown"
    (tmp_path / "LICENSE").write_bytes(MIT.encode() + b" " * (65 * 1024))
    assert (_run(licence.for_directory(str(tmp_path))))["id"] == "MIT"


def test_binary_junk_is_unknown(tmp_path):
    (tmp_path / "LICENSE").write_bytes(os.urandom(2048))
    assert (_run(licence.for_directory(str(tmp_path))))["kind"] in ("unknown", "none")


class _P:
    def __init__(self, trust, kind=None, ident=""):
        self.trust = trust
        self.licence = {"kind": kind, "id": ident} if kind else None


def test_badge_rules():
    assert licence.badge(_P("own", "permissive", "MIT")) is None
    assert licence.badge(_P("own", "unknown", "unknown")) is None
    assert licence.badge(_P("external", "permissive", "MIT")) == {"label": "MIT", "tone": "info"}
    g = licence.badge(_P("own", "copyleft", "GPL-3.0"))
    assert g == {"label": "GPL-3.0 — share-alike", "tone": "warning"}
    assert licence.badge(_P("own", "none", "none"))["label"] == "No licence found"
    p = licence.badge(_P("own", "proprietary", "All rights reserved"))
    assert p == {"label": "All rights reserved", "tone": "danger"}
    assert licence.badge(_P("external", "unknown", "unknown"))["tone"] == "warning"
    assert licence.badge(_P("external")) is None  # pre-0017 row


# ---- API ----
def _mk(client, tmp_path, text=None, **extra):
    d = tmp_path / uuid.uuid4().hex[:8]
    d.mkdir()
    if text:
        (d / "LICENSE").write_text(text)
    r = client.post("/projects", json={"name": f"lic-{uuid.uuid4().hex[:8]}", "directory": str(d), **extra})
    assert r.status_code == 201, r.text
    return r.json(), d


def test_create_stores_licence_and_badge(client, tmp_path):
    p, _ = _mk(client, tmp_path, GPL3)
    assert p["licence"]["kind"] == "copyleft"
    assert p["licence_badge"]["label"] == "GPL-3.0 — share-alike"


def test_create_own_mit_no_badge(client, tmp_path):
    p, _ = _mk(client, tmp_path, MIT)
    if p["trust"] == "own":
        assert p["licence_badge"] is None
    assert p["licence"]["id"] == "MIT"


def test_create_no_file_says_none(client, tmp_path):
    p, _ = _mk(client, tmp_path)
    assert p["licence"]["kind"] == "none"
    assert p["licence_badge"]["label"] == "No licence found"


def test_licence_failure_never_fails_create(client, tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(licence, "_read_sync", boom)
    p, _ = _mk(client, tmp_path, MIT)
    assert p["licence"] is None and p["licence_badge"] is None


def test_directory_change_recomputes(client, tmp_path):
    p, _ = _mk(client, tmp_path, MIT)
    d2 = tmp_path / "other"
    d2.mkdir()
    (d2 / "LICENCE.md").write_text(AGPL)
    r = client.patch(f"/projects/{p['id']}", json={"directory": str(d2)})
    assert r.status_code == 200, r.text
    assert r.json()["licence"]["id"] == "AGPL-3.0"


def test_refresh_rereads(client, tmp_path):
    p, d = _mk(client, tmp_path, MIT)
    (d / "LICENSE").write_text(GPL3)
    r = client.post(f"/projects/{p['id']}/licence/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["licence"]["kind"] == "copyleft"
    assert client.get(f"/projects/{p['id']}").json()["licence"]["id"] == "GPL-3.0"


def test_refresh_is_maintainer_only(client, tmp_path):
    from conftest import login_as

    p, _ = _mk(client, tmp_path, MIT)
    contrib = login_as(f"lic-c-{uuid.uuid4().hex[:6]}@test.local")
    stranger = login_as(f"lic-s-{uuid.uuid4().hex[:6]}@test.local")
    r = client.put(f"/projects/{p['id']}/members",
                   json=[{"subject_type": "user", "subject_id": contrib.user_id, "role": "contributor"}])
    assert r.status_code == 200, r.text
    assert contrib.post(f"/projects/{p['id']}/licence/refresh").status_code == 403
    assert stranger.post(f"/projects/{p['id']}/licence/refresh").status_code == 404
