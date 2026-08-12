# ARTEKI — полная мобильная дизайн-спецификация (1:1 с вебом)

Единый документ со всей вёрсткой мобильной версии. Все значения взяты из
веб-фронта (`gen-frontend/src/app/**`) — мобильные (`isMobile` / `compact`)
ветки. Порти в SwiftUI как есть: цвета, размеры, радиусы, отступы, шрифты и
поведение указаны точно.

- **Тема:** тёмная, только портрет. Фон `#0A0A0A`.
- **Шрифт:** Instrument Sans (weights 400/500/600/700). `fontVariationSettings:'wdth' 100` = обычная ширина, в iOS игнорируй.
- **Брейкпоинт «мобилка»:** `< 768px` (`useIsMobile`); лендинг — `< 1120px` или портрет.
- Токены (цвета/отступы/радиусы) — в `DESIGN.md` и `DesignSystem/Theme.swift`. Ниже — дополнительные цвета, встречающиеся в вёрстке.

## Дополнительная палитра (из вёрстки)
| Назначение | Hex |
|---|---|
| Пузырь юзера (градиент) | `#5B3FD9` → `#302173` (слева→направо) |
| Пузырь AI | `#222222` |
| Акцент-кнопки (Generate CTA, активный таб, tour) | `#8101FF` |
| Пилюли-шаблоны (фон/бордер) | `#360078` / бордер `#8D12FF` |
| Активный элемент истории | `#363636` |
| Текст вторичный (лейблы истории/почта) | `#A3A3A3`, `#858585`, `#555` |
| Кредит-баланс чип (в профиле) | `#755BEF` |
| Delete-account кнопка | фон `#2A1215`, бордер `#58181C`, текст `#E84749`; hover `#3D1217`; активная Delete `#E43E46` |
| Тултип онбординга / модалки | `#141414` |
| Тост success галочка glow | `#8BBB11` |
| Индикатор генерации (градиент) | `rgba(20,16,32,.97)`→`rgba(30,20,52,.97)`, бордер `rgba(183,148,246,.28)`, акцент-текст `#C4A7F7` |
| Placeholder-текст композера | `#A3A3A3` |

## Ассеты (положить в Assets.xcassets; имена из веба)
Логотип/иконки: `arteki-logo.svg`, `icon-token.svg`, `icon-new-chat.svg`,
`icon-add-image.svg`, `icon-generate.svg`, `icon-upload.svg`, `magic-icon.svg`,
`icon-retry.svg`, `icon-cancel.svg`, `icon-checkmark-toast.svg`, `icon-download.svg`,
`icon-sparkling.svg`, `Pic.svg` (крутящийся orb), `Close.svg`, `Dropdown Icon.svg`,
`Menu.svg`, `Option Arrow.svg`, `Userpic.png`, `ai-avatar.png`, `chat-avatar.png`,
`icon-profile-dropdown.svg`, `icon-signout-dropdown.svg`, `icon-delete-profile.svg`,
`icon-mail-profile.svg`, `icon-tech-profile.svg`, `Icon.svg`, `image-fill.svg`,
`icon-video.svg`, `icon-postcard.svg`, `bg-pattern.svg`, `bg-element-0/1/2.svg`,
`freepik-avatar.png`, `BG.png`, `icon-upgrade-star.svg`.
Лендинг: `landing/logo.svg`, `landing/icon-get-started.svg`, `landing/icon-sign-in.svg`,
`landing/pill-avatar.png`, `landing/bg-element.svg`, `landing/glow-ellipse.svg`,
`landing/bg-pattern.svg`, `landing/card-far-left|left|center|right|far-right.webp`.
Пилюли главного экрана: `pill-morning|animal|nature|sport|city|abstract|universe|people.png`.
Постер-шрифты (для открыток): Russo One, Comfortaa, Caveat, Nunito, Manrope, Yeseva One (все с кириллицей).

---

# 1. Landing (`landing/page.tsx` → `LandingCompact`)

Вертикальный стек, центрируется как единая группа (симметричные поля).

