import asyncio
import json
import math
import random
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import TypedDict

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketDisconnect

# ── Constants ─────────────────────────────────────────────────────────────────

WORLD_SIZE = 2000
MAX_PLAYERS = 25
GAME_DURATION = 600
ORB_COUNT = 40
ORB_MIN = 30
ORB_RESPAWN_DELAY = 3.0
TICK_INTERVAL = 0.1
TIMER_INTERVAL = 1.0
MAX_SPEED = 350  # px/s sprint + buffer
SINGLE_PLAYER_END_DELAY = 5.0
RECONNECT_GRACE = 30.0
MAX_MOVE_RATE = 30
MAX_COLLECT_RATE = 10
MIN_ORB_DIST = 120        # px minimum distance between orbs
COLLECT_RADIUS = 40       # px server-side hitbox (wider = lag-tolerant)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
ALLOWED_ORIGINS = None  # None = allow all origins (LAN/ngrok friendly)

COLORS = [
    "#FF4ECD","#00CFFF","#FFD700","#7FFF00","#FF6B35",
    "#A855F7","#06FFA5","#FF3366","#00D4FF","#FFB800",
    "#FF1744","#00E5FF","#69FF47","#FF6D00","#D500F9",
    "#00BFA5","#FF4081","#FFEA00","#00B0FF","#76FF03",
    "#FF6E40","#40C4FF","#B2FF59","#FF9100","#EA80FC",
]

ADJECTIVES = [
    "Neon","Frost","Blaze","Crimson","Void","Solar","Lunar","Quantum",
    "Hyper","Turbo","Cyber","Ghost","Storm","Shadow","Flux","Astro",
    "Cosmic","Static","Phantom","Ultra","Apex","Venom","Prism","Stealth",
]
NOUNS = [
    "Vortex","Pulse","Comet","Atlas","Echo","Orion","Nova","Zephyr",
    "Nexus","Titan","Cipher","Arc","Lyra","Drift","Pixel","Flash",
    "Spark","Blaze","Ghost","Vega",
]

ORB_SPECS = [
    {"type": "common",    "value": 1,  "weight": 70, "color": "#FFD700"},
    {"type": "rare",      "value": 3,  "weight": 20, "color": "#00CFFF"},
    {"type": "legendary", "value": 10, "weight": 10, "color": "#FF4ECD"},
]

# ── Types ─────────────────────────────────────────────────────────────────────

class GameState(StrEnum):
    WAITING = "waiting"
    ACTIVE  = "active"
    ENDED   = "ended"

class PlayerState(TypedDict):
    id: str; name: str; color: str
    x: float; y: float; score: int
    last_move_time: float

# ── Session (single global instance) ─────────────────────────────────────────

class Session:
    def __init__(self):
        self.state: GameState = GameState.WAITING
        self.time_remaining: int = GAME_DURATION
        self.orbs: dict[str, dict] = {}
        self.players: dict[str, PlayerState] = {}
        self.color_idx: int = 0
        self.resetting: bool = False
        self.chat_history: deque[dict] = deque(maxlen=20)
        self.player_last_chat: dict[str, float] = {}

    def next_color(self) -> str:
        c = COLORS[self.color_idx % len(COLORS)]
        self.color_idx += 1
        return c

    def next_name(self) -> str:
        used = {p["name"] for p in self.players.values()}
        while True:
            name = random.choice(ADJECTIVES) + random.choice(NOUNS)
            if name not in used:
                return name

session = Session()

active: dict[str, WebSocket] = {}
lobby: list[tuple[str, WebSocket]] = []
_rate: dict[str, dict] = {}
_respawn_tasks: set[asyncio.Task] = set()
_solo_task: asyncio.Task | None = None
_grace: dict[str, tuple[int, float, float, float]] = {}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_spawn_pos() -> tuple[float, float]:
    """Rejection sampling — pick position ≥ MIN_ORB_DIST from all live orbs."""
    best: tuple[float, float] = (random.uniform(100, WORLD_SIZE - 100), random.uniform(100, WORLD_SIZE - 100))
    best_d = -1.0
    for _ in range(25):
        cx = random.uniform(100, WORLD_SIZE - 100)
        cy = random.uniform(100, WORLD_SIZE - 100)
        if not session.orbs:
            return cx, cy
        min_d = min(
            (cx - o["x"]) ** 2 + (cy - o["y"]) ** 2
            for o in session.orbs.values()
        )
        if min_d >= MIN_ORB_DIST * MIN_ORB_DIST:
            return cx, cy
        if min_d > best_d:
            best, best_d = (cx, cy), min_d
    return best

