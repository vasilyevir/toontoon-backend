# Video Tile: `nature-video` (Seedance 2.0)

Живое видео природы: один кадр-якорь → Seedance 2.0 добавляет амбиентное движение → бесшовный
loop ≤ 15 секунд. Сцена без персонажа, со звуком.

| Параметр | Значение |
|---|---|
| Режим | `kinemagraph` (1 кадр + амбиентное движение) |
| Длительность | 8 секунд |
| Loop | да (бесшовный) |
| Формат | вертикаль 9:16, 720p |
| Текст в кадре | нет |
| Звук | `generate_audio` (синтезирует Seedance) |
| Цена | 2 TOONTOON |

---

## 1. Сквозной флоу

```
ЧАТ                          СЕРВЕР (/api)                         kie.ai (Seedance 2.0)
──────────────────────────────────────────────────────────────────────────────────────
1. юзер выбирает nature-video
   → отвечает на 5 вопросов: place / time_season / special / mood / audio
2. ответы собраны ─────────▶ POST /api/video
                              ├ auth + баланс ≥ 2 TOONTOON + rateLimit
                              ├ detectMode(answers) → STRUCTURED | FREE_TEXT
                              ├ buildStoryboardInstruction("nature-video", answers, mode)
                              ├ chatCompletion → строгий JSON:
                              │     { mode, loop, durationSec, anchor, motion, audio, negative }
                              ├ генерим ЯКОРЬ через Pollinations flux → /api/img/{id} URL
                              ├ Generation{ status:"running", resultType:"video", taskId } в Redis
                              ├ createVideoTask(...) ───────────────▶ POST /jobs/createTask
                              │                                ◀───── taskId
                              └ сохраняем taskId
   ◀── { generationId } ──────┘
3. бабл «генерим видео ~4-5 мин…» + прогресс; клиент поллит /api/video/status?id
4. state:success → resultUrl, status:"done"; списываем 2 TOONTOON; бабл → <video loop muted autoplay>
```

---

## 2. Базовые блоки (дословно)

`nature-video` — сцена без персонажа, поэтому используем сценные анкоры.

```
STYLE_3D_SCENE_COZY =      // лес, поле, озеро, луг
"cozy stylized 3D cartoon render, modern animated feature film look,
soft rounded chunky toy-like shapes, charming miniature diorama aesthetic,
richly detailed tactile materials with visible texture (wood grain, stone, fabric),
warm naturalistic saturated colors, lush detailed environment,
no people, no characters, inviting heartwarming atmosphere"

STYLE_3D_SCENE_EPIC =      // горы, море, скалы, океан
"epic stylized 3D cartoon landscape render, modern animated feature film look,
bold saturated colors, dramatic depth and scale, lush detailed environment,
no people, no characters, majestic cinematic atmosphere"

TECHNICAL =
"Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows,
glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic),
ray-traced global illumination, high quality 3D render, 8k resolution,
crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh"

NEGATIVE =
"distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions,
messy cluttered background, small unreadable text,
watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs,
low quality, jpeg artifacts, scary dark atmosphere,
dull muted colors, washed out palette, grey tones,
flat 2D illustration, flat vector art, matte plastic look, flat even lighting"
```

Выбор анкора по месту: Горы / Море / Скалы / Океан → `EPIC`; Лес / Поле / Озеро / Луг → `COZY`.

Запрещено в промпте: Pixar, Disney, realistic, beautiful, big expressive eyes, character design,
matte ceramic, PBR, subsurface scattering, плоские стили, текст/буквы в кадре.

---

## 3. Структура данных

### Интерфейс шаблона

```ts
interface VideoTemplate {
  mode: "kinemagraph" | "keyframes";
  durationSec: number;                 // ≤ 15
  loop: boolean;
  styleAnchor: "living" | "scene_auto";// scene_auto → COZY/EPIC по месту
  anchor: string;
  hasLastFrame: boolean;
  motion: string;
  collectPassport: boolean;
  defaultCategory: "person" | "animal" | "nature" | "object" | "food";
  text: { enabled: boolean; zone: "upper_third"; timing: "final_hold" | "full_loop" };
  audio: { enabled: boolean; default: "music" | "ambient" | "sfx" | "silence" };
}
```

