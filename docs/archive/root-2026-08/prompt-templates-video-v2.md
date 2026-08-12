# Prompt Templates: Видео v2.0

Версия: 2.0 | Дата: 2026-06-18 | Статус: Approved-draft

Полный фреймворк видео-генерации Arteki: все 8 видео-тайлов + свободный ввод.
Видео строится на той же системе стилей, что и картинки
(prompt-templates-pictures.md, prompt-templates-cards.md): те же STYLE_3D / SCENE_COZY /
SCENE_EPIC, TECHNICAL, NEGATIVE, паспорт субъекта (prompt-semantic-attributes.md).
Контракт интеграции с kie.ai — в video-flow.md (этот док его не дублирует, только ссылается).

Не заменяет prompt-templates-video.md (v1.0) автоматически — это новый файл на новом
контракте (text-to-video одним промптом).

---

## 1. Главный принцип

```
LLM собирает ОДИН текстовый промпт (сцена + стиль + движение + аудио)
   -> Seedance 2.0 генерит видео из текста (text-to-video)
   -> видео <= 15 секунд
```

Картинку-якорь НЕ генерим — отдельного image-генератора (Pollinations и т.п.) у нас нет.
Сцену, стиль и движение описываем словами прямо в промпте Seedance. «Биты» (смена состояния)
живут как **временная дуга внутри motion**, а не как отдельные кадры.

Исключение — **группа C** (фото юзера): там image-to-video, `first_frame_url` = загруженное фото.

Жёсткий лимит: `MAX_VIDEO_DURATION_SEC = 15`.

---

## 2. Базовые блоки (общие с картинками — дословно)

```
STYLE_3D                 // живое: персонаж / животное / птица / рыба
"vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
soft rounded chunky shapes, smooth glossy surfaces with subtle texture,
bold warm saturated colors, cheerful charming character design, bright modern animation quality"

STYLE_3D_SCENE_COZY      // неживое уютное: лес, поле, озеро, предметы, еда
"cozy stylized 3D cartoon render, modern animated feature film look,
soft rounded chunky toy-like shapes, charming miniature diorama aesthetic,
richly detailed tactile materials with visible texture (wood grain, stone, fabric),
warm naturalistic saturated colors, lush detailed environment,
no people, no characters, inviting heartwarming atmosphere"

STYLE_3D_SCENE_EPIC      // неживое масштабное: горы, море, эпичные пейзажи
"epic stylized 3D cartoon landscape render, modern animated feature film look,
bold saturated colors, dramatic depth and scale, lush detailed environment,
no people, no characters, majestic cinematic atmosphere"

TECHNICAL
"Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows,
glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic),
ray-traced global illumination, high quality 3D render, 8k resolution,
crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh"

LAYOUT_TEXT              // тайлы с текстом-оверлеем
"generous negative space in upper third for text overlay, centered composition, rule of thirds,
high contrast between subject and soft bokeh background, no busy patterns behind text areas,
clean uncluttered layout"

LAYOUT_CENTER            // тайлы без текста
"subject centered in frame, soft bokeh background, clean uncluttered composition"

NEGATIVE
"distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions,
messy cluttered background, small unreadable text,
watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs,
low quality, jpeg artifacts, scary dark atmosphere,
dull muted colors, washed out palette, grey tones,
flat 2D illustration, flat vector art, matte plastic look, flat even lighting"
```

**Выбор анкора (по `hasLiving` + масштаб):**
- живое (существо с лицом) -> `STYLE_3D`
- неживое уютное -> `STYLE_3D_SCENE_COZY` (+ `no people, no characters`)
- неживое масштабное -> `STYLE_3D_SCENE_EPIC` (+ `no people, no characters`)
- смешанная сцена (живое + масштабный фон) -> `STYLE_3D` БЕЗ `no people`; масштаб описываем
  словами (`against majestic mountains`)

**Запрещено в промптах:** Pixar, Disney, realistic, photorealistic, beautiful, high quality,
perfect, matte ceramic, PBR, subsurface scattering, clay-like, warm pastel, плоские стили
(storybook / watercolor / flat vector / 2D illustration). Буквы в кадр НЕ генерим.

