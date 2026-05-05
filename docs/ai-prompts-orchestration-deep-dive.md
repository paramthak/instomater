# AI Prompts And Orchestration Deep Dive

This is the source-map version. It points to the exact code that controls AI prompts, routing, validation, retries, provider calls, and pipeline orchestration.

Use the links as the source of truth. They jump directly into the code files where the real strings and functions live.

## 1. Read This First

There are two prompt files, and both are active:

| File | Runtime status | What it contains |
|---|---|---|
| [backend/pipeline/skill_prompts.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:1) | Active | Script and storyboard prompts from the uploaded writer skills. |
| [backend/pipeline/prompts.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:1) | Active | Topic brief, clarifying questions, image prompts, video prompt fallback, and video prompt regeneration. |

The proof is here:

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:14) imports topic/image/video prompts from `pipeline.prompts`.

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:24) imports script/storyboard prompts from `pipeline.skill_prompts`.

There are no inactive script/storyboard prompts left in `prompts.py`.

## 2. Exact Active Prompt Sources

| Stage | Exact source |
|---|---|
| Topic brief | [TOPIC_BRIEF_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:9) |
| Topic brief rewrite | [TOPIC_BRIEF_REWRITE_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:44) |
| Script generation | [SCRIPT_WRITER_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:8) |
| Script rewrite | [SCRIPT_REWRITE_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:190) |
| Storyboard generation | [STORYBOARD_WRITER_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:210) |
| Storyboard rewrite | [STORYBOARD_REWRITE_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:386) |
| Clarifying questions | [CLARIFYING_QUESTIONS_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:56) |
| First image prompt | [IMAGE_PROMPT_1_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:88) |
| Chain image prompt | [IMAGE_PROMPT_CHAIN_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:143) |
| Image regeneration prompt | [IMAGE_PROMPT_REGEN_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:177) |
| Video prompt fallback | [VIDEO_PROMPT_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:216) |
| Video prompt regeneration | [VIDEO_PROMPT_REGEN_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:246) |
| ElevenLabs TTS tags | [START_TAG and _direct_for_eleven_v3](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:31) |
| Gemini image identity lock | [identity_lock](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:203) |

## 3. Model And Provider Settings

Main config is in [backend/config.py](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:39).

| Setting | Exact code |
|---|---|
| OpenAI model | [OPENAI_MODEL = "gpt-4.1"](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:39) |
| Gemini text fallback | [GEMINI_TEXT_MODEL](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:40) |
| Gemini image model | [GEMINI_IMAGE_MODEL](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:41) |
| Veo standard model | [VEO_STANDARD_MODEL](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:42) |
| Veo fast model | [VEO_FAST_MODEL](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:43) |
| Veo resolution | [VEO_RESOLUTION](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:44) |
| Veo sample count | [VEO_SAMPLE_COUNT](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:45) |
| ElevenLabs model | [get_elevenlabs_tts_model](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:76) |
| ElevenLabs language | [get_elevenlabs_tts_language_code](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:86) |
| ElevenLabs voice settings | [get_elevenlabs_tts_voice_settings](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:91) |
| ElevenLabs voice IDs | [get_elevenlabs_voice_ids](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:102) |
| ElevenLabs post-TTS tempo | [get_elevenlabs_audio_tempo](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:110) |
| Hook categories | [HOOK_CATEGORIES](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:128) |

Important: ElevenLabs env values are refreshed dynamically:

[refresh_elevenlabs_env](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:67)

That means changing voice IDs in `backend/.env` should apply without app restart for future calls.

## 4. Text AI Routing

All OpenAI text calls go through these wrappers:

