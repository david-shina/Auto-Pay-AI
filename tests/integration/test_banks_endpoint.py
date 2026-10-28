"""Integration tests for /api/v1/banks (the bank picker endpoints)."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _signup(c: TestClient) -> str:
    stamp = int(time.time())
    r = c.post(
        "/api/v1/auth/signup",
        json={
            "first_name": "Bank",
            "last_name": "Tester",
            "email": f"banks_{stamp}_{id(c)}@x.com",
            "phone_number": f"+1{str(stamp)[-10:]}",
            "password": "Secret123",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/banks").status_code == 401
    assert client.get("/api/v1/banks/resolve?q=GTBank").status_code == 401


def test_search_by_name_returns_the_bank(client: TestClient) -> None:
    token = _signup(client)
    r = client.get("/api/v1/banks?q=GTBank", headers=_auth(token))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows
    assert rows[0]["code"] == "058"
    assert rows[0]["name"] == "Guaranty Trust Bank"
    assert rows[0]["exact"] is True


def test_empty_query_populates_a_dropdown(client: TestClient) -> None:
    token = _signup(client)
    r = client.get("/api/v1/banks?limit=5", headers=_auth(token))
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 5
    # Alphabetical, so the picker has a stable first screen.
    assert [row["name"] for row in rows] == sorted(row["name"] for row in rows)


def test_resolve_exact_match(client: TestClient) -> None:
    token = _signup(client)
    r = client.get("/api/v1/banks/resolve?q=UBA", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["exact"] is not None
    assert body["exact"]["code"] == "033"


def test_resolve_ambiguous_returns_candidates_not_an_exact(
    client: TestClient,
) -> None:
    token = _signup(client)
    r = client.get("/api/v1/banks/resolve?q=microfinance", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["exact"] is None
    assert len(body["matches"]) > 1