**Куда идут блоки:** STYLE + сцена + TECHNICAL = визуальная часть текстового промпта Seedance;
`NEGATIVE` -> `negative_prompt`. Отдельной генерации картинки нет.

---

## 3. Пять слоёв видео

1. **Визуал** — `anchor` = текстовое описание сцены и стиля (STYLE + сцена + TECHNICAL), НЕ
   картинка; `motion` (движение субъекта + камера + атмосфера; биты как временная дуга).
   Сервер склеивает оба в ОДИН промпт Seedance: `prompt = anchor + ". " + motion`.
2. **Семантика** — паспорт субъекта (Шаг 0) для живых тайлов: animacy / gender / number / age /
   category. Снимает русские ловушки (котёнок != male, подруга -> female friend, дети -> group,
   бабушке -> elderly). Для чистых сцен паспорт не нужен.
3. **Текст-слой** — оверлей реальным шрифтом поверх готового видео; в кадр буквы НЕ генерим
   (`no text and no letters in the image` в якоре). См. §3.1.
4. **Аудио-слой** — `generate_audio:true` (звук синтезирует сам Seedance) + `audioPrompt`
   (английское описание звука), который вплетается в конец `motion` как `Audio: …`.
5. **Режим** — `kinemagraph` (амбиентное движение, спокойные сцены, `loop:true`) или `keyframes`
   (разворачивающееся действие, дугу start->peak описываем в `motion`, `loop:false`).
   Литеральных кадров нет — режим управляет стилем движения и `loop`.

### 3.1 Текст-слой (reuse слоя открыток)

Текст НЕ генерится в кадр — накладывается отдельным слоем реальным шрифтом (кириллица).
Пресет и вес подбираются по длине:

| Длина | Пресет | Вес | Зона |
|---|---|---|---|
| 1–3 слова | bubble / handwritten | 700–900 | верхняя треть |
| 4–8 слов | rounded / handwritten | 500–600 | верх или низ |
| > 8 слов | clean / elegant | 300–400 | нижняя треть |

Пресеты: bubble (Lobster/Russo One, обводка), rounded (Comfortaa/Nunito), handwritten
(Caveat/Pacifico), elegant (Cormorant/Yeseva One, разрядка), marker (Pangolin), clean
(Nunito/Manrope). Цвет из палитры тайла, контраст >= WCAG AA.
Тайминг: `keyframes` — текст на финальном спокойном бите и держится до конца;
`kinemagraph` — текст держится весь луп статично.

---

## 4. Структура данных

### 4.1 Интерфейс шаблона

```ts
interface VideoTemplate {
  mode: "kinemagraph" | "keyframes";
  durationSec: number;                 // <= 15
  loop: boolean;
  styleAnchor: "living" | "scene_auto";// living -> STYLE_3D; scene_auto -> COZY/EPIC по месту
  anchor: string;                      // текст: STYLE + сцена + TECHNICAL ([PLACEHOLDERS])
  motion: string;                      // движение + камера + атмосфера; дуга start->peak внутри
  collectPassport: boolean;            // живые тайлы -> true
  defaultCategory: "person"|"animal"|"nature"|"object"|"food";
  text: { enabled: boolean; zone: "upper_third"|"lower_third"; preset: string };
  audio: { enabled: boolean; default: "music"|"ambient"|"sfx"|"silence" };
}
```

### 4.2 Контракт выхода LLM (v2.0)

```json
{
  "mode": "kinemagraph",
  "loop": true,
  "durationSec": 8,
  "passport": { "category":"animal", "animacy":"living", "gender":"neutral", "number":"single", "age":"child" },
  "anchor": "<STYLE + сцена + TECHNICAL, no text in image>",
  "motion": "<движение + камера + атмосфера; Audio: ...>",
  "audio": { "enabled": true, "prompt": "<английское описание звука>" },
  "textOverlay": null,
  "negative": "<NEGATIVE>"
}
```

- Сервер склеивает Seedance `prompt = anchor + ". " + motion`; картинку не генерим.
- `passport` присутствует только для живых тайлов (`collectPassport:true`); для сцен опускаем.
- `textOverlay` = `{ text, lang }` для тайлов с текстом, иначе `null`.
- `audio.enabled:false` -> `audio.prompt` опускаем, `Audio:`-блок в motion не добавляем.