**Фон:** база `#000208` + радиальный градиент (чёрный→синий `#086DFF`) + два
SVG-глоу слоя (`bg-element.svg` вверху ~30%, `glow-ellipse.svg` ~54%, blend
color-dodge).

**Хедер** (`px 20, pt 16`): слева `logo.svg` (89×44); справа кнопка **Sign In** —
фон `#E0E0E0`, radius 16 (`rounded-2xl`), `px16 py10`, иконка `icon-sign-in.svg`
20×20 + текст 16/semibold чёрный. active: scale .95.

**Контент (по центру, gap 20):**
1. Пилюля: фон `rgba(168,194,238,0.12)`, capsule, `py8 pl6 pr16`; аватар
   `pill-avatar.png` 34×34 + текст «Your reative agent» 15/regular белый.
2. H1 «Any picture can be taken in a minute» — semibold, `clamp(32→74)`,
   line-height 0.96, letter-spacing -0.02em, белый, по центру.
3. Подзаголовок «Transform your ideas into breathtaking visuals with cutting-edge
   AI technology» — 17/medium (clamp 15→23), line-height 1.4, цвет `#AFB5CD`.
4. **Get Started** — фон `#8101FF`, radius 16, `px32 py18`, иконка
   `icon-get-started.svg` 22×22 + текст 20/semibold белый. active: scale .95.
5. **Карусель** — 5 карточек coverflow-веером (`card-*.webp`). Центр в фокусе
   (scale ~1.14, без блюра, тень `0 30px 90px rgba(0,0,0,.55)`), боковые повёрнуты
   ±15°/±40°, blur растёт к краям, авто-ротация каждые 2800мс, easing
   `cubic-bezier(.22,.61,.36,1)`, длительность перехода 1.1s. Карточка при
   заворачивании с последнего слота на первый — телепортируется (без анимации).
   Размер карточки: высота = `region/1.6` где `region=clamp(208, vh*0.34, 380)`,
   ширина = высота/1.22, горизонтальный разброс между слотами `cardW*0.46`.

Обе кнопки ведут на **Login**.

---

# 2. Login (`login/page.tsx`, мобильная колонка 424pt)

Экран: `fixed inset-0`, фон `#0A0A0A`, контент по центру, снизу декоративный
градиент (`h301`, `linear-gradient(to bottom, rgba(1,1,1,0), #010101 189%)`, opacity .48).

Карточка на мобиле = только форма (без правой картинки), maxWidth 424, `px24`.
Крестик закрытия (`Close`) в правом-верхнем → Landing.

Порядок сверху:
1. Заголовок в 2 строки «Sign up to generate» / «for free» — 30/bold, LH 38, по центру.
2. **Continue with Google** — фон `#E0E0E0`, radius 16, `py12`, иконка
   `google-icon.svg` 24 + текст 16/semibold `#222`. По клику — notice «Google
   sign-in is coming soon…» и фокус на email.
3. **Continue with Boostyfi** — фон `#1A0D2E`, бордер `#8D12FF`/30%, radius 16,
   `py12`; текст 16/semibold белый + бейдж «Web3» (фон `#8D12FF`, radius 8,
   `px10 py4`, 13/bold). → Трек B (в приложении сейчас заглушка/notice).
4. Разделитель: две линии `#222` (h2) + «or» по центру 16/semibold `#575757`.
5. **Email input** — фон `#191919`, бордер `#222` (focus `#8D12FF`), radius 16,
   `px16 py12`; иконка `auth-mail-icon.svg` 20 (opacity .6) + поле 16/medium
   белое, placeholder «Enter your email» белый/40%. Валидация regex email.
6. **Continue with email** — фон `#222`, radius 16, `py12`, текст 16/semibold
   `#EBEBEB` (opacity 0.24 пока email невалиден / 1 когда ок). Текст «Signing in…»
   во время отправки. По клику: `POST /auth/magic-link`, затем переход на Code.
7. Ошибка — 13, цвет `#E84749`. Notice — 13, цвет `#8D12FF`.

---