| Wrapper | Source | Behavior |
|---|---|---|
| `_chat_json` | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:251) | Calls OpenAI with `response_format: {"type": "json_object"}`. Parses JSON. Falls back to Gemini only on quota errors. |
| `_chat_text` | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:285) | Calls OpenAI for plain text prompts. Falls back to Gemini text only on quota errors. |
| `_gemini_json` | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:226) | Gemini fallback for JSON. Adds `Return only valid JSON.` |
| `_gemini_text` | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:239) | Gemini fallback for plain text. |
| `_is_openai_quota_error` | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:118) | Decides whether OpenAI error is quota and should fallback. |
| JSON extraction/repair | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:160) | Extracts fenced/embedded JSON and removes trailing commas. |

The exact OpenAI request shape for JSON is here:

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:268)

The exact OpenAI request shape for text is here:

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:301)

## 5. Validation And Failure Logic

There is no separate `validation_svc.py`.

Validation currently lives inside [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:330), right next to the OpenAI prompt-generation functions. The orchestrator then decides whether to save the asset, retry, show an error card, or block approval.

Runtime storage is session-based:

| Thing | Where it is stored |
|---|---|
| Session folder | Default is `backend/sessions/<session_id>`, configured by [SESSIONS_DIR](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:31). |
| Chat history, status pills, error cards | `chat_history.json`, via [_chat_path](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:30) and [_write_chat](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:114). |
| Generated script/storyboard versions | `script_vN.json`, `storyboard_vN.json`, etc., via [save_json_asset](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:220). |
| Approved script/storyboard | `script_approved.json` and `storyboard_approved.json`, also via [save_json_asset](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:220). |
| Validation failure details | Kept as `validation_error` in the failed generated object, then turned into an `error_card` in `chat_history.json` by the orchestrator. Failed storyboard output is not saved as an approved asset. |

### JSON Parsing Validation

JSON parsing is shared by all JSON prompt calls.

| Logic | Source | What it does |
|---|---|---|
| Extract JSON from model output | [_extract_json_candidate](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:160) | Pulls JSON out of fenced code blocks or mixed text. |
| Remove trailing commas | [_remove_trailing_json_commas](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:156) | Tries one light JSON repair. |
| Parse JSON | [_parse_json_text](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:200) | Attempts parse, then repaired parse. |
| JSON OpenAI wrapper | [_chat_json](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:251) | Calls OpenAI with JSON response format, parses result, falls back to Gemini only on quota errors. |

### Script Validation

Script validation is here:

[_validate_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:482)

Before validation, scripts are normalized here:

[_normalize_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:436)

Normalization does this:

- removes raw audio tags like `[pause]`
- removes hashtags
- collapses whitespace
- replaces banned corporate-poetic phrases with plainer phrases
- infers `hook/setup/build/landing` if the model gave only `full_text`
- recalculates `estimated_word_count`
- recalculates `estimated_duration_seconds`
- recalculates `name_mentions_count`
- rebuilds `self_check`

Important helper logic:

| Logic | Source |
|---|---|
| Word count | [_word_count](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:331) |
| Clean script text | [_clean_script_text](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:335) |
| Infer structure | [_infer_script_structure](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:349) |
| Count name mentions | [_name_mentions](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:381) |
| Build self-check | [_with_self_check](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:404) |

Exact script validation rules:

| Check | Logic |
|---|---|
| Required top-level fields | Must include `hook_category`, `hook_subtype_used`, `perspective`, `structure`, `full_text`, counts, duration, name count, self-check. |
| Required structure fields | Must include `hook`, `setup`, `build`, `landing`. |
| Hook subtype | Must be `pattern_interrupt`, `curiosity_gap`, or `proof_first`. |
| Perspective | Must be `first_person`, `second_person`, or `third_person_documentary`. |
| Full text integrity | `full_text` must exactly equal `hook + setup + build + landing`. |
| Total word count | Must be between 70 and 125 words. |
| Hook word count | Must be 4-22 words. |
| Section word counts | `setup`: 6-45, `build`: 15-95, `landing`: 4-40. |
| Sentence length | No sentence above 34 words. |
| Banned phrases | Rejects phrases listed in [_SCRIPT_BANNED_PHRASES](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:38). |
| Landing start | Landing cannot begin with `today`. |
| No hashtags | Rejects `#`. |
| No raw audio tags | Rejects bracket tags in final displayed script. |
| No emoji/non-text symbols | Rejects high Unicode symbol range. |
| Name mentions | Must be 0-4 after recalculation. |