def spawn_orb() -> dict:
    weights = [s["weight"] for s in ORB_SPECS]
    spec = random.choices(ORB_SPECS, weights=weights)[0]
    x, y = _find_spawn_pos()
    return {
        "id":    str(uuid.uuid4()),
        "x":     x,
        "y":     y,
        "type":  spec["type"],
        "value": spec["value"],
        "color": spec["color"],
    }

def _init_orbs() -> None:
    """Jittered grid — one orb per cell, no clustering."""
    session.orbs.clear()
    cols = math.ceil(math.sqrt(ORB_COUNT))
    rows = math.ceil(ORB_COUNT / cols)
    cw = (WORLD_SIZE - 200) / cols
    ch = (WORLD_SIZE - 200) / rows
    positions: list[tuple[float, float]] = []
    for row in range(rows):
        for col in range(cols):
            if len(positions) >= ORB_COUNT:
                break
            px = 100 + col * cw + random.uniform(cw * 0.1, cw * 0.9)
            py = 100 + row * ch + random.uniform(ch * 0.1, ch * 0.9)
            positions.append((px, py))
    random.shuffle(positions)
    for px, py in positions[:ORB_COUNT]:
        weights = [s["weight"] for s in ORB_SPECS]
        spec = random.choices(ORB_SPECS, weights=weights)[0]
        o: dict = {
            "id":    str(uuid.uuid4()),
            "x":     px, "y": py,
            "type":  spec["type"],
            "value": spec["value"],
            "color": spec["color"],
        }
        session.orbs[o["id"]] = o

async def broadcast(message: dict) -> None:
    if not active:
        return
    payload = json.dumps(message)
    results = await asyncio.gather(
        *[ws.send_text(payload) for ws in active.values()],
        return_exceptions=True,
    )
    dead = [pid for pid, r in zip(list(active.keys()), results) if isinstance(r, Exception)]
    for pid in dead:
        active.pop(pid, None)

async def broadcast_lobby(message: dict) -> None:
    payload = json.dumps(message)
    dead_idx = []
    for i, (pid, ws) in enumerate(lobby):
        try:
            await ws.send_text(payload)
        except Exception:
            dead_idx.append(i)
    for i in reversed(dead_idx):
        lobby.pop(i)

def _check_rate(player_id: str, msg_type: str) -> bool:
    now = time.monotonic()
    bucket = _rate.setdefault(player_id, {})
    count, window = bucket.get(msg_type, (0, now))
    if now - window >= 1.0:
        bucket[msg_type] = (1, now)
        return True
    limit = MAX_MOVE_RATE if msg_type == "move" else MAX_COLLECT_RATE
    if count >= limit:
        return False
    bucket[msg_type] = (count + 1, window)
    return True

def _players_list() -> list[dict]:
    return [
        {k: p[k] for k in ("id", "name", "color", "x", "y", "score")}
        for p in session.players.values()
    ]

# ── Player lifecycle ──────────────────────────────────────────────────────────

async def join_game(player_id: str, ws: WebSocket) -> None:
    grace = _grace.pop(player_id, None)
    if grace and time.monotonic() < grace[3]:
        score, x, y, _ = grace
        color = session.next_color()
        name  = session.next_name()
    else:
        score = 0
        x = random.uniform(100, WORLD_SIZE - 100)
        y = random.uniform(100, WORLD_SIZE - 100)
        color = session.next_color()
        name  = session.next_name()

    player: PlayerState = {
        "id": player_id, "name": name, "color": color,
        "x": x, "y": y, "score": score,
        "last_move_time": time.monotonic(),
    }
    session.players[player_id] = player
    active[player_id] = ws

    await ws.send_text(json.dumps({
        "type": "init", "player_id": player_id,
        "name": name, "color": color,
        "world_size": WORLD_SIZE,
        "players": _players_list(),
        "orbs": list(session.orbs.values()),
        "game_state": str(session.state),
        "time_remaining": session.time_remaining,
    }))

    if session.chat_history:
        await ws.send_text(json.dumps({
            "type": "chat_history",
            "messages": list(session.chat_history),
        }))

    await broadcast({
        "type": "player_joined",
        "player": {k: player[k] for k in ("id", "name", "color", "x", "y", "score")},
    })

    await _maybe_start_game()
    _check_solo()

