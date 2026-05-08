from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config  # validates env vars on import — raises RuntimeError if missing

from routers import sessions, stages, assets, ws
from services import session_svc

app = FastAPI(title="Instomator API", version="0.1.0")


@app.on_event("startup")
async def _archive_legacy_on_startup() -> None:
    try:
        moved = session_svc.archive_legacy_sessions()
        if moved:
            print(f"[startup] archived {moved} legacy sessions to sessions_legacy/")
    except Exception as exc:
        print(f"[startup] legacy session archive failed (non-fatal): {exc}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   "http://localhost:3001", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(stages.router)
app.include_router(assets.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
