# TOONTOON — Generation Report & Smoke Test (2026-06-20)

This document explains **exactly how content generation works** (images, postcards,
videos), the **results of live smoke tests** run against the production code, and a
**prioritized list of real problems** found — especially the ones that cause "good
quality but wrong content" generations that waste TOONTOON/credits.

---

## 0. TL;DR — Smoke Test Results

| Area | Result | Evidence |
|------|--------|----------|
| Backend imports / routes | ✅ OK | `app.main` imports, push routes registered |
| Offline builders (29 tiles) | ✅ 113/113 checks pass | image+card mechanical build, all 8 video builders, IP/brand guards, living/epic detection |
| Live IMAGE generation | ✅ OK (30.7s) | OpenAI `gpt-image-1` → `/uploads/img_…png`, on-brand 3D cartoon |
| Live VIDEO generation | ✅ OK (158s) | Seedance-2-fast → valid `720×1280`, `8.04s`, h264 MP4 |
| OpenAI | ✅ reachable | `gpt-4o-mini` ✓, `gpt-image-1` ✓, **`dall-e-3` ✗ (not in account)** |
| kie.ai | ✅ **2993 credits** | `/api/v1/jobs/createTask` + `recordInfo` work, model slugs valid |

**Both pipelines technically work.** The core issue is **not** that generation is broken —
it's that **~42% of video tile answer-options are silently ignored** (mismatch between the
tile questions and the prompt builders), so users pay 2 TOONTOON and get a generic/wrong video.
Details in §8.

---

## 1. High-level architecture

```
Frontend (/generate)                Backend (FastAPI)                    External
────────────────────                ─────────────────                    ────────
pick tile / free text  ──POST /api/generate──►  routers/generate.py
                                                  │ 1. rate-limit (30/h/user)
                                                  │ 2. resolve tile + cost
                                                  │ 3. wallet.reserve(TOONTOON)
                                                  │
                        ┌─────────────────────────┴───────────────────────┐
                        │ IMAGE (sync)                  VIDEO (async job)   │
                        ▼                               ▼                   │
              content_gen.generate()         video_gen.schedule_video_job()│
                        │                               │                  │
              gpt.build_prompt() ──OpenAI──►  video_prompts.build_storyboard()
                        │                               │                  │
              OpenAI Images / Pollinations    kie.ai createTask ──Seedance─┤
                        │                               │ poll recordInfo  │
              save → /uploads/img_*.png        download → /uploads/vid_*.mp4
                        │                               │ ffmpeg thumbnail │
              wallet.confirm()                 status=DONE + web-push      │
                        ▼                               ▼                   │
              return result_url            client polls GET /generations/{id}
```

### Two-phase TOONTOON billing (both paths)
1. **Reserve** TOONTOON up-front (`wallet.reserve`). Insufficient → `402`.
2. **Run** the generation.
3. **Confirm** on success (`wallet.confirm`) / **Cancel+refund** on failure (`wallet.cancel`).

- Image cost: **1 TOONTOON** · Video cost: **2 TOONTOON** (driven by `tile.cost` or type default).
- Type/tile mismatch is rejected **before** reserving TOONTOON (`generate.py` L70–79).
- New users start with **50 TOONTOON** (`SIGNUP_TOONTOON_BALANCE=50`).
- Rate limit: **30 generations / hour / user**.

---

## 2. IMAGE generation (`content_gen.py`)

**Provider:** `IMAGE_PROVIDER=openai` → OpenAI Images API `gpt-image-1`
(`openai_image_size=1024x1024`, `quality=medium`, 55s timeout).
Fallback model `dall-e-3` if `gpt-image-1` returns 400/403/404.
Alternative provider `pollinations` (FLUX) exists but is **off**.

**Prompt pipeline (`content_gen.generate`)**
1. If a photo was uploaded → `analyze_photo()` (Pollinations vision, 7s, soft-fail).
2. `gpt.build_prompt()` (GPT-4o-mini) builds the prompt:
   - **Picture tiles** (6) → fill the exact per-tile template (`picture_prompts.TEMPLATES`).
   - **Card tiles** (15) → fill the per-card template (`card_prompts.TEMPLATES`).
   - **Everything else** → GPT writes a 45–80 word **SCENE**, then `prompt_style.assemble()`
     wraps it: `[STYLE ANCHOR] , [SCENE] (, [LAYOUT if text tile]) , [TECHNICAL]`.
