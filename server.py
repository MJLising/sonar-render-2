#!/usr/bin/env python3
"""
server.py — FastAPI backend for sonar-render system
POST /publish receives sensor updates from the Pi
GET / returns the web viewer (static/index.html)
WebSocket /ws/viewer broadcasts updates to all viewers
"""

import os
import json
import logging
from typing import List

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI()

# Allow browser clients and Pi sender
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Serve static files correctly
# -----------------------------
# All static files (index.html, JS, CSS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve index.html at root
@app.get("/", response_class=FileResponse)
def root():
    return FileResponse("static/index.html")

# -----------------------------
# Shared viewer connection manager
# -----------------------------
class ViewerManager:
    def __init__(self):
        self.active_viewers: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_viewers.append(ws)
        logging.info(f"Viewer connected. Total: {len(self.active_viewers)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active_viewers:
            self.active_viewers.remove(ws)
        logging.info(f"Viewer disconnected. Total: {len(self.active_viewers)}")

    async def broadcast(self, message: str):
        """Send data to all connected viewer websockets."""
        dead = []
        for ws in self.active_viewers:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        # Remove broken sockets
        for ws in dead:
            self.disconnect(ws)

manager = ViewerManager()

# -----------------------------
# Data model for POST /publish
# -----------------------------
class SonarData(BaseModel):
    distance_m: float
    angle_deg: float
    timestamp: str


# -----------------------------
# POST endpoint for Pi sender
# -----------------------------
@app.post("/publish")
async def publish(request: Request, payload: SonarData):
    """Receives JSON from Pi and broadcasts to all viewers."""
    token = request.headers.get("x-pub-token")
    required = os.environ.get("PUBLISH_TOKEN")

    if not required:
        logging.warning("PUBLISH_TOKEN missing — accepting all publishers.")

    if required and token != required:
        return {"error": "unauthorized"}

    msg = payload.dict()
    logging.info(f"Publish received: {msg}")

    await manager.broadcast(json.dumps(msg))
    return {"status": "ok"}


# -----------------------------
# WebSocket for viewer clients
# -----------------------------
@app.websocket("/ws/viewer")
async def ws_viewer(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # viewer rarely sends messages, but keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# -----------------------------
# If running locally: uvicorn server.py
# -----------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