Script retry/failure behavior:

| Flow | Source | Behavior |
|---|---|---|
| Generate script | [generate_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:849) | Tries 3 times. |
| Retry prompt | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:867) | Tells the model exactly what validation failed and asks for full regeneration. |
| Fallback script | [_fallback_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:795) | If 3 attempts fail, creates a deterministic script instead of killing the session. |
| Script rewrite | [rewrite_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:875) | Tries 3 times; if still bad, prefers current valid script or normalized output. |
| Orchestrator guard | [start_script_generation](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:347) | Shows an error only if a `validation_error` comes back. |
| Approval guard | [_handle_script_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:420) | Blocks approving a script if it has `validation_error`. |

Product meaning: script generation should almost never hard-fail anymore. If the model keeps messing up, the fallback path keeps the session moving.

### Storyboard Validation

Storyboard validation is stricter.

Normalization:

[_normalize_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:600)

Validation:

[_validate_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:678)

Normalization does this:

- forces `total_scenes` and `total_clips` to match actual scene count
- sets `image_count` and `total_images` to `scene_count + 1`
- fills default `visual_style`
- coerces every duration to nearest valid Veo duration: 4, 6, or 8 seconds
- rewrites `start_time` and `end_time` sequentially from durations
- rewrites image chain IDs as `img_01 -> img_02 -> ...`
- validates/repairs shot type
- validates/repairs camera motion
- prevents two `SLOW_PUSH_IN` motions in a row during normalization
- fills compatibility `visual_description`
- fills `visual_narration_check` if missing
- normalizes transition fields
- recalculates `total_duration_seconds`
- updates storyboard `self_check`

Exact storyboard validation rules:

| Check | Logic |
|---|---|
| Must have scenes | Empty storyboard fails. |
| Scene count | Must be 7-11 scenes. |
| Image count | `total_images` must equal `scene_count + 1`. |
| Durations | Every scene duration must be exactly 4, 6, or 8. |
| Duration sum | Sum of scene durations must match declared `total_duration_seconds` within 0.5s. |
| Shot variation | Same shot type cannot appear 3 times in a row. |
| Camera motion | Two `SLOW_PUSH_IN`s cannot appear in a row. |
| Final motion | Final clip must be `SLOW_PULL_BACK` or `STATIC_LOCK`. |
| Image chain | Scene 1 must start `img_01` and end `img_02`, scene 2 must start `img_02` and end `img_03`, etc. |
| Visual narration check | Every scene must include `visual_narration_check`. |
| Timing continuity | Gap between scene `end_time` and next `start_time` must be <= 0.1s. |
| ECU limit | Only one `ECU` shot max. |
| Self-check booleans | Required self-check fields must be true. |

Storyboard retry/failure behavior:

| Flow | Source | Behavior |
|---|---|---|
| Generate storyboard | [generate_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:903) | Tries 3 times. |
| Malformed JSON retry | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:920) | Catches `JSONDecodeError` and asks for strict JSON. |
| Validation retry | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:932) | Tells the model the validation error and asks for a full corrected storyboard. |
| Final failure | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:939) | Returns `validation_error` after 3 failed attempts. |
| UI error card | [_generate_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:598) | Converts `validation_error` into a visible storyboard error card. |
| Storyboard rewrite | [rewrite_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:943) | Same 3-attempt validation pattern for user feedback rewrites. |

Product meaning: storyboard can still hard-stop because a bad storyboard breaks many expensive downstream steps. The current code prefers stopping and asking for a fix over generating wrong images/videos.

### Error Card And Retry UI Behavior

Error/status cards live in chat history, not in a separate validation service.

