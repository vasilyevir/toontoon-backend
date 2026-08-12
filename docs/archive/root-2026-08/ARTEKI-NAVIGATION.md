# Навигация: куда попадает пользователь (полный флоу)

Источник истины: роутинг веб-фронта + `auth_success_redirect` бэкенда
(`app/config.py` → по умолчанию `/generate`). В нативном приложении это
реализовано в `App/ArtekiApp.swift` (`RootView`) и `Auth/AuthManager.swift`.

## Карта экранов

```
                        ┌─────────────────────────────┐
   не авторизован  ───► │  Landing  (/landing)        │
                        │  «Any picture … in a minute»│
                        └───────────────┬─────────────┘
                            Get Started / Sign In
                                        ▼
                        ┌─────────────────────────────┐
                        │  Login  (/login)            │
                        │  email → magic-link         │
                        └───────────────┬─────────────┘
                            POST /auth/magic-link
                                        ▼
                        ┌─────────────────────────────┐
                        │  Code  (/login/code)        │  ← 4 цифры (косметика)
                        │  подтвердить                │
                        └───────────────┬─────────────┘
                     GET devLink → Set-Cookie arteki-session
                                        ▼
        ★ ТОЧКА ВХОДА ПОСЛЕ ЛОГИНА (auth_success_redirect) ★
                                        ▼
                        ┌─────────────────────────────┐
                        │  Generate  (/generate)      │
                        │  первым виден ПИКЕР ТАЙЛОВ   │
                        └───────────────┬─────────────┘
                                        │  (тап по лого)
                                        ▼
                        ┌─────────────────────────────┐
                        │  Start  («/»)  welcome      │  (только для авторизованных;
                        │  prompt + фильтр-пилюли      │   гость → redirect на /landing)
                        └─────────────────────────────┘
```

## 1. Что происходит сразу после логина
- Бэкенд в `GET /api/auth/verify` ставит cookie `arteki-session` и делает
  **302 на `{frontend_url}{auth_success_redirect}` = `/generate`**.
- Значит **пользователь после входа попадает на экран Generate**, а не на
  стартовый «/». Экран `/` — это отдельный welcome, доступный уже внутри
  приложения (тап по логотипу в топ-баре).

> Нюанс веб-флоу: на вебе `/login` после `magic-link` сразу делает
> `location.href = devLink` (экран `/login/code` фактически не задействован —
> легаси). В приложении мы оставляем шаг **Code** как экран (см. `AUTH.md`),
> он косметический: подтверждение просто продолжает по devLink. Итог тот же —
> попадаем на Generate.

## 2. Что видно на Generate в первый момент
Начальное состояние (`generate/page.tsx`): `aiFlowState = "none"`,
`flowMode = "pickTile"`, `messages = []` — то есть **первым показывается пикер
тайлов** (табы категорий + пилюли-тайлы) внутри AI-пузыря «choosing».

На маунте экран:
1. Проверяет сессию `GET /auth/me` (нет сессии → на Landing).
2. Грузит каталог `GET /tiles` (+ `GET /tiles/featured`), баланс `GET /balance`.
3. Восстанавливает историю чатов и незавершённые генерации (в вебе — из
   localStorage; в приложении — из app-state/persistence).
4. **Первый запуск** → показывает онбординг-тур (3 шага: history → templates →
   prompt), см. `MOBILE-DESIGN-FULL.md §9`. Флаг «показан» сохраняется.
5. Если пришли с Landing/Start с выбранной категорией или готовым промптом
   (в вебе — `arteki_start_category` / `arteki_pending_prompt`), они
   предвыбираются/предзаполняются.

## 3. Дальнейшие переходы внутри Generate (без смены экрана)
Это состояния одного экрана, не отдельные страницы:
`pickTile → answering (вопросы тайла) → confirming (оплата TEKI) → generating →
результат в чате`. Свободный текст: `composer → thinking → freeformStyle/Video →
confirming → generating`. Тайлы с `needs_photo`: перед confirming — загрузка фото.

## 4. Выход / смена аккаунта
- **Sign out** (профиль/меню) → `DELETE /api/auth/me`, чистка локальных данных →
  **Login**.
- **Delete account** → `DELETE /api/auth/profile` → **Login**.
- Повторный запуск приложения: если cookie `arteki-session` в Keychain жива и
  `GET /auth/me` вернул юзера → сразу **Generate** (минуя Landing/Login).

## 5. Как это уже реализовано в приложении
`RootView` (`App/ArtekiApp.swift`):
```
isBootstrapping → ProgressView
isAuthenticated → GenerateView          // ← точка входа после логина
else            → NavigationStack{ LandingView }  // Landing → Login → Code
```
`AuthManager.bootstrap()` при старте читает cookie из Keychain и зовёт
`/auth/me`; успех → сразу Generate. `confirmCode()` после ввода кода
захватывает сессию и переключает `isAuthenticated` → RootView показывает
GenerateView. То есть навигация «после логина → Generate» уже зашита.
```
Landing → Login → Code → [confirm] → Generate (пикер тайлов) → … → результат
```
