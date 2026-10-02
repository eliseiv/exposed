# exposed-backend

Бэкенд мобильной мультиплеерной пати-игры (аналог «Explode 2 / Get Exposed»): гостевой вход,
лобби по короткому коду, игровой цикл в реальном времени по WebSocket и шесть мини-игр.
Построен на `service-template` — ядро шаблона не изменялось, весь код игры живёт в
`src/app/domain/` (описание ядра — ниже).

## Игра

| `kind` | Игра | Мин. игроков | Очки |
|---|---|---|---|
| `question_list` | Список вопросов: «Кто вероятнее», «Да/нет», дуэли, «голосуй и выполняй» | 3 | опционально (штрафные) |
| `wheel` | Колесо фортуны из вопросов/заданий/сплетен игроков | 2 | — |
| `bomb` | Бомба: отвечай и передавай, пока не взорвалась | 2 | взрывы |
| `impostor` | Импостер: объясни слово, найди того, кто его не знает | 3 | да |
| `fill_blank` | Допиши фразу: варианты или свободный ввод, судья выбирает лучший | 3 | да |
| `hot_seat` | «21 вопрос»: случайный игрок — случайный вопрос | 2 | — |

Новая игра типа «список вопросов» создаётся через admin API (`kind=question_list` + карточки) —
без изменений кода сервера и клиента.

Контент на двух языках — `ru` и `en` (`SUPPORTED_LOCALES`): каталог отдаётся по
`Accept-Language`, у комнаты свой язык, колоды выбираются на нём.

* **REST** (OpenAPI — `/docs`): `POST /v1/guest`, `GET/PUT /v1/players/me`, `GET /v1/avatars`,
  `GET /v1/modes`, `POST /v1/rooms`, `POST /v1/rooms/{code}/join`, `GET /v1/rooms/{code}`,
  `POST /v1/rooms/{code}/leave`, admin — `/v1/admin/content/*` (`X-Admin-Token`).
* **WebSocket**: `/v1/ws/rooms/{code}` — протокол в [`docs/realtime-protocol.md`](docs/realtime-protocol.md).

### Архитектура realtime

* Правила игр — чистый детерминированный движок (`domain/game/`): `apply(room, command, ctx)`
  без I/O, время и случайность внедряются. Каждый режим — `domain/game/modes/<kind>.py`.
* Состояние комнаты — JSON в Redis; команды применяются под распределённым локом комнаты;
  события рассылаются через Redis pub/sub всем воркерам (`gunicorn -w 4` и несколько реплик).
* Таймеры фаз и 30-секундные grace-периоды переподключения — Redis ZSET, срабатывают ровно
  один раз и переживают рестарт.
* Контент (игры, карточки, слова импостера, варианты фраз, аватары) — PostgreSQL
  (`migrations/versions/0002_*`, стартовый контент — `0003_seed_content`); завершённые игры
  архивируются в `game_sessions`.

```
src/app/domain/
  config.py            DomainSettings (ROOM_*, RECONNECT_GRACE_SECONDS, WS_*, ...)
  models.py            avatars, player_profiles, game_modes, cards, impostor_words,
                       blank_answers, game_sessions
  game/                движок: state, engine, voting, lifecycle, modes/*
  realtime/            store (Redis + lock), bus (pub/sub), timers, hub, manager, runtime, ws
  routers/             players (guest, профиль, каталог), rooms, admin_content
tests/domain/          движок (юнит), REST + WebSocket на реальных PostgreSQL и Redis
```

---

# service-template (ядро)

Переиспользуемое backend-**ядро** на FastAPI: auth (deviceId + Sign in with Apple, встроенный
RS256-issuer, refresh-rotation), биллинг (Apple StoreKit IAP + Adapty + CloudPayments/broadapps),
кредитный кошелёк с идемпотентным ledger, policy-гейт, генерация через сменный провайдер, admin,
audit, observability, rate-limit.

Из шаблона поднимается новый сервис с **любым** видом генерации (изображения, видео, аудио, текст):
всё, что отличает ваш сервис, живёт в `src/app/domain/`. Ядро не редактируется.

---

## Принцип

```
src/app/            ← ЯДРО. Копируется в новый сервис как есть. Не редактируется.
src/app/domain/     ← ТОЧКА РАСШИРЕНИЯ. Здесь живёт весь код вашего сервиса.
```

