# Arteki — Слой генерации (итоговый продукт)

## §0. Обзор
Arteki генерит контент через чат. **3 категории**: Картинки, Открытки, Видео (Оживление фото —
часть категории Видео). По способу генерации — **4 типа**:

| Тип | Что | Тайлов | Движок |
|---|---|---|---|
| 🖼 Картинки | text → image | 6 | image-движок |
| 💌 Открытки | text → image (сцена + текст) | 13 | image-движок |
| 🎬 Видео | картинка-кадр → видео (image → video) | 6 | image-движок (кадр) + kie.ai Seedance |
| 📸 Оживление фото | фото юзера → видео (image → video) | 2 | kie.ai Seedance |

**Видео — это image→video в ДВА шага:** 1) по ответам собираем промпт → генерим **кадр-картинку**;
2) когда кадр готов → Seedance оживляет его движением + звуком. Текст — отдельным слоем.
Для оживления фото кадр — это фото пользователя (шаг 1 пропускаем).

Принцип: пользователь выбирает тайл **или** пишет свободно → чат задаёт вопросы по одному →
собирается промпт → генерация → результат в чате (сохранение, шеринг).

---

## §1. Общий слой

### 1.0 Область применения
| Элемент | Картинки | Открытки | Видео | Оживление фото |
|---|---|---|---|---|
| 1.1 Флоу | ✓ | ✓ | ✓ | ✓ (промпт = движение) |
| 1.2 Стили | ✓ | ✓ (COZY) | ✓ (для кадра) | ✗ фото не перерисовываем |
| 1.3 Technical | ✓ | ✓ | ✓ (для кадра) | ✗ |
| 1.3 Negative | ✓ | ✓ | ✓ | частично (`keep face, no distortion`) |
| 1.3 Layout | ✓ center | ✓ text | ✓ (для кадра) | ✗ |
| 1.4 Паспорт | живые | ✗ | живые | ✗ |
| 1.5 Текст-слой | ✗ | ✓ | morning / have-a-great-day / greeting | ✗ |
| 1.6 Свободный ввод + slot-filling | ✓ | ✓ | ✓ | ✓ |
| 1.7 Язык (English) | ✓ | ✓ | ✓ | ✓ |

### 1.1 Флоу
**Гейт перед стартом:** авторизация + баланс TEKI. Нет логина / нет TEKI → CTA на логин / пополнение,
генерацию не начинаем.
```
вход (тайл | свободный текст)
  → вопросы тайла ПО ОДНОМУ (AI-бабл + кнопки + «✍️ Other»)
  → режим: все кнопками → STRUCTURED;  есть свободный ввод → FREE_TEXT (slot-filling, §1.6)
  → LLM собирает промпт:
       картинка / открытка → "prompt | negative"
       видео               → JSON { anchor (промпт КАДРА), motion, audio, textOverlay, negative,
                                     loop, durationSec }
       оживление фото      → motion-промпт (кадр = фото юзера)
  → генерация:
       картинка / открытка → image-движок → изображение (сразу)
       видео → ШАГ 1: генерим КАДР по anchor (image-движок) → URL картинки
               ШАГ 2: когда кадр готов → Seedance image→video
                      (first_frame_url = кадр, prompt = motion, generate_audio)
       оживление фото → Seedance image→video (first_frame_url = фото юзера, prompt = motion)
  → текст-слой (где есть) — оверлеем поверх результата
  → результат → сохранение → шеринг
```
Картинка синхронно; видео асинхронно ~4–5 мин (джоба переживает уход из чата, прогресс в чате).

**Повторы и навигация:** «Create another» (новая генерация) · «Try again» (та же) ·
«Edit» (изменить ответ → пересборка промпта) · «Back» (шаг назад по вопросам).

