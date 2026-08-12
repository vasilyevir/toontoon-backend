# Video — фиксы v3: Video Greeting + EN (US-рынок)

Версия: 3 | Дата: 2026-06-19 | Статус: Approved-draft

Патч к [prompt-templates-video-v2.md](./prompt-templates-video-v2.md). Не переписывает основную
доку — это список исправлений после первого прогона. Применять поверх v2.0.

Причина: продукт идёт на **американский рынок**, открытки (`card`) уже целиком на английском
(13 тем US-рынка). Видео-тайлы и мой первый драфт были на русском — это правится здесь.
Проза-объяснения в доках остаётся русской; **всё продуктовое (тайтлы, вопросы, кнопки,
дефолтные тексты) — на английском.**

---

## 1. Язык: продуктовый текст -> английский

- Тайтлы тайлов, вопросы в чате, лейблы кнопок, дефолтные тексты-пожелания — **English**.
- Английские промпт-строки (STYLE/сцена/motion/audio) уже были на английском — без изменений.
- Русскими остаются только пояснения в .md (для команды).

---

## 2. Тайтлы видео-тайлов (EN)

| id | было | станет |
|---|---|---|
| `nature-video` | Живая природа | **Living Nature** |
| `morning-video` | Доброе утро | **Good Morning** |
| `inspiring-video` | Хорошего дня | **Have a Great Day** |
| `cute-animal-video` | Милое животное | **Cute Animal** |
| `cartoon-video` | Мультяшный персонаж | **Cartoon Character** |
| `video-greeting` | Видео-поздравление | **Video Greeting** |
| `animate-photo` | Оживить фото | **Animate Photo** |
| `animate-pet` | Оживить питомца | **Animate Pet** |

---

## 3. `video-greeting` — повод из 13 тем открыток

**Один тайл**, первый вопрос — повод; переиспользуем темы карточек (`card`). Показываем
топ-поводы + «More…» (полный список) + «Other».

**Вопрос 1 — occasion:**
```
"What's the occasion?"
🎂 Birthday · 🎄 New Year / Christmas · 💕 Valentine's Day · 💐 Mother's Day · More… · ✍️ Other
```
За «More…» — полный список 13: Birthday, Milestone Birthday, Valentine's Day, Wedding,
Anniversary, Mother's Day, Father's Day, Easter, Thanksgiving, New Year / Christmas, Graduation,
Get Well Soon, Just Because.

**Дефолтный текст-пожелание по поводу** (подставляется в вопрос про текст):

| occasion | default text |
|---|---|
| Birthday | Happy Birthday! |
| Milestone Birthday | Happy Birthday! |
| Valentine's Day | Happy Valentine's Day! |
| Wedding | Congratulations! |
| Anniversary | Happy Anniversary! |
| Mother's Day | Happy Mother's Day! |
| Father's Day | Happy Father's Day! |
| Easter | Happy Easter! |
| Thanksgiving | Happy Thanksgiving! |
| New Year / Christmas | Happy Holidays! |
| Graduation | Congratulations, Grad! |
| Get Well Soon | Get Well Soon! |
| Just Because | Thinking of You |

