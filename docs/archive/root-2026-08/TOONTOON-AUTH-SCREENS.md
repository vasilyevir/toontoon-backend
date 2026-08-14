# Экраны входа/регистрации (email + пароль) — 1:1 со стилем сайта

Реализовано и лежит в git. Стиль повторяет веб-карточку авторизации
(`login/page.tsx`): тёмная карточка, бордеры `#222`, инпуты `#191919` с фокусом
`#8D12FF`, заголовок 30/bold, шрифт Instrument Sans. Общие блоки — в
`Features/Auth/AuthComponents.swift`.

## Флоу
```
Landing → Login ⇄ Register
Register (успех) → Verification (заглушка) → Continue → Generate
Login (успех) → Generate
Повторный запуск с живой сессией → сразу Generate
```

## Общие компоненты (`AuthComponents.swift`)
- **AuthCard** — карточка: фон `#0A0A0A`, бордер `#222`, radius 24, maxWidth 424, `p24`.
- **AuthField** — поле: иконка + фон `#191919`, бордер `#222` (фокус `#8D12FF`),
  radius 16, `px16 py12`, текст белый; поддержка `SecureField`, автозаполнение iOS.
- **AuthPrimaryButton** — кнопка: фон `#222`, radius 16, `py12`, текст 16/semibold
  `#EBEBEB` (opacity .24 когда неактивна) — как «Continue with email» на сайте.
- **AuthCloseButton** — крестик (назад на Landing).

## Экран Login (`LoginView.swift`)
- Заголовок «Log in» 30/bold.
- Поля: email (иконка конверта), password (`SecureField`, иконка замка).
- Кнопка «Log in» (активна при валидном email и пароле ≥8).
- Ошибка сервера — красный текст `#E84749` (401 «Invalid email or password»).
- Разделитель «or» + ссылка «Don’t have an account? **Sign up**» (`#8D12FF`) → Register.
- Снизу — декоративный градиент (как на сайте).

## Экран Register (`RegisterView.swift`)
- Заголовок «Sign up to generate / for free» (как на сайте) 30/bold.
- Поля по порядку: **Your name**, email, **Password (min 8 characters)**,
  **Repeat password**.
- Клиентская валидация: имя не пустое, email по regex, пароль ≥8, пароли совпадают.
  Подсказки серым (`#575757`), ошибки — красным.
- Кнопка «Sign up» (активна только при валидной форме).
- Ссылка «Already have an account? **Log in**» → Login.
- Прокрутка (ScrollView) — форма длиннее экрана на маленьких устройствах.
- Ошибки сервера: 409 (email занят), 422 (слабый пароль), 429 (лимит).

## Экран Verification (`VerificationView.swift`) — ЗАГЛУШКА
Пока нет домена/почты (по твоему решению): экран есть, но **не работает по-настоящему**.
- Заголовок «Verify your email», пояснение «Email delivery is coming soon — for
  now you can continue».
- «Open verification link» — текст-ссылка, **которая ничего не открывает**.
- Поле «Enter code (optional)» — принимает что угодно.
- Кнопка «Continue» → `auth.completeVerification()` → переход в Generate.
- Примечание: «Verification will become required once the domain is set up.»

Когда появится домен/почта — подключить реальную отправку и проверку
(см. `AUTH-EMAIL-PASSWORD-PLAN.md`, раздел «позже»).

## Клиентская логика
- `AuthManager.login()` — вход, токен из JSON → Keychain → сразу авторизован.
- `AuthManager.register()` — регистрация; сессия сохраняется, но UI гейтится
  флагом `needsVerification`, пока не нажмут Continue.
- `AuthManager.completeVerification()` — финализирует вход.
- Все запросы — по HTTPS с пиннингом (`APISecurity`).

## Серверные ручки (задеплоено)
`POST /api/auth/register`, `POST /api/auth/login` — тела/ответы в `API.md`.
Пароль хранится как Argon2id-хеш; вход/регистрация с rate-limit по email+IP.