| Logic | Source | What it does |
|---|---|---|
| Resolve one status pill | [resolve_status_pill](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:134) | Stops one spinner. |
| Update status pill text | [update_status_pill_message](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:144) | Changes visible attempt/progress text. |
| Resolve old status pills | [resolve_status_pills](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:154) | Clears stale loaders before retry/change. |
| Resolve old error cards | [resolve_error_cards](/Users/paramthakkar/Development/Projects/Instomater/backend/services/session_svc.py:175) | Marks old errors resolved when corrective work starts. |

### Non-Text Validation Elsewhere

| Area | Source | Logic |
|---|---|---|
| Photo upload validation | [backend/routers/sessions.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/sessions.py:81) | Checks file type, size, image readability, and stores photo extension. |
| ElevenLabs retry validation | [backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:143) | Retries rate limits, blocks non-retryable statuses, tempo-adjusts output. |
| Forced alignment retry | [backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:192) | Retries alignment up to 2 times. |
| Gemini image retry | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:189) | Retries image generation once for transient failures, blocks content policy errors. |
| Veo polling cap | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:332) | Polls with hard cap; throws timeout if exceeded. |
| Final assembly preflight | [backend/services/ffmpeg_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:149) | Verifies every approved clip exists, is portrait, 24fps, and has expected duration. |

## 6. Prompt Construction Per Stage

### Topic Brief

Prompt source:

[TOPIC_BRIEF_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:9)

Runtime user message:

[generate_topic_brief](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:765)

Exact runtime input shape:

```text
person_name: {name}
user_context: {context or '(none provided)'}
```

Rewrite runtime user message:

[rewrite_topic_brief](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:770)

### Script

Prompt source:

[SCRIPT_WRITER_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:8)

System prompt is filled here:

[_script_system_for](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:314)

It replaces:

```text
{topic_brief}
{assigned_hook_category}
```

Generation flow:

[generate_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:849)

Hook category assignment:

If no caller passes a hook category, [generate_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:849) picks one randomly from [HOOK_CATEGORIES](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:128). The current orchestrator call, [start_script_generation](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:357), passes only the approved topic brief, so the hook category is backend-random by default.

Exact first user message:

```text
Generate the script JSON now.
```

Exact retry user message:

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:867)

Script validation and normalization:

| Logic | Source |
|---|---|
| Banned phrases list | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:38) |
| Script word count constants | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:90) |
| Banned phrase replacements | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:95) |
| Normalize script | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:436) |
| Validate script | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:482) |
| Deterministic fallback script | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:795) |
| Trim over-limit script | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:832) |

Rewrite prompt source:

[SCRIPT_REWRITE_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:190)

Rewrite runtime flow:

[rewrite_script](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:875)

### Voiceover / ElevenLabs

Voice selection happens here:

[_handle_voice_select](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:448)

Voiceover generation happens here:

[_generate_voiceover](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:467)

The actual ElevenLabs payload is assembled here:

[generate_voiceover](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:143)

TTS-only audio tags are injected here:

[_direct_for_eleven_v3](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:91)

The exact start tag is:

```text
[strong Indian English accent] [fast] [confident]
```

Source:

[backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:31)

More inline tags:

| Tag logic | Source |
|---|---|
| Sentence 1 gets accent/fast/confident | [backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:108) |
| Sentence 2 gets `[fast] [engaged]` | [backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:110) |
| Midpoint gets `[quick pace] [focused]` | [backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:112) |
| Last sentence gets `[warm]` | [backend/services/elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:114) |

Important: forced alignment still uses clean script text, not the tagged text.

Forced alignment call:

[forced_alignment](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:192)

Audio tempo boost:

[_apply_audio_tempo](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:58)

### Storyboard

Prompt source:

[STORYBOARD_WRITER_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:210)

System prompt is filled here:

[_storyboard_system_for](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:322)

It replaces:

```text
{script}
{alignment}
{topic_brief}
```

Generation flow:

[generate_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:903)

Exact first user message:

```text
Generate the rich storyboard JSON now.
```

