"""Domain integration scaffolding: a REAL Redis (testcontainers), the realtime runtime on it, an
ASGI client that speaks both HTTP and WebSocket, and seeded game content."""

from __future__ import annotations

import asyncio
import importlib
import itertools
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import redis.asyncio as redis
import uvicorn
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect

from app.domain.models import Avatar, BlankAnswer, Card, GameMode, ImpostorWord

_DOMAIN_LIMITERS = (
    ("app.domain.routers.players", "enforce_auth_limits"),
    ("app.domain.routers.players", "enforce_other_limits"),
    ("app.domain.routers.rooms", "enforce_other_limits"),
    ("app.domain.routers.admin_content", "enforce_admin_limits"),
)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


class FakeClock:
    """Wall clock + a manual offset: tests jump forward instead of sleeping."""

    def __init__(self) -> None:
        self.offset = 0

    def __call__(self) -> int:
        return int(time.time() * 1000) + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += int(seconds * 1000)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[redis.Redis]:
    client = redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def runtime(
    redis_client: redis.Redis,
    clock: FakeClock,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Any]:
    """The realtime runtime on the Redis container. The bus runs; timers are fired by hand
    (``await runtime.fire_due_timers()`` after ``clock.advance(...)``) — no sleeping."""
    from app.domain.config import get_domain_settings
    from app.domain.realtime import runtime as runtime_mod

    rt = runtime_mod.GameRuntime(
        redis_client, get_domain_settings(), sessions=lambda: sessionmaker_, clock=clock
    )
    rt.subscribed = asyncio.Event()
    rt._tasks = [asyncio.create_task(rt.bus.run(rt.hub.dispatch, rt.subscribed))]
    await asyncio.wait_for(rt.subscribed.wait(), timeout=10)
    runtime_mod.set_runtime(rt)
    yield rt
    await rt.stop()
    runtime_mod.set_runtime(None)


@pytest.fixture
async def gclient(
    sessionmaker_: async_sessionmaker[AsyncSession],
    allow_limits: None,
    runtime: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    from app import deps
    from app.main import create_app

    async def _allow(**_kwargs: Any) -> bool:
        return True

    for module_name, attr in _DOMAIN_LIMITERS:
        monkeypatch.setattr(importlib.import_module(module_name), attr, _allow)

    async def _override_db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[deps.get_db] = _override_db
    # A REAL server in the test's event loop: real WebSocket framing, real close codes.
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 — uvicorn exposes a flag, not an event
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as ac:
        yield ac
    server.should_exit = True
    await asyncio.wait_for(task, timeout=10)


# --------------------------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------------------------
@pytest.fixture
async def content(sessionmaker_: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """One mode per kind with a few cards; returns ``{kind: mode_id}``."""
    kinds = {
        "question_list": ("pick_player", 3),
        "wheel": (None, 2),
        "bomb": ("question", 2),
        "impostor": (None, 3),
        "fill_blank": ("blank_prompt", 3),
        "hot_seat": ("question", 2),
    }
    ids: dict[str, int] = {}
    async with sessionmaker_() as s:
        s.add_all(Avatar(key=f"avatar_{i}", sort_order=i) for i in range(1, 4))
        for kind, (card_type, min_players) in kinds.items():
            mode = GameMode(slug=kind, kind=kind, title=kind.title(), min_players=min_players)
            s.add(mode)
            await s.flush()
            ids[kind] = mode.id
            if card_type:
                s.add_all(
                    Card(mode_id=mode.id, type=card_type, category="friendly", text=f"{kind} {i}")
                    for i in range(3)
                )
        s.add_all(ImpostorWord(word=f"w{i}", hint=f"h{i}", category="friendly") for i in range(3))
        s.add_all(BlankAnswer(text=f"answer {i}", category="friendly") for i in range(30))
        await s.commit()
    return ids


# --------------------------------------------------------------------------------------------
# Players over the wire
# --------------------------------------------------------------------------------------------
_msg_ids = itertools.count(1)


class WsPlayer:
    """A test client socket. Messages not yet asked for wait in ``inbox`` (an event may arrive
    before the ack of the command that caused it)."""

    def __init__(self, ws: ClientConnection, user_id: str) -> None:
        self.ws = ws
        self.user_id = user_id
        self.log: list[dict[str, Any]] = []
        self.inbox: list[dict[str, Any]] = []

    async def send(self, type_: str, **data: Any) -> str:
        msg_id = f"m{next(_msg_ids)}"
        await self.ws.send(json.dumps({"type": type_, "msgId": msg_id, "data": data}))
        return msg_id

    async def recv(self, wait: float = 5.0) -> dict[str, Any]:
        msg: dict[str, Any] = json.loads(await asyncio.wait_for(self.ws.recv(), wait))
        self.log.append(msg)
        return msg

    async def wait_for(self, type_: str, wait: float = 5.0) -> dict[str, Any]:
        for i, msg in enumerate(self.inbox):
            if msg["type"] == type_:
                return self.inbox.pop(i)
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                seen = [m["type"] for m in self.log]
                raise AssertionError(f"{self.user_id}: no '{type_}' in {seen}")
            msg = await self.recv(wait=left)
            if msg["type"] == type_:
                return msg
            self.inbox.append(msg)

    async def command(self, type_: str, **data: Any) -> dict[str, Any]:
        """Send a command and return its ack / error reply."""
        msg_id = await self.send(type_, **data)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            msg = await self.recv(wait=deadline - time.monotonic())
            if msg["type"] in ("ack", "error", "pong") and msg["data"].get("msgId") == msg_id:
                return msg
            self.inbox.append(msg)
        raise AssertionError(f"no reply to {type_}")


async def guest(client: AsyncClient, nickname: str) -> tuple[str, dict[str, str]]:
    resp = await client.post(
        "/v1/guest", json={"deviceId": f"dev-{uuid.uuid4()}", "nickname": nickname}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["tokens"]["userId"], {"Authorization": f"Bearer {body['tokens']['accessToken']}"}


def connect(client: AsyncClient, code: str, headers: dict[str, str]) -> Any:
    url = str(client.base_url).replace("http://", "ws://").rstrip("/")
    return ws_connect(f"{url}/v1/ws/rooms/{code}", additional_headers=headers, open_timeout=5)


async def closed_code(ws: ClientConnection, wait: float = 5.0) -> int | None:
    """Drain until the server closes the socket; return the close code."""
    from websockets.exceptions import ConnectionClosed

    try:
        while True:
            await asyncio.wait_for(ws.recv(), wait)
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd else None