async def disconnect_player(player_id: str) -> None:
    active.pop(player_id, None)
    player = session.players.pop(player_id, None)
    _rate.pop(player_id, None)
    session.player_last_chat.pop(player_id, None)

    if player and session.state == GameState.ACTIVE:
        _grace[player_id] = (
            player["score"], player["x"], player["y"],
            time.monotonic() + RECONNECT_GRACE,
        )

    while lobby:
        next_pid, next_ws = lobby.pop(0)
        try:
            await promote_player(next_pid, next_ws)
            break
        except Exception:
            continue

    for i, (pid, ws_l) in enumerate(lobby):
        try:
            await ws_l.send_text(json.dumps({"type": "lobby", "position": i + 1}))
        except Exception:
            pass

    _check_solo()

async def promote_player(player_id: str, ws: WebSocket) -> None:
    color = session.next_color()
    name  = session.next_name()
    await ws.send_text(json.dumps({
        "type": "promoted", "player_id": player_id,
        "name": name, "color": color,
    }))
    await join_game(player_id, ws)

def _check_solo() -> None:
    global _solo_task
    if _solo_task and not _solo_task.done():
        _solo_task.cancel()
        _solo_task = None
    if session.state == GameState.ACTIVE and len(active) < 2:
        _solo_task = asyncio.create_task(_solo_end_after())

async def _solo_end_after() -> None:
    await asyncio.sleep(SINGLE_PLAYER_END_DELAY)
    if session.state == GameState.ACTIVE and len(active) < 2:
        await end_game()

# ── Game lifecycle ────────────────────────────────────────────────────────────

async def _maybe_start_game() -> None:
    if session.state == GameState.WAITING and len(active) >= 2:
        await start_game()

async def start_game() -> None:
    session.state = GameState.ACTIVE
    session.time_remaining = GAME_DURATION
    _init_orbs()
    for p in session.players.values():
        p["score"] = 0
    await broadcast({
        "type": "game_start",
        "orbs": list(session.orbs.values()),
        "time_remaining": session.time_remaining,
    })

async def end_game() -> None:
    if session.state == GameState.ENDED:
        return
    session.state = GameState.ENDED
    for t in list(_respawn_tasks):
        t.cancel()
    _respawn_tasks.clear()

    leaderboard = sorted(
        [{"name": p["name"], "score": p["score"], "color": p["color"]}
         for p in session.players.values()],
        key=lambda x: x["score"], reverse=True,
    )
    await broadcast({"type": "game_over", "leaderboard": leaderboard})
    await broadcast_lobby({"type": "game_over", "leaderboard": leaderboard})
    await asyncio.sleep(10)
    await reset_game()

async def reset_game() -> None:
    session.resetting = True
    session.state = GameState.WAITING
    session.time_remaining = GAME_DURATION
    session.orbs.clear()
    session.color_idx = 0
    session.chat_history.clear()
    session.player_last_chat.clear()
    _grace.clear()

    while lobby and len(active) < MAX_PLAYERS:
        next_pid, next_ws = lobby.pop(0)
        try:
            await promote_player(next_pid, next_ws)
        except Exception:
            continue

    session.resetting = False
    await _maybe_start_game()

# ── Message handling ──────────────────────────────────────────────────────────

async def handle_message(player_id: str, data: dict) -> None:
    if session.state != GameState.ACTIVE or session.resetting:
        return
    msg_type = data.get("type")
    if msg_type == "move":
        if _check_rate(player_id, "move"):
            await handle_move(player_id, data)
    elif msg_type == "collect":
        if _check_rate(player_id, "collect"):
            await handle_collect(player_id, data)
    elif msg_type == "chat":
        await handle_chat(player_id, data)

async def handle_chat(player_id: str, data: dict) -> None:
    now = time.time()
    if now - session.player_last_chat.get(player_id, 0) < 1.0:
        return
    text = data.get("text", "")
    if not isinstance(text, str):
        return
    text = text[:100].strip()
    if not text:
        return
    player = session.players.get(player_id)
    if not player:
        return
    session.player_last_chat[player_id] = now
    entry = {"type": "chat", "name": player["name"], "text": text, "t": int(now)}
    session.chat_history.append(entry)
    await broadcast(entry)