Malformed JSON retry message:

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:925)

Validation retry message:

[backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:938)

Storyboard normalization and validation:

| Logic | Source |
|---|---|
| Normalize storyboard | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:600) |
| Validate storyboard | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:678) |
| Convert rich storyboard motion into video prompt | [backend/services/openai_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:745) |

Rewrite prompt source:

[STORYBOARD_REWRITE_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:386)

Rewrite runtime flow:

[rewrite_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:943)

### Clarifying Questions

Prompt source:

[CLARIFYING_QUESTIONS_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:56)

Runtime content:

[generate_clarifying_questions](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:987)

It sends:

```text
storyboard: {storyboard json}

uploaded_photo: (attached as Image 1)
```

plus the uploaded photo as image input.

### Image Prompts

First image prompt source:

[IMAGE_PROMPT_1_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:88)

First image runtime content:

[write_image_prompt_1](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:1007)

It sends:

```text
uploaded_photo: (attached as Image 1)
storyboard_scene_1: {scene json}
frame_role_context: {frame_context json}
clarifying_answers: {answers json}
person_name: {person_name}
```

Chain image prompt source:

[IMAGE_PROMPT_CHAIN_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:143)

Chain image runtime content:

[write_image_prompt_chain](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:1031)

It sends the uploaded photo, previous approved image, storyboard scene, image slot, frame role context, and clarifying answers.

Image regeneration prompt source:

[IMAGE_PROMPT_REGEN_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:177)

Image regeneration runtime content:

[write_image_prompt_regen](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:1061)

It sends uploaded photo, previous chain image when available, rejected iteration, previous prompt, feedback, scene, slot, frame context, and clarifying answers.

Frame-role context is created here:

[_image_scene_context](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:137)

Gemini image call:

[generate_image](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:189)

Hard identity lock is prepended here:

[backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:203)

Generated prompt files are saved here:

[backend/pipeline/orchestrator.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:818)

File shape:

```text
backend/sessions/<session_id>/images/img_NN_prompt_vX.txt
backend/sessions/<session_id>/images/img_NN_vX.png
backend/sessions/<session_id>/images/img_NN_approved.png
```

### Video Prompts

Important: video prompts often do not call the `VIDEO_PROMPT_SYSTEM`.

First, code checks if the storyboard scene already has a rich `video_motion_prompt`.

Source:

[_storyboard_video_prompt](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:745)

Routing:

[write_video_prompt](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:1097)

If storyboard has usable `video_motion_prompt`, the app returns a deterministic prompt assembled from the storyboard and skips another OpenAI prompt-writing call.

If storyboard does not have that object, fallback prompt source is:

[VIDEO_PROMPT_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:216)

Video prompt rewrite source:

[VIDEO_PROMPT_REGEN_SYSTEM](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:246)

Rewrite runtime:

[rewrite_video_prompt](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:1122)

Generated prompt files are saved here:

[_generate_video_prompt](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:922)

File shape:

```text
backend/sessions/<session_id>/video_prompts/clip_NN_prompt_vX.txt
backend/sessions/<session_id>/video_prompts/clip_NN_prompt_approved.txt
```

Veo call:

[run_video_job](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:332)

Veo submission config:

[submit_video_job](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:245)

Key exact settings:

| Setting | Source |
|---|---|
| `generate_audio=False` | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:265) |
| `last_frame=end_image` | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:266) |
| `number_of_videos=VEO_SAMPLE_COUNT` | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:267) |
| `person_generation="allow_adult"` | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:268) |
| `resolution=VEO_RESOLUTION` | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:269) |
| Best candidate selection | [backend/services/gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:173) |

## 7. Orchestration: Exact Runtime State Machine

Main state machine:

[backend/pipeline/orchestrator.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1)

Anti-loop promises are documented here:

[backend/pipeline/orchestrator.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:4)

Stage order:

[_STAGE_ORDER](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:88)

Action router:

[advance](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:192)

Exact action-to-handler map:

