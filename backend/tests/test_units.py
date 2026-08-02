"""Pure-function unit tests — no app/DB required."""

import hashlib
import hmac

from app import crypto, mcp_manager, reports, scheduler, security
from app.models import Task
from app.routers import webhooks
from app.task_service import classify_risk
from app.worker import _is_transient


def test_crypto_roundtrip():
    blob = crypto.encrypt_json({"token": "super-secret", "n": 1})
    assert blob and "super-secret" not in blob
    assert crypto.decrypt_json(blob) == {"token": "super-secret", "n": 1}
    assert crypto.decrypt_json("") == {}


def test_classify_risk():
    assert classify_risk("merge PR #1 into main", None) == "critical"
    assert classify_risk("git push --force", None) == "critical"
    assert classify_risk("list the files", None) == "info"
    assert classify_risk("do something", "proj-id") == "warning"
    # Read-only security reviews mention 'merge' in prohibitions — must NOT gate.
    assert classify_risk("never merge PRs; audit only", None, ["vapt"]) == "warning"


def test_is_transient():
    assert _is_transient("API Error: 429 rate limit exceeded") is True
    assert _is_transient("overloaded_error 529") is True
    assert _is_transient("stopped: error_max_turns reached") is False
    assert _is_transient("some deterministic bug") is False


def test_fingerprint_normalized():
    a = security._fingerprint("p", "SQL Injection", "auth.py", "OWASP A03")
    b = security._fingerprint("p", "  sql   injection ", "auth.py", "owasp a03")
    assert a == b
    assert a != security._fingerprint("p", "SQL Injection", "other.py", "OWASP A03")


def test_security_prompt():
    p = security.build_security_prompt("feature/login", "main", "changed")
    assert "feature/login" in p and "READ-ONLY" in p and "OWASP" in p


def test_webhook_signature():
    body = b'{"action":"opened"}'
    good = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    assert webhooks._verify(body, good) is True
    assert webhooks._verify(body, "sha256=deadbeef") is False
    assert webhooks._verify(body, None) is False


def test_reports_pdf_docx():
    task = Task(
        id="report01",
        title="Test Report",
        prompt="p",
        status="completed",
        result_text="## Summary\n- finding one\n- finding two\n\nSome details.",
    )
    pdf = reports.build_pdf(task)
    docx = reports.build_docx(task)
    assert pdf[:4] == b"%PDF"
    assert docx[:2] == b"PK"  # zip/docx magic


def test_mcp_tool_classification():
    assert mcp_manager.classify_tool("list_pull_requests") == "read"
    assert mcp_manager.classify_tool("get_file_contents") == "read"
    assert mcp_manager.classify_tool("send_message") == "write"
    assert mcp_manager.default_action("read") == "auto_approve"
    assert mcp_manager.default_action("write") == "require_approval"


def test_cron_validation():
    assert scheduler.is_valid_cron("0 9 * * 1-5")
    assert not scheduler.is_valid_cron("nonsense")