### Инстанс `nature-video`

```ts
"nature-video": {
  mode: "kinemagraph",
  durationSec: 8,
  loop: true,
  styleAnchor: "scene_auto",
  hasLastFrame: false,
  collectPassport: false,
  defaultCategory: "nature",
  text: { enabled: false, zone: "upper_third", timing: "full_loop" },
  audio: { enabled: true, default: "ambient" },
}
```

### JSON-выход LLM

```json
{
  "mode": "kinemagraph",
  "loop": true,
  "durationSec": 8,
  "anchor": "<COZY|EPIC + сцена + TECHNICAL>",
  "motion": "<амбиентное движение + дрейф камеры (+ Audio: ...)>",
  "audio": { "enabled": true, "prompt": "<английское описание звука>" },
  "negative": "<NEGATIVE>"
}
```

---

## 4. Вопросы в чате (5 шагов)

По одному вопросу за раз: AI-бабл + 4 кнопки + **✍️ Своё** (свободный ввод). Кнопки «Пропустить»
нет — если не ответили, применяется дефолт.

| # | key | Вопрос | Кнопки |
|---|---|---|---|
| 1 | `place` | Какое место покажем? | 🌲 Лес · 🌊 Море · ⛰️ Горы · 🌾 Поле · ✍️ Своё |
| 2 | `time_season` | Какое время года и суток? | ☀️ Летнее утро · 🍂 Осенний закат · ❄️ Зимняя ночь · 🌸 Весенний день · ✍️ Своё |
| 3 | `special` | Что особенного в кадре? | 🍃 Ветер · 🌫️ Туман · ❄️ Снег · 🌸 Цветение · ✍️ Своё |
| 4 | `mood` | Какое настроение? | 😌 Спокойное · ✨ Волшебное · 🌟 Яркое · ✍️ Своё |
| 5 | `audio` | Какой звук добавим? | 🌿 Звуки природы · 🎶 Спокойная мелодия · 🔇 Тишина · ✍️ Своё |

---

## 5. Маппинг ответов → английские слова

### `place` → `{PLACE}` + анкор

| Кнопка | `{PLACE}` | Анкор |
|---|---|---|
| 🌲 Лес | `lush green forest with rolling hills` | COZY |
| 🌊 Море | `open ocean coastline with a rocky shore` | EPIC |
| ⛰️ Горы | `majestic mountain range with deep valleys` | EPIC |
| 🌾 Поле | `wide open flower meadow` | COZY |
| ✍️ Своё | перевод текста на английский | EPIC если масштабное (горы/океан/скалы/каньон), иначе COZY |

Дефолт: `lush green forest with rolling hills` (COZY).

### `time_season` → `{TIME_SEASON}`

| Кнопка | `{TIME_SEASON}` |
|---|---|
| ☀️ Летнее утро | `summer morning with warm golden sunrise light and soft mist` |
| 🍂 Осенний закат | `autumn sunset with golden-amber light and long warm shadows` |
| ❄️ Зимняя ночь | `winter night with cool blue moonlight and a soft starry sky` |
| 🌸 Весенний день | `bright spring day with fresh clear daylight and gentle sun` |
| ✍️ Своё | перевод сезона + времени суток на английский |

Дефолт: `summer morning with warm golden sunrise light and soft mist`.

### `special` → `{SPECIAL_IN_AIR}` (в якорь) + `{SPECIAL_MOTION}` (в motion)