3. Fallback mechanical builder (`build_prompt`) if OpenAI is unavailable.
4. `strip_brands()` removes Pixar/Disney/etc., then OpenAI gets `prompt + OPENAI_VISUAL_GUARDS`
   (OpenAI ignores `negative_prompt`, so guards go in the positive prompt).
5. Download → store locally `/uploads/img_*.png`; on provider failure raise
   `GenerationUnavailable` → refund.

**Auto style selection** (when no explicit style): living subject → `3d_cartoon`;
epic landscape → `scene_epic`; otherwise → `scene_cozy`.

**Live result:** birthday postcard → 30.7s, clean 3D-cartoon character + cake + balloons,
clear upper-third space for the overlaid text. ✅

---

## 3. POSTCARD generation (`card_prompts.py`)

15 card tiles (birthday, jubilee, valentine, wedding, anniversary, mother's/father's day,
easter, thanksgiving, new_year, graduation, get_well, just_because, good_morning, good_day).

- Each card has a **template** (anchor + fields like `WHO_CHAR`, `NAME_PART`, `AGE_DETAIL`)
  filled by GPT via `_TEMPLATING_SYSTEM`. Card question ids match template field keys.
- **STRUCTURED** mode (answers) vs **FREE_TEXT** mode (typed idea).
- The greeting **text is never drawn by the generator** — the prompt reserves a clean
  area (`LAYOUT_BLOCK`), and the frontend overlays the text with `CardTextOverlay`
  (per-tile font/box placement).

---

## 4. VIDEO generation (`video_gen.py` + `video_prompts.py`)

**Architecture = text-to-video v2** (no keyframe images; the spec's two-step image→video
is **not** what the code does — see Problem P9).

**Three groups:**
- **Group A — scenes** (`living_nature`, `morning_video`, `inspiring_video`): deterministic
  builder → cozy/epic anchor + ambient motion.
- **Group B — living/characters** (`cute_animal_video`, `cartoon_character_video`,
  `video_greeting`): deterministic builder → 3D anchor + action motion.
- **Group C — photo-to-video** (`animate_photo`, `animate_pet`): user photo as
  `first_frame_url` + motion preset (image-to-video).
- **Free text** (no tile) → GPT categorises and writes anchor+motion JSON
  (`_build_freetext_storyboard`, fallback `_fallback_freetext`).

**Prompt formula:** `seedance_prompt = anchor + ". " + motion (+ " Audio: …")`.

**kie.ai call (`run_video_pipeline`)**
- `POST /api/v1/jobs/createTask` with `{model, input:{prompt, negative_prompt, resolution,
  aspect_ratio, duration, generate_audio[, first_frame_url]}}`.
- Model: `bytedance/seedance-2-fast` (no audio) or `bytedance/seedance-2` (audio).
- Poll `recordInfo` every 6s up to 600s; on `success` pull `resultUrls[0]`.
- Download MP4 → `/uploads/vid_*.mp4`, extract first-frame **thumbnail** via ffmpeg.
- Background job updates the `Generation` record DONE/FAILED (+refund), then **web-push**.