### 4.3 Общая инструкция STRUCTURED (шаблон)

Код собирает её из блоков тайла; повторять прозу на каждый тайл не нужно.

```
Build a {MODE} text-to-video prompt for tile "{tileId}". Translate the answers to English, fill the template.
{PASSPORT_STEP — только для живых тайлов, см. §4.5}
Keep all fixed blocks (vibrant 3D…, cozy/epic…, Technical:…) exactly as written.
{IF hasText: no text and no letters rendered in the image — the greeting is an overlay layer.}
Output ONLY valid JSON: { mode, loop, durationSec, {passport,} anchor, motion, audio, {textOverlay,} negative }

ANSWERS: {answers}
ANCHOR TEMPLATE: {tile.anchorTemplate}
MOTION TEMPLATE: {tile.motionTemplate}
MAPPINGS: {tile.mappings}
AUDIO MAPPING: {tile.audioMapping}
{IF hasText: TEXT: put the user's text verbatim into textOverlay.text; do NOT render it in the image.}
mode: {mode} | loop: {loop} | durationSec: {durationSec}
negative: {NEGATIVE}
```

### 4.4 Общая инструкция FREE_TEXT (шаблон)

```
The user described this video in free text. Extract meaning, use defaults if unclear.
Build the same {MODE} prompt with the tile's ANCHOR / MOTION / MAPPINGS. Output ONLY the JSON.
{PASSPORT_STEP — для живых}
Extract: {tile.freeTextFields with defaults}
If the input is contradictory or illogical (e.g. "snow in summer"), DO NOT ask — render it as a
charming whimsical fantasy in our warm 3D-cartoon style. Always produce a coherent beautiful result.
AUDIO: if the user mentioned sound/music -> audio.prompt (enabled:true); else tile default. No extra audio question.
```

### 4.5 PASSPORT_STEP (вставляется в инструкции живых тайлов)

```
STEP 0 — Subject passport. Before filling the template, infer:
category, animacy (living = a creature with a face in frame),
gender (male/female/neutral — default neutral; do NOT guess from grammatical gender of a Russian
noun or from ambiguous names like Sasha/Zhenya),
number (single/group), age (child/adult/elderly; for animals "котёнок/щенок" -> baby animal).
Then translate to English and fill the template using the passport.
```

---

# Группа A — Сцена (text -> video, без персонажа)

Сценные анкоры COZY/EPIC, паспорт не нужен. Текст-слой — у `morning-video` и `Хорошего дня`.

## A.1 `nature-video` 🌿 — Живая природа

Режим: `kinemagraph` · loop:true · 8с · `scene_auto` · текста нет · аудио ambient.

Вопросы: `place` (Лес·Море·Горы·Поле·Своё) · `time_season`
(Летнее утро·Осенний закат·Зимняя ночь·Весенний день·Своё) · `special`
(Ветер·Туман·Снег·Цветение·Своё) · `mood` (Спокойное·Волшебное·Яркое·Своё) ·
`audio` (Звуки природы·Спокойная мелодия·Тишина·Своё).

Маппинги, якорь, motion и готовые примеры — в video-nature-pilot.md (эталонный тайл группы A;
остальные сцены строятся по той же схеме). Кратко:

```
ANCHOR:
{COZY|EPIC}, cinematic {PLACE} landscape, {TIME_SEASON}, with {SPECIAL_IN_AIR},
serene natural color palette, atmospheric depth of field, wide establishing shot,
the full landscape at rest, balanced natural composition, inviting calm, {MOOD},
{TECHNICAL}

MOTION:
ambient motion only — {SPECIAL_MOTION}, very slow camera drift with subtle parallax depth,
no subject morphing, natural seamless looping movement. Audio: {AUDIO_PROMPT}
```

## A.2 `morning-video` ☀️ — Доброе утро

Тёплое утреннее пожелание: рассветная сцена + текст-оверлей.
Режим: `kinemagraph` · loop:false (есть текст) · 8с · `scene_auto` (COZY) · **текст: да** ·
аудио music/ambient.

Вопросы:

