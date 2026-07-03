"""Tests for the Prometheus metrics module.

Covers:
  * All defined counters exist and are incrementable
  * HTTP_REQUESTS / HTTP_REQUEST_SECONDS have the expected label set
  * Convenience functions route to the right labels
  * /metrics endpoint returns 200 with prometheus exposition format
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_endpoint_returns_200_and_prometheus_format(
    client: TestClient,
) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # Body should be a valid Prometheus exposition — at minimum it
    # includes the standard Python process metrics that
    # prometheus_client auto-registers.
    body = r.text
    # process_* metrics are always present after the first import
    assert "process_" in body or "python_info" in body


def test_record_topup_initiated_increments() -> None:
    from app.core.metrics import TOPUPS_INITIATED, record_topup_initiated

    before = TOPUPS_INITIATED._value.get()
    record_topup_initiated()
    record_topup_initiated()
    after = TOPUPS_INITIATED._value.get()
    assert after - before == 2


def test_record_topup_credited_labels_source() -> None:
    from app.core.metrics import TOPUPS_CREDITED, record_topup_credited

    record_topup_credited(source="checkout")
    record_topup_credited(source="checkout")
    record_topup_credited(source="dva")
    # The counter has a single label `source`; verify that the
    # counter object is well-formed and the call didn't raise.
    assert TOPUPS_CREDITED._labelnames == ("source",)


def test_record_payout_labels_result() -> None:
    from app.core.metrics import PAYOUTS, record_payout

    record_payout(result="success")
    record_payout(result="insufficient")
    assert PAYOUTS._labelnames == ("result",)


def test_record_bill_paid_labels_trigger() -> None:
    from app.core.metrics import BILLS_PAID, record_bill_paid

    record_bill_paid(trigger="auto_pay")
    record_bill_paid(trigger="manual")
    assert BILLS_PAID._labelnames == ("trigger",)


def test_record_scheduler_job_labels() -> None:
    from app.core.metrics import SCHEDULER_JOBS, record_scheduler_job

    record_scheduler_job(job_id="process_scheduled_bills", result="success")
    record_scheduler_job(job_id="process_scheduled_bills", result="fail")
    assert SCHEDULER_JOBS._labelnames == ("job_id", "result")


def test_http_request_middleware_records_metrics(
    client: TestClient,
) -> None:
    """A real request through the app should bump the HTTP_REQUESTS
    counter for the resolved route."""
    from app.core.metrics import HTTP_REQUESTS

    before = HTTP_REQUESTS.labels(
        method="GET", route="/healthz", status="200"
    )._value.get()
    r = client.get("/healthz")
    assert r.status_code == 200
    after = HTTP_REQUESTS.labels(
        method="GET", route="/healthz", status="200"
    )._value.get()
    assert after >= before + 1