async def handle_move(player_id: str, data: dict) -> None:
    player = session.players.get(player_id)
    if not player:
        return
    try:
        x = float(data["x"])
        y = float(data["y"])
    except (KeyError, ValueError, TypeError):
        return

    x = max(0.0, min(float(WORLD_SIZE), x))
    y = max(0.0, min(float(WORLD_SIZE), y))

    # Teleport detection (S1)
    now = time.monotonic()
    elapsed = now - player["last_move_time"]
    max_dist = MAX_SPEED * max(elapsed, 0.05)
    dx = x - player["x"]
    dy = y - player["y"]
    if dx * dx + dy * dy > max_dist * max_dist * 1.5:
        return

    player["x"] = x
    player["y"] = y
    player["last_move_time"] = now

async def handle_collect(player_id: str, data: dict) -> None:
    orb_id = data.get("orb_id")
    if not isinstance(orb_id, str):
        return
    orb = session.orbs.get(orb_id)
    if not orb:
        return
    player = session.players.get(player_id)
    if not player:
        return
    dx = player["x"] - orb["x"]
    dy = player["y"] - orb["y"]
    if dx * dx + dy * dy > COLLECT_RADIUS * COLLECT_RADIUS:
        return

    del session.orbs[orb_id]
    player["score"] += orb["value"]

    await broadcast({
        "type": "orb_collected",
        "orb_id": orb_id,
        "collector_id": player_id,
        "new_score": player["score"],
    })
    _schedule_respawn()

def _schedule_respawn() -> None:
    task = asyncio.create_task(_respawn_after())
    _respawn_tasks.add(task)
    task.add_done_callback(_respawn_tasks.discard)

async def _respawn_after() -> None:
    await asyncio.sleep(ORB_RESPAWN_DELAY)
    if session.state != GameState.ACTIVE:
        return
    # Always replace collected orb 1-for-1; cap at ORB_COUNT ceiling
    if len(session.orbs) < ORB_COUNT:
        new_orb = spawn_orb()
        session.orbs[new_orb["id"]] = new_orb
        await broadcast({"type": "orb_spawned", "orb": new_orb})

# ── Background loops ──────────────────────────────────────────────────────────

async def broadcast_loop() -> None:
    loop = asyncio.get_event_loop()
    try:
        while True:
            t0 = loop.time()
            if session.state == GameState.ACTIVE:
                await broadcast({
                    "type": "state_update",
                    "players": _players_list(),
                    "time_remaining": session.time_remaining,
                })
            elapsed = loop.time() - t0
            await asyncio.sleep(max(0, TICK_INTERVAL - elapsed))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[broadcast_loop] {e}")

async def ticker_loop() -> None:
    try:
        while True:
            await asyncio.sleep(TIMER_INTERVAL)
            if session.state != GameState.ACTIVE:
                continue
            session.time_remaining = max(0, session.time_remaining - 1)
            await broadcast({"type": "tick", "time_remaining": session.time_remaining})
            if session.time_remaining == 0:
                await end_game()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[ticker_loop] {e}")

# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(broadcast_loop()),
        asyncio.create_task(ticker_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok", "players": len(active), "game_state": str(session.state)}

@app.websocket("/ws/{player_id}")
async def websocket_endpoint(ws: WebSocket, player_id: str):
    if not UUID4_RE.match(player_id):
        await ws.close(code=1008)
        return

    if ALLOWED_ORIGINS is not None:
        origin = ws.headers.get("origin", "")
        if origin not in ALLOWED_ORIGINS:
            await ws.close(code=1008)
            return

    await ws.accept()

    # Reconnect: close old connection
    if player_id in active:
        try:
            await active[player_id].close(code=4001)
        except Exception:
            pass
        active.pop(player_id, None)
        session.players.pop(player_id, None)

    if len(active) >= MAX_PLAYERS:
        pos = len(lobby) + 1
        lobby.append((player_id, ws))
        await ws.send_text(json.dumps({
            "type": "lobby", "position": pos,
            "message": f"Game is full. You are #{pos} in the lobby.",
        }))
        try:
            async for _ in ws.iter_text():
                pass
        except WebSocketDisconnect:
            lobby[:] = [(p, w) for p, w in lobby if p != player_id]
            for i, (pid, ws_l) in enumerate(lobby):
                try:
                    await ws_l.send_text(json.dumps({"type": "lobby", "position": i + 1}))
                except Exception:
                    pass
        return

    await join_game(player_id, ws)

    try:
        async for raw in ws.iter_text():
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") not in ("move", "collect", "chat"):
                continue
            await handle_message(player_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        await disconnect_player(player_id)
        await broadcast({"type": "player_left", "player_id": player_id})

# Mount static AFTER all routes
app.mount("/", StaticFiles(directory=".", html=True), name="static")