| key | Вопрос | Кнопки |
|---|---|---|
| `scene` | Что в кадре? | ☀️ Рассвет · 🌷 Цветы · ☕ Чашка кофе · 🪟 Окно с видом · ✍️ Своё |
| `special` | Что в воздухе? | 🌅 Лучи солнца · 🌫️ Лёгкий туман · 🌸 Распускаются цветы · ✨ Пылинки света · ✍️ Своё |
| `text` | Что написать? | «Доброе утро!» · «Хорошего дня!» · «Доброго дня и настроения» · ✍️ Своё |
| `audio` | Какой звук? | 🎶 Спокойная мелодия · 🐦 Звуки утра (птицы) · 🔇 Тишина · ✍️ Своё |

Дефолты: scene `warm sunrise over a cozy meadow`, special `soft golden sun rays`,
text `«Доброе утро!»`, audio music.

```
ANCHOR (style = STYLE_3D_SCENE_COZY):
cozy stylized 3D cartoon render, modern animated feature film look, soft rounded chunky toy-like
shapes, charming miniature diorama aesthetic, richly detailed tactile materials with visible
texture, warm naturalistic saturated colors, lush detailed environment, no people, no characters,
inviting heartwarming atmosphere,
gentle warm morning scene with {SCENE}, soft golden sunrise light, with {SPECIAL_IN_AIR},
fresh uplifting mood,
generous negative space in upper third for text overlay, centered composition, rule of thirds,
high contrast between subject and soft bokeh background, no busy patterns behind text areas,
clean uncluttered layout, no text and no letters rendered in the image,
{TECHNICAL}

MOTION:
gentle ambient morning motion — {SPECIAL_MOTION}, soft warm light slowly warming up,
very slow cinematic push-in, no subject morphing, smooth and calm. Audio: {AUDIO_PROMPT}

TEXT OVERLAY: text = {text}; preset rounded; zone upper_third; держится весь клип.
```

`{SPECIAL_MOTION}`: лучи -> `warm sun rays gently shifting`; туман -> `soft mist drifting`;
цветы -> `flowers slowly blooming`; пылинки -> `dust motes of light floating softly`.
Audio: «Спокойная мелодия» -> `calm gentle ambient instrumental music, soft and soothing, no vocals`;
«Звуки утра» -> `soft morning ambience, gentle birdsong`.

## A.3 `inspiring-video` ✨ — Хорошего дня

> ⚠️ Тайл переименован (`title: "Хорошего дня"`). Смысл сменился с «эпик-мотивация» на тёплое
> дневное пожелание. Под код это требует обновить `description` («Мотивирующий видео-ролик» ->
> «Тёплое дневное пожелание») и `visualPrompt` (эпичный горный пик -> светлый солнечный день).
> id остаётся `inspiring-video`.

Режим: `kinemagraph` · loop:false (есть текст) · 8с · `scene_auto` · **текст: да** ·
аудио music/ambient.

Вопросы:

| key | Вопрос | Кнопки |
|---|---|---|
| `scene` | Что в кадре? | 🌞 Солнечный пейзаж · 🌾 Цветущее поле · 🌊 Море · 🏙️ Город днём · ✍️ Своё |
| `special` | Что особенного? | 🍃 Лёгкий ветер · ✨ Солнечные блики · 🌸 Парящие лепестки · ☁️ Облака плывут · ✍️ Своё |
| `text` | Что написать? | «Хорошего дня!» · «Прекрасного дня!» · «Удачного дня» · ✍️ Своё |
| `audio` | Какой звук? | 🎶 Лёгкая позитивная музыка · 🌿 Звуки природы · 🔇 Тишина · ✍️ Своё |

Дефолты: scene `bright sunny daytime landscape`, special `a soft breeze`, text `«Хорошего дня!»`,
audio music. Анкор: яркое/доброе -> COZY; масштабное (горы/море) -> EPIC.

```
ANCHOR ({COZY|EPIC}):
{COZY|EPIC}, bright cheerful daytime scene, {SCENE}, warm sunny daylight, with {SPECIAL_IN_AIR},
uplifting positive mood, vivid warm colors,
generous negative space in upper third for text overlay, centered composition, rule of thirds,
high contrast between subject and soft bokeh background, no busy patterns behind text areas,
clean uncluttered layout, no text and no letters rendered in the image,
{TECHNICAL}

MOTION:
gentle uplifting ambient motion — {SPECIAL_MOTION}, warm daylight shimmering,
very slow camera drift with parallax, no subject morphing, smooth and bright. Audio: {AUDIO_PROMPT}

TEXT OVERLAY: text = {text}; preset bubble (короткое пожелание); zone upper_third.
```

