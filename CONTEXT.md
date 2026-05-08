# Instomater — Full Project Context

## What This Is

A local web app that takes a person's **name + photo** and produces a finished **Instagram vertical reel** (~40s MP4, 1080×1920) via an 11-stage sequential pipeline. Built as a greenfield project from a PRD.

**Stack:** FastAPI (Python, port 8000) + Next.js/TypeScript (port 3001), filesystem storage, WebSocket for real-time updates.

---

## How to Run

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in keys
uvicorn main:app --port 8000 --reload

# Frontend
cd frontend
npm install
npm run dev   # runs on port 3001
```

---

## Directory Structure

```
Instomater/
  backend/
    main.py                    # FastAPI app, mounts all routers
    config.py                  # Env vars, model names, constants
    requirements.txt
    .env                       # Not committed — contains API keys
    .env.example
    models/
      session.py               # All Pydantic models
    routers/
      sessions.py              # CRUD + photo upload endpoints
      stages.py                # /action, /assemble, /redo-clip
      assets.py                # Static file serving
      ws.py                    # WebSocket endpoint + ConnectionManager
    services/
      session_svc.py           # Filesystem session CRUD
      openai_svc.py            # GPT-4.1 (all text/prompt generation)
      gemini_svc.py            # Gemini image gen + Veo video gen
      elevenlabs_svc.py        # TTS + forced alignment
      ffmpeg_svc.py            # Assembly pipeline
    pipeline/
      orchestrator.py          # Core state machine
      prompts.py               # All 12 prompt templates
      skill_prompts.py         # Uploaded script/storyboard skill prompts
    tests/
      conftest.py
      test_sessions.py
      test_stages.py
      test_services.py
  frontend/
    app/
      page.tsx                 # Sessions list (home)
      sessions/[id]/page.tsx   # Session chat view
      final/[id]/page.tsx      # Final review + download
    components/
      ChatHistory.tsx          # Renders all message types
      StatusPill.tsx           # Spinner → green checkmark when resolved
      ErrorCard.tsx            # Error with Retry/Change buttons
      Composer.tsx             # Text input at bottom
      Sidebar.tsx              # Session list sidebar
      cards/
        ScriptCard.tsx
        VoiceoverCard.tsx
        StoryboardCard.tsx
        ImageCard.tsx
        VideoPromptCard.tsx
        VideoCard.tsx
    hooks/
      useWebSocket.ts
      useSession.ts
    lib/
      api.ts                   # All HTTP calls, base URL http://localhost:8000
      types.ts                 # TypeScript interfaces