# 3. Code (`login/code/page.tsx`)

Тот же фон/карточка. **Экран косметический** — бэк OTP не проверяет; ввод 4 цифр
просто продолжает по devLink (см. AUTH.md).

1. Заголовок «Enter the code» 30/bold LH38.
2. Подзаголовок «We have sent the code to the email address you specified» 16/regular, по центру.
3. **4 бокса кода**: каждый 76×84, radius 16, текст 32/semibold белый по центру,
   `inputMode=numeric`. Пустой/не в фокусе: фон `#191919`, бордер `#222`. С цифрой
   или в фокусе: фон `#363636`, бордер прозрачный. Автопереход к следующему,
   Backspace — к предыдущему. Когда все 4 заполнены → подтверждение (confirmCode).
4. «Didn’t recieve the code?» 16/regular `#575757` + «Resend» 16 `#8D12FF`.
5. Разделитель + «or».
6. **Enter another email** — фон `#E0E0E0`, radius 16, `py12`, 16/semibold `#222` → назад на Login.
7. Ошибка 13 `#E84749`.

---

# 4. Start screen «/» (`page.tsx` → `StartScreenMobile`)

Открывается только для авторизованного (иначе редирект на Landing). Это «пустой»
экран приветствия перед чатом.

**Фон:** `#0A0A0A` + три радиальных глоу `bg-element-0/1/2.svg` (1840×1840,
центр `top 44%`, scale .85) + нижний градиент `h240`.

**Топ-бар** (`px12 pt12`, z30): слева логотип-плашка (фон `#141414`, radius 14,
h52, `px14`, `arteki-logo.svg` 82×40); справа `TopNavRight mobile` (см. §8).

**Центр (колонка, gap 20, maxWidth 440):**
1. Пилюля-анонс: h44, бордер `#474747`, capsule; `freepik-avatar.png` 32×32
   (margin 6) + «I'm Arteki, your assistant» 15/medium белый.
2. H1 «How can I help you today?» — 34/medium, LH 42, letter-spacing -0.8px,
   градиентный текст `linear-gradient(to right,#fff 50%,rgba(153,153,153,.56))` (clip).
3. **PromptInput (мобильный, см. §7).**
4. Фильтр-пилюли (горизонтальный скролл, gap 8): `FilterPill` × 3 — «Generate
   Image» / «Generate Video» / «Generate Postcard». Пилюля: фон `#222`, capsule,
   `pl24 pr28 py12`, иконка 24 (`image-fill`/`icon-video`/`icon-postcard`) +
   текст 16/semibold `#E0E0E0`. active: scale .95. Клик → сохранить категорию в
   стейт и перейти на Generate.

---

# 5. Generate — главный экран (`generate/page.tsx`)

Самый большой экран. Ниже все мобильные части.

## 5.1 Топ-бар (мобилка)
`absolute top0 px12 pt12`, z30, три элемента через space-between:
- **Бургер** 52×52, фон `#141414`, radius 14; три линии (20/14/20 × h2, белые,
  radius 2, gap 5). Открывает боковой drawer.
- **Логотип-плашка** h52, фон `#141414`, radius 14, `px14`, `arteki-logo.svg`
  82×40 → ведёт на «/».
- **`TopNavRight mobile`** (токены + профиль, §8).

## 5.2 Боковой drawer истории (мобилка)
Всегда смонтирован, анимируется. Бэкдроп `rgba(0,0,0,.65)` + blur 6px (fade
0.32s). Панель слева: ширина 300, фон `#0A0A0A`, бордер справа
`rgba(255,255,255,.07)`, `p16 12`, `translateX(-100%→0)` easing
`cubic-bezier(.16,1,.3,1)` 0.32s, тень `8px 0 40px rgba(0,0,0,.55)`.

Контент:
- Хедер: «History» 18/bold + крестик (`Close` 20, opacity .6, active scale .9).
- Список чатов (gap 8): элемент — фон `#222` (активный `#363636`), radius 12,
  `p8 10`; слева превью 40×40 radius 8 (object-cover), справа лейбл 14/medium
  `#A3A3A3` (эллипсис). Пусто → «No chats yet» 14 `#555`.