### 1.2 Стили (3 анкора + правило выбора)
> **Применяется к:** Картинки, Открытки, и КАДР видео. **НЕ** к оживлению фото (фото оставляем как есть).
```
STYLE_3D  (живое: персонаж/животное/птица/рыба)
vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
soft rounded chunky shapes, smooth glossy surfaces with subtle texture,
bold warm saturated colors, cheerful charming character design, bright modern animation quality

STYLE_3D_SCENE_COZY  (неживое уютное: лес/поле/озеро/еда/предметы/праздничная сцена)
cozy stylized 3D cartoon render, modern animated feature film look,
soft rounded chunky toy-like shapes, charming miniature diorama aesthetic,
richly detailed tactile materials with visible texture (wood grain, stone, fabric),
warm naturalistic saturated colors, lush detailed environment,
no people, no characters, inviting heartwarming atmosphere

STYLE_3D_SCENE_EPIC  (неживое масштабное: горы/море/эпичные пейзажи)
epic stylized 3D cartoon landscape render, modern animated feature film look,
bold saturated colors, dramatic depth and scale, lush detailed environment,
no people, no characters, majestic cinematic atmosphere
```
**Выбор:** живое → STYLE_3D · неживое уютное → COZY · неживое масштабное → EPIC ·
смешанное (живое+масштаб) → STYLE_3D без `no people`. Критерий «живое» — существо **с лицом**.

### 1.3 Technical · Negative · Layout
> **Применяется к:** Картинки, Открытки, КАДР видео. К оживлению фото — только Negative частично.
```
TECHNICAL (в конце промпта):
Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows,
glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic),
ray-traced global illumination, high quality 3D render, 8k resolution,
crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh

NEGATIVE:
distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions,
messy cluttered background, small unreadable text, watermarks, signatures,
cropped limbs, blurry faces, extra fingers or limbs, low quality, jpeg artifacts,
scary dark atmosphere, dull muted colors, washed out palette, grey tones,
flat 2D illustration, flat vector art, matte plastic look, flat even lighting
   (для сцен без живого добавляем: people, humans, faces, characters, animals)

LAYOUT_TEXT  (тайлы с текстом):
generous negative space in upper third for text overlay, centered composition, rule of thirds,
high contrast between subject and soft bokeh background, no busy patterns behind text areas,
clean uncluttered layout

LAYOUT_CENTER (без текста):
subject centered in frame, soft bokeh background, clean uncluttered composition
```
**Запрещено:** Pixar, Disney, realistic, photorealistic, beautiful, high quality, perfect,
matte ceramic, PBR, subsurface scattering, clay-like, warm pastel, плоские стили, буквы/текст в кадре.

### 1.4 Семантика субъекта (паспорт)
> **Применяется к:** живые субъекты (персонаж/животное в Картинках и Видео).
> **НЕ** к Открыткам, сценам, оживлению фото.
```
passport = { category, animacy (living|scene), gender (default neutral),
             number (default single), age (default adult) }
```
`animacy` → выбор анкора. `gender` не угадываем (неоднозначные имена → person/friend).
`number=group` для kids/family. Для животных «kitten/puppy» → baby.

### 1.5 Текст-слой
> **Применяется к:** Открытки, и видео morning / have-a-great-day / greeting.

Буквы **не генерим в кадре** — текст накладываем **отдельным слоем** реальным шрифтом.
- **Картинки / Открытки:** оверлей **реализован**.
- **Видео:** текст **запекаем в файл** — на тесте **Cloudinary**.

**Пресеты:**

| preset | вайб | шрифт | вес |
|---|---|---|---|
| bubble | объёмный, радостный | Lobster / Russo One | 700–900 |
| rounded | мягкий, дружелюбный | Comfortaa / Nunito | 500–700 |
| handwritten | личный, «от руки» | Caveat / Pacifico | 400–600 |
| elegant | тонкий, торжественный | Cormorant / Yeseva One | 300–400 |
| marker | небрежный, молодой | Pangolin / Marmelad | 500–700 |
| clean | нейтральный, читаемый | Nunito / Manrope | 400–600 |

**Вес / размер / зона — по длине:** 1–3 слова → bubble / handwritten, верхняя треть ·
4–8 слов → rounded / handwritten, верх или низ · > 8 слов → clean / elegant, нижняя треть.
Цвет — из палитры тайла, контраст ≥ WCAG AA.

### 1.6 Свободный ввод + адаптивный slot-filling
«✍️ Other» доступен на **каждом** вопросе. Тайл = набор **слотов**, нужных промпту.
- **Все кнопки** → фиксированные вопросы (дефолт).
- **Появился свободный ввод** → нейронка: (1) извлекает **все слоты**, что может;
  (2) закрытые **не переспрашивает**; (3) оставшиеся спрашивает **адаптивно**
  (формулировка + кнопки под контекст); (4) слоты заполнены → генерим.
- **Валидация:** извлечённые слоты проверяются против набора слотов тайла; субъект не для тайла →
  re-route/нудж (§6). Свободный ввод **на любом языке** → нормализуем в English (паспорт §1.4),
  выход всегда English.