| Кнопка | `{SPECIAL_IN_AIR}` | `{SPECIAL_MOTION}` |
|---|---|---|
| 🍃 Ветер | `a soft breeze` | `tall grass and leaves swaying gently, soft ripples moving across the scene` |
| 🌫️ Туман | `soft drifting fog` | `fog rolling softly, light rays gently shifting through it` |
| ❄️ Снег | `gently falling snow` | `snowflakes drifting slowly downward` |
| 🌸 Цветение | `floating blossom petals` | `flower petals drifting softly on the breeze` |
| ✍️ Своё | подходящая по смыслу фраза для якоря | подходящая фраза движения для motion |

Дефолт: `a soft breeze` / `tall grass and leaves swaying gently, soft ripples moving across the scene`.

### `mood` → `{MOOD}`

| Кнопка | `{MOOD}` |
|---|---|
| 😌 Спокойное | `serene and calm, soft soothing atmosphere` |
| ✨ Волшебное | `magical and dreamy, enchanting glow, subtle sparkles in the light` |
| 🌟 Яркое | `vivid and uplifting, bright saturated colors, lively energy` |
| ✍️ Своё | перевод настроения на английский |

Дефолт: `serene and calm, soft soothing atmosphere`.

### `audio` → `audio.enabled` + `{AUDIO_PROMPT}`

| Кнопка | `enabled` | `{AUDIO_PROMPT}` |
|---|---|---|
| 🌿 Звуки природы | `true` | `soft ambient nature sounds matched to the scene — ocean waves for the sea, wind for mountains, birdsong and rustling leaves for the forest` |
| 🎶 Спокойная мелодия | `true` | `calm gentle ambient instrumental music, soft and soothing, no vocals` |
| 🔇 Тишина | `false` | — |
| ✍️ Своё | `true` | перевод описания звука на английский |

Дефолт: «Звуки природы». В свободном тексте звук включаем молча по дефолту, если он не упомянут.

---

## 6. Шаблоны промпта

### Якорь (единственный кадр)

```
{ANCHOR_STYLE},
cinematic {PLACE} landscape, {TIME_SEASON}, with {SPECIAL_IN_AIR},
serene natural color palette, atmospheric depth of field, wide establishing shot,
the full landscape at rest, balanced natural composition, inviting calm, {MOOD},
{TECHNICAL}
```

`{ANCHOR_STYLE}` = `STYLE_3D_SCENE_COZY` или `STYLE_3D_SCENE_EPIC` по месту.

### Motion (директива Seedance)

```
ambient motion only — {SPECIAL_MOTION}, very slow camera drift with subtle parallax depth,
no subject morphing, natural seamless looping movement. Audio: {AUDIO_PROMPT}
```

`Audio: {AUDIO_PROMPT}` добавляется только при `audio.enabled = true`. При «Тишина» — предложение
про Audio не пишем.

---

## 7. Готовые примеры

### Пример A — Лес · Летнее утро · Ветер · Спокойное · Звуки природы

```json
{
  "mode": "kinemagraph",
  "loop": true,
  "durationSec": 8,
  "anchor": "cozy stylized 3D cartoon render, modern animated feature film look, soft rounded chunky toy-like shapes, charming miniature diorama aesthetic, richly detailed tactile materials with visible texture (wood grain, stone, fabric), warm naturalistic saturated colors, lush detailed environment, no people, no characters, inviting heartwarming atmosphere, cinematic lush green forest with rolling hills landscape, summer morning with warm golden sunrise light and soft mist, with a soft breeze, serene natural color palette, atmospheric depth of field, wide establishing shot, the full landscape at rest, balanced natural composition, inviting calm, serene and calm, soft soothing atmosphere, Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), ray-traced global illumination, high quality 3D render, 8k resolution, crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh",
  "motion": "ambient motion only — tall grass and leaves swaying gently, soft ripples moving across the scene, very slow camera drift with subtle parallax depth, no subject morphing, natural seamless looping movement. Audio: soft ambient nature sounds matched to the scene, birdsong and rustling leaves",
  "audio": { "enabled": true, "prompt": "soft ambient nature sounds matched to the scene, birdsong and rustling leaves" },
  "negative": "distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions, messy cluttered background, small unreadable text, watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs, low quality, jpeg artifacts, scary dark atmosphere, dull muted colors, washed out palette, grey tones, flat 2D illustration, flat vector art, matte plastic look, flat even lighting"
}
```