**Остальные вопросы** (отдельного «What's in the frame?» НЕТ — сцену задаёт повод, см. §3.1):
```
"Who's it for?"          ❤️ Mom · 👨 Dad · 🧑‍🤝‍🧑 Friend · 💑 Partner · More · ✍️ Other (name)
"What should it say?"     [occasion default, e.g. "Happy Thanksgiving!"] · [+ who → "…, Mom!"] · ✍️ Other
"Add sound?"             🎉 Festive music · 🎶 Warm melody · 🔇 Silence · ✍️ Other
```

### 3.1 Как повод формируется в видео-промпт

Повод задаёт **и текст, и сцену** (сцену берём из промптов карточек, см. таблицу). Поэтому
video-greeting — это **праздничная СЦЕНА** (объекты повода: торт, розы, тыквы, ёлка), а не
персонаж: якорь `STYLE_3D_SCENE_COZY` (`no people, no characters`). `{WHO}` персонализирует
**текст** ("Happy Birthday, Mom!"), а не рисует человека в кадре. Если юзер явно просит героя —
переключаемся на `STYLE_3D` + паспорт.

> ⚠️ Это правка к §B.3 основной доки: там якорь был `STYLE_3D` с персонажем. Для тем открыток
> вернее сценный объектный якорь `STYLE_3D_SCENE_COZY`.

**Повод -> `{OCCASION_SCENE}` (объекты сцены, извлечено из `prompt` карточек) + дефолтный текст:**

| occasion | `{OCCASION_SCENE}` (в якорь) | default text |
|---|---|---|
| Birthday | balloons, flowers and confetti, a festive cake, warm golden glow | Happy Birthday! |
| Milestone Birthday | golden balloons, champagne and elegant roses, luxurious golden celebration | Happy Birthday! |
| Valentine's Day | red roses and floating hearts, soft pink-and-red romantic glow | Happy Valentine's Day! |
| Wedding | white roses and soft petals, gold accents, dreamy soft light | Congratulations! |
| Anniversary | red roses and hearts, warm candlelight, soft golden tones | Happy Anniversary! |
| Mother's Day | soft pink peonies and white daisies, gentle morning light | Happy Mother's Day! |
| Father's Day | warm outdoor nature, golden sunlight, cozy proud mood | Happy Father's Day! |
| Easter | colorful painted eggs in a nest, spring flowers, a baby chick | Happy Easter! |
| Thanksgiving | autumn harvest with pumpkins, acorns and orange maple leaves, cozy amber fall | Happy Thanksgiving! |
| New Year / Christmas | a decorated Christmas tree with lights, falling snow, golden-and-blue magic | Happy Holidays! |
| Graduation | a graduation cap and diploma scroll, gold stars and confetti, purple-and-gold | Congratulations, Grad! |
| Get Well Soon | bright sunflowers and butterflies, warm sunshine, uplifting healing warmth | Get Well Soon! |
| Just Because | colorful flowers and confetti, a cheerful surprise, bright spontaneous joy | Thinking of You |

**Сборка текста-тайтла (occasion-aware + родство):**
- база = default text по поводу (таблица выше) — НЕ generic «Wishing you all the best»;
- если выбран `who` (родство/имя) -> `"{база}, {who}!"` — напр. "Happy Thanksgiving" + Mom = "Happy Thanksgiving, Mom!";
- ✍️ Other — свой текст (перекрывает всё). Это `textOverlay.text`; в кадр НЕ генерим (текст-слой §5).
- `who` = **родство** (Mom/Dad/Friend/Partner/…), НЕ Woman/Man/Kid — для сцены-открытки пол/возраст не нужен.

**Формирование якоря:**
```
{STYLE_3D_SCENE_COZY},
warm {OCCASION_TITLE} scene, {OCCASION_SCENE}, cozy festive setting,
warm celebration palette with soft bokeh,
{LAYOUT_TEXT}, no text and no letters rendered in the image,
{TECHNICAL}
```

**Motion:**
```
festive ambient motion — confetti and petals gently rising then drifting down, sparkles twinkling,
warm light softly pulsing, slow cinematic push-in, ends on a calm hold for the text.
Audio: {AUDIO_PROMPT}
```

**Пример — Thanksgiving, для мамы:**
```
textOverlay: "Happy Thanksgiving, Mom!"   (default "Happy Thanksgiving!" + who = Mom)

anchor:
cozy stylized 3D cartoon render, modern animated feature film look, soft rounded chunky toy-like
shapes, charming miniature diorama aesthetic, richly detailed tactile materials with visible
texture, warm naturalistic saturated colors, lush detailed environment, no people, no characters,
inviting heartwarming atmosphere,
warm Thanksgiving scene, autumn harvest with pumpkins, acorns and orange maple leaves, cozy amber
fall atmosphere, cozy festive setting, warm celebration palette with soft bokeh,
generous negative space in upper third for text overlay, centered composition, rule of thirds,
high contrast between subject and soft bokeh background, no busy patterns behind text areas,
clean uncluttered layout, no text and no letters rendered in the image,
Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows,
glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic),
ray-traced global illumination, high quality 3D render, 8k resolution, crisp sharp details,
clean anti-aliased edges, soft depth of field with gentle background bokeh

motion:
festive ambient motion — maple leaves gently drifting down, warm amber light softly shifting,
slow cinematic push-in, ends on a calm hold for the text. Audio: gentle warm heartfelt
instrumental melody, no vocals

audio: enabled = true
```

---

## 4. Вопросы и кнопки остальных тайлов (EN)

### Living Nature (`nature-video`)
```
"Which place?"            🌲 Forest · 🌊 Sea · ⛰️ Mountains · 🌾 Field · ✍️ Other
"Time of year & day?"     ☀️ Summer morning · 🍂 Autumn sunset · ❄️ Winter night · 🌸 Spring day · ✍️ Other
"Anything special?"       🍃 Wind · 🌫️ Fog · ❄️ Snow · 🌸 Blossom · ✍️ Other
"Mood?"                   😌 Calm · ✨ Magical · 🌟 Vivid · ✍️ Other
"Add sound?"              🌿 Nature sounds · 🎶 Calm melody · 🔇 Silence · ✍️ Other
```

### Good Morning (`morning-video`)  — есть текст-слой
```
"What's in the frame?"    ☀️ Sunrise · 🌷 Flowers · ☕ Cup of coffee · 🪟 Window view · ✍️ Other
"What's in the air?"      🌅 Sun rays · 🌫️ Light fog · 🌸 Blooming flowers · ✨ Floating light · ✍️ Other
"What should it say?"     "Good morning!" · "Have a great day!" · "Good morning, sunshine!" · ✍️ Other
"Add sound?"              🎶 Calm melody · 🐦 Morning birds · 🔇 Silence · ✍️ Other
```

### Have a Great Day (`inspiring-video`)  — есть текст-слой
```
"What's in the frame?"    🌞 Sunny landscape · 🌾 Blooming field · 🌊 Sea · 🏙️ City by day · ✍️ Other
"Anything special?"       🍃 Light breeze · ✨ Sun glints · 🌸 Floating petals · ☁️ Drifting clouds · ✍️ Other
"What should it say?"     "Have a great day!" · "Have a wonderful day!" · "Make it a good one" · ✍️ Other
"Add sound?"              🎶 Light upbeat music · 🌿 Nature sounds · 🔇 Silence · ✍️ Other
```

### Cute Animal (`cute-animal-video`)  — голос животного
```
"Which animal?"           🐱 Kitten · 🐶 Puppy · 🐰 Bunny · 🦊 Fox cub · ✍️ Other
"What's it doing?"        Sitting & blinking · Playing · Grooming · Wagging tail · ✍️ Other
"Where is it?"            🛋️ On a cozy blanket · 🌳 In the garden · 🪟 On the windowsill · 🌸 Among flowers · ✍️ Other
"Add sound?"              🐾 Animal sound · 🎶 Cute melody · 🌿 Nature sounds · 🔇 Silence · ✍️ Other
```

### Cartoon Character (`cartoon-video`)
```
"Who's the hero?"         🧙 Wizard · 🛡️ Knight · 🧚 Fairy · 🤖 Robot · ✍️ Other
"What's it doing?"        👋 Waving · 💃 Dancing · ✨ Casting a spell · 🤸 Jumping for joy · ✍️ Other
"Where is it?"            🏘️ In a village · 🌲 In a forest · 🏙️ In the city · 🏰 In a castle · ✍️ Other
"Add sound?"              🎶 Fun music · ✨ Magical sounds · 🔇 Silence · ✍️ Other
```

### Animate Photo (`animate-photo`)  — фото обязательно
```
"What movement?"          😊 Warm smile · 👁 Blink · 🙂 Nod · 🌬️ Gentle life · ✍️ Other
"How expressive?"         Barely · Soft · Expressive
"Sound?"                  🎶 Music · 🔇 Silence · ✍️ Other
```

### Animate Pet (`animate-pet`)  — фото обязательно
```
"What movement?"          👁 Blink · 🙂 Head tilt · 🐾 Wagging tail · 😪 Yawn · ✍️ Other
"How expressive?"         Barely · Soft · Expressive
"Sound?"                  🐾 Pet sound · 🎶 Music · 🔇 Silence · ✍️ Other
```

---

## 5. Текст-слой — откладываем (причина «видео есть, текста нет»)

**Почему не отработали Good Morning / Have a Great Day / Video Greeting:**
в якорь этих тайлов зашито `no text and no letters rendered in the image` — мы СПЕЦИАЛЬНО
запрещаем Seedance рисовать буквы (видео-модели коверкают текст). Текст должен накладываться
**отдельным слоем-оверлеем поверх готового видео**, а этот шаг **не реализован**. Поэтому
видео есть, а текста нет — генерация отработала верно, оверлея просто нет.

**Решение — отдельный этап (позже):**
- для отображения в приложении — текст CSS-слоем поверх `<video>` (реальный шрифт, пресеты §3.1
  основной доки, позиция из спека);
- для скачивания/шеринга — «вшить» текст в файл через ffmpeg `drawtext`.

Пока этот этап не сделан: тайлы с текстом дают видео **без вшитого текста** (это ожидаемо).
НЕ просим Seedance рисовать текст — будет каша из букв.

---

## 6. Правки в коде (флаги, отдельной задачей)

- [tileConfig.ts](../src/lib/tileConfig.ts): перевести тайтлы видео/картинок/объявлений на
  английский (открытки уже EN).
- `inspiring-video`: тайтл `Хорошего дня` -> **`Have a Great Day`** (я ранее по ошибке поставил
  русский), плюс `description` («Мотивирующий видео-ролик» -> тёплое дневное пожелание) и
  `visualPrompt` (эпичный горный пик -> светлый солнечный день).
- `video-greeting`: вопрос `occasion` с 13 темами (§3); `who` = **родство** (Mom/Dad/Friend/…), НЕ
  Woman/Man/Kid; текст-тайтл **occasion-aware** (default по поводу) + подстановка родства "…, Mom!"
  (§3.1); убрать вопрос «What's in the frame?».
- Вопросы тайлов (`TILE_QUESTIONS`) привести к ключам и английским лейблам из §3–4.