**Обработка free-text по типу поля:**

| Тип поля | Что делаем |
|---|---|
| Субъект (character/animal/place/occasion/dish…) | паспорт + перевод в EN; проверка «подходит ли тайлу» |
| Модификатор (action/setting/special/mood/time…) | перевод в EN-фразу; неуместное — смягчить |
| Текст-надпись (text) | дословно в textOverlay (sanitize + лимит); не переводим |
| Движение (motion, фото) | зажать к выполнимому, держать узнаваемым |
| Интенсивность | смаппить к Barely/Soft/Expressive |
| Аудио | EN-описание для generate_audio |
| Адресат (who) | родство/имя → в текст, не рисуем человека |

**Fallback'и:** пусто/бессмыслица → переспрос · субъект не для тайла → re-route/нудж ·
невыполнимо → ближайшее + пояснение · противоречие → волшебная небылица ·
NSFW → отказ; чужой IP → «по мотивам» оригинал (§8) · длинно → обрезка.

### 1.7 Язык
US-рынок, весь продуктовый текст и промпты — **English**. Пользователь может **писать на любом
языке** — нормализуем в English (паспорт §1.4); тайтлы, вопросы, кнопки, дефолтные тексты, оверлей — English.

---

## §2. Движки генерации
- **Картинки / Открытки и КАДР видео:** image-движок **выбран** (в этом документе не указываем).
  Вертикаль 768×1024, вход `"prompt | negative"`.
- **Видео:** **image→video** в два шага (§1.1). kie.ai **Seedance 2.0** (`bytedance/seedance-2`,
  URL не base64): `first_frame_url = кадр`, `prompt = motion`, `generate_audio`,
  9:16 / 720p / `duration ≤ 15`. Асинхронно ~4–5 мин (`createTask → poll recordInfo`).

---

## §3. Картинки (6)
Стиль фиксирован (бренд-3D) — вопроса про стиль нет; это картинки — вопроса про анимацию нет.
Маршрутизация свободного текста по субъекту: персонаж → Cartoon Character; животное → Cute Animal;
птица → Birds; рыба → Fish; место → Landscape; блюдо → Food.

### Cartoon Character (`cartoon-char`)
**Что делает:** рисует персонажа (в т.ч. названного, SpongeBob). Картинка.
**Слоты:** `character · action · setting`
1. `character` — "Who's the character?" → 🧙 Wizard · 🛡️ Knight · 🧚 Fairy · 🤖 Robot · ✍️ Other (any name)
2. `action` — "What are they doing?" → 👋 Waving hello · 😊 Smiling · ✨ Casting a spell · 🤸 Jumping · ✍️ Other
3. `setting` — "Where are they?" → 🏘️ Village · 🌲 Forest · 🏙️ City · 🏰 Castle · ✍️ Other
**Промпт:** `{STYLE_3D}, expressive {CHARACTER} with big friendly eyes and a genuine smile, {ACTION} in {SETTING}, warm golden hour glow, {LAYOUT_CENTER}, {TECHNICAL}`

### Cute Animal (`cute-animal`)
**Что делает:** рисует милое животное. Картинка.
**Слоты:** `animal · action · setting`
1. `animal` — "Which animal?" → 🐱 Kitten · 🐶 Puppy · 🐰 Bunny · 🦊 Fox cub · ✍️ Other
2. `action` — "What's it doing?" → 😴 Sleeping · 🧶 Playing · 🪑 Sitting · 🧼 Grooming · ✍️ Other
3. `setting` — "Where is it?" → 🛋️ Home on a blanket · 🌳 Garden · 🪟 Windowsill · 🌲 Forest · ✍️ Other
**Промпт:** `{STYLE_3D}, adorable {ANIMAL} {ACTION} in {SETTING}, soft fur with rich texture, heartwarming sweet expression, {LAYOUT_CENTER}, {TECHNICAL}`

### Birds (`birds`)
**Что делает:** рисует птицу. Картинка.
**Слоты:** `bird · action · setting`
1. `bird` — "Which bird?" → 🐦 Songbird · 🐧 Bullfinch · 🦉 Owl · 🦜 Parrot · ✍️ Other
2. `action` — "What's it doing?" → 🎵 Singing · 🌿 Perched · 🕊️ Flying · 🪶 Preening · ✍️ Other
3. `setting` — "Where is it?" → 🌸 Blossom branch · 🌳 Garden · 🪵 Feeder · 🌲 Forest · ✍️ Other
**Промпт:** `{STYLE_3D}, adorable {BIRD} {ACTION} in {SETTING}, soft fluffy feathers, sweet cheerful expression, {LAYOUT_CENTER}, {TECHNICAL}`

