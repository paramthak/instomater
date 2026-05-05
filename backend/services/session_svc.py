from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles

from config import SESSIONS_DIR, MIN_DISK_BYTES
from models.session import (
    SessionMetadata,
    SessionSettings,
    ApprovalState,
    ChatHistory,
    StageEnum,
)


def _session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id


def _metadata_path(session_id: str) -> Path:
    return _session_dir(session_id) / "metadata.json"


def _chat_path(session_id: str) -> Path:
    return _session_dir(session_id) / "chat_history.json"


def _check_disk_space() -> None:
    usage = shutil.disk_usage(SESSIONS_DIR)
    if usage.free < MIN_DISK_BYTES:
        free_gb = usage.free / (1024 ** 3)
        raise IOError(
            f"Insufficient disk space: {free_gb:.1f} GB free. "
            "At least 2 GB required to create a new session."
        )


def create_session(name: str, context: Optional[str] = None) -> SessionMetadata:
    _check_disk_space()
    session_id = str(uuid.uuid4())
    session_path = _session_dir(session_id)
    session_path.mkdir(parents=True)
    (session_path / "images").mkdir()
    (session_path / "videos").mkdir()
    (session_path / "video_prompts").mkdir()
    (session_path / "final").mkdir()
    (session_path / "logs").mkdir()

    metadata = SessionMetadata(
        session_id=session_id,
        person_name=name,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    _write_metadata(metadata)

    chat = ChatHistory(session_id=session_id, messages=[])
    _write_chat(session_id, chat)

    return metadata


def get_session(session_id: str) -> SessionMetadata:
    path = _metadata_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    data = json.loads(path.read_text())
    return SessionMetadata(**data)


def list_sessions() -> list[SessionMetadata]:
    sessions = []
    for path in sorted(SESSIONS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta_file = path / "metadata.json"
        if meta_file.exists():
            try:
                sessions.append(SessionMetadata(**json.loads(meta_file.read_text())))
            except Exception:
                pass
    return sessions


def delete_session(session_id: str) -> None:
    path = _session_dir(session_id)
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    shutil.rmtree(path)


def update_metadata(session_id: str, **fields) -> SessionMetadata:
    metadata = get_session(session_id)
    for k, v in fields.items():
        if k == "session_id":
            continue  # never overwrite the immutable session ID
        setattr(metadata, k, v)
    metadata.updated_at = datetime.utcnow()
    _write_metadata(metadata)
    return metadata


def _write_metadata(metadata: SessionMetadata) -> None:
    path = _metadata_path(metadata.session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(metadata.model_dump_json(indent=2))
    tmp.replace(path)


def _write_chat(session_id: str, chat: ChatHistory) -> None:
    path = _chat_path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(chat.model_dump_json(indent=2))
    tmp.replace(path)


def get_chat_history(session_id: str) -> ChatHistory:
    path = _chat_path(session_id)
    if not path.exists():
        return ChatHistory(session_id=session_id, messages=[])
    return ChatHistory(**json.loads(path.read_text()))


def append_chat_message(session_id: str, message: dict) -> None:
    history = get_chat_history(session_id)
    history.messages.append(message)
    _write_chat(session_id, history)


def resolve_status_pill(session_id: str, pill_id: str) -> None:
    """Mark a status pill as resolved so the spinner stops."""
    history = get_chat_history(session_id)
    for msg in reversed(history.messages):
        if msg.get("msg_type") == "status_pill" and msg.get("pill_id") == pill_id:
            msg["resolved"] = True
            break
    _write_chat(session_id, history)


def update_status_pill_message(session_id: str, pill_id: str, message: str) -> None:
    """Update the visible text for a running status pill."""
    history = get_chat_history(session_id)
    for msg in reversed(history.messages):
        if msg.get("msg_type") == "status_pill" and msg.get("pill_id") == pill_id:
            msg["message"] = message
            break
    _write_chat(session_id, history)


def resolve_status_pills(
    session_id: str,
    stage: str | None = None,
    substage_index: int | None = None,
) -> None:
    """Mark active status pills as resolved when a retry supersedes them."""
    history = get_chat_history(session_id)
    changed = False
    for msg in history.messages:
        if msg.get("msg_type") != "status_pill" or msg.get("resolved"):
            continue
        if stage is not None and msg.get("stage") != stage:
            continue
        if substage_index is not None and msg.get("substage_index") != substage_index:
            continue
        msg["resolved"] = True
        changed = True
    if changed:
        _write_chat(session_id, history)


def resolve_error_cards(
    session_id: str,
    stage: str | None = None,
    substage_index: int | None = None,
) -> None:
    """Mark active error cards as resolved once corrective work starts."""
    history = get_chat_history(session_id)
    changed = False
    for msg in history.messages:
        if msg.get("msg_type") != "error_card" or msg.get("resolved"):
            continue
        if stage is not None and msg.get("stage") != stage:
            continue
        if substage_index is not None and msg.get("substage_index") != substage_index:
            continue
        msg["resolved"] = True
        changed = True
    if changed:
        _write_chat(session_id, history)


def approve_last_asset_card(session_id: str, subtype: str) -> None:
    """Set the most recent asset card of a given subtype to 'approved'."""
    history = get_chat_history(session_id)
    for msg in reversed(history.messages):
        if msg.get("msg_type") == "asset_card" and msg.get("subtype") == subtype:
            msg["status"] = "approved"
            break
    _write_chat(session_id, history)


def save_asset(session_id: str, relative_path: str, data: bytes) -> Path:
    dest = _session_dir(session_id) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def save_text_asset(session_id: str, relative_path: str, text: str) -> Path:
    dest = _session_dir(session_id) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    return dest


def save_json_asset(session_id: str, relative_path: str, data: dict) -> Path:
    dest = _session_dir(session_id) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2))
    return dest


def load_json_asset(session_id: str, relative_path: str) -> dict:
    path = _session_dir(session_id) / relative_path
    return json.loads(path.read_text())


def asset_exists(session_id: str, relative_path: str) -> bool:
    return (_session_dir(session_id) / relative_path).exists()


def symlink_approved(session_id: str, src_relative: str, approved_relative: str) -> None:
    """Copy src to the approved path (using copy instead of symlink for portability)."""
    src = _session_dir(session_id) / src_relative
    dst = _session_dir(session_id) / approved_relative
    import shutil as _shutil
    _shutil.copy2(src, dst)


def get_asset_path(session_id: str, relative_path: str) -> Path:
    return _session_dir(session_id) / relative_path


def get_session_dir(session_id: str) -> Path:
    return _session_dir(session_id)


def log_api_call(session_id: str, entry: str) -> None:
    log_path = _session_dir(session_id) / "logs" / "api_calls.log"
    with open(log_path, "a") as f:
        f.write(entry + "\n")
