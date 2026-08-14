# TOONTOON Backend

FastAPI backend for TOONTOON — identity, TOONTOON wallet, and AI content generation.
The Next.js frontend lives in `/opt/frontend` (separate project).

> Status: fully working end-to-end with mock providers. Generation uses
> **Pollinations** (temporary — to be swapped for the real stack). Content is in
> **English**.

---

## Quick start

```bash
cd /opt/backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # tweak if needed

# Redis must be running:
redis-server --daemonize yes

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API: `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- Health: `GET /health`

To run without a Redis server (e.g. quick demo): set `USE_FAKE_REDIS=true` in
`.env` and `pip install fakeredis`.

---

## Architecture

```
app/
├── main.py            FastAPI app: lifespan (Redis), CORS, static /uploads, routers
├── config.py          pydantic-settings; all env in one place
├── redis_client.py    async Redis (real or fakeredis), single shared client
├── deps.py            session-cookie → (user, session) dependencies
├── cookies.py         set/clear the toontoon-session cookie consistently
├── core/
│   ├── security.py        token generation + HMAC webhook signatures
│   └── rate_limit.py      fixed-window limiter in Redis
├── models/            Pydantic models (user, session, generation, payment, tile)
├── services/
│   ├── auth_service.py        users / sessions / magic-link tokens in Redis
│   ├── boostify.py            Boostify client — MOCK + real HTTP (v2)
│   ├── wallet.py              unified wallet over both providers (two-phase pay)
│   ├── content_gen.py         сборка промпта (GPT + механический запасной путь)
│   ├── generation/            реестр провайдеров: операция → модель, фолбэк
│   ├── generations_service.py generation records, library, share links
│   └── tiles_data.py          the 31-tile catalog (static)
└── routers/
    ├── auth.py         magic-link (v1) + Boostify OAuth (v2) + /me + logout
    ├── profile.py      update name / delete account
    ├── tiles.py        catalog, featured, free-form question, balance
    ├── generate.py     uploads + two-phase /generate
    ├── generations.py  library, queued create, share, public share view
    ├── payments.py     transaction history
    └── webhooks.py     Boostify webhook (HMAC verified)
```

### Why these choices
- **Redis is the only datastore** (per the spec): users, sessions, magic tokens,
  generation history, share links, rate-limit counters, OAuth state.
- **Two auth contours, one wallet.** `wallet.py` hides whether the balance is the
  local `toontoon_balance` (magic-link) or live from Boostify (v2). Routers never
  branch on provider — except transaction history, which is genuinely different.
- **Модель выбирает реестр, а не код.** `services/generation` берёт провайдера
  из таблицы `generation_providers` по операции и приоритету и падает на
  следующего при отказе. Включить модель или поменять порядок — строка в базе,
  не релиз. `content_gen` отвечает только за текст промпта.
- **Boostify has a mock mode** (`BOOSTIFY_MOCK=true`) so the full v2 flow works
  today; flip to `false` + fill credentials when Boostify ships.

---

## API surface (all under `/api`)