### Fish (`fish`)
**Что делает:** рисует рыбку. Картинка.
**Слоты:** `fish · action · setting`
1. `fish` — "Which fish?" → 🐠 Goldfish · 🤡 Clownfish · 🐟 Betta · 🐡 Pufferfish · ✍️ Other
2. `action` — "What's it doing?" → 🏊 Swimming · 🫧 Bubbles · 🪸 Hiding · 🐠 Playing · ✍️ Other
3. `setting` — "Where is it?" → 🪸 Coral reef · 🐚 Aquarium · 🌊 Seabed · 🌿 Seaweed · ✍️ Other
**Промпт:** `{STYLE_3D}, adorable {FISH} {ACTION} in {SETTING}, glossy iridescent scales, underwater scene with soft sun rays and gentle bubbles, {LAYOUT_CENTER}, {TECHNICAL}`

### Landscape (`landscape`)
**Что делает:** пейзаж — сцена без персонажа. Картинка.
**Слоты:** `place · time_of_day · detail`
1. `place` — "Which place?" → 🌲 Forest · ⛰️ Mountains · 🌊 Sea · 🌾 Field · 🏞️ Lake · ✍️ Other
2. `time_of_day` — "Time of day?" → 🌅 Morning · ☀️ Day · 🌇 Sunset · 🌙 Night
3. `detail` — "Add something?" → 🌷 Flowers · 🌫️ Fog · 🌈 Rainbow · ⭐ Stars · 🛤️ Path · ✍️ Other
**Промпт:** `{COZY|EPIC}, breathtaking {PLACE} at {TIME_OF_DAY}, {DETAIL}, natural light, serene atmosphere, no people no characters, {LAYOUT_CENTER}, {TECHNICAL}`
**Анкор:** Mountains/Sea → EPIC; Forest/Field/Lake → COZY.

### Food (`food`)
**Что делает:** блюдо — сцена без персонажа. Картинка.
**Слоты:** `dish · setting`
1. `dish` — "Which dish?" → 🥞 Pancakes · 🎂 Cake · 🍝 Pasta · 🍲 Soup · ✍️ Other
2. `setting` — "How is it served?" → 🪵 Wooden table · 🪟 By the window · ☕ Cafe · 🎉 Festive table · ✍️ Other
**Промпт:** `{COZY}, delicious appetizing {DISH} on {SETTING}, mouth-watering presentation with soft steam, no people no characters, {LAYOUT_CENTER}, {TECHNICAL}`

---

## §4. Открытки (13)
Поздравительная **картинка**: сцена из объектов повода + надпись. Анкор `COZY` (без персонажа).
`who` = родство → в текст.

**Общие вопросы:**
1. `who` — "Who's it for?" → ❤️ Mom · 👨 Dad · 🧑‍🤝‍🧑 Friend · 💑 Partner · ✍️ Other (name) [→ "Happy Birthday, Mom!"]
2. `scene` — акцент сцены (по поводу — таблица)
3. `text` — "What should it say?" → *[дефолт по поводу]* · ✍️ Other
**Доп.:** Birthday/Milestone → "How old?"; Wedding → "Names?"; Anniversary → "How many years?" (→ в текст)

**Промпт:** `{COZY}, warm {OCCASION} scene, {SCENE}, cozy festive setting, warm celebration palette with soft bokeh, no people no characters, {LAYOUT_TEXT}, no text in image, {TECHNICAL}`