---

# Группа B — Живое (text -> video, персонаж/животное)

Анкор `STYLE_3D`, **паспорт субъекта обязателен** (Шаг 0). Текст — у `video-greeting`.

## B.1 `cute-animal-video` 🐾 — Милое животное

Режим: `kinemagraph` · loop:true · 6–8с · `living` · текста нет · **аудио: голос животного** —
это и есть кейс «котик мяукает».

Вопросы:

| key | Вопрос | Кнопки |
|---|---|---|
| `animal` | Какое животное? | 🐱 Котёнок · 🐶 Щенок · 🐰 Кролик · 🦊 Лисёнок · ✍️ Своё |
| `action` | Что оно делает? | Сидит и моргает · Играет · Умывается · Виляет хвостом · ✍️ Своё |
| `setting` | Где оно? | 🛋️ Дома на пледе · 🌳 В саду · 🪟 На подоконнике · 🌸 В цветах · ✍️ Своё |
| `audio` | Какой звук? | 🐾 Голос животного · 🎶 Милая мелодия · 🌿 Звуки природы · 🔇 Тишина · ✍️ Своё |

Паспорт: animal, gender neutral, age — «котёнок/щенок» -> baby animal. Дефолты: action
`sitting with a gentle curious expression`, setting `cozy warm home with a soft blanket`,
audio голос животного.

```
ANCHOR (style = STYLE_3D):
vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
soft rounded chunky shapes, smooth glossy surfaces with subtle texture, bold warm saturated colors,
adorable {ANIMAL} {ACTION} in {SETTING},
soft fur with rich detailed texture, genuinely heartwarming and sweet expression,
tender loving atmosphere, soft warm light,
subject centered in frame, soft bokeh background, clean uncluttered composition,
{TECHNICAL}

MOTION:
gentle lifelike animal motion — {ACTION_MOTION} (soft blinking, ear twitch, slow tail movement,
calm breathing), very slow camera drift, no subject morphing, natural seamless looping.
Audio: {AUDIO_PROMPT}
```

`{ACTION_MOTION}` из `action`: моргает -> `slow blinking and calm breathing`;
играет -> `playful little bounces`; умывается -> `gently grooming`; виляет хвостом ->
`softly wagging tail`. Audio: «Голос животного» ->
`a soft gentle {animal} sound (a kitten meow / a puppy yip), plus light cozy ambience`;
«Милая мелодия» -> `cute soft playful instrumental music, no vocals`.

## B.2 `cartoon-video` 🎭 — Мультяшный персонаж

Режим: `keyframes` · loop:false · 8с · `living` · текста нет · аудио music/sfx.

Вопросы:

| key | Вопрос | Кнопки |
|---|---|---|
| `character` | Кто герой? | 🧙 Волшебник · 🛡️ Рыцарь · 🧚 Фея · 🤖 Робот · ✍️ Своё |
| `action` | Что он делает? | 👋 Машет рукой · 💃 Танцует · ✨ Колдует · 🤸 Прыгает от радости · ✍️ Своё |
| `setting` | Где он? | 🏘️ В деревне · 🌲 В лесу · 🏙️ В городе · 🏰 В замке · ✍️ Своё |
| `audio` | Какой звук? | 🎶 Весёлая музыка · ✨ Волшебные звуки · 🔇 Тишина · ✍️ Своё |

Паспорт: character type + gender (из слова/имени, иначе neutral) + age + number. Дефолты:
character `friendly adventurer`, action `waving hello with a big warm smile`, setting
`cozy magical village at golden hour`, audio весёлая музыка.

```
ANCHOR (style = STYLE_3D):
vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
soft rounded chunky shapes, smooth glossy surfaces with subtle texture, bold warm saturated colors,
expressive {CHARACTER} character with a genuine warm smile, {ACTION_START} in {SETTING},
cheerful charming personality, warm golden hour glow,
subject centered in frame, soft bokeh background, clean uncluttered composition,
{TECHNICAL}

MOTION:
the character performs {ACTION} from {ACTION_START} to {ACTION_PEAK} with expressive lively
movement, smooth continuous motion, gentle cinematic push-in, no hard cuts. Audio: {AUDIO_PROMPT}
```

