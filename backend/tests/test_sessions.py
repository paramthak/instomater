"""Tests for session CRUD and photo upload endpoints."""
from __future__ import annotations

import json
import io
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import make_test_image


def test_create_session(client):
    resp = client.post("/sessions", json={"name": "Sundar Pichai", "context": "CEO of Google"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["person_name"] == "Sundar Pichai"
    assert data["session_id"]
    assert data["current_stage"] == "topic_brief"


def test_create_session_name_too_short(client):
    resp = client.post("/sessions", json={"name": "A"})
    assert resp.status_code == 422


def test_list_sessions(client):
    for name in ["Alice", "Bob", "Carol"]:
        client.post("/sessions", json={"name": name})
    resp = client.get("/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) >= 3
    names = [s["person_name"] for s in sessions]
    assert "Alice" in names


def test_get_session(client):
    r = client.post("/sessions", json={"name": "GetTest"})
    sid = r.json()["session_id"]
    resp = client.get(f"/sessions/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert "metadata" in data
    assert "chat_history" in data
    assert data["metadata"]["session_id"] == sid


def test_get_session_not_found(client):
    resp = client.get("/sessions/nonexistent-uuid")
    assert resp.status_code == 404


def test_delete_session(client):
    r = client.post("/sessions", json={"name": "ToDelete"})
    sid = r.json()["session_id"]
    assert client.delete(f"/sessions/{sid}").status_code == 204
    assert client.get(f"/sessions/{sid}").status_code == 404


def test_upload_photo_valid(client):
    r = client.post("/sessions", json={"name": "PhotoTest"})
    sid = r.json()["session_id"]
    img_bytes = make_test_image(600, 800, "JPEG")
    resp = client.post(
        f"/sessions/{sid}/photo",
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "photo_path" in data


def test_upload_photo_small_dimensions_allowed(client):
    r = client.post("/sessions", json={"name": "SmallPhoto"})
    sid = r.json()["session_id"]
    img_bytes = make_test_image(300, 400, "JPEG")
    resp = client.post(
        f"/sessions/{sid}/photo",
        files={"file": ("photo.jpg", img_bytes, "image/jpeg")},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_upload_photo_too_large(client):
    r = client.post("/sessions", json={"name": "LargePhoto"})
    sid = r.json()["session_id"]
    # 11 MB of zeros — content-type check happens first for mime type, but size check second
    big_data = b"\x00" * (11 * 1024 * 1024)
    # Create a valid-looking JPEG header but large
    img_bytes = make_test_image(600, 800, "JPEG") + b"\x00" * (11 * 1024 * 1024)
    resp = client.post(
        f"/sessions/{sid}/photo",
        files={"file": ("photo.jpg", img_bytes[:11*1024*1024], "image/jpeg")},
    )
    assert resp.status_code == 400
    assert "large" in resp.json()["detail"].lower()


def test_upload_photo_wrong_type(client):
    r = client.post("/sessions", json={"name": "WrongType"})
    sid = r.json()["session_id"]
    resp = client.post(
        f"/sessions/{sid}/photo",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "type" in resp.json()["detail"].lower()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