| Tile | `scene` accent | `{SCENE}` | Default text |
|---|---|---|---|
| Birthday (`birthday`) | Balloons · Cake · Confetti · Flowers | balloons, confetti, festive cake, warm golden glow | Happy Birthday! |
| Milestone Birthday (`jubilee`) | Champagne · Roses · Gold décor | golden balloons, champagne, elegant roses, luxurious gold | Happy Birthday! |
| Valentine's Day (`valentine`) | Roses · Hearts · Chocolate · Teddy | red roses and floating hearts, pink-and-red glow | Happy Valentine's Day! |
| Wedding (`wedding`) | Roses · Rings · Petals | white roses and soft petals, gold rings, dreamy light | Congratulations! |
| Anniversary (`anniversary`) | Roses · Hearts · Candles | red roses and hearts, warm candlelight, golden tones | Happy Anniversary! |
| Mother's Day (`mothers-day`) | Peonies · Daisies · Tulips · Bouquet | soft pink peonies and white daisies, gentle morning light | Happy Mother's Day! |
| Father's Day (`fathers-day`) | Nature · Sports · Fishing · Tools | warm outdoor nature, golden sunlight, cozy proud mood | Happy Father's Day! |
| Easter (`easter`) | Eggs · Chick · Bunny · Flowers | painted eggs in a nest, spring flowers, a baby chick | Happy Easter! |
| Thanksgiving (`thanksgiving`) | Pumpkins · Leaves · Harvest · Turkey | pumpkins, acorns and orange maple leaves, cozy amber fall | Happy Thanksgiving! |
| New Year / Christmas (`new-year`) | Tree · Santa · Snow · Animals | Christmas tree with lights, falling snow, gold-and-blue | Happy Holidays! |
| Graduation (`graduation`) | Cap & diploma · Confetti · Flowers · Books | graduation cap and diploma, gold stars and confetti | Congratulations, Grad! |
| Get Well Soon (`get-well`) | Sunflowers · Sunshine · Birds · Rainbow | bright sunflowers and butterflies, warm sunshine | Get Well Soon! |
| Just Because (`just-because`) | Flowers · Nature · Animal · Surprise | colorful flowers and confetti, a cheerful surprise | Thinking of You |

---

## §5. Видео (8)
**image→video в два шага:**
1. По ответам **этого видео-тайла** собираем промпт **кадра** на общих стилевых блоках (§1.2–1.3) —
   **не из тайла-картинки**, а из ответов видео-тайла → image-движок → картинка-кадр.
2. Когда кадр готов → Seedance image→video: `first_frame_url = кадр`, `prompt = motion`.

9:16 / 720p. Звук — `generate_audio` (Silence → `generate_audio: false`). Текст — оверлеем (§1.5).

### §5.0 Свободный ввод («✍️ Other») в видео
На каждом вопросе. При свободном ответе — slot-filling (§1.6): извлечь все слоты → закрытые
пропустить → оставшиеся доспросить адаптивно → генерим.

| Ввод | Поведение |
|---|---|
| Cartoon `character`=«a wizard named Merlin» | закрыт `character`; `action`/`setting` под мага |
| «a baker grandma flipping pancakes in a cozy kitchen» | закрыты все слоты → «Everything is ready!» |
| Cartoon `character`=«SpongeBob» (чужой IP) | распознаём персонажа → делаем «по мотивам» оригинального морского губчатого героя (§8), не копию |
| Nature `place`=«misty pine forest» | нормализуем, анкор COZY, идём к `special`/`mood`/`audio` |
| Animate Pet `motion`=«backflip» | держим узнаваемым → сводим к выполнимому |

### A. Сцена (кадр: COZY/EPIC сцена → анимация)

### Living Nature (`nature-video`)
**Что делает:** атмосферное видео природы, луп ~8с. Без текста.
**Слоты:** `place · time_season · special · mood · audio`
1. `place` — "Which place?" → 🌲 Forest · 🌊 Sea · ⛰️ Mountains · 🌾 Field · ✍️ Other
2. `time_season` — "Time of year & day?" → ☀️ Summer morning · 🍂 Autumn sunset · ❄️ Winter night · 🌸 Spring day · ✍️ Other
3. `special` — "Anything special?" → 🍃 Wind · 🌫️ Fog · ❄️ Snow · 🌸 Blossom · ✍️ Other
4. `mood` — "Mood?" → 😌 Calm · ✨ Magical · 🌟 Vivid · ✍️ Other
5. `audio` — "Add sound?" → 🌿 Nature sounds · 🎶 Calm melody · 🔇 Silence · ✍️ Other
**Кадр (anchor):** COZY/EPIC сцена `{PLACE} landscape, {TIME_SEASON}, {SPECIAL}` (Mountains/Sea → EPIC).
**Motion:** ambient drift + Audio.