`{ACTION_START}` / `{ACTION_PEAK}` — начало и пик действия (машет: рука внизу -> рука вверху).
Audio: «Весёлая музыка» -> `upbeat cheerful playful instrumental music, no vocals`;
«Волшебные звуки» -> `magical sparkly chimes and gentle whooshes`.

## B.3 `video-greeting` 🎬 — Видео-поздравление

Режим: `keyframes` · loop:false · 8–10с · `living` · **текст: да** · аудио festive.

Вопросы:

| key | Вопрос | Кнопки |
|---|---|---|
| `occasion` | Повод? | 🎂 День рождения · 🥂 Юбилей · 🎄 Новый год · ✍️ Своё |
| `who` | Для кого? | 👩 Женщина · 👨 Мужчина · 🧒 Ребёнок · ✍️ Своё |
| `scene` | Что в кадре? | 🌷 Цветы · 🕯️ Свечи · 🎈 Шарики · 🌿 Природа · ✍️ Своё |
| `text` | Что написать? | «С Днём Рождения!» · «Желаю счастья и здоровья» · «С праздником!» · ✍️ Своё |
| `audio` | Какой звук? | 🎉 Праздничная музыка · 🎶 Тёплая мелодия · 🔇 Тишина · ✍️ Своё |

Паспорт: who -> gender (Женщина/Мужчина -> female/male; Ребёнок -> age child) + number
(дети/семья -> group). Свободный ввод разрешает русские ловушки. Дефолты: occasion `birthday`,
who `someone dear`, scene `balloons, confetti and flowers`, text `«С праздником!»`,
audio праздничная музыка.

```
ANCHOR (style = STYLE_3D + LAYOUT_TEXT):
vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
soft rounded chunky shapes, smooth glossy surfaces with subtle texture, bold warm saturated colors,
cheerful charming character design,
warm {OCCASION} celebration scene for {WHO}, featuring {SCENE}, cozy festive setting,
warm golden celebration palette with soft bokeh,
generous negative space in upper third for text overlay, centered composition, rule of thirds,
high contrast between subject and soft bokeh background, no busy patterns behind text areas,
clean uncluttered layout, no text and no letters rendered in the image,
{TECHNICAL}

MOTION (дуга: разгар -> спокойный финал под текст):
confetti and flower petals rise then gently drift down, sparkles twinkling, warm light gently
pulsing, slow cinematic push-in, smooth continuous motion, no hard cuts, ends on a calm hold
with clear upper text space for the greeting reveal. Audio: {AUDIO_PROMPT}

TEXT OVERLAY: text = {text}; пресет по поводу (birthday/new-year -> bubble; jubilee -> elegant);
zone upper_third; текст проявляется на финале и держится до конца.
```

Audio: «Праздничная музыка» -> `upbeat warm celebratory music, festive and joyful, no vocals`;
«Тёплая мелодия» -> `gentle warm heartfelt instrumental melody, no vocals`.

---

# Группа C — Фото -> видео (photo -> video)

Другой флоу: фото пользователя -> Seedance **image-to-video**. Текстового якоря нет,
LLM-раскадровки нет, паспорт не нужен (это реальное фото). Стиль не меняем — оживляем как есть.

Фото **обязательно** (кнопки «Пропустить» нет). `first_frame_url` = публичный URL фото юзера
(`/api/upload` -> `/api/img/{id}`). `prompt` = motion-промпт из пресета. `generate_audio`
по ответу. duration ~5–6с, loop:true (деликатное движение).

## C.1 `animate-photo` 📸 — Оживить фото

| key | Вопрос | Кнопки |
|---|---|---|
| `motion` | Какое движение? | 😊 Тёплая улыбка · 👁 Моргание · 🙂 Кивок · 🌬️ Лёгкое оживление · ✍️ Своё |
| `intensity` | Насколько выразительно? | Еле заметно · Мягко · Выразительно |
| `audio` | Звук? | 🎶 Музыка · 🔇 Тишина · ✍️ Своё |

