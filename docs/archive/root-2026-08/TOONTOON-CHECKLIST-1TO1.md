# Чек-лист «что ещё нужно для 1:1»

Полный список того, что довести, чтобы приложение совпадало с вебом 1:1.
Отмечай по мере готовности.

## A. Инфраструктура проекта
- [ ] Создать Xcode-проект (SwiftUI, min iOS 16), подключить папку `ToontoonApp/`.
- [ ] `Config.xcconfig` из примера → `TOONTOON_BASE_URL = http://193.149.190.155`.
- [ ] Шрифт **Instrument Sans** (.ttf) в `Resources/Fonts` + `UIAppFonts` (уже в Info.plist).
- [ ] Картинки из `Resources/Images/{assets,landing}` завести в `Assets.xcassets`
      (для SVG — Preserve Vector Data). Имена оставить как есть.
- [ ] Проверить ATS-исключение для HTTP-сервера (уже в Info.plist).

## B. Анимации (описаны и/или реализованы)
- [x] Печатающий плейсхолдер + мигающий курсор — `TypewriterPlaceholder.swift`,
      логика в `PROMPT-ANIMATION.md`.
- [ ] Крутящийся orb `Pic.svg` при генерации (rotationEffect, 1.5с).
- [ ] Плавающий индикатор генерации: пульсирующий halo (`animate-ping`), таймер `M:SS`.
- [ ] Три «думающие» точки (bouncing dots, задержки 0/150/300мс).
- [ ] Спиннеры (в карточке генерации, в attachments) — `ProgressView`/кастом.
- [ ] Карусель лендинга: 5 карточек coverflow, авто-ротация 2800мс, ±15/±40°, blur, телепорт на завороте.
- [ ] Drawer истории: slide-in + fade бэкдропа (0.32s, cubic-bezier .16,1,.3,1).
- [ ] Тосты: выезд сверху (top -100 → 24), авто-скрытие 4с/6с.
- [ ] Онбординг-тур: спотлайт + тултип со стрелкой (fade 0.2s).

## C. Экраны (перенести полную вёрстку из MOBILE-DESIGN-FULL.md)
- [ ] Landing (карусель + градиент-фон).
- [ ] Login / Code (сейчас есть; довести пиксели).
- [ ] Start «/» (welcome с prompt + фильтр-пилюлями).
- [ ] Generate: топ-бар, drawer, чат-скролл, все типы пузырей, все состояния флоу,
      композер, индикатор, zoom-превью.
- [ ] Профиль: dropdown, модалка, подтверждения sign-out/delete, тосты.

## D. Функциональность
- [ ] Auth Track A end-to-end против живого сервера (magic-link → cookie → /me).
- [ ] Видео-плеер (AVKit/`VideoPlayer`) для сгенерированного видео (loop, muted, 9:16).
- [ ] Загрузка фото: `PhotosPicker`/камера → `POST /uploads` → `photo_url`.
- [ ] Поллинг видео (`GET /generations/{id}` каждые ~5–6с) — есть в APIClient.
- [ ] Постоянство истории чатов (в вебе localStorage → в iOS: файлы/CoreData/@AppStorage).
- [ ] Восстановление незавершённой генерации после перезапуска (age-gate: видео 20 мин, картинка 5 мин).
- [ ] Text-overlay на открытках (`CardTextOverlay`) — шрифты Russo One/Comfortaa/Caveat/Nunito/Manrope/Yeseva One.
- [ ] Скачивание/шеринг результата (`ShareLink`/`UIActivityViewController`).
- [ ] Обработка ошибок: 402 (не хватает TOONTOON), 429 (лимит), 503 (генератор занят, TOONTOON возвращён).
- [ ] Клавиатура: сдвиг композера над клавиатурой (safe area / keyboard avoidance).

## E. На будущее (требует доработки бэкенда)
- [ ] APNs-пуши (сейчас на бэке Web-Push/VAPID — на iOS не работает).
- [ ] «Правильный» Boostyfi/Google OAuth (Трек B) — см. `AUTH.md`.
- [ ] Перед релизом: `https://toontoon.ai` + убрать ATS-исключение + cookie secure=true.

## F. Что тебе (владельцу) понадобится
- [ ] Apple Developer аккаунт ($99/год) — для устройства и App Store.
- [ ] Bundle ID и (для Трека B) URL-схема `toontoon://`.
- [ ] Валидный HTTPS на `toontoon.ai` перед релизом.
- [ ] Отозвать засвеченный GitHub-токен, выдать второму Claude свежий.