### Good Morning (`morning-video`) — с текстом
**Что делает:** утреннее видео-пожелание (сцена + текст), ~8с.
**Слоты:** `scene · special · text · audio`
1. `scene` — "What's in the frame?" → ☀️ Sunrise · 🌷 Flowers · ☕ Coffee · 🪟 Window view · ✍️ Other
2. `special` — "What's in the air?" → 🌅 Sun rays · 🌫️ Light fog · 🌸 Blooming · ✨ Light specks · ✍️ Other
3. `text` — "What should it say?" → "Good morning!" · "Have a great day!" · ✍️ Other
4. `audio` — "Add sound?" → 🎶 Calm melody · 🐦 Morning birds · 🔇 Silence · ✍️ Other
**Кадр (anchor):** COZY сцена рассвета + `{SCENE}` + `{SPECIAL}` + LAYOUT_TEXT. **Текст:** rounded / upper_third.

### Have a Great Day (`inspiring-video`) — с текстом
**Что делает:** дневное пожелание (сцена + текст), ~8с.
**Слоты:** `scene · special · text · audio`
1. `scene` — "What's in the frame?" → 🌞 Sunny landscape · 🌾 Blooming field · 🌊 Sea · 🏙️ City by day · ✍️ Other
2. `special` — "Anything special?" → 🍃 Breeze · ✨ Sun glints · 🌸 Petals · ☁️ Clouds · ✍️ Other
3. `text` — "What should it say?" → "Have a great day!" · "Have a wonderful day!" · ✍️ Other
4. `audio` — "Add sound?" → 🎶 Upbeat music · 🌿 Nature · 🔇 Silence · ✍️ Other
**Кадр (anchor):** COZY/EPIC яркая дневная сцена + `{SCENE}` + `{SPECIAL}` + LAYOUT_TEXT. **Текст:** bubble / upper_third.

### Video Greeting (`video-greeting`) — с текстом
**Что делает:** видео-открытка на повод (объектная сцена + текст), ~8–10с. Анкор COZY.
**Слоты:** `occasion · who · text · audio`
1. `occasion` — "What's the occasion?" → 🎂 Birthday · 🎄 New Year/Christmas · 💕 Valentine's · 💐 Mother's Day · More… · ✍️ Other
2. `who` — "Who's it for?" → ❤️ Mom · 👨 Dad · 🧑‍🤝‍🧑 Friend · 💑 Partner · ✍️ Other (name) [→ в текст]
3. `text` — "What should it say?" → *[дефолт по поводу]* · ✍️ Other
4. `audio` — "Add sound?" → 🎉 Festive music · 🎶 Warm melody · 🔇 Silence · ✍️ Other
**Кадр (anchor):** объектная сцена по поводу (см. §4) + LAYOUT_TEXT. **Motion:** festive ambient + Audio.

### B. Живое (кадр: STYLE_3D персонаж/животное → анимация, паспорт)

### Cute Animal (`cute-animal-video`)
**Что делает:** видео с животным, луп ~6–8с. Без текста. Звук — голос животного.
**Слоты:** `animal · action · setting · audio`
1. `animal` — "Which animal?" → 🐱 Kitten · 🐶 Puppy · 🐰 Bunny · 🦊 Fox cub · ✍️ Other
2. `action` — "What's it doing?" → Sitting & blinking · Playing · Grooming · Wagging tail · ✍️ Other
3. `setting` — "Where is it?" → 🛋️ Blanket · 🌳 Garden · 🪟 Windowsill · 🌸 Flowers · ✍️ Other
4. `audio` — "Add sound?" → 🐾 Animal sound · 🎶 Cute melody · 🌿 Nature · 🔇 Silence · ✍️ Other
**Кадр (anchor):** STYLE_3D `{ANIMAL} {ACTION} in {SETTING}`. **Motion:** gentle lifelike motion + Audio.

### Cartoon Character (`cartoon-video`)
**Что делает:** анимированный персонаж в действии, ~8с. Без текста. Именованные — через Other.
**Слоты:** `character · action · setting · audio`
1. `character` — "Who's the character?" → 🧙 Wizard · 🛡️ Knight · 🧚 Fairy · 🤖 Robot · ✍️ Other (any name)
2. `action` — "What's it doing?" → 👋 Waving · 💃 Dancing · ✨ Casting a spell · 🤸 Jumping for joy · ✍️ Other
3. `setting` — "Where is it?" → 🏘️ Village · 🌲 Forest · 🏙️ City · 🏰 Castle · ✍️ Other
4. `audio` — "Add sound?" → 🎶 Fun music · ✨ Magical sounds · 🔇 Silence · ✍️ Other
**Кадр (anchor):** STYLE_3D `{CHARACTER} in {SETTING}`. **Motion:** персонаж выполняет `{ACTION}` + Audio.