Ядро **не имеет права** импортировать `app.domain.*` (проверяется тестом). Сцепка — только через
`DomainRegistry`: домен экспортирует `REGISTRY = DomainRegistry(...)`, ядро подхватывает роутеры,
теги, body-limit правила, провайдера генерации и таблицы для truncate в тестах.

Идентичность сервиса задаётся **через env** (`SERVICE_NAME`, `SERVICE_DOMAIN`) — имя python-пакета
остаётся `app` во всех сервисах, импорты переписывать не нужно.

---

## Свой домен за 3 шага

**1. Склонировать шаблон и поднять его как есть**

```bash
git clone <repo> my-service && cd my-service
cp .env.example .env          # дефолтов достаточно для локального запуска
docker compose up --build -d  # postgres + redis + migrate + api
curl -fsS http://127.0.0.1:8000/ready
```

`POST /v1/generate` уже работает: ядро поставляется с `EchoProvider` — эталонной реализацией
`GenerationProvider` без внешних зависимостей. Биллинг, policy-гейт и списание кредитов работают
вокруг него с первого запуска.

**2. Написать свой домен в `src/app/domain/`**

```python
# src/app/domain/provider.py
class FluxProvider:                       # реализует GenerationProvider
    async def generate(self, req: GenerationRequest) -> GenerationResult: ...

# src/app/domain/__init__.py
from app.extensions.registry import DomainRegistry
from app.domain.provider import FluxProvider
from app.domain.routers import images_router

REGISTRY = DomainRegistry(
    routers=[images_router],
    generation_provider=FluxProvider(),
)
```

Свои зависимости — в `[project.dependencies]` (`pyproject.toml`); свои настройки — в
`DomainSettings`; свои таблицы — миграцией `0002+` (ядро занимает ровно одну baseline `0001`);
свои тесты — в `tests/domain/`, подключив готовый contract-suite провайдера одной строкой:

```python
# tests/domain/test_flux_provider.py
from tests.contract.provider_suite import provider_contract_suite
test_flux = provider_contract_suite(FluxProvider(), kind="image")
```

**3. Настроить env под свой сервис**

Минимум: `SERVICE_NAME`, `PRODUCTS` (единственный источник числа кредитов), `GENERATION_PROVIDER`,
`PRICING_MODE` + тариф. Полный перечень переменных ядра — в `.env.example` и `.env.prod.example`.

> **Приёмочный критерий шаблона:** новый домен **не требует** правок в `src/app/{main,config,deps,errors,db}.py`,
> в core-пакетах и в `tests/conftest.py`. Если пришлось править ядро — это дефект шаблона, а не вашего домена.

---

## Команды

`Makefile` оборачивает канонические команды разработки:

```bash
make install    # uv sync
make fmt        # ruff format
make lint       # ruff check
make type       # mypy src
make test       # pytest
make ci         # всё, что проверяет CI: format-check + lint + mypy + coverage-гейты
make up         # docker compose: postgres + redis + migrate + api
make up-obs     # то же + Prometheus overlay
make migrate    # alembic upgrade head
```

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16 · Redis 7 · Alembic · uv · Ruff ·
mypy · pytest + testcontainers. Gunicorn + UvicornWorker в проде, образ `python:3.12-slim-bookworm`
(multi-stage, non-root).

Ядро **не тянет** ни одной LLM/домен-специфичной зависимости (`anthropic`, `openai`, `pypdf` —
вырезаны сознательно). Нужна — добавляет домен.

## Деплой

Общий сервер за внешним Traefik, несколько изолированных инстансов, образ собирается на сервере,
миграции — отдельным шагом до старта нового `api`, readiness-gate по health контейнера.
Провижининг, релиз, откат — [`infra/deploy/README.md`](infra/deploy/README.md).

Список инстансов автодеплоя — GitHub Actions Variable `DEPLOY_INSTANCES` (`dir:project`, через
пробел). Не задана → deploy job пропускается, CI остаётся зелёным.

## Безопасность

Секретов в репозитории нет: `.env*.example` — только плейсхолдеры, реальный `.env` живёт на
сервере (gitignored), ключи и сертификаты монтируются read-only в рантайме и никогда не попадают в
образ.
