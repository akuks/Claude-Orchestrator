from app.health import runner


def test_health_is_admin_only(client, member_client, anon_client):
    assert member_client.get("/admin/health").status_code == 403
    assert anon_client.get("/admin/health").status_code == 401


def test_health_shape(client):
    runner.clear_cache()
    r = client.get("/admin/health")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["overall"] in ("ok", "attention", "problem")
    assert d["checked_at"]
    assert [c["id"] for c in d["checks"]][:2] == ["claude", "python"]
    for c in d["checks"]:
        assert c["status"] in ("ok", "attention", "problem") and c["title"] and c["summary"]
    runner.clear_cache()


def test_refresh_skips_the_cache(client):
    runner.clear_cache()
    a = client.get("/admin/health").json()
    assert client.get("/admin/health").json()["checked_at"] == a["checked_at"]  # cached
    b = client.get("/admin/health?refresh=true").json()
    assert b["checked_at"] != a["checked_at"]
    runner.clear_cache()
