export type StageEnum =
  | "script"
  | "photo_upload"
  | "voiceover"
  | "alignment"
  | "storyboard"
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
  settings: { voice_id?: string; voice_gender?: string; voice_speed?: number; autopilot_enabled?: boolean };
  photo_ext?: string;
  assembly_locked?: boolean;
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
  status: "pending_approval" | "approved" | "previous" | "rejected";
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

export interface ScriptData {
  full_text: string;
  display_text?: string;
  estimated_word_count: number;
  estimated_duration_seconds: number;
  cost_summary?: CostSummary;
}

export type CameraAngle =
  | "front-3/4"
  | "side-profile"
  | "over-shoulder"
  | "low-angle"
  | "high-angle"
  | "wide-establish";

export type FaceReferenceMode = "match_age" | "age_down_to" | "skip_face_ref";

export interface ImageDescription {
  subject_and_pose: string;
  environment: string;
  camera_framing: string;
  lighting: string;
  color_palette: string;
  era_constraints: string;
  camera_angle: CameraAngle;
  no_text_displays: boolean;
  realism_directive: string;
}

export interface MotionArc {
  camera_move: string;
  subject_action: string;
  traversal: string;
  era_atmosphere: string;
}

export interface Scene {
  scene_id: string;
  start_time: number;
  end_time: number;
  duration_seconds: number;
  voiceover_text: string;
  voiceover_words?: string; // legacy alias
  setting_category?: string;
  location_anchor?: string;
  visual_beat?: string;
  subject_life_stage?: string;
  age_continuity_note?: string;
  era_year?: number | null;
  shot_type?: "WS" | "MS" | "CU" | "ECU";
  camera_motion?: string;
  image_slot: string;
  image_description: ImageDescription;
  motion_arc: MotionArc;
  face_reference_mode: FaceReferenceMode;
  face_reference_target_age: number | null;
  transition_out: { type: string; duration_seconds: number } | string;
  transition_duration_seconds?: number;
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
  cost_summary?: CostSummary;
}

export interface CostSummary {
  total_usd: number;
  by_provider?: Record<string, number>;
  by_stage?: Record<string, number>;
  entry_count?: number;
}

export interface CostLedger {
  entries: Array<Record<string, unknown>>;
  summary: CostSummary;
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