### Пример B — Горы · Осенний закат · Туман · Волшебное · Спокойная мелодия

```json
{
  "mode": "kinemagraph",
  "loop": true,
  "durationSec": 8,
  "anchor": "epic stylized 3D cartoon landscape render, modern animated feature film look, bold saturated colors, dramatic depth and scale, lush detailed environment, no people, no characters, majestic cinematic atmosphere, cinematic majestic mountain range with deep valleys landscape, autumn sunset with golden-amber light and long warm shadows, with soft drifting fog, serene natural color palette, atmospheric depth of field, wide establishing shot, the full landscape at rest, balanced natural composition, inviting calm, magical and dreamy, enchanting glow, subtle sparkles in the light, Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), ray-traced global illumination, high quality 3D render, 8k resolution, crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh",
  "motion": "ambient motion only — fog rolling softly, light rays gently shifting through it, very slow camera drift with subtle parallax depth, no subject morphing, natural seamless looping movement. Audio: calm gentle ambient instrumental music, soft and soothing, no vocals",
  "audio": { "enabled": true, "prompt": "calm gentle ambient instrumental music, soft and soothing, no vocals" },
  "negative": "distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions, messy cluttered background, small unreadable text, watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs, low quality, jpeg artifacts, scary dark atmosphere, dull muted colors, washed out palette, grey tones, flat 2D illustration, flat vector art, matte plastic look, flat even lighting"
}
```

### Пример C — Море · Зимняя ночь · Снег · Спокойное · Тишина (звук выключен)

`audio.enabled = false`, в `motion` предложение `Audio:` отсутствует, на сервере
`generate_audio = false`. Остальное — по тем же шаблонам (анкор `EPIC`, place
`open ocean coastline with a rocky shore`, `winter night with cool blue moonlight and a soft starry sky`,
`with gently falling snow`, motion `snowflakes drifting slowly downward, …`).

---

## 8. Инструкция LLM — STRUCTURED (кнопки)