[handlers map](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:205)

Stage gate:

[stage_to_enum](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:236)

## 8. Orchestrator Handler Map

| User/backend action | Handler |
|---|---|
| `topic_brief.start` | [_handle_topic_brief_start](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:258) |
| `topic_brief.change` | [_handle_topic_brief_change](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:289) |
| `topic_brief.approve` | [_handle_topic_brief_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:321) |
| `script.retry` | [_handle_script_retry](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:343) |
| script generation | [start_script_generation](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:347) |
| `script.change` | [_handle_script_change](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:383) |
| `script.approve` | [_handle_script_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:420) |
| `voiceover.select_voice` | [_handle_voice_select](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:448) |
| voiceover generation | [_generate_voiceover](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:467) |
| `voiceover.approve` | [_handle_voiceover_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:522) |
| forced alignment | [_run_alignment](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:537) |
| `storyboard.retry` | [_handle_storyboard_retry](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:571) |
| storyboard generation | [_generate_storyboard](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:575) |
| `storyboard.change` | [_handle_storyboard_change](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:624) |
| `storyboard.approve` | [_handle_storyboard_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:668) |
| clarifying questions generation | [_generate_clarifying_questions](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:697) |
| `clarifying_questions.answer` | [_handle_clarifying_answer](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:718) |
| image generation | [_generate_image](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:741) |
| `image_generation.change` | [_handle_image_change](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:838) |
| `image_generation.approve` | [_handle_image_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:877) |
| video prompt generation | [_generate_video_prompt](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:922) |
| `video_generation.prompt_change` | [_handle_video_prompt_change](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:963) |
| `video_generation.prompt_approve` | [_handle_video_prompt_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1008) |
| Veo run | [_run_veo](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1028) |
| `video_generation.change` | [_handle_video_change](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1094) |
| `video_generation.approve` | [_handle_video_approve](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1104) |
| all assets complete check | [_check_all_complete](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1135) |
| `assembly.start` | [_handle_assembly_start](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1154) |
| redo clip | [_handle_redo_clip](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1194) |

## 9. Frontend To Backend Routing

Frontend API client:

[frontend/lib/api.ts](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:25)

Important calls:

| Frontend call | Backend endpoint |
|---|---|
| `startSession` | [POST `/sessions/{id}/start`](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:55) |
| `uploadPhoto` | [POST `/sessions/{id}/photo`](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:41) |
| `confirmPhoto` | [POST `/sessions/{id}/photo/confirm`](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:52) |
| `sendAction` | [POST `/sessions/{id}/action`](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:59) |
| `startAssembly` | [POST `/sessions/{id}/assemble`](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:63) |
| `redoClip` | [POST `/sessions/{id}/redo-clip/{clipIndex}`](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:66) |

Session page widget actions:

[frontend/app/sessions/[id]/page.tsx](/Users/paramthakkar/Development/Projects/Instomater/frontend/app/sessions/[id]/page.tsx:115)

Male/female voice selection:

[frontend/app/sessions/[id]/page.tsx](/Users/paramthakkar/Development/Projects/Instomater/frontend/app/sessions/[id]/page.tsx:119)

Assemble button:

[frontend/app/sessions/[id]/page.tsx](/Users/paramthakkar/Development/Projects/Instomater/frontend/app/sessions/[id]/page.tsx:130)

Backend stage router:

[backend/routers/stages.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/stages.py:32)

Background actions:

[backend/routers/stages.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/stages.py:11)

Assembly endpoint:

[backend/routers/stages.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/stages.py:79)

Start-session endpoint:

[backend/routers/stages.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/stages.py:101)

Photo upload and confirm:

[backend/routers/sessions.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/sessions.py:81)

[backend/routers/sessions.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/sessions.py:131)

## 10. Live Status Routing

WebSocket manager:

[backend/routers/ws.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/ws.py:12)

Status messages:

[send_status](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/ws.py:36)

Asset-ready messages:

[send_asset_ready](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/ws.py:52)

