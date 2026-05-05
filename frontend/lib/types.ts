export type StageEnum =
  | "topic_brief"
  | "photo_upload"
  | "script"
  | "voiceover"
  | "alignment"
  | "storyboard"
  | "clarifying_questions"
  | "image_generation"
  | "video_generation"
  | "assembly"
  | "final_review";

export interface SessionMetadata {
  session_id: string;
  person_name: string;
  created_at: string;
  updated_at: string;
  current_stage: StageEnum;
  current_substage: { type: string; index: number; iteration: number } | null;
  completed_stages: string[];
  approval_state: ApprovalState;
  settings: { voice_id?: string; voice_gender?: string };
  photo_ext?: string;
}

export interface ImageApproval {
  slot: string;
  approved: boolean;
  iterations: number;
  approved_version: number | null;
}

export interface VideoApproval {
  clip_index: number;
  approved: boolean;
  iterations: number;
  approved_version: number | null;
  veo_model: string | null;
  prompt_iterations: number;
  prompt_approved_version: number | null;
}

export interface StageApproval {
  approved: boolean;
  iterations: number;
  approved_version: number | null;
}

export interface ApprovalState {
  topic_brief: StageApproval;
  script: StageApproval;
  voiceover: StageApproval;
  storyboard: StageApproval;
  images: ImageApproval[];
  videos: VideoApproval[];
  final_reel: { assembled: boolean; version: number } | null;
}

export interface SessionListItem {
  session_id: string;
  person_name: string;
  current_stage: string;
  created_at: string;
  updated_at: string;
}

// ── Chat message types ───────────────────────────────────────────────────────

export type ChatMessageType =
  | "system"
  | "app_question"
  | "user_reply"
  | "asset_card"
  | "status_pill"
  | "error_card";

export interface SystemMessage {
  msg_type: "system";
  message: string;
  timestamp: string;
}

export interface AppQuestion {
  msg_type: "app_question";
  question: string;
  widget?: {
    type: "buttons" | "photo_upload" | "photo_confirm" | "voice_select";
    options?: string[];
    photo_path?: string;
  };
  timestamp: string;
}

export interface UserReply {
  msg_type: "user_reply";
  text: string;
  timestamp: string;
}

export interface AssetCard {
  msg_type: "asset_card";
  subtype: string;
  iteration: number;
  data: Record<string, unknown>;
  status: "pending_approval" | "approved" | "rejected";
  timestamp: string;
}

export interface StatusPill {
  msg_type: "status_pill";
  pill_id: string;
  message: string;
  stage: string;
  substage_index: number | null;
  resolved: boolean;
  timestamp: string;
}

export interface ErrorCard {
  msg_type: "error_card";
  error_message: string;
  stage: string;
  substage_index: number | null;
  allow_retry: boolean;
  allow_change: boolean;
  resolved?: boolean;
  timestamp: string;
}

export type ChatMessage =
  | SystemMessage
  | AppQuestion
  | UserReply
  | AssetCard
  | StatusPill
  | ErrorCard;

// ── Asset-specific data shapes ───────────────────────────────────────────────

export interface LifeMilestone {
  year: number;
  event: string;
}

export interface TopicBriefData {
  person_name: string;
  person_slug: string;
  gender: string;
  origin_country: string;
  origin_city: string;
  current_role_or_legacy: string;
  key_life_milestones: LifeMilestone[];
  narrative_arc_options: string[];
  selected_narrative_arc: string;
  tone_suggestions: string[];
  selected_tone: string;
  factual_anchors_for_visuals: string[];
  estimated_target_duration_seconds: number;
}

export interface ScriptData {
  hook_category: string;
  hook_formula_used: string;
  hook_subtype_used?: string;
  perspective: string;
  structure?: {
    hook: string;
    setup: string;
    build: string;
    landing: string;
  };
  full_text: string;
  estimated_word_count: number;
  estimated_duration_seconds: number;
  name_mentions_count?: number;
  self_check?: Record<string, unknown>;
}

export interface Scene {
  scene_id: string;
  script_part?: "hook" | "setup" | "build" | "landing";
  start_time: number;
  end_time: number;
  duration_seconds: number;
  voiceover_words: string;
  voiceover_text?: string;
  visual_description: string;
  image_slot_start: string;
  image_slot_end: string;
  image_start?: string;
  image_end?: string;
  shot_type?: "WS" | "MS" | "CU" | "ECU";
  camera_motion?: string;
  image_start_description?: Record<string, string>;
  image_end_description?: Record<string, string>;
  video_motion_prompt?: Record<string, string>;
  transition_in: string;
  transition_out: string;
  transition_out_detail?: { type: string; duration_seconds: number };
  transition_duration_seconds: number;
  visual_narration_check?: string;
}

export interface StoryboardData {
  total_scenes: number;
  total_clips?: number;
  total_duration_seconds: number;
  image_count: number;
  total_images?: number;
  visual_style?: {
    era: string;
    film_stock: string;
    dominant_palette: string;
    lens_feel: string;
  };
  scenes: Scene[];
  self_check?: Record<string, unknown>;
}

export interface ClarifyingQuestion {
  id: string;
  question_text: string;
  options: string[];
  rationale: string;
}

export interface ClarifyingQuestionsData {
  questions: ClarifyingQuestion[];
}

// ── WS messages ──────────────────────────────────────────────────────────────

export interface WSMessage {
  type: "status" | "asset_ready" | "error";
  message?: string;
  stage: string;
  substage_index?: number;
  pill_id?: string;
  data?: Record<string, unknown>;
}
