from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field


class StageEnum(str, Enum):
    script = "script"
    photo_upload = "photo_upload"
    voiceover = "voiceover"
    alignment = "alignment"
    storyboard = "storyboard"
    image_generation = "image_generation"
    video_generation = "video_generation"
    assembly = "assembly"
    final_review = "final_review"


class SubstageType(str, Enum):
    image = "image"
    video = "video"
    video_prompt = "video_prompt"


class CurrentSubstage(BaseModel):
    type: SubstageType
    index: int  # 1-based
    iteration: int = 1


class ImageApproval(BaseModel):
    slot: str  # e.g. "img_01"
    approved: bool = False
    iterations: int = 0
    approved_version: Optional[int] = None


class VideoApproval(BaseModel):
    clip_index: int  # 1-based
    approved: bool = False
    iterations: int = 0
    approved_version: Optional[int] = None
    veo_model: Optional[str] = None  # "fast" | "standard"
    prompt_iterations: int = 0
    prompt_approved_version: Optional[int] = None


class StageApproval(BaseModel):
    approved: bool = False
    iterations: int = 0
    approved_version: Optional[int] = None


class FinalReel(BaseModel):
    assembled: bool = False
    version: int = 0


class ApprovalState(BaseModel):
    script: StageApproval = Field(default_factory=StageApproval)
    # audio_tags has no user gate — tracked here only so we can version-tag
    # the script_tagged_v{n}.txt artifacts alongside the corresponding script.
    audio_tags: StageApproval = Field(default_factory=StageApproval)
    voiceover: StageApproval = Field(default_factory=StageApproval)
    storyboard: StageApproval = Field(default_factory=StageApproval)
    images: list[ImageApproval] = Field(default_factory=list)
    videos: list[VideoApproval] = Field(default_factory=list)
    final_reel: Optional[dict] = None  # serialised FinalReel; kept as dict for JSON compat


class SessionSettings(BaseModel):
    voice_id: Optional[str] = None
    voice_gender: Optional[str] = None  # "male" | "female" | "custom"
    voice_speed: float = 1.2
    autopilot_enabled: bool = False


SCHEMA_VERSION = 2  # 2 = single-image-per-clip; legacy sessions (with image_slot_end) are v1.


class SessionMetadata(BaseModel):
    session_id: str
    person_name: str
    schema_version: int = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    current_stage: StageEnum = StageEnum.script
    current_substage: Optional[CurrentSubstage] = None
    completed_stages: list[str] = Field(default_factory=list)
    approval_state: ApprovalState = Field(default_factory=ApprovalState)
    settings: SessionSettings = Field(default_factory=SessionSettings)
    photo_ext: Optional[str] = None  # extension of uploaded photo
    assembly_locked: bool = False


# ── Chat message types ──────────────────────────────────────────────────────

class SystemMessage(BaseModel):
    msg_type: Literal["system"] = "system"
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AppQuestion(BaseModel):
    msg_type: Literal["app_question"] = "app_question"
    question: str
    widget: Optional[dict] = None  # {"type": "buttons", "options": [...]} etc.
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class UserReply(BaseModel):
    msg_type: Literal["user_reply"] = "user_reply"
    text: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AssetCard(BaseModel):
    msg_type: Literal["asset_card"] = "asset_card"
    subtype: str  # "script" | "voiceover" | "storyboard" | etc.
    iteration: int = 1
    data: dict  # asset-specific payload
    status: str = "pending_approval"  # "pending_approval" | "approved" | "previous" | "rejected"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StatusPill(BaseModel):
    msg_type: Literal["status_pill"] = "status_pill"
    pill_id: str  # used to replace in-place on frontend
    message: str
    stage: str
    substage_index: Optional[int] = None
    resolved: bool = False  # True when replaced by asset card
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ErrorCard(BaseModel):
    msg_type: Literal["error_card"] = "error_card"
    error_message: str
    stage: str
    substage_index: Optional[int] = None
    allow_retry: bool = True
    allow_change: bool = True
    resolved: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)


ChatMessage = Union[SystemMessage, AppQuestion, UserReply, AssetCard, StatusPill, ErrorCard]


class ChatHistory(BaseModel):
    session_id: str
    messages: list[dict] = Field(default_factory=list)  # stored as dicts for JSON compat


# ── Asset schemas ───────────────────────────────────────────────────────────

class Script(BaseModel):
    """Spoken script as written by the script writer. Single source of truth."""
    full_text: str


class ImageDescription(BaseModel):
    subject_and_pose: str
    environment: str
    camera_framing: str
    lighting: str
    color_palette: str
    era_constraints: str
    camera_angle: str
    no_text_displays: bool = True
    realism_directive: str = (
        "photorealistic, documentary still, 35mm film grain, indistinguishable "
        "from a real archival photograph, no illustration, no CGI, no glossy AI sheen"
    )


class MotionArc(BaseModel):
    camera_move: str
    subject_action: str
    traversal: str
    era_atmosphere: str


class TransitionOut(BaseModel):
    type: str  # dissolve | fade | smoothleft | smoothright | fadeblack
    duration_seconds: float


class Scene(BaseModel):
    scene_id: str
    start_time: float       # filled by orchestrator from durations
    end_time: float         # filled by orchestrator from durations
    duration_seconds: int   # 4 | 6 | 8 (Veo 3.1 hard constraint)
    voiceover_text: str     # exact contiguous slice of alignment words
    setting_category: str
    location_anchor: str
    subject_life_stage: str
    age_continuity_note: str
    visual_beat: str
    era_year: Optional[int] = None
    shot_type: str          # WS | MS | CU | ECU
    camera_motion: str      # SLOW_PUSH_IN | SLOW_PULL_BACK | STATIC_LOCK | ...
    image_slot: str         # e.g. "img_01"
    face_reference_mode: Literal["match_age", "age_down_to", "skip_face_ref"]
    face_reference_target_age: Optional[int] = None
    image_description: ImageDescription
    motion_arc: MotionArc
    transition_out: TransitionOut


class VisualStyle(BaseModel):
    era: str
    film_stock: str
    dominant_palette: str
    lens_feel: str


class Storyboard(BaseModel):
    total_scenes: int
    total_clips: int
    total_images: int
    image_count: int
    total_duration_seconds: float
    visual_style: VisualStyle
    scenes: list[Scene]
    timing_calculation: str
    setting_plan: str
    word_coverage_check: str


class WordTimestamp(BaseModel):
    text: str
    start: float
    end: float


class AlignmentData(BaseModel):
    characters: list[dict]
    words: list[WordTimestamp]
    loss: Optional[float] = None


# ── Request/Response models ─────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    context: Optional[str] = Field(None, max_length=500)


class ActionRequest(BaseModel):
    action: str  # "approve" | "change" | "regenerate" | "select"
    stage: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionListItem(BaseModel):
    session_id: str
    person_name: str
    current_stage: str
    created_at: datetime
    updated_at: datetime


class WSMessage(BaseModel):
    type: str  # "status" | "asset_ready" | "error"
    message: str
    stage: str
    substage_index: Optional[int] = None
    pill_id: Optional[str] = None
    data: Optional[dict] = None
