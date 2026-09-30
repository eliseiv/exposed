# service-template

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
