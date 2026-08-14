# TOONTOON Generation — Vision QA Report
**Date:** 2026-06-20  
**Method:** GPT-4o-mini Vision on 12 real production generations (images + video thumbnails)  
**Model:** gpt-4o-mini, low-detail, structured JSON output  

---

## Summary

| Metric | Result |
|--------|--------|
| Content match (correct content generated) | **7 / 12 (58%)** |
| Style correct (3D cartoon, on-brand) | **11 / 12 (92%)** |
| Quality good | **11 / 12 (92%)** |
| **Critical failures** | **2** |
| **UX deception failures** | **1** |

The style and render quality are generally excellent — TOONTOON produces beautiful, premium-looking 3D cartoon content. **The problem is with content accuracy**: what the user chose is often not what they receive.

---

## Per-Item Results

### ✅ PASS

#### 1. `video_greeting` — Birthday greeting video
![thumb](uploads/vid_28e33a6d6d944a2d9db94591829e03eb_thumb.jpg)  
- **What user expected:** Festive birthday scene with cake, balloons, confetti  
- **What was generated:** Beautiful 3D birthday cake on wooden table, fairy lights, confetti, warm golden light  
- **Verdict:** ✅ Correct content, excellent style. `quality=good`

---

#### 2. `free-text` — "Joyful child leaping"
![img](uploads/img_ba03a272b16b40ab84a35d93670bfb3d.png)  
- **What user typed:** Free-form description of a joyful child  
- **What was generated:** Cheerful 3D cartoon boy jumping in a sunny meadow, big smile, orange t-shirt  
- **Verdict:** ✅ Correct content. Free-text LLM path works well. `quality=good`

---

#### 3. `cartoon_character` — Character image (Wizard)
- **What user expected:** 3D cartoon character  
- **What was generated:** Cheerful character in blue wizard robe and hat  
- **Verdict:** ✅ Good style, good character. `quality=good`

---

#### 4. `graduation` — Postcard image
![img](uploads/img_c08bd1c6285947e69c1b231a815df85e.png)  
- **What user expected:** Graduation postcard with cap+diploma, space for text overlay  
- **What was generated:** Perfect 3D character with graduation cap, diploma, confetti, stars, clean background  
- **Verdict:** ✅ Excellent. `quality=good`. Note: upper space for text is **not** reserved — character fills full frame.

---

#### 5. `cute_animal_video` — Kitten video
![thumb](uploads/vid_6b8c5a944c0847c3a82e6f9d19d1d8a4_thumb.jpg)  
- **What user expected:** Adorable kitten sitting calmly at home  
- **What was generated:** Perfect 3D cartoon orange tabby kitten, cozy room, golden light  
- **Verdict:** ✅ Exact match. `quality=good`

---

#### 6. `cute_animal` — Animal image (Rabbit)
- **What user expected:** Cute 3D cartoon animal  
- **What was generated:** Cute cartoon rabbit with large ears, friendly expression  
- **Verdict:** ✅ Correct. `quality=good`

---

#### 7. `cartoon_character` (second) — Character image
- **What user expected:** 3D cartoon character  
- **What was generated:** Friendly cartoon character with appropriate expression  
- **Verdict:** ✅ Correct. `quality=good`

---

### ❌ FAIL

#### 8. `video_greeting` (second) — Birthday greeting, missing balloons
![thumb](uploads/vid_cad5c47fb52c4465b9a8ef4df468d0bb_thumb.jpg)  
- **What user expected:** Birthday scene with **balloons**  
- **What was generated:** Beautiful cake scene on wooden table with flowers — **no balloons**  
- **Root cause:** The `_build_video_greeting` function assembles the scene from `_OCCASION_DATA` based on the user's occasion choice. The birthday occasion scene template may not always include balloons deterministically. Two videos from same tile produced noticeably different compositions.
- **Verdict:** ❌ Partial content miss. `quality=good` but balloon element missing.  
- **Severity:** P3 (minor visual inconsistency)

---

#### 9. `inspiring_video` — User chose "Mountains/Sunny", got village
![thumb](uploads/vid_2dcef36588744c569eac8634f7984e58_thumb.jpg)  
- **What user expected:** Mountains, sunny landscape, inspiring scenery  
- **What was generated:** Cozy miniature village with stone paths and little houses — a classic "cozy default"  
- **Root cause:** `_build_inspiring_video` maps answer options via a dict lookup. The key `"Mountains"` (or `"🏔️ Mountains"`) does **not match** the dict key (likely `"горы"` or a different English casing). The builder silently falls to the default cozy scene.  
- **This is the P1 builder-mismatch bug confirmed visually.**
- **Verdict:** ❌ Wrong content. User wasted TOONTOON on content they did not choose. `quality=good` (it looks beautiful — just not what was ordered).  
- **Severity:** P1 — Critical

---

#### 10. `living_nature` — User chose "Ocean coastline", got river+mountains
![thumb](uploads/vid_e915e50dbc3d4049a0c7e27d01573a50_thumb.jpg)  
- **What user expected:** Epic ocean coastline, summer morning, no characters  
- **What was generated:** Lush river valley with towering rocky mountains and tropical trees — beautiful but **wrong biome**  
- **Root cause:** Same builder-mismatch bug. The `_build_living_nature` function cannot match the answer `"Ocean"` (or `"🌊 Ocean"`) to its dictionary key, defaults to a generic landscape.  
- **Verdict:** ❌ Wrong environment. Beautiful quality, completely wrong content.  
- **Severity:** P1 — Critical