```

---

## Pipeline Stages (Sequential, Gate-Enforced)

| # | Stage | What happens |
|---|-------|-------------|
| 1 | `script` | GPT generates a script directly from the user's initial person/context text. User approves or changes. |
| 2 | `photo_upload` | User uploads a photo. No aspect ratio check (removed). Only size check (10MB max). |
| 3 | `voiceover` | ElevenLabs TTS generates MP3. User selects female/male/custom voice ID and speed. |
| 4 | `alignment` | ElevenLabs forced alignment runs automatically on approve. No user gate. |
| 5 | `storyboard` | GPT generates storyboard (8 scenes, durations must be in {4,6,8}s). User approves. |
| 6 | `image_generation` | Gemini generates two unique keyframes per clip. Interleaved with video. |
| 7 | `video_generation` | Veo generates clips from each clip's unique start/end images. |
| 8 | `assembly` | FFmpeg assembles: normalize clips → storyboard xfade transitions → burn subtitles → layer voiceover → final encode. |
| 9 | `final_review` | User views final locked reel, downloads MP4, and reviews costs. |

**Interleave logic:** Scene `i` owns `img_{2i-1}` and `img_{2i}`. After an even image is approved, generate that clip's video prompt/video. After the clip is approved, continue to the next scene's first image.

---

## API Endpoints

```
POST   /sessions                         create session
GET    /sessions                         list sessions
GET    /sessions/{id}                    get metadata + chat history
DELETE /sessions/{id}                    delete session
POST   /sessions/{id}/photo              upload photo (multipart)
POST   /sessions/{id}/photo/confirm      confirm photo after script approval
POST   /sessions/{id}/action             unified action dispatch
POST   /sessions/{id}/assemble           trigger FFmpeg assembly
POST   /sessions/{id}/redo-clip/{n}      reset a clip for redo
GET    /sessions/{id}/assets/{path}      serve static files
GET    /ws/sessions/{id}                 WebSocket upgrade
GET    /health                           health check
```

### Action endpoint body

```json
{
  "action": "approve | change | regenerate | restore | prompt_approve | prompt_change | prompt_restore | retry | select_voice | autopilot",
  "stage": "script | photo_upload | voiceover | storyboard | image_generation | video_generation | assembly | settings",
  "payload": { ...stage-specific fields... }
}
```

Orchestrator dispatch key format: `"{stage}.{action}"` e.g. `"video_generation.prompt_approve"`.

---

## Key Implementation Details

### `backend/pipeline/orchestrator.py`

The core state machine. All logic lives here.

- `advance(session_id, action, stage, payload, ws)` — main entry point called from the action endpoint
- Stage gate check: rejects actions targeting stages not yet reached
- **Veo and image generation are fired as background tasks** via `_fire_bg()` so the HTTP response returns immediately and WebSocket pushes updates
- `_fire_bg(coro)` uses `asyncio.create_task` with a module-level `_bg_tasks` set to prevent GC

**Anti-loop guards:**
- Veo polling: `range(VEO_MAX_POLLS)` = 90 iterations × 10s = 15 min hard cap, then `VeoTimeoutError`
- Storyboard duration validation: `range(3)` retries
- ElevenLabs 429 backoff: 3 retries with increasing sleep
- Image transient errors: 1 auto-retry

**Status pill lifecycle:** Each stage creates a `status_pill` with a `pill_id`. On success, `session_svc.resolve_status_pill(session_id, pill_id)` is called before appending the asset card → pill shows green checkmark in UI. On error, pill stays spinning but an `error_card` is appended instead.

**Asset card approval:** When a video prompt is approved, `session_svc.approve_last_asset_card(session_id, "video_prompt")` is called → card shows "✓ Approved", buttons disappear.

### `backend/services/gemini_svc.py`

**Image generation (`generate_image`):**
- Model: `gemini-3-pro-image-preview`, aspect ratio 9:16
- Accepts 1–3 reference images (uploaded photo first, then chain image, then rejected)
- 1 auto-retry on transient errors
- Returns PNG bytes

**Video generation:**
- Using Gemini Developer API (NOT Vertex AI) with `google-genai` SDK
- `submit_video_job`: passes `image=start_image` directly to `generate_videos()` — NOT inside `GenerateVideosConfig`
- `GenerateVideosConfig` only has `aspect_ratio` and `duration_seconds` — `generate_audio`, `last_frame`, `resolution` are NOT supported in Gemini Developer API (Vertex AI only)
- `poll_video_job`: reconstructs operation via `gtypes.GenerateVideosOperation(name=operation_name)` then calls `_client.operations.get(stub)`
- **The API returns a URI, not inline bytes.** On success, `video.video.video_bytes` is `None` and `video.video.uri` contains a download URL. Download it with `httpx.AsyncClient(follow_redirects=True)` using header `X-Goog-Api-Key: <key>` where key is `_client._api_client.api_key`

### `backend/services/session_svc.py`

Key functions:
- `resolve_status_pill(session_id, pill_id)` — sets `resolved=True` on a pill in chat history
- `approve_last_asset_card(session_id, subtype)` — sets `status="approved"` on last card of that subtype
- `symlink_approved` — actually copies the file (not a symlink, for portability)
- Chat history stored as `{session_id, messages: [...]}` in `chat_history.json`

### `backend/routers/stages.py`

- `/action` endpoint: `await advance(...)` synchronously (fast stages)
- Assembly and Veo run via `_fire_bg` inside orchestrator — HTTP returns `{"status": "ok"}` immediately
- `_bg_tasks` set prevents task GC

### `frontend/components/ChatHistory.tsx`

**Stage-gated UI:** Only asset cards matching `STAGE_ACTIVE_SUBTYPES[currentStage]` get action buttons (`onAction` prop). Past/inactive cards get `undefined` → buttons hidden.

```typescript
const STAGE_ACTIVE_SUBTYPES = {
  script: ["script"],
  voiceover: ["voiceover"],
  storyboard: ["storyboard"],
  image_generation: ["image", "video_prompt"],
  video_generation: ["video", "video_prompt"],
};
```

Within active subtypes, only the **last card per subtype** gets `onAction`. All card components guard buttons with `!approved && onAction !== undefined`.

### `frontend/components/StatusPill.tsx`

- `resolved: false` → spinning indigo indicator
- `resolved: true` → green ✓ checkmark (pill stays visible, doesn't disappear)

---

## Known Issues / Pending Work

### 1. CRITICAL: Switch to Vertex AI for Veo

**Why:** The Gemini Developer API does NOT support:
- `generate_audio=False` — videos always include Veo-generated audio (gets stripped in FFmpeg but wastes bandwidth/cost)
- `last_frame` (end frame) — can't control end frame for clips, breaks visual continuity

**What's needed from the user:**
1. GCP Project ID (string like `my-project-123456`) — find at console.cloud.google.com
2. Service account JSON key with **Vertex AI User** (`roles/aiplatform.user`) role
   - Create at: console.cloud.google.com/iam-admin/serviceaccounts
   - Download JSON key from Keys tab

**Code change needed** in `backend/services/gemini_svc.py`:
```python
# Current (Gemini Developer API):
_client = genai.Client(api_key=GEMINI_API_KEY)

