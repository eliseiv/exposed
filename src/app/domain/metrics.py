"""Prometheus metrics of the party-game domain (imported via ``DomainRegistry.metrics_module``)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

ws_connections = Gauge("game_ws_connections", "WebSocket connections open on this worker.")
ws_messages_total = Counter(
    "game_ws_messages_total", "Client WebSocket messages by outcome.", ["outcome"]
)
commands_total = Counter(
    "game_commands_total",
    "Room commands by type and outcome (ok | error code).",
    ["type", "outcome"],
)
command_latency_seconds = Histogram(
    "game_command_latency_seconds",
    "Time to apply a room command (lock + load + apply + save + publish).",
    ["type"],
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
rooms_created_total = Counter("game_rooms_created_total", "Rooms created.")
games_finished_total = Counter(
    "game_games_finished_total", "Finished games by kind and reason.", ["kind", "reason"]
)