**Live result:** `inspiring_video` → 158s, valid `720×1280` 9:16, **8.04s**, 3.9 MB MP4,
beautiful cozy 3D forest scene. ✅ (but it ignored the user's "Mountains" choice — see P1.)

**Config:** `720p`, `9:16`, durations 8/10s (builders) — all valid per Seedance (4–15s).

---

## 5. Prompt & style system (`prompt_style.py`)

- **Style presets** (anchor + technical): `3d_cartoon` (living + cards), `scene_cozy`,
  `scene_epic`, `anime`, `realistic`. Flat styles (watercolor/pastel/storybook) all
  remap to the 3D family via `_STYLE_MAP`.
- **Single technical tail** `_TECHNICAL`: warm cinematic, textured, NOT flat/matte, 8k.
- **VIS layers** (used by card/picture templates): `PALETTES`, `EXPRESSION`, `POSE`,
  `COMPOSITION`, `LIGHT` — per-tile palette/expression/pose/framing/light injection.
- **Negative prompt** `NEGATIVE_PROMPT`: anti-horror, anti-flat, anti-text, anti-bad-framing.
  Used by Pollinations/Seedance-supporting providers; **OpenAI ignores it** (guards moved
  into positive prompt).
- **Brand guard** `strip_brands()` removes Pixar/Disney/Ghibli/Marvel/Nintendo/Pokémon/etc.
- **IP neutralizer** `neutralize_ip()` replaces named characters with generic look-alikes
  (SpongeBob → "a cheerful yellow cartoon sea-sponge character", Elsa, Pikachu, Mickey,
  Shrek, Minions, Peppa, Cheburashka, Smeshariki, …). Applied to free-text on both image
  and video paths.

---

## 6. Tiles inventory (29 total)

- **Image (6, 1 TOONTOON):** cartoon_character, cute_animal, birds, fish, nature, food
- **Postcard (15, 1 TOONTOON):** birthday, jubilee, valentine, wedding, anniversary,
  mothers_day, fathers_day, easter, thanksgiving, new_year, graduation, get_well,
  just_because, good_morning, good_day
- **Video (8, 2 TOONTOON):** animate_photo, animate_pet, cartoon_character_video,
  video_greeting, living_nature, cute_animal_video, morning_video, inspiring_video

---

## 7. Smoke tests performed

1. **Static/offline (113 assertions, all pass):**
   - Every image tile is a picture tile with a TEMPLATE; every postcard is a card tile with a TEMPLATE.
   - Mechanical `build_prompt()` produces a non-empty, brand-free prompt for all 21 image+card tiles.
   - All 8 video builders run without error; durations ∈ {5,8,10}; assembled prompts > 50 chars.
   - `neutralize_ip()` removes SpongeBob/Elsa/Pikachu/Mickey; `strip_brands()` removes Pixar.
   - Living-subject detection (котик, grandmother) true; car false; epic (mountains) true.
2. **Live image:** real OpenAI `gpt-image-1` generation via the actual pipeline → success, good quality.
3. **Live video:** real Seedance-2-fast generation via the actual pipeline → success, valid 9:16 8s MP4.
4. **External:** OpenAI `/models` (200), kie.ai credit `2993.0`, kie.ai `recordInfo` reachable,
   Seedance model slugs + input schema verified against kie.ai docs.

---

## 8. PROBLEMS FOUND (prioritized)

### 🔴 P1 — Video tile options don't match the builder keys → 23/55 (42%) answers ignored
The biggest cause of "good quality, wrong content" videos. The deterministic builders look up
answer values in fixed dictionaries; if the tile's option text isn't a key, it **silently
falls back to a default** and the user's choice is discarded.

| Tile | Question | Options that are IGNORED (fall to default) |
|------|----------|--------------------------------------------|
| `morning_video` | subject | **Nature, Tea** (builder knows: sunrise, flowers, cup of coffee, window) |
| `morning_video` | light | **Pastel, Bright, Golden, Soft** (builder knows: sun rays, light fog, blooming flowers, light dust) |
| `inspiring_video` | subject | **Mountains, Forest, Sunset, Flowers** (builder knows: sunny landscape, blooming field, sea, city) |
| `inspiring_video` | light | **Sunny, Fog, Golden hour, Stars** (builder knows: light wind, sun glints, floating petals, drifting clouds) |
| `cute_animal_video` | animal/action/place | **Other, Surprised, In the forest, Outside** |
| `cartoon_character_video` | action | **Laughing, Surprised** |
| `video_greeting` | occasion | **Jubilee, New Year, Other** → all become a generic **Birthday** scene |

**Proof (live):** `inspiring_video` with subject="Mountains", light="Sunny" produced a cozy
**forest treehouse** (scene_cozy default), not a sunny mountain (scene_epic). The user paid
2 TOONTOON for content unrelated to their choices.

**Fix:** align `tiles_data` video option strings with the builder dictionary keys
(or add the option strings as keys/aliases in `video_prompts.py`). Lowercase + emoji-strip
matching already exists — only the vocabularies are out of sync.

### 🔴 P2 — Group C (photo→video) ignores the motion question entirely
`build_photo_motion_prompt()` reads answer keys `motion` / `intensity` / `audio`, but:
- `animate_photo` asks `target`, `movement`, `intensity`, `mood` → `movement` ≠ `motion`,
  so motion is **always** the default "gentle life"; there is **no `audio` question** so audio is always off.
- `animate_pet` asks `action`, `effect`, `mood` → **none** map → motion + intensity always default.

**Effect:** every animated photo/pet uses the same default micro-movement regardless of what the
user picks. **Fix:** rename the question ids (or read `movement`/`action`/`effect`) and add the
intensity/audio questions where intended.

### 🟠 P3 — `negative_prompt` is silently dropped for video
Seedance 2.0's `createTask` schema has **no `negative_prompt` field** (verified against kie.ai
docs). The carefully-assembled `board.negative` is sent but ignored. Not harmful, but the video
anti-horror/anti-text guard does nothing. **Fix:** bake key negatives into the positive prompt
(as done for OpenAI images) or drop the field and document it.