# Change to (Vertex AI):
from google.oauth2.service_account import Credentials
_creds = Credentials.from_service_account_file(
    GCP_SERVICE_ACCOUNT_PATH,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
_client = genai.Client(
    vertexai=True,
    project=GCP_PROJECT_ID,
    location="us-central1",
    credentials=_creds
)
```

Add to `config.py`: `GCP_PROJECT_ID`, `GCP_SERVICE_ACCOUNT_PATH`  
Add to `.env`: `GCP_PROJECT_ID=...`, `GCP_SERVICE_ACCOUNT_PATH=path/to/key.json`

With Vertex AI, restore in `GenerateVideosConfig`:
```python
config = gtypes.GenerateVideosConfig(
    aspect_ratio="9:16",
    duration_seconds=duration_seconds,
    generate_audio=False,
    last_frame=end_image,   # gtypes.Image(image_bytes=end_frame_bytes, mime_type="image/png")
)
```

### 2. Photo upload gives wrong person in images

The uploaded photo (Sundar Pichai) failed the old min-size guardrail and was never stored. All 8 images + clip 1 were generated with the wrong person (generic AI person). The min-size guardrail has since been removed.

**To fix the existing session:** Start a fresh session with the correct photo. The current session (`64a51886-ea95-4d3c-9b95-b7718587c461`) has clip 1 done but with the wrong person — discard it.

### 3. Backend tests

`backend/tests/` exists with `test_sessions.py`, `test_stages.py`, `test_services.py` but tests have not been run against the current implementation. Run `pytest` from the backend directory to check.

---

## `.env` Keys Required

```
OPENAI_API_KEY=sk-...          # GPT-4.1
GEMINI_API_KEY=AIzaSy...       # Gemini image gen (Gemini Developer API)
ELEVENLABS_API_KEY=sk_...      # TTS + forced alignment

# Needed once switching to Vertex AI:
GCP_PROJECT_ID=my-project-123
GCP_SERVICE_ACCOUNT_PATH=./service-account.json
```

---

## Session Filesystem Layout

```
sessions/{uuid}/
  metadata.json              # Stage, approval state, settings
  chat_history.json          # {session_id, messages: [...]}
  script_prompt.txt          # Initial user input passed to script generation
  uploaded_photo.jpg         # Original photo
  script_v1.json
  script_approved.json
  voiceover_v1.mp3
  voiceover_approved.mp3
  alignment.json
  storyboard_v1.json
  storyboard_approved.json
  images/
    img_01_prompt_v1.txt
    img_01_v1.png
    img_01_approved.png
    ...img_08...
  video_prompts/
    clip_01_prompt_v1.txt
    clip_01_prompt_approved.txt
    ...
  videos/
    clip_01_v1.mp4
    clip_01_approved.mp4
    ...
  final/
    reel_v1.mp4
  logs/
    ffmpeg.log
    api_calls.log
```

---

## Chat Message Types

All messages stored in `chat_history.json` under `messages[]`:

| `msg_type` | Purpose |
|---|---|
| `system` | Italic centered system text |
| `status_pill` | Spinner (resolved=false) or green checkmark (resolved=true) |
| `app_question` | Bot question with optional widget (buttons, photo_upload) |
| `user_reply` | Right-aligned user bubble |
| `asset_card` | Rich card: script, voiceover, storyboard, image, video_prompt, video, final_reel |
| `error_card` | Red error with Retry/Change buttons |

Asset card `status` field: `"pending_approval"`, `"approved"`, `"previous"`, or `"rejected"`.

---

## WebSocket

- Endpoint: `ws://localhost:8000/ws/sessions/{session_id}`
- Frontend connects on mount, disconnects on unmount
- No state reconstruction on reconnect — just re-attaches to live broadcasts
- WS pushes status updates during long operations (Veo polling, image gen)
- WS events do NOT trigger generation — only explicit REST actions do

---

## Veo-Specific Notes (Important)

The Gemini Developer API Veo integration has several quirks discovered during QA:

1. `image` (start frame) must be passed directly to `generate_videos(image=start_image)`, NOT inside `GenerateVideosConfig`
2. `generate_audio`, `last_frame`, `resolution` in `GenerateVideosConfig` → `ValueError` in Gemini API (Vertex AI only)
3. `_client.operations.get()` takes an operation object, not `name=str`. Reconstruct: `gtypes.GenerateVideosOperation(name=op_name)`
4. On success, `video.video.video_bytes` is `None` — video is at `video.video.uri` (a Google Files API download URL)
5. Download the URI with `httpx.AsyncClient(follow_redirects=True)` + `X-Goog-Api-Key` header
6. Veo takes ~40 seconds per clip (fast model), not 15 minutes
7. The `/action` endpoint previously awaited Veo synchronously → browser timed out → polling died silently. Fixed: Veo runs via `_fire_bg` background task

---

## FFmpeg Assembly (Not Yet QA'd)

`backend/services/ffmpeg_svc.py` implements:
1. `preflight_check` — ffprobe validates clips
2. `normalize_clips` — strip audio, force 24fps, scale/pad to 1080×1920
3. `generate_ass` — subtitle file from alignment data
4. `concat_with_transitions` — xfade filter chain
5. `burn_subtitles` — ass subtitles overlay
6. `layer_voiceover` — mix voiceover with loudnorm
7. `final_encode` — H.264 CRF18 + AAC 192k + faststart
8. `run_assembly` — orchestrates 1–7, broadcasts status pills

The assembly pipeline has **not been end-to-end tested** yet. It's the next major thing to verify after the Vertex AI switch.