- Низ (gap 10): **New Chat** — фон `#E0E0E0`, radius 16, `p14 20`, иконка
  `icon-new-chat.svg` 20 + текст 16/semibold `#222`; сбрасывает к пикеру.
  Профиль-строка: фон `#141414`, radius 14, `p12 14`; аватар 28×28 круг +
  имя 14/semibold белый и почта 12 `#555` (обе truncate).

## 5.3 Чат-контейнер (скролл)
`absolute left0 right0 top76`, низ `bottom 108` (или `176`, если есть
attachments), плавный `transition bottom .2s`, `px12`. Спейсер flex-1 сверху
прижимает сообщения вниз. Сообщения gap 32.

## 5.4 Пузыри сообщений
**Аватары:** мобилка 44×44 (десктоп 64), круглые. User → `chat-avatar.png`
(или аватар юзера), справа с `ml16`. AI → `ai-avatar.png`, слева с `mr16`.

- **User bubble:** justify-end, макс. ширина 70%. Если есть вложения — сетка
  картинок 140×140 radius 8, бордер `rgba(255,255,255,.1)`. Текст-пузырь: фон
  градиент `#5B3FD9→#302173`, radius `32 32 8 32`, `px24 py16`, текст 20/regular белый.
- **AI bubble (текст):** фон `#222`, radius `32 32 32 8`, `px24 py16`, текст 20/regular белый.
- **AI видео:** контейнер radius 24, бордер `#474747`, фон чёрный, ширина 100%
  (maxWidth 350). `<video controls loop autoplay muted playsInline>`, aspect 9:16.
  → в SwiftUI: `AVPlayer`/`VideoPlayer`, зациклить, autoplay muted.
- **AI одиночная картинка:** контейнер radius 24, бордер `#474747`, ширина 100%
  (maxWidth 612, aspect 612/410). Клик → zoom-превью. (hover-оверлей на мобиле не нужен.)
- **AI несколько квадратных картинок:** сетка (wrap, gap 12), каждая 150×150
  (десктоп 350), radius 24, бордер `#474747`. Клик → zoom. Для открыток поверх —
  `CardTextOverlay` (§10).
- **Пилюли-шаблоны в пузыре** (`showPills`): контейнер фон `#141414`, radius 40,
  `p12`, gap 8, wrap. Пилюля: фон `#360078`, бордер `#8D12FF`, capsule,
  `pl8 pr16 py8`; кружок-картинка 32×32 + лейбл 16/semibold белый. Список PILLS:
  Good morning / Animal / Nature / Sport / City / Abstract / Universe / People
  (`pill-*.png`). Кнопка «…» (more): фон `#222`, capsule, `px24 py12`, иконка `Icon.svg` 24.
- **Ошибка генерации** (`isGenerationError`): карточка 150×150 (десктоп 350),
  radius 24, фон `#222`, бордер `#333`, по центру иконка `icon-cancel.svg` 24
  (opacity .7) + «Generation error» 18/medium `#A3A3A3`. Под ней **Try again** —
  фон `#8101FF`, radius 16, `p16 36`, `icon-retry` 24 (белая) + 18/semibold белый.

## 5.5 Состояния AI-флоу (рендерятся в конце чата)
- **thinking:** аватар AI + пузырь `#222` capsule `p20`, три точки 5×5 белые,
  `animate-bounce` со сдвигами 0/150/300мс (bouncing dots).
- **choosing / pickTile:** контейнер фон `#141414`, radius 40, `p12`, maxWidth 700.
  - **Табы категорий:** пилюли, активная фон `#8101FF`, неактивная `#222`,
    capsule `px16 py8`; эмодзи 16 + лейбл 15/semibold белый. Категории приходят
    с `/tiles` (image/postcard/announcement/video).
  - **Тайлы активной категории:** пилюли (`pillClass/pillStyle` — capsule фон
    `#222`, `px16 py8`, gap 8, hover/active); эмодзи 18 + title 16/semibold белый;
    если `cost > 1` — бейдж «{cost}T» 12 `#D9B8FF`. Клик → выбор тайла.