### C. Оживление фото (кадр = фото юзера → анимация)
Загружаем фото → оживляем. Не перерисовываем, стиль не меняем. **Фото обязательно** (§6, §8).
Сборка: `first_frame_url = <фото>` · `prompt = "{motion}, {intensity}, subtle natural movement,
keep the same face and subject, no distortion. Audio: {audio}"` · `generate_audio = (audio ≠ Silence)`
· duration 5 · 720p.
Intensity словами: Barely → `very subtle` · Soft → `soft and natural` · Expressive → `clearly expressive but smooth`.

### Animate Photo (`animate-photo`)
**Что делает:** оживляет фото человека.
**Слоты:** `motion · intensity · audio`
1. `motion` — "What movement?" → 😊 Warm smile · 👁 Blink · 🙂 Nod · 🌬️ Gentle life · ✍️ Other
2. `intensity` — "How expressive?" → Barely · Soft · Expressive
3. `audio` — "Add sound?" → 🎶 Music · 🔇 Silence · ✍️ Other

### Animate Pet (`animate-pet`)
**Что делает:** оживляет фото питомца.
**Слоты:** `motion · intensity · audio`
1. `motion` — "What movement?" → 👁 Blink · 🙂 Head tilt · 🐾 Wagging tail · 😪 Yawn · ✍️ Other
2. `intensity` — "How expressive?" → Barely · Soft · Expressive
3. `audio` — "Add sound?" → 🐾 Pet sound · 🎶 Music · 🔇 Silence · ✍️ Other

---

## §6. Граничные случаи и обработка ошибок

**Ввод:**
- Свободный субъект не для тайла → re-route/нудж на верный тайл.
- Пусто/бессмыслица → переспрос вопроса.
- Противоречие (снег+лето) → волшебная небылица.
- Живое на сценовом тайле → нудж / далёкий силуэт (`no people` сохраняем).
- Оживление фото: нет фото → просим загрузить (генерацию не начинаем).
- Текст пустой (тайлы с текстом) → дефолт по поводу.

**Ошибки генерации:**

| Сбой | Поведение | TEKI |
|---|---|---|
| Картинка/открытка не сгенерилась | сообщение + «Try again» | не списываем |
| Видео ШАГ 1 (кадр) не сгенерился | сообщение + «Try again» (с начала) | не списываем |
| Видео ШАГ 2 (Seedance) `fail` | ретрай Seedance **на готовом кадре** (кадр не перегенерим); после N попыток — сообщение | не списываем |
| Таймаут рендера (> ~6 мин) | помечаем failed, сообщение; при возврате в чат — ленивый до-полл | не списываем |
| Сетевая ошибка поллинга | продолжаем поллить с бэкоффом; джоба живёт в Redis | — |

TEKI списываем **только при финальном success** результата.

**Параллельные генерации:** одна активная видео-джоба на пользователя; картинки — синхронно, по одной.

**NSFW → отказ; чужой IP → «по мотивам» оригинал** (детали — §8).

---

## §7. Экономика
Картинка / Открытка = **1 TEKI** · Видео / Оживление фото = **2 TEKI**.
Видео = **2 шага генерации** (кадр + Seedance) → себестоимость и время выше картинки; курс TEKI↔$ — по тарифу.
Списываем только при успешной генерации (видео — на финальный `success`).

---

## §8. Приватность и модерация

**Фото пользователя (Оживление фото):**
- **Согласие перед загрузкой:** чек-бокс «Это моё фото / у меня есть право его использовать».
- **Только своё** лицо/питомец. Чужие лица и **дети** — запрещены (ToS).
- **Хранение:** фото и результат удаляем после выдачи (или через короткий срок); на обучение НЕ используем.
- **kie.ai:** фото уходит временным URL; подтвердить в условиях, что провайдер не тренируется на данных.
- **US:** обработка лиц — с согласия (CCPA; биометрия — BIPA и т.п.).

**Модерация:**
- NSFW / насилие → вежливый отказ.
- **Чужой защищённый IP** (именованные персонажи — SpongeBob, Pikachu, Disney и т.п.) и **реальные
  люди** → делаем **«по мотивам»**: оригинального похожего героя (напр. морской губчатый персонаж),
  **не точную копию**. Generic / public-domain / описанные пользователем герои — рисуем как есть.
- Включаем `nsfw_checker` Seedance.