| Method | Endpoint | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/auth/magic-link` | — | Issue magic token; returns `{ ok, devLink }` (email not sent) |
| GET | `/api/auth/verify?token=` | — | Consume token → set `toontoon-session` cookie → redirect to frontend |
| GET | `/api/auth/boostify/login` | — | Start Boostify OAuth |
| GET | `/api/auth/boostify/callback` | — | Exchange code → session → redirect |
| GET | `/api/auth/me` | optional | Current user or `null` |
| DELETE | `/api/auth/me` | — | Logout (delete session + cookie) |
| PATCH | `/api/auth/profile` | ✓ | Update name |
| DELETE | `/api/auth/profile` | ✓ | Delete account |
| GET | `/api/tiles` | — | Full catalog (4 categories, 31 tiles, questions) |
| GET | `/api/tiles/featured` | — | The 6 featured tiles |
| GET | `/api/tiles/freeform-question` | — | The single "What style?" question |
| GET | `/api/balance` | ✓ | `{ available, locked }` |
| POST | `/api/uploads` | ✓ | Upload reference photo → `{ id, url }` |
| POST | `/api/generate` | ✓ | Two-phase generate; returns `{ id, url, type, balance, prompt }` |
| GET | `/api/generations` | optional | Library (`[]` if no session) |
| POST | `/api/generations` | ✓ | Reserve a queued record |
| POST | `/api/generations/{id}/share` | ✓ | Create public share → `{ share_id, share_url }` |
| GET | `/api/share/{share_id}` | — | **Public** view backing `/v/[shareId]` |
| GET | `/api/transactions` | ✓ | History (Boostify live / local synth) |
| POST | `/api/webhooks/boostify` | HMAC | Async payment events |

### Generation flow (`POST /api/generate`)
1. Rate limit (`RATE_LIMIT_PER_HOUR`, default 30/h per user).
2. Resolve tile + cost (image = 1 TOONTOON, video = 2 TOONTOON).
3. **Reserve** funds (`wallet.reserve`) → 402 if not enough.
4. Run generation (optional photo → Pollinations vision, 7s timeout, graceful
   fallback → build Pollinations image URL; video → mock MP4).
5. **Confirm** on success / **cancel** (refund) on failure.
6. Store generation in the user's library; return result + new balance.

---

## Connecting the frontend

The frontend (`/opt/frontend`) is currently a **visual prototype** — it does not
call any backend yet (chat flow is `setTimeout`-driven, balance is local state,
uploads are `URL.createObjectURL`, pages are only `/` and `/generate`). To wire
it up:

1. Point the frontend at this API. Recommended: add Next.js `rewrites` so
   `/api/*` proxies to `http://localhost:8000/api/*` — then the session cookie is
   same-origin and "just works" with `SameSite=lax`.
   - Alternatively call the API cross-origin: set `NEXT_PUBLIC_API_URL`,
     `fetch(..., { credentials: "include" })`, and on the backend set
     `SESSION_COOKIE_SAMESITE=none` + `SESSION_COOKIE_SECURE=true` (needs HTTPS).
2. Replace the mock chat state machine with real calls:
   `GET /api/auth/me` → `GET /api/tiles` → `POST /api/generate`.
3. CORS is already configured for `http://localhost:3000` (see `CORS_ORIGINS`).

---

## What's mocked / pending (by request)

- **Генерация по фото не работает по-настоящему.** `image_to_image` написан,
  но в реестре выключен до проверки на реальных лицах: пока не ясно, узнаётся
  ли человек на результате. Видео отключено (`video_enabled=false`).
- **Boostify = mock** (`BOOSTIFY_MOCK=true`): OAuth returns a demo user, balance is
  `200 available / 1000 locked`, payments succeed. Real HTTP calls are already
  implemented for when Boostify is live.
- **Magic-link email is not sent** — the `devLink` is returned in the JSON
  response (per spec, temporary).

### Open questions for the Boostify team (from the spec)
1. OAuth protocol (assumed OAuth 2.0 / OIDC).
2. Token claims (`sub`, `email`, `name`, `avatar`).
3. Are **locked** tokens spendable in-product? (mock assumes yes)
4. Behaviour when Boostify is unreachable (block login or degrade?).
5. Is `reason` required on charge for analytics? (we send `toontoon:<type>_generate`)

---

## Verified (smoke tests)
Magic-link login → `me` → balance(3) → generate image (balance 3→2, library +1) →
share → public share (no auth) → video generate (2 TOONTOON, balance→0) → insufficient
funds (402) → free-form generate (prompt+style) → transactions. Boostify mock:
OAuth → session → balance(200/1000) → two-phase generate. Profile rename, logout,
account delete. Webhook: bad signature 401 / valid signature 200. Unauthenticated
`me`=null, library=[].