- **answering:** контейнер как выше. Пилюли-опции текущего вопроса (16/semibold
  белый). Если `allow_custom` — строка ввода: input фон `#222`, бордер `#333`,
  capsule `px16 py10`, focus `#8D12FF`, placeholder «Or type your own…» + кнопка
  **OK** фон `#8101FF` capsule `px20 py10`.
- **freeformStyle / freeformVideo:** контейнер как выше; пилюли стилей.
  Image-стили: Cartoon 3D / Cozy scene / Epic scene / Anime / Realistic.
  Video-стили: Cartoon 3D / Cozy scene / Epic scene.
- **confirming:** аватар AI + текст-пузырь `#222` («Everything is ready! Click to
  create a picture/video», либо «Now upload a photo, then press Generate…»). Ниже
  **токен-карточка**: ширина 100% (maxWidth 400), фон `#141414`, radius 24, `p12`,
  center, gap 16:
  - Если нужен фото — кнопка **Upload a photo**: h64, фон `#222`, бордер `#8101FF`,
    radius 16; `icon-add-image` 24 + 18/semibold белый.
  - Иначе — **Let's generate – {cost} TEKI**: h64, фон `#8101FF`, radius 16,
    `icon-token` 24 + 18/semibold белый.
  - Под кнопкой строка баланса: `icon-token` 24 + «{tokens} TEKI» 14/medium белый.
- **generating:** аватар AI + карточка `data-gen-card` 150×150 (десктоп 350),
  radius 24, бордер `#222`, фон `linear-gradient(#222 0%,#363636 70%,#2c2c2c)`.
  Внутри: спиннер 20×20 (`border 2px rgba(255,255,255,.15)`, top
  `rgba(255,255,255,.75)`, animate-spin) + «Generating...» / «Generating video...»
  14/medium `#A3A3A3`. Для видео — таймер `M:SS` 11 `#6F6F6F`. Ниже плашка-нотис:
  фон `#141414`, radius 40, `p10 20`, `icon-sparkling` 24 (белая) + курсивный
  текст 16 белый: видео → «Video usually takes 2–6 minutes, please wait...»,
  картинка (после ~5с) → «It takes longer than usual, wait...».

## 5.6 Композер (нижняя панель ввода) — `data-tour="prompt"`
`absolute left12 right12 bottom12`, фон `#222`, radius 24, бордер `#000`,
`p10 12`, gap 12, easing 0.25s. Клик по панели → фокус в поле.

- **Строка attachments** (если есть): горизонтальный скролл, gap 7; тайл 64×64
  radius 8, object-cover; при `uploading` — затемнение `#363636`/80% + спиннер
  24; при `ready` — кнопка-удаление `icon-cancel` 24 в правом-верхнем углу (offset -9).
- **Строка ввода** (space-between, gap 24):
  - Левая часть (h52): **orb** `Pic.svg` 44×44 (`mixBlendMode screen`, крутится
    при генерации); анимированный typewriter-placeholder (left 52, 16, `#A3A3A3`,
    с мигающим курсором `prompt-cursor` h22); реальный `<input>` 18/medium белый.
    PROMPTS для placeholder: «Describe your idea...», «Describe style
    references...», «Enter a text prompt or upload reference photos...», «A
    majestic dragon in 3D cartoon style...». Печать 60мс/символ, пауза 2200мс,
    удаление 32мс/символ, пауза 350мс, затем следующий.
  - Правая часть (gap 8): **Add image** — 48×48, фон `#0A0A0A`, radius 16,
    `icon-add-image` 24 (на мобиле только иконка). **Generate** — фон `#E0E0E0`,
    radius 16, `px18` h48, `icon-generate` 24 + «Generate» 16/semibold `#222`.
    Enter в поле = отправка.