```
Build a 1-frame kinemagraph storyboard for a nature video. Translate the answers to English and fill the template.
Pick the STYLE ANCHOR by the place: grand/dramatic (mountains, ocean, cliffs) → EPIC; gentle/intimate (forest, field, lake, meadow) → COZY.
Keep all fixed blocks exactly as written. No people, no characters, no text in the image.
Output ONLY valid JSON: { mode, loop, durationSec, anchor, motion, audio, negative }

ANSWERS:
place: {place}
time_season: {time_season}
special: {special}
mood: {mood}
audio: {audio}

COZY ANCHOR:
cozy stylized 3D cartoon render, modern animated feature film look, soft rounded chunky toy-like shapes, charming miniature diorama aesthetic, richly detailed tactile materials with visible texture (wood grain, stone, fabric), warm naturalistic saturated colors, lush detailed environment, no people, no characters, inviting heartwarming atmosphere

EPIC ANCHOR:
epic stylized 3D cartoon landscape render, modern animated feature film look, bold saturated colors, dramatic depth and scale, lush detailed environment, no people, no characters, majestic cinematic atmosphere

ANCHOR TEMPLATE (the only keyframe):
{CHOSEN ANCHOR}, cinematic {PLACE} landscape, {TIME_SEASON}, with {SPECIAL_IN_AIR}, serene natural color palette, atmospheric depth of field, wide establishing shot, the full landscape at rest, balanced natural composition, inviting calm, {MOOD}, Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), ray-traced global illumination, high quality 3D render, 8k resolution, crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh

MOTION TEMPLATE:
ambient motion only — {SPECIAL_MOTION}, very slow camera drift with subtle parallax depth, no subject morphing, natural seamless looping movement.
If audio is not silence, append: " Audio: {AUDIO_PROMPT}"

MAPPINGS:
place        → {PLACE}: forest="lush green forest with rolling hills"(COZY); sea="open ocean coastline with a rocky shore"(EPIC); mountains="majestic mountain range with deep valleys"(EPIC); field="wide open flower meadow"(COZY)
time_season  → {TIME_SEASON}: summer morning="summer morning with warm golden sunrise light and soft mist"; autumn sunset="autumn sunset with golden-amber light and long warm shadows"; winter night="winter night with cool blue moonlight and a soft starry sky"; spring day="bright spring day with fresh clear daylight and gentle sun"
special      → {SPECIAL_IN_AIR} / {SPECIAL_MOTION}: wind="a soft breeze" / "tall grass and leaves swaying gently, soft ripples moving across the scene"; fog="soft drifting fog" / "fog rolling softly, light rays gently shifting through it"; snow="gently falling snow" / "snowflakes drifting slowly downward"; blossom="floating blossom petals" / "flower petals drifting softly on the breeze"
mood         → {MOOD}: calm="serene and calm, soft soothing atmosphere"; magical="magical and dreamy, enchanting glow, subtle sparkles in the light"; vivid="vivid and uplifting, bright saturated colors, lively energy"
audio        → nature sounds: enabled=true, {AUDIO_PROMPT}="soft ambient nature sounds matched to the scene (waves/wind/birdsong by place)"; calm music: enabled=true, {AUDIO_PROMPT}="calm gentle ambient instrumental music, soft and soothing, no vocals"; silence: enabled=false (omit the Audio sentence)

mode: kinemagraph | loop: true | durationSec: 8
negative: {NEGATIVE}
```

## 9. Инструкция LLM — FREE_TEXT (свободный текст)

```
The user described a nature video in free text. Extract meaning, pick the COZY/EPIC anchor by the place, build the same 1-frame kinemagraph. Output ONLY the JSON.

Extract (use defaults if unclear):
→ PLACE: landscape type (default: lush green forest with rolling hills)
→ TIME_SEASON: season + time of day (default: summer morning with warm golden sunrise light and soft mist)
→ SPECIAL: ambient element — wind / fog / snow / blossom (default: a soft breeze)
→ MOOD: tone (default: serene and calm, soft soothing atmosphere)
→ AUDIO: if the user mentioned sound or music → put it in audio.prompt (enabled:true); otherwise default to nature sounds (enabled:true). Do NOT ask a follow-up just about audio.

Use the same ANCHOR TEMPLATE, MOTION TEMPLATE and MAPPINGS as STRUCTURED above.
```

---

## 10. Граничные случаи

**А. Противоречие пресетов → уточняющий бабл.** Детектим детерминированно по ответам-кнопкам:

| Противоречие | Триггер |
|---|---|
| снег в тепле | `special = Снег` + `time_season ∈ {Летнее утро, Весенний день}` |
| цветение зимой | `special = Цветение` + `time_season = Зимняя ночь` |

Бабл: «❄️ Снег летним утром — так и задумано (волшебная небылица) или сделать сцену зимней?»
→ кнопки `✨ Так и хочу` / `❄️ Сделать зимней`. «Так и хочу» — оставляем как есть; «Сделать зимней»
— меняем `time_season` на зимний пресет. Свободный ввод на противоречия не проверяем — LLM
гармонизирует сам.

**Б. Живое существо в свободном вводе → мягкая подсказка.** Если в «Своё» LLM видит существо с
лицом (медведь, олень, кот, человек), бабл: «🐾 Похоже, ты описываешь живого героя. Здесь мы делаем
пейзаж — для героев лучше подойдёт отдельный шаблон. Оставить существо далёким силуэтом или сменить
шаблон?» → `🏞️ Оставить фоном` (LLM рисует далёкий силуэт без морды, `no people, no characters`
сохраняем) / `🔁 Сменить шаблон` (возврат в выбор тайла).

