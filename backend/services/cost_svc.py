from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from services.session_svc import get_session_dir


# OpenAI pricing (May 2026, USD per 1M tokens).
# Short-context tier applies for prompts ≤ 272K input tokens.
# Long-context tier (2x input, 1.5x output) kicks in above that threshold.
_LONG_CONTEXT_THRESHOLD_TOKENS = 272_000

OPENAI_RATES = {
    "gpt-5.4": {
        "input_per_million": 2.50,
        "output_per_million": 15.00,
        "long_input_per_million": 5.00,
        "long_output_per_million": 22.50,
    },
    "gpt-5.4-mini": {
        "input_per_million": 0.75,
        "output_per_million": 4.50,
    },
    "gpt-5.4-pro": {
        "input_per_million": 60.00,
        "output_per_million": 270.00,
    },
    # Legacy fallback — kept for any session that ran before the model migration.
    "gpt-4.1": {"input_per_million": 2.00, "output_per_million": 8.00},
}

_OPENAI_DEFAULT_MODEL = "gpt-5.4"

GEMINI_TEXT_RATES = {
    "gemini-2.5-flash": {"input_per_million": 0.30, "output_per_million": 2.50},
}

GEMINI_IMAGE_RATES = {
    "gemini-3-pro-image-preview": {
        "input_per_million": 2.00,
        "output_per_million": 12.00,
        "input_image": 0.0011,
        "output_image": 0.134,
    },
}

VEO_RATES = {
    "fast": {"video_only_per_second": 0.10},
    "standard": {"video_only_per_second": 0.20},
}

ELEVENLABS_TTS_RATES = {
    "default_per_1k_chars": 0.10,
    "flash_turbo_per_1k_chars": 0.05,
}

ELEVENLABS_ALIGNMENT_RATE_PER_HOUR = 0.22


def _ledger_path(session_id: str) -> Path:
    return get_session_dir(session_id) / "logs" / "cost_ledger.json"