## 5.7 Плавающий индикатор генерации
`fixed top72 left12 right12`, z60, radius 32, `p12 18`, backdrop-blur; фон
градиент `rgba(20,16,32,.97)→rgba(30,20,52,.97)`, бордер `rgba(183,148,246,.28)`,
тень `0 8px 28px rgba(0,0,0,.5)`. Слева иконка `Pic.svg` 36×36 (крутится) с
пульсирующим halo (`animate-ping`, radial glow `rgba(183,148,246,.35)`); если
задач >1 — бейдж «{pos}/{total}» (min 22×22, фон градиент `#8b5cf6→#6d28d9`,
бордер 2px тёмный). Центр: «Generating image/video...» (или «Generating N
items...») 14/semibold белый + лейбл 11 `#C4A7F7` (truncate, maxW 160). Справа
таймер `M:SS` в чипе (фон `rgba(183,148,246,.14)`, бордер `.22`, `#C4A7F7`,
`px14`). Клик → проскроллить к карточке генерации/чату. Держится при
переключении чатов (в вебе — через localStorage; в приложении — через app-state).

## 5.8 Zoom-превью (оверлей)
`inset0`, z50, фон `rgba(10,10,10,.95)` + blur 4. Карточка: мобилка
`min(88vw,405)` × `min(70vh,500)`, radius 24, бордер `#333`, фон `#141414`.
Внутри картинка (object-cover) или `<video controls autoplay loop muted
playsInline>`; поверх — `CardTextOverlay` для открыток. Крестик (`Close` 24) в
`top4 right4` в круге `bg-black/60`. Снизу кнопка **Download** — фон `#222`,
radius 8, `px12 py8`, `icon-download` 16 + «Download» 14 `#A3A3A3` (mp4 или png).

## 5.9 Галерея справа
Только десктоп (`display:none` на мобиле). На мобиле не порти — доступ к прошлым
генерациям идёт через drawer истории + zoom.

---

# 6. Флоу генерации (логика для флоу-состояний)
1. Пользователь пишет свободный текст (composer) ИЛИ жмёт тайл (pickTile).
2. Свободный текст → `POST /api/chat` (ассистент отвечает, может показать пилюли/
   тайлы) → выбор стиля (freeformStyle/Video).
3. Тайл → последовательность его `questions` (answering, quick-reply пилюли).
   Тайлы с `needs_photo` → сначала загрузка фото (`POST /api/uploads`) → `photo_url`.
4. confirming → `POST /api/generate` (тело: `tile_id`+`answers`, либо `prompt`+`style`).
   - Картинка: ответ ~15–45с с `url` → пузырь-картинка.
   - Видео: ответ `status:queued` → polling `GET /api/generations/{id}` каждые ~5–6с
     до ~15 мин → `result_url` → пузырь-видео. Ошибка → карточка ошибки + Try again
     (TEKI возвращается автоматически).
5. Баланс обновляется из ответа `/generate` (`balance`) и `GET /api/balance`.
Стоимость: картинка/открытка 1 TEKI, видео 2 TEKI.

---

# 7. PromptInput (мобильный вариант, стартовый экран) `PromptInput.tsx`
Карточка: фон `#0A0A0A`, бордер 2px (`#912BFF` в фокусе / `rgba(145,43,255,.35)`),
radius 24, `p12`, gap 12.
- Текст-карточка: minHeight 120, фон `#222`, radius 16, `p18`; typewriter-
  placeholder 20/`#666` с курсором h26; `<textarea rows=2>` 20 белый. PROMPTS:
  «Pixar characters...», «Landscape in Studio Ghibli style...», «Portrait of a
  mysterious girl with glowing eyes...», «Vintage analog collage...», «A
  futuristic city at sunset...», «I want nature, but in the Pixar style...».
- Строка действий (gap 8): **Upload** 56×56, фон `#222`, radius 14, `icon-upload`
  22. **Generate** flex-1 h56, фон `#E0E0E0`, radius 14, `magic-icon` 24 +
  «Generate» 17/semibold `#222`. Enter (без Shift) = submit → переход на Generate.

---