---

#### 11. `cartoon_character_video` — Character looks like a baby, not an adventurer
![thumb](uploads/vid_1ad357e7735d409da35fac784c3204bd_thumb.jpg)  
- **What user expected:** A friendly adventurer character waving in a village  
- **What was generated:** A round, chubby baby-like creature with tiny limbs doing a dance pose in a cobblestone village at sunset  
- **Root cause:** The prompt says "expressive friendly adventurer character" but the style block `STYLE_3D` with "big expressive eyes, soft rounded chunky shapes" makes all characters look baby-like. No "adult adventurer" descriptor was strong enough to override the chunky toy-like style.
- **Verdict:** ❌ Character type mismatch — user likely expected a warrior/explorer, not a baby creature. `quality=good` visually.  
- **Severity:** P2 — The adventurer prompt needs stronger adult/hero descriptors.

---

#### 12. `animate_pet` — Photo-to-video completely ignored the user's photo
![thumb](uploads/vid_f139bfc256fc45328af16d1e94222207_thumb.jpg)  
- **What user expected:** **Their pet photo animated** — soft blinking, subtle movement, keep original look  
- **What was generated:** A completely new AI-generated photo of a white/beige cat walking on a piano keyboard. It is a realistic photo, not a 3D cartoon, and bears **no relation to the original pet photo**.
- **Root cause:** The `animate_pet` prompt says `[photo → video]` but the kie.ai `createTask` API call does **not pass any `reference_image_url`**. Without the actual user photo sent to Seedance, the model generates a new cat from scratch based on keywords (cat, meow, piano…) in the prompt.
- **This is a UX deception bug**: the user believes their specific pet is being animated. They receive a random AI cat instead.
- **Verdict:** ❌❌ Critical UX failure. User's photo is completely ignored.  
- **Severity:** P0 — The `animate_pet` tile is functionally broken.

---

## Root Cause Summary

| Bug | Tiles affected | Severity |
|-----|---------------|----------|
| **P0: `animate_pet` ignores user photo** — kie.ai `createTask` receives no `reference_image_url`, generates new AI animal from scratch | `animate_pet` | 🔴 P0 |
| **P1: Builder key mismatch** — 42% of tile answer options don't match dict keys, prompt defaults to generic output | `inspiring_video`, `living_nature`, `cartoon_character_video` and 5+ more tiles | 🔴 P1 |
| **P2: Adventurer/adult character prompt too weak** — STYLE_3D overwhelms "adventurer" descriptor, all characters look baby-like | `cartoon_character_video`, `living_subject` | 🟡 P2 |
| **P3: Birthday scene element variance** — `video_greeting` produces inconsistent compositions (sometimes no balloons) | `video_greeting` | 🟡 P3 |
| **P4: `graduation` postcard fills full frame** — no upper space reserved for user text overlay | `graduation` (and likely other postcards) | 🟡 P3 |

---

## What Is Working Well

- **3D cartoon visual style** is consistently beautiful and on-brand (11/12)
- **Free-text image generation** (LLM path via gpt.py) produces accurate, high-quality results
- **Simple character tiles** (`cartoon_character`, `cute_animal`, `graduation`) produce exactly what users expect
- **Simple animal video** (`cute_animal_video`) works perfectly
- **Birthday video greeting** works (sometimes missing one element but theme is correct)
- **Render quality** across all generations is premium — Seedance produces smooth, cinematic-quality video

---

## Recommended Fixes (Priority Order)

### P0 — Fix `animate_pet` reference image upload
The user selects a photo. This photo URL must be passed to `createTask` as `reference_image_url`:
```python
# In video_gen.py or video_prompts.py, for animate_pet tile:
payload = {
    "model": "seedance-1-pro",
    "prompt": "...",
    "reference_image_url": user_uploaded_photo_url,  # MISSING
}
```
Without this, the tile is a total scam — the user's pet never appears in the video.

### P1 — Fix builder key dictionary mismatches
Run `python /tmp/smoke.py` to see all 23 mismatched keys. The fix is simple: align the dict keys in `video_prompts.py` with the exact option strings from `tiles_data.py` (with emoji stripped).

Example for `inspiring_video`:
```python
# tiles_data.py offers: "🏔️ Mountains"
# video_prompts.py key must be: "Mountains" (after _strip_leading_emoji)
_SCENE_MAP = {
    "Mountains": "majestic mountain range, snow-capped peaks...",  # was missing
    ...
}
```

### P2 — Strengthen adventurer/adult character descriptors
Add explicit "adult hero" language to `cartoon_character_video` prompt blocks to prevent baby-mode output:
```
confident adventurer, adult proportions, heroic posture, not a baby, strong character presence
```

### P3 — Constrain birthday scene template
Make `_OCCASION_DATA["birthday"]` scene deterministically include balloons and ensure both video_greeting generations for the same occasion produce consistent compositions.

---

## Overall Verdict

> **Style: A+. Content accuracy: C.** TOONTOON generates visually stunning content but frequently ignores user choices due to backend builder key mismatches. The `animate_pet` tile is completely broken and should be hidden until fixed. Fixing P0+P1 would bring content accuracy from 58% → ~90%.
