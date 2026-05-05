# Instomator Pipeline Architecture

PM-facing architecture note. This explains the whole pipeline, what flows where, and what each prompt is responsible for.

## TL;DR

Instomator turns a person/topic plus a reference photo into a short vertical reel.

```text
topic/person
  -> research brief
  -> uploaded reference photo
  -> script
  -> ElevenLabs voiceover
  -> word-level alignment
  -> storyboard
  -> visual choices
  -> generated images
  -> generated videos
  -> final assembled reel
```

The product is a gated pipeline: each major step creates an asset, shows it to the user, and waits for approval before spending money/time on the next step.

## Main System Pieces

| Piece | Where | What it does |
|---|---|---|
| Frontend | `frontend/` | Chat-style production console. Shows history, pipeline status, assets, errors, approval/change/retry buttons, and final review. |
| Backend | `backend/` | Owns the pipeline. Calls AI providers, validates outputs, saves assets, tracks approvals, broadcasts progress, and assembles the final video. |
| Session storage | `backend/sessions/<session_id>/` | One folder per reel project. Stores metadata, chat history, generated assets, logs, and final MP4. |
| WebSocket | backend to frontend | Pushes live status updates so the UI can show progress without refreshing. |

## Provider Map

| Need | Provider/tool | Output |
|---|---|---|
| Topic research | OpenAI `gpt-4.1` | structured topic brief |
| Script | OpenAI `gpt-4.1` | short voiceover script |
| Storyboard | OpenAI `gpt-4.1` | scene plan with timing |
| Clarifying questions | OpenAI `gpt-4.1` | user visual-style choices |
| Image prompts | OpenAI `gpt-4.1` | prompts for image generation |
| Voiceover | ElevenLabs `eleven_v3` | MP3 |
| Word timing | ElevenLabs forced alignment | word-level timestamps |
| Images | Gemini image model | PNG keyframes |
| Videos | Veo | MP4 clips |
| Final assembly | FFmpeg | final reel MP4 |

If OpenAI quota fails, text generation can fall back to Gemini text. Image/video generation still uses Gemini/Veo.

## Pipeline, Cut To Cut

| # | Stage | What comes in | What gets created | User gate / next step |
|---|---|---|---|---|
| 1 | Session start | Person/topic name, optional context | Session folder, `metadata.json`, `chat_history.json` | Backend moves to topic brief |
| 2 | Topic brief | Topic/person | `topic_brief_v1.json`, topic brief card | User approves or asks for changes |
| 3 | Photo upload | Uploaded reference photo | `uploaded_photo.<ext>` | User confirms photo |
| 4 | Script | Approved topic brief | `script_v1.json`, script card | User approves/changes/retries |
| 5 | Voiceover | Approved script, selected gender, voice ID from `.env` | `voiceover_v1.mp3`, voiceover card | User approves/changes voice |
| 6 | Forced alignment | Voiceover MP3 plus clean script text | `alignment.json` with word timestamps | Automatic next step |
| 7 | Storyboard | Script, topic brief, alignment, photo reference | `storyboard_v1.json`, storyboard card | User approves/changes/retries |
| 8 | Clarifying questions | Approved storyboard and brief | 2-4 visual-style questions | User answers |
| 9 | Image generation | Storyboard, uploaded photo, visual answers, previous image when chaining | Keyframe PNGs in `images/` | User approves/regenerates each image |
| 10 | Video generation | Approved image, storyboard scene, video prompt | Prompt in `video_prompts/`, MP4 in `videos/` | User approves/regenerates each clip |
| 11 | Assembly | Approved videos, voiceover, alignment, script | `final/reel_v1.mp4`, FFmpeg logs | User reviews final video |
| 12 | Final review | Final MP4 and approved assets | Review page with final reel, clips, and conversation history | End state |

## What Flows Where

### Creative Asset Flow

```text
topic brief
  -> gives facts and story direction to script
script
  -> becomes voiceover
voiceover + script
  -> becomes alignment
script + alignment + brief
  -> becomes storyboard
storyboard + photo + user visual answers
  -> becomes images
images + storyboard
  -> become video clips
videos + voiceover + alignment
  -> become final reel
```

### User Action Flow

```text
approve/change/retry buttons
  -> frontend API call
  -> backend updates session metadata
  -> backend starts next stage or regenerates current stage
  -> WebSocket updates frontend status
```

### Error Flow

```text
provider failure or validation failure
  -> backend records the error in metadata/chat
  -> frontend shows error card
  -> user can retry/change where supported
```

## Prompt Logic

| Prompt area | Main job | Logic inside the prompt |
|---|---|---|
| Topic brief | Build the factual foundation | Act like a documentary research producer. Use verifiable facts, identify milestones, story arcs, visual anchors, and tone options. Avoid fake or vague claims. |
| Script | Write the spoken reel | Use the uploaded script-writer skill. Keep it short, simple, human, and conversational. Structure it as hook/setup/build/landing. Avoid heavy corporate-poetic phrases. Keep it around 40-50 seconds. |
| TTS tags | Improve ElevenLabs delivery | The visible script stays clean. Backend injects ElevenLabs-only tags before TTS: Indian English accent, faster pacing, confident delivery, and controlled pauses. |
| Storyboard | Turn script into scenes | Use the uploaded storyboard skill. Split the script into visual scenes, align timing to the voiceover, plan images/clips, and keep duration math consistent. |
| Clarifying questions | Get visual direction | Ask 2-4 global visual-style questions only. No identity questions. Choices influence era, lighting, realism, mood, and production style. |
| First image prompt | Establish identity | Use the uploaded photo as the canonical face reference. Generate a realistic cinematic image while preserving identity strongly. |
| Later image prompts | Continue the visual story | Preserve identity and continuity, but visibly advance the scene. Avoid repetitive frames. |
| Image regeneration | Fix only the rejected part | Use the rejected image as the base and apply only the user's feedback while preserving identity and scene purpose. |
| Video prompt | Animate approved images | Describe motion, camera, environment, and transitions. Avoid dialogue, audio instructions, public names, logos, and brands. Make movement start immediately. |

## Approval Gates

The app pauses at these points:

```text
topic brief approval
photo confirmation
script approval
voice selection + voiceover approval
storyboard approval
visual question answers
each image approval
each video approval
assemble click
final review
```

This prevents expensive video generation from happening on bad upstream decisions.

## Key Files Created Per Session

```text
metadata.json              current stage, approvals, selected voice, errors
chat_history.json          conversation history shown in UI
topic_brief_v1.json        generated research brief
script_v1.json             generated script
voiceover_v1.mp3           generated narration
alignment.json             word-level timestamps
storyboard_v1.json         generated storyboard
images/                    generated keyframes
video_prompts/             Veo prompts
videos/                    generated clips
final/reel_v1.mp4          assembled reel
logs/ffmpeg.log            assembly logs
```

## Current Backend Stage Order

```text
topic_brief
photo_upload
script
voiceover
alignment
storyboard
clarifying_questions
image_generation
video_generation
assembly
final_review
```

## Product Takeaway

The final reel quality depends on clean handoffs. The brief feeds the script, the script feeds the voiceover, the voiceover creates real timing, the timing shapes the storyboard, the storyboard guides images/videos, and approved clips give assembly reliable inputs.

So this is not one giant generation call. It is a staged creative pipeline where each approved asset becomes the source of truth for the next one.