# 8. Профиль / меню / тосты (`TopNavRight.tsx`, `mobile`)
Плашка: `relative`, фон `#141414`, radius 16, `p6 8`, z50. Внутри (gap 10):
- **Токен-чип:** фон `#222`, radius 12, `pt4 pb4 pl8 pr4`; `icon-token` 24 +
  «{tokens} TEKI» 14/medium белый. (Кнопка Upgrade — `display:none` на мобиле.)
- **Профиль-кнопка:** аватар 40×40 круг (`Userpic.png` по умолчанию); имя
  скрыто на мобиле; шеврон `Dropdown Icon` 24 (rotate 180° при открытии).
  Клик → dropdown.

**Dropdown** (172 ширина, фон `#222`, radius 24, `p16`, бордер `#333`, ниже кнопки):
пункты «Profile» и «Sign Out» — иконка в квадрате 36×36 `#363636` radius 12 +
текст 14/medium `#D6D6D6` (hover white).

**Profile-модалка** (fullscreen оверлей `black/80` + blur 4): карточка 420 (на
мобиле по ширине), фон `#141414`, бордер `#222`, radius 24, `p32`, gap 24:
- Хедер: «Profile» 30/bold + крестик `Close` 24.
- Аватар 82×88 radius 16 + «Your photo» 16/semibold + кнопка **Upload** (фон
  `#363636`, radius 8, `px16 py8`, `Icon.svg` 20 + «Upload» 16). Загрузка →
  `POST /api/uploads` → `PATCH /api/auth/profile {avatar}`.
- **Name input** фон `#191919`, бордер `#222`, radius 16, `px16 py12`, 16 белый +
  кнопка **Save name** (активна `#8101FF` / неактивна `#222` opacity .4), radius
  16, `py12`, 16/semibold `#EBEBEB` → `PATCH /api/auth/profile {name}`.
- Разделитель `#222` h2.
- **Overview:** «Email» (иконка `icon-mail-profile` в квадрате 36 `#0A0A0A`) →
  значение; «With us since» (иконка `icon-tech-profile`) → дата `created_at`
  (формат «5 Jul 2026»). Лейблы `#A3A3A3`, значения белые, 16.
- **Credit balance** 16/semibold + чип фон `#755BEF`, radius 12, `p8`,
  `icon-token` 24 + «{tokens} TEKI» 16/semibold белый.
- **Session:** кнопки **Sign Out** (фон `#363636`, radius 8, `px16 py8` h40,
  `icon-signout-dropdown` 20 + 16/medium белый) и **Delete account** (фон
  `#2A1215`, бордер `#58181C`, `icon-delete-profile` 20 + 16/medium `#E84749`).

**Подтверждения** (модалки 420, фон `#141414`, radius 24, `p32`, gap 24):
- **Sign out:** заголовок 30/bold, текст 20/regular белый; кнопки Cancel (фон
  `#222`, бордер `#333`, h52, radius 16, 18/semibold белый) и Sign out (фон
  `#E0E0E0`, `#222`). → `DELETE /api/auth/me`, чистка локальных данных, на Login.
- **Delete account:** текст «This action cannot be undone…»; Cancel + Delete
  (фон `#E43E46` белый). → `DELETE /api/auth/profile`, на Login.

**Тосты** (`fixed top24 center`, фон `#141414`, бордер `#333`, radius 8):
- Success: зелёная галочка `icon-checkmark-toast` с glow `#8BBB11` +
  сообщение 14 `#CCC` + разделитель + close. Авто-скрытие 4с.
- Error (генерация): красная `icon-cancel` (glow red) + текст 14 `#CCC`. Авто 6с.
На мобиле в приложении можно заменить нативным toast/HUD, стиль сохранить.

---