Error messages:

[send_error](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/ws.py:66)

Important design rule:

[WebSocket never retriggers generation](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/ws.py:91)

## 11. Data Model / State

Stage enum:

[StageEnum](/Users/paramthakkar/Development/Projects/Instomater/backend/models/session.py:9)

Approval state:

[ApprovalState](/Users/paramthakkar/Development/Projects/Instomater/backend/models/session.py:58)

Session metadata:

[SessionMetadata](/Users/paramthakkar/Development/Projects/Instomater/backend/models/session.py:73)

Chat message types:

[models/session.py](/Users/paramthakkar/Development/Projects/Instomater/backend/models/session.py:86)

Asset schemas:

[models/session.py](/Users/paramthakkar/Development/Projects/Instomater/backend/models/session.py:145)

Action request:

[ActionRequest](/Users/paramthakkar/Development/Projects/Instomater/backend/models/session.py:228)

## 12. Final Assembly Routing

Assembly starts in orchestrator:

[_handle_assembly_start](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:1154)

FFmpeg pipeline:

[run_assembly](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:448)

Assembly steps:

| Step | Source |
|---|---|
| Preflight approved clips | [preflight_check](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:149) |
| Normalize clips | [normalize_clips](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:191) |
| Generate ASS subtitles from alignment | [generate_ass](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:258) |
| Stitch clips | [concat_with_transitions](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:308) |
| Extend to voiceover | [extend_to_voiceover](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:357) |
| Burn subtitles | [burn_subtitles](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:383) |
| Layer voiceover | [layer_voiceover](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:402) |
| Final encode | [final_encode](/Users/paramthakkar/Development/Projects/Instomater/backend/services/ffmpeg_svc.py:424) |

## 13. Where To Inspect Generated AI Artifacts

For any session:

```text
backend/sessions/<session_id>/
```

Useful files:

```text
topic_brief_vX.json
script_vX.json
voiceover_vX.meta.json
alignment.json
storyboard_vX.json
clarifying_questions.json
clarifying_answers.json
images/img_NN_prompt_vX.txt
images/img_NN_vX.png
images/img_NN_approved.png
video_prompts/clip_NN_prompt_vX.txt
video_prompts/clip_NN_prompt_approved.txt
videos/clip_NN_vX.mp4
videos/clip_NN_approved.mp4
final/reel_vX.mp4
logs/ffmpeg.log
```

The most useful debugging files for prompt quality are:

```text
script_vX.json
storyboard_vX.json
images/img_NN_prompt_vX.txt
video_prompts/clip_NN_prompt_vX.txt
voiceover_vX.meta.json
```

## 14. Fast Reading Order

If you want to understand the whole AI brain in order:

1. [config.py](/Users/paramthakkar/Development/Projects/Instomater/backend/config.py:39) for provider/model settings.
2. [skill_prompts.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/skill_prompts.py:8) for active script/storyboard prompts.
3. [prompts.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/prompts.py:9) for topic, image, video, and clarifying prompts.
4. [openai_svc.py imports](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:13) to see active prompt routing.
5. [openai_svc.py generation functions](/Users/paramthakkar/Development/Projects/Instomater/backend/services/openai_svc.py:763) to see exact user messages, retries, validation, and fallback.
6. [elevenlabs_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/elevenlabs_svc.py:31) for TTS tags, voice settings, speed, and alignment.
7. [gemini_svc.py](/Users/paramthakkar/Development/Projects/Instomater/backend/services/gemini_svc.py:189) for image/video provider calls.
8. [orchestrator.py](/Users/paramthakkar/Development/Projects/Instomater/backend/pipeline/orchestrator.py:192) for the state machine.
9. [stages.py](/Users/paramthakkar/Development/Projects/Instomater/backend/routers/stages.py:32) for backend API actions.
10. [frontend/lib/api.ts](/Users/paramthakkar/Development/Projects/Instomater/frontend/lib/api.ts:25) for frontend API calls.