def _read_entries(session_id: str) -> list[dict[str, Any]]:
    path = _ledger_path(session_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return list(data.get("entries", []))
    return data if isinstance(data, list) else []


def _write_entries(session_id: str, entries: list[dict[str, Any]]) -> None:
    path = _ledger_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_entries(entries)
    payload = {"entries": entries, "summary": summary}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _new_entry(
    *,
    provider: str,
    model: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    status: str,
    units: dict[str, Any],
    unit_rates: dict[str, Any],
    cost_usd: float,
) -> dict[str, Any]:
    return {
        "id": f"cost_{uuid.uuid4().hex[:10]}",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "provider": provider,
        "model": model,
        "stage": stage,
        "asset_type": asset_type,
        "asset_id": asset_id,
        "version": version,
        "status": status,
        "units": units,
        "unit_rates": unit_rates,
        "cost_usd": round(float(cost_usd), 6),
    }


def append_entry(session_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    entries = _read_entries(session_id)
    entries.append(entry)
    _write_entries(session_id, entries)
    return entry


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_provider: dict[str, float] = {}
    by_stage: dict[str, float] = {}
    total = 0.0
    for entry in entries:
        cost = float(entry.get("cost_usd") or 0)
        total += cost
        by_provider[entry.get("provider", "unknown")] = by_provider.get(entry.get("provider", "unknown"), 0.0) + cost
        by_stage[entry.get("stage", "unknown")] = by_stage.get(entry.get("stage", "unknown"), 0.0) + cost
    return {
        "total_usd": round(total, 6),
        "by_provider": {key: round(value, 6) for key, value in sorted(by_provider.items())},
        "by_stage": {key: round(value, 6) for key, value in sorted(by_stage.items())},
        "entry_count": len(entries),
    }


def get_ledger(session_id: str) -> dict[str, Any]:
    entries = _read_entries(session_id)
    return {"entries": entries, "summary": summarize_entries(entries)}


def asset_cost(
    session_id: str,
    *,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
) -> dict[str, Any]:
    entries = [
        entry for entry in _read_entries(session_id)
        if entry.get("asset_type") == asset_type
        and entry.get("asset_id") == asset_id
        and entry.get("version") == version
    ]
    return {"entries": entries, "summary": summarize_entries(entries)}


def log_openai_chat(
    session_id: str,
    *,
    model: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    usage: Any,
    status: str = "succeeded",
) -> dict[str, Any]:
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    reasoning_tokens = int(
        getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
    )
    rates = OPENAI_RATES.get(model, OPENAI_RATES[_OPENAI_DEFAULT_MODEL])
    is_long_context = (
        prompt_tokens > _LONG_CONTEXT_THRESHOLD_TOKENS
        and "long_input_per_million" in rates
    )
    input_rate = rates["long_input_per_million" if is_long_context else "input_per_million"]
    output_rate = rates["long_output_per_million" if is_long_context else "output_per_million"]
    # Reasoning tokens are billed at the output rate.
    cost = (
        prompt_tokens / 1_000_000 * input_rate
        + completion_tokens / 1_000_000 * output_rate
    )
    return append_entry(session_id, _new_entry(
        provider="openai",
        model=model,
        stage=stage,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        status=status,
        units={
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "reasoning_tokens": reasoning_tokens,
            "context_tier": "long" if is_long_context else "short",
        },
        unit_rates={"input_per_million": input_rate, "output_per_million": output_rate},
        cost_usd=cost,
    ))


def log_gemini_text(
    session_id: str,
    *,
    model: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    usage_metadata: Any,
    status: str = "succeeded",
) -> dict[str, Any]:
    input_tokens = int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
    output_tokens = int(getattr(usage_metadata, "candidates_token_count", 0) or 0)
    rates = GEMINI_TEXT_RATES.get(model, GEMINI_TEXT_RATES["gemini-2.5-flash"])
    cost = (
        input_tokens / 1_000_000 * rates["input_per_million"]
        + output_tokens / 1_000_000 * rates["output_per_million"]
    )
    return append_entry(session_id, _new_entry(
        provider="gemini",
        model=model,
        stage=stage,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        status=status,
        units={"input_tokens": input_tokens, "output_tokens": output_tokens},
        unit_rates=rates,
        cost_usd=cost,
    ))


def log_gemini_image(
    session_id: str,
    *,
    model: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    input_images: int,
    output_images: int = 1,
    usage_metadata: Any = None,
    status: str = "succeeded",
) -> dict[str, Any]:
    input_tokens = int(getattr(usage_metadata, "prompt_token_count", 0) or 0)
    output_tokens = int(getattr(usage_metadata, "candidates_token_count", 0) or 0)
    rates = GEMINI_IMAGE_RATES.get(model, GEMINI_IMAGE_RATES["gemini-3-pro-image-preview"])
    cost = (
        input_tokens / 1_000_000 * rates["input_per_million"]
        + output_tokens / 1_000_000 * rates["output_per_million"]
        + input_images * rates["input_image"]
        + output_images * rates["output_image"]
    )
    return append_entry(session_id, _new_entry(
        provider="gemini",
        model=model,
        stage=stage,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        status=status,
        units={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_images": input_images,
            "output_images": output_images,
        },
        unit_rates=rates,
        cost_usd=cost,
    ))


def log_veo(
    session_id: str,
    *,
    model_variant: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    duration_seconds: float,
    sample_count: int,
    status: str = "succeeded",
) -> dict[str, Any]:
    variant = "standard" if model_variant == "standard" else "fast"
    rates = VEO_RATES[variant]
    billable_seconds = float(duration_seconds) * int(sample_count)
    cost = billable_seconds * rates["video_only_per_second"]
    return append_entry(session_id, _new_entry(
        provider="veo",
        model=f"veo-3.1-{variant}",
        stage=stage,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        status=status,
        units={"duration_seconds": duration_seconds, "sample_count": sample_count, "billable_seconds": billable_seconds},
        unit_rates=rates,
        cost_usd=cost,
    ))


def log_elevenlabs_tts(
    session_id: str,
    *,
    model: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    characters: int,
    status: str = "succeeded",
) -> dict[str, Any]:
    lowered = model.lower()
    rate = (
        ELEVENLABS_TTS_RATES["flash_turbo_per_1k_chars"]
        if "flash" in lowered or "turbo" in lowered
        else ELEVENLABS_TTS_RATES["default_per_1k_chars"]
    )
    cost = characters / 1000 * rate
    return append_entry(session_id, _new_entry(
        provider="elevenlabs",
        model=model,
        stage=stage,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        status=status,
        units={"characters": characters},
        unit_rates={"per_1k_chars": rate},
        cost_usd=cost,
    ))


def log_elevenlabs_alignment(
    session_id: str,
    *,
    model: str,
    stage: str,
    asset_type: str,
    asset_id: str,
    version: Optional[int],
    audio_seconds: float,
    status: str = "succeeded",
) -> dict[str, Any]:
    hours = max(float(audio_seconds), 0.0) / 3600
    cost = hours * ELEVENLABS_ALIGNMENT_RATE_PER_HOUR
    return append_entry(session_id, _new_entry(
        provider="elevenlabs",
        model=model,
        stage=stage,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        status=status,
        units={"audio_seconds": audio_seconds, "audio_hours": hours},
        unit_rates={"per_audio_hour": ELEVENLABS_ALIGNMENT_RATE_PER_HOUR},
        cost_usd=cost,
    ))