# 9. Онбординг-тур (`OnboardingTour.tsx`)
3-шаговый коачмарк при первом заходе на Generate. Затемнение `rgba(10,10,10,.72)`,
«прорезь»-спотлайт вокруг элемента (radius 20, бордер 2px `rgba(141,18,255,.9)`,
`box-shadow 0 0 0 9999px` затемнение). Тултип: фон `#141414`, radius 8, `p12`,
ширина `min(430, vw-32)`, тень `0 12px 40px`, стрелка-указатель. Внутри: title
14/medium белый + body 14 `#CCC`; строка «{n} of 3», кнопки Back (`#858585`) и
Next/Got it (фон `#8101FF`, radius 8, `px20 py8`, 14/medium белый) + крестик.
**Триггер:** только первый заход (нет флага `arteki_onboarding_done`). После
«Got it» флаг ставится; отложенный промт со стартового экрана запускается.

**Шаги (точные тексты, порядок именно такой):**
1. `prompt` — **«Describe your idea»** / «Type what you'd like to create here and
   press Generate — Arteki turns your words into an image.»
2. `templates` — **«Or pick a template»** / «Not sure where to start? Choose a
   ready-made template and just answer a couple of quick questions.»
3. `history` — **«Your History»** / «Every image you create is saved here. Click
   any item to open it again anytime.»

Элементы подсвечиваются по `data-tour` (`prompt`/`templates`/`history`).

---

# 10. CardTextOverlay — текст на открытках (`CardTextOverlay.tsx`)
Текст открытки рисуется **шрифтом поверх картинки** (не генератором). Порти как
`Text`-оверлей в `ZStack` над картинкой.

**Preset → шрифт / стиль:**
| Preset | Шрифт | Weight | Обводка | Letter-spacing |
|---|---|---|---|---|
| bubble | Russo One | 400 | 3px `rgba(255,255,255,.25)` | 0.01em |
| rounded | Comfortaa | 600 | — | 0 |
| handwritten | Caveat | 600 | — | 0.01em |
| elegant | Yeseva One | 400 | — | 0.06em |
| marker | Nunito | 700 | — | 0.01em |
| clean | Manrope | 400 | — | 0 |

**Tile id → (preset, zone):** birthday/new_year/graduation→bubble,top;
jubilee→elegant,top; valentine/anniversary/mothers_day→handwritten,top;
wedding→handwritten,bottom; fathers_day→marker,bottom; easter/get_well/
good_morning/good_day→rounded,top; thanksgiving/just_because→marker,top.

**Размер по числу слов:** ≤3 слов → 7% высоты (LH 1.15, weight+300, max 26px);
≤8 → 5.5% (LH 1.25, +100, max 20px); иначе → 4.5% (LH 1.4, +0, max 16px).
Weight cap 900. Позиция: top/bottom = 6% от края, center-band = по центру.
Цвет всегда белый + тень `0 2px 8px rgba(0,0,0,.7), 0 1px 2px rgba(0,0,0,.9)`.
`whitespace: nowrap`, переносы только по `\n`. Размер считается от высоты
контейнера картинки (150 на мобиле в чате, 280–405 в zoom).

---

# 11. Экраны и API — сводка соответствия
| Экран | Ключевые ручки |
|---|---|
| Landing / Login / Code | `POST /auth/magic-link`, `GET /auth/verify` |
| Start «/» | `GET /auth/me`, `GET /balance` |
| Generate — пикер | `GET /tiles`, `GET /tiles/featured`, `GET /tiles/freeform-question` |
| Generate — свободный чат | `POST /chat` |
| Generate — загрузка фото | `POST /uploads` |
| Generate — создание | `POST /generate`, poll `GET /generations/{id}` |
| История/галерея | `GET /generations`, `GET /generations/{id}`, `POST /generations/{id}/share` |
| Профиль | `GET /auth/me`, `PATCH /auth/profile`, `DELETE /auth/profile`, `DELETE /auth/me` |
| Баланс/транзакции | `GET /balance`, `GET /transactions` |

Полные тела/ответы — в `API.md` и `openapi.json`. Авторизация (cookie
`arteki-session`) — в `AUTH.md`.
```
Порядок сборки экранов: Landing → Login → Code → Generate (пикер → вопросы →
confirming → generating → результат) → композер → drawer истории → профиль →
zoom-превью → индикатор генерации → онбординг.
```
