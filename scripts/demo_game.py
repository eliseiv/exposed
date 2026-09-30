"""Play a short party over a running server: 3 guests, a "most likely" round and an impostor game.

    uv run python scripts/demo_game.py [http://127.0.0.1:8000]

With gunicorn -w 4 the three sockets usually land on different workers — every event still
reaches everyone (Redis pub/sub). Server timers are exercised too (short speaking/vote times).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import uuid
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


class Client:
    def __init__(self, name: str, token: str, user_id: str, ws: ClientConnection) -> None:
        self.name, self.token, self.user_id, self.ws = name, token, user_id, ws
        self.inbox: list[dict[str, Any]] = []
        self.n = 0

    async def send(self, type_: str, **data: Any) -> None:
        self.n += 1
        await self.ws.send(
            json.dumps({"type": type_, "msgId": f"{self.name}-{self.n}", "data": data})
        )

    async def wait(self, type_: str, wait: float = 60) -> dict[str, Any]:
        for i, m in enumerate(self.inbox):
            if m["type"] == type_:
                return self.inbox.pop(i)
        async with asyncio.timeout(wait):
            while True:
                m = json.loads(await self.ws.recv())
                if m["type"] == "error":
                    print(f"  [{self.name}] error: {m['data']}")
                if m["type"] == type_:
                    return m
                self.inbox.append(m)


async def guest(http: httpx.AsyncClient, name: str) -> tuple[str, str]:
    r = await http.post("/v1/guest", json={"deviceId": f"demo-{uuid.uuid4()}", "nickname": name})
    r.raise_for_status()
    t = r.json()["tokens"]
    return t["userId"], t["accessToken"]


async def main() -> None:
    ws_base = BASE.replace("http", "ws", 1)
    async with httpx.AsyncClient(base_url=BASE) as http, contextlib.AsyncExitStack() as stack:
        modes = {m["slug"]: m for m in (await http.get("/v1/modes")).json()}
        print("catalogue:", ", ".join(f"{m['title']} ({m['kind']})" for m in modes.values()))

        names = ["Alice", "Bob", "Carol", "Dave"]
        creds = [await guest(http, n) for n in names]
        h0 = {"Authorization": f"Bearer {creds[0][1]}"}
        room = (
            await http.post("/v1/rooms", json={"modeId": modes["most_likely"]["id"]}, headers=h0)
        ).json()
        code = room["code"]
        print("room code:", code)
        for _uid, token in creds[1:]:
            (
                await http.post(
                    f"/v1/rooms/{code}/join", headers={"Authorization": f"Bearer {token}"}
                )
            ).raise_for_status()

        clients: list[Client] = []
        for name, (uid, token) in zip(names, creds, strict=True):
            ws = await stack.enter_async_context(
                connect(
                    f"{ws_base}/v1/ws/rooms/{code}",
                    additional_headers={"Authorization": f"Bearer {token}"},
                )
            )
            c = Client(name, token, uid, ws)
            snap = await c.wait("room.snapshot")
            print(f"  {name} connected, sees {len(snap['data']['players'])} players")
            clients.append(c)
        host = clients[0]

        # --- round of "who is most likely" -----------------------------------------------
        await host.send("game.start")
        rs = [await c.wait("round.started") for c in clients]
        card = rs[0]["data"]["card"]
        print(f"\nQ: {card['text']}")
        bob = clients[1].user_id
        for c in clients:
            await c.send("vote.cast", choice=bob)
        res = [await c.wait("round.results") for c in clients]
        target = res[0]["data"]["targets"][0]
        print(
            f"  result: {target['nickname']} — {res[0]['data']['percentages'][bob]}%"
            " (all 4 clients got it)"
        )
        await host.send("game.end")
        for c in clients:
            await c.wait("game.finished")

        # --- impostor with server timers ---------------------------------------------------
        await host.send(
            "room.update_settings",
            modeId=modes["impostor"]["id"],
            settings={"speakSec": 5, "voteSec": 5, "gamesCount": 1, "maxRounds": 1},
        )
        await host.wait("room.settings")
        await host.send("game.start")
        roles = {c.name: (await c.wait("impostor.role"))["data"] for c in clients}
        for name, role in roles.items():
            print(f"  {name}: {role['role']:9} word={role['word']} hint={role['hint']}")
        print("  waiting for the server's speaking timers (4 x 5s)...")
        voting = await host.wait("impostor.voting", wait=40)
        impostor = next(c for c in clients if roles[c.name]["role"] == "impostor")
        for c in clients:
            target = impostor.user_id if c is not impostor else clients[0].user_id
            if c is impostor and impostor is clients[0]:
                target = clients[1].user_id
            await c.send("impostor.vote", target=target)
        result = await host.wait("impostor.vote_result")
        over = await host.wait("impostor.game_over")
        print(
            f"  eliminated: {result['data']['eliminated']['nickname']},"
            f" winner: {over['data']['winner']}"
        )
        await host.send("game.next")
        finished = await clients[3].wait("game.finished")
        assert finished["data"]["kind"] == "impostor"
        print(
            "  leaderboard:", [(r["nickname"], r["score"]) for r in finished["data"]["leaderboard"]]
        )
        assert voting["data"]["candidates"]
    print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