### 🟠 P4 — Greeting/inspirational TEXT never appears on videos
`video_greeting` / `morning_video` / `inspiring_video` builders compute `text_overlay`
("Happy Birthday, Mom!", "Live life fully") and reserve clean space (`LAYOUT_TEXT`), **but**:
- Seedance isn't asked to render text (correct — models render text badly), and
- the frontend `<video>` element (page.tsx L1642–1658) has **no** `CardTextOverlay`
  (overlay is applied to images only).

So the text feature for video is **dead** — the user's message is silently lost.
**Fix:** overlay the text on top of the `<video>` (like `CardTextOverlay` for images), or
drop the text questions from video tiles to avoid promising something we don't deliver.

### 🟠 P5 — `dall-e-3` fallback is dead on this account
OpenAI `/models` does **not** list `dall-e-3` for the current key. If `gpt-image-1` ever
returns 400/403, the fallback also 404s → the image fails and TOONTOON is refunded. Single point
of failure. **Fix:** confirm dall-e-3 access or set the fallback to a model the account has
(or fall back to Pollinations).

### 🟡 P6 — Push-notification text is Russian; rest of product is English
`video_gen.run_video_job` sends `"🎬 Видео готово!"` / `"«…» готово!"` while the UI, chat,
and prompts were standardized to English. **Fix:** translate the push title/body to English.

### 🟡 P7 — Builder reaches "epic" only by accident
Because `inspiring_video` scene options never match (`P1`), `scale` is never `epic`, so
`STYLE_3D_SCENE_EPIC` is effectively unreachable for that tile even when the user picks
"Mountains". Fixing P1 resolves this.

### 🟡 P8 — `morning_video`/`inspiring_video` mood option is unused
The `mood` question has no builder mapping (it's not read at all), so it's purely decorative.
Either feed it into the anchor or remove it.

### 🟢 P9 — Code diverges from `generation-master.md` (documentation drift)
The master spec mandates a **two-step image→video** pipeline; the code is **single-step
text-to-video v2**. Quality is good, but the spec and code disagree — decide which is the
source of truth and reconcile (otherwise future work will re-introduce confusion).

### 🟢 P10 — Reference photo not used for non-Group-C tiles
A `photo_url` uploaded on a non-`animate_*` video tile is ignored by the video pipeline
(only `analyze_photo` for images uses it). Acceptable, but worth confirming the UI doesn't
offer photo upload where it has no effect.

---

## 9. External dependencies & config snapshot

| Setting | Value |
|---------|-------|
| `IMAGE_PROVIDER` | `openai` (`gpt-image-1`, 1024², quality=medium) |
| OpenAI models available | `gpt-4o-mini` ✓, `gpt-image-1` ✓, `dall-e-3` ✗ |
| kie.ai credits | **2993** |
| Seedance models | `bytedance/seedance-2-fast`, `bytedance/seedance-2` (valid) |
| Video | 720p · 9:16 · 8–10s · poll 6s/600s |
| `SIGNUP_TOONTOON_BALANCE` | 50 · image=1 · video=2 · rate-limit=30/h |
| Push (VAPID) | configured ✓ |
| `PUBLIC_BASE_URL` | `http://193.149.190.155` (HTTP, not HTTPS) |

---

## 10. Recommended fix order (most TOONTOON saved first)
1. **P1 + P2** — align video tile options ↔ builder keys (stops 42% of wasted video TOONTOON).
2. **P4** — render text on videos (or remove the text questions).
3. **P5** — fix/replace the dead dall-e-3 image fallback (prevents image outages).
4. **P3 / P6 / P8** — drop/relocate video negative prompt, English push text, use `mood`.
5. **P9 / P10** — reconcile spec vs code; audit photo-upload affordances.