## C.2 `animate-pet` 🐶 — Оживить питомца

| key | Вопрос | Кнопки |
|---|---|---|
| `motion` | Какое движение? | 👁 Моргание · 🙂 Наклон головы · 🐾 Виляет хвостом · 😪 Зевок · ✍️ Своё |
| `intensity` | Насколько выразительно? | Еле заметно · Мягко · Выразительно |
| `audio` | Звук? | 🐾 Голос питомца · 🎶 Музыка · 🔇 Тишина · ✍️ Своё |

**Сборка motion-промпта (без LLM, по пресетам):**

```
motion = "{MOTION_PRESET}, {INTENSITY}, subtle natural movement, gentle parallax,
          keep the original photo look, no distortion of the face. Audio: {AUDIO_PROMPT}"
```

`{MOTION_PRESET}`: улыбка -> `a warm gentle smile`; моргание -> `soft natural blinking`;
кивок -> `a slow gentle nod`; оживление -> `subtle lifelike micro-movements`;
наклон головы -> `a gentle head tilt`; хвост -> `softly wagging tail`; зевок -> `a slow cozy yawn`.
`{INTENSITY}`: Еле заметно -> `very subtle`; Мягко -> `soft and natural`;
Выразительно -> `clearly expressive but smooth`.
Audio: «Голос питомца» -> `a soft gentle pet sound (a meow / a bark)`; «Музыка» ->
`calm gentle background music, no vocals`.

**Seedance input:** `first_frame_url = {user photo URL}`, `prompt = motion`,
`generate_audio = audio.enabled`, без `reference_image_urls` (фото и есть референс),
`resolution 720p`, `aspect_ratio` по фото, `duration 5`.

---

# Свободный ввод (free-text -> video)

Юзер описывает видео словами, без выбора тайла. Один общий флоу:

```
POST /api/video { mode:"freetext", text }
  1. LLM определяет КАТЕГОРИЮ по тексту:
     - сцена/пейзаж (лес, горы, море, закат)        -> группа A, kinemagraph, scene-анкор
     - живое (кот, персонаж, фея, питомец)           -> группа B, паспорт, STYLE_3D
     - поздравление (есть повод + адресат + текст)   -> video-greeting (keyframes + текст-оверлей)
  2. Паспорт субъекта (для живого/поздравления).
  3. Режим: спокойная сцена -> kinemagraph; действие/персонаж/поздравление -> keyframes.
  4. Собирает anchor (нужный STYLE) + motion + audio + textOverlay (если есть текст-пожелание).
  5. Тот же JSON-контракт (§4.2) -> Seedance text-to-video.
```

Правила:
- Стиль по умолчанию — бренд-3D. Если юзер ЯВНО назвал стиль (акварель/реализм) — для видео
  всё равно мягко ведём к бренд-3D (видео у нас только в 3D).
- Аудио: упомянул звук/музыку -> в `audio.prompt`; иначе дефолт по категории
  (сцена -> ambient; живое -> лёгкая музыка; поздравление -> праздничная) — включаем молча.
- Текст: явное пожелание в кавычках или «напиши …» -> `textOverlay.text`, в кадр не рендерим.
- **Нелогичный ввод -> волшебная небылица.** «снег лето холод» и подобные противоречия НЕ
  переспрашиваем — рисуем уютную фэнтези-сцену в нашем 3D-стиле (снежное лето как сказка).

Примеры:

| Ввод | Категория | Режим | Стиль |
|---|---|---|---|
| «лес на рассвете с туманом» | сцена | kinemagraph | COZY |
| «горы на закате» | сцена | kinemagraph | EPIC |
| «котёнок играет на подоконнике» | живое | kinemagraph | STYLE_3D + голос животного |
| «фея колдует в волшебном лесу» | живое | keyframes | STYLE_3D |
| «снег лето холод» | сцена | kinemagraph | волшебная небылица (снежное лето) |
| «поздравь бабушку с днём рождения, напиши “С юбилеем!”» | поздравление | keyframes | STYLE_3D + текст |

---

# Граничные случаи (общие)

