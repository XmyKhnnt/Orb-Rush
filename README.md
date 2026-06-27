# Orb Rush

Realtime multiplayer orb collection game. Players race to grab orbs on a shared 2000×2000 canvas before time runs out.

Built for fun and learning — no serious business here.

## How It Works

- Up to 25 players join a shared arena
- Collect orbs (common, rare, epic) scattered across the map
- 10-minute rounds, top score wins
- Players get auto-generated names like "Neon Vortex" or "Ghost Titan"

## Stack

- **Frontend** — vanilla HTML/CSS/Canvas (`index.html`)
- **Backend** — Python + FastAPI + WebSockets (`server.py`)

## Run Locally

```bash
pip install fastapi uvicorn websockets
uvicorn server:app --reload
```

Open `http://localhost:8000`.

## Deploy on Render

1. Push repo to GitHub
2. New Web Service → connect repo
3. **Build Command:** `pip install fastapi uvicorn`
4. **Start Command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
5. Done

## License

Do whatever you want with it.