**В. Пустое / бессмыслица в свободном вводе → переспрос.** Ответ пустой, «не знаю», «asdf» —
переспрашиваем именно этот вопрос: «Не совсем понял 🙂 опиши парой слов — например,
“горное озеро на рассвете”.»

---

## 11. Флоу отправки → Seedance input (kie.ai)

| Поле storyboard | Seedance `input` |
|---|---|
| `anchor` → Pollinations flux → `/api/img/{id}` URL | `first_frame_url` + `reference_image_urls[0]` |
| `motion` | `prompt` |
| `audio.enabled` | `generate_audio` |
| `negative` | `negative_prompt` |
| `durationSec` | `duration` |
| — (сервер) | `resolution: "720p"`, `aspect_ratio: "9:16"` |
| `loop` | при воспроизведении, в Seedance не уходит |

```
POST https://api.kie.ai/api/v1/jobs/createTask   (Authorization: Bearer ${KIE_API_KEY})
{
  "model": "bytedance/seedance-2",
  "input": {
    "prompt": "<motion>",
    "negative_prompt": "<NEGATIVE>",
    "first_frame_url": "<URL якоря>",
    "reference_image_urls": ["<URL якоря>"],
    "resolution": "720p",
    "aspect_ratio": "9:16",
    "duration": 8,
    "generate_audio": true
  }
}
→ { taskId }

GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=...
→ data.state: success → JSON.parse(data.resultJson).resultUrls[0] = видео
            fail    → data.failMsg (TOONTOON не списываем)
```

`KIE_API_KEY` — только server-side. TOONTOON списываем только при `state:success`.

---

## 12. Применение в коде

| Файл | Что сделать |
|---|---|
| `src/lib/videoTemplates.ts` | интерфейс `VideoTemplate`; инстанс `nature-video`; `buildStoryboardInstruction(tileId, answers, mode)` с маппингами §5; STRUCTURED/FREE_TEXT из §8–9 |
| `src/lib/tileConfig.ts` | вопросы `nature-video` (§4); `priceToontoon = 2` |
| `src/lib/redis.ts` | поле `Generation.taskId?` |
| `src/lib/seedance.ts` (новый) | клиент kie.ai: `createVideoTask(input)`, `getVideoTask(taskId)`, маппинг §11 |
| `src/app/api/video/route.ts` (новый) | storyboard → якорь Pollinations → `/api/img` → createTask → `Generation{running}` |
| `src/app/api/video/status/route.ts` (новый) | `recordInfo` → обновить `Generation`, на `success` списать 2 TOONTOON |
| `src/app/chat/page.tsx` + `chatReducer.ts` | вопросы §4, граничные баблы §10, прогресс-бабл → `<video>`, ленивый до-полл при возврате |
| env | `KIE_API_KEY` |

---

## 13. Чек-лист

- [ ] Анкор начинается со `STYLE_3D_SCENE_COZY` или `_EPIC` (выбор по месту)
- [ ] Анкор заканчивается `TECHNICAL`, есть `no people, no characters`
- [ ] Режим `kinemagraph`, 1 кадр, `loop: true`, `durationSec ≤ 15`
- [ ] Нет запрещённых слов (Pixar, Disney, realistic, big expressive eyes, matte ceramic, PBR)
- [ ] Motion: только амбиент + медленный дрейф камеры; движение замкнутое (loop)
- [ ] `Audio:` в motion присутствует только при `audio.enabled = true`
- [ ] `generate_audio` = `audio.enabled`
- [ ] Текст/буквы в кадр не генерим
- [ ] LLM отдаёт валидный JSON `{ mode, loop, durationSec, anchor, motion, audio, negative }`
- [ ] Все плейсхолдеры заполнены, лишние удалены
- [ ] Противоречие пресетов → уточняем; живое → подсказка; пустое → переспрос
- [ ] TOONTOON списываются только при `state:success`