**А. Противоречие.** Кнопки-пресеты (снег + тёплый сезон / цветение зимой) -> уточняющий бабл
«так и задумано или поправить?». СВОБОДНЫЙ текст («снег лето холод») -> НЕ переспрашиваем:
LLM превращает в уютную волшебную небылицу (фэнтези в нашем 3D-стиле), всегда красивый результат.

**Б. Живое в сцене -> мягко подсказываем.** В сценном тайле («Своё» = существо с лицом) ->
бабл «оставить далёким силуэтом / сменить шаблон». Силуэт = далёкий амбиентный, без морды
крупным планом, `no people, no characters` сохраняем.

**В. Пустое / бессмыслица -> переспрос** именно того вопроса.

**Г. Фото обязательно (группа C).** Нет фото -> не идём в генерацию, просим загрузить.

**Д. Текст пустой (тайлы с текстом).** Разрешаем видео без текста (оверлей не рисуем) либо
подставляем дефолтное пожелание.

---

# Флоу отправки -> Seedance input (kie.ai)

| Поле storyboard | Seedance `input` |
|---|---|
| `anchor` + `motion` (склейка) | `prompt` (text-to-video, без картинки) |
| `audio.enabled` | `generate_audio` |
| `negative` | `negative_prompt` |
| `durationSec` | `duration` |
| — (сервер) | `resolution:"720p"`, `aspect_ratio:"9:16"` |
| `loop` | при воспроизведении, в Seedance не уходит |
| `textOverlay` | пост-оверлей реальным шрифтом, в Seedance не уходит |
| фото юзера (группа C) | `first_frame_url` = фото (image-to-video); `prompt` = motion-пресет |

Точный контракт createTask/recordInfo, цена, поллинг, TEKI — в video-flow.md.

---

# Применение в коде

| Файл | Что сделать |
|---|---|
| `src/lib/promptTemplates.ts` | держать STYLE_BLOCKS/TECHNICAL/NEGATIVE по §2 (их строки идут в видео-промпт) |
| `src/lib/videoTemplates.ts` | интерфейс §4.1; инстансы всех 8 тайлов; `buildStoryboardInstruction(tileId, answers, mode)` по §4.3–4.5 |
| `src/lib/tileConfig.ts` | вопросы по группам A/B/C; `inspiring-video`: обновить description/visualPrompt |
| `src/lib/redis.ts` | `Generation.taskId?` |
| `src/lib/seedance.ts` (новый) | клиент kie.ai: createTask (text-to-video / image-to-video для C) + маппинг |
| `src/app/api/video/route.ts` (новый) | собрать `prompt = anchor + ". " + motion` -> Seedance text-to-video; группа C -> image-to-video от фото; режимы STRUCTURED/FREE_TEXT/freetext |
| `src/app/api/video/status/route.ts` (новый) | recordInfo -> Generation, списание TEKI на success |
| `src/app/chat/page.tsx` + `chatReducer.ts` | вопросы, граничные баблы, видео-бабл, фото-аплоад для C |
| env | `KIE_API_KEY` |

---

# Чек-лист (на каждый тайл)

- [ ] Якорь начинается с правильного STYLE (living -> STYLE_3D; scene -> COZY/EPIC)
- [ ] Якорь заканчивается TECHNICAL
- [ ] Для сцены есть `no people, no characters`
- [ ] Для живого заполнен паспорт (gender по умолчанию neutral; «котёнок» != male)
- [ ] Режим верный: спокойная сцена -> kinemagraph; действие/персонаж/поздравление -> keyframes
- [ ] `durationSec <= 15`
- [ ] Нет запрещённых слов (§2)
- [ ] Motion: субъект + камера + атмосфера; для loop:true движение замкнутое
- [ ] `Audio:` в motion только при `audio.enabled`; `generate_audio` = `audio.enabled`
- [ ] Текст (если есть) — оверлей; в кадр буквы НЕ генерим; пресет/вес по длине
- [ ] Группа C: фото обязательно; `first_frame_url` = фото юзера; `prompt` = motion-пресет
- [ ] Нелогичный свободный ввод -> волшебная небылица (не переспрашиваем)
- [ ] LLM отдаёт валидный JSON по §4.2 (Seedance `prompt` = anchor + motion)
- [ ] TEKI списываются только при `state:success`
