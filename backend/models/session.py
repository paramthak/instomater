from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field


class StageEnum(str, Enum):
    topic_brief = "topic_brief"
    photo_upload = "photo_upload"
    script = "script"
    voiceover = "voiceover"
    alignment = "alignment"
    storyboard = "storyboard"
    clarifying_questions = "clarifying_questions"
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


class ApprovalState(BaseModel):
    topic_brief: StageApproval = Field(default_factory=StageApproval)
    script: StageApproval = Field(default_factory=StageApproval)
    voiceover: StageApproval = Field(default_factory=StageApproval)
    storyboard: StageApproval = Field(default_factory=StageApproval)
    images: list[ImageApproval] = Field(default_factory=list)
    videos: list[VideoApproval] = Field(default_factory=list)
    final_reel: Optional[dict] = None


class SessionSettings(BaseModel):
    voice_id: Optional[str] = None
    voice_gender: Optional[str] = None  # "male" | "female"


class SessionMetadata(BaseModel):
    session_id: str
    person_name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    current_stage: StageEnum = StageEnum.topic_brief
    current_substage: Optional[CurrentSubstage] = None
    completed_stages: list[str] = Field(default_factory=list)
    approval_state: ApprovalState = Field(default_factory=ApprovalState)
    settings: SessionSettings = Field(default_factory=SessionSettings)
    photo_ext: Optional[str] = None  # extension of uploaded photo


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
    subtype: str  # "topic_brief" | "script" | "voiceover" | "storyboard" | etc.
    iteration: int = 1
    data: dict  # asset-specific payload
    status: str = "pending_approval"  # "pending_approval" | "approved" | "rejected"
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

class LifeMilestone(BaseModel):
    year: int
    event: str


class TopicBrief(BaseModel):
    person_name: str
    person_slug: str
    gender: str
    origin_country: str
    origin_city: str
    current_role_or_legacy: str
    key_life_milestones: list[LifeMilestone]
    narrative_arc_options: list[str]
    selected_narrative_arc: str
    tone_suggestions: list[str]
    selected_tone: str
    factual_anchors_for_visuals: list[str]
    estimated_target_duration_seconds: int


class Script(BaseModel):
    hook_category: str
    hook_formula_used: str
    perspective: str
    full_text: str
    estimated_word_count: int
    estimated_duration_seconds: int


class Scene(BaseModel):
    scene_id: str
    start_time: float
    end_time: float
    duration_seconds: int  # must be 4, 6, or 8
    voiceover_words: str
    visual_description: str
    image_slot_start: str
    image_slot_end: str
    transition_in: str
    transition_out: str
    transition_duration_seconds: float


class Storyboard(BaseModel):
    total_scenes: int
    total_duration_seconds: float
    image_count: int
    scenes: list[Scene]


class ClarifyingQuestion(BaseModel):
    id: str
    question_text: str
    options: list[str]
    rationale: str


class ClarifyingQuestions(BaseModel):
    questions: list[ClarifyingQuestion]


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
