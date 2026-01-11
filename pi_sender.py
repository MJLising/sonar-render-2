#!/usr/bin/env python3
"""
pi_sender.py

Sonar reader + HTTP publisher.

Behavior:
- Reads distance from ultrasonic sensor (TRIG/ECHO).
- Builds JSON payload: {"distance_m", "angle_deg", "timestamp", "quality"}
- POSTs payload to HTTP_PUBLISH_URL (default: https://sonar-render-4.onrender.com/publish)
- Optional lightweight auth via X-Pub-Token header (PUB_TOKEN env var)
- Cleans up GPIO on exit.

Requirements:
- RPi.GPIO
- requests

Installation example (no venv, may require --break-system-packages as you used before):
    pip3 install requests --break-system-packages
"""

import os
import time
import json
import logging
import datetime
import signal
import sys

try:
    import RPi.GPIO as GPIO
except Exception as e:
    # If running on non-Pi machine for testing, re-raise with clear message
    raise ImportError("RPi.GPIO not available. Run this on your Raspberry Pi or install RPi.GPIO.") from e

import requests

# ---------- Configuration (via environment variables) ----------
HTTP_PUBLISH_URL = os.environ.get("HTTP_PUBLISH_URL", "https://sonar-render-2-1.onrender.com/publish")
PUB_TOKEN = os.environ.get("PUB_TOKEN", "")              # optional token to include in X-Pub-Token header
SAMPLE_INTERVAL = float(os.environ.get("SAMPLE_INTERVAL", "0.5"))  # seconds between readings
TRIG = int(os.environ.get("TRIG_GPIO", "5"))             # BCM pin for TRIG (default 5)
ECHO = int(os.environ.get("ECHO_GPIO", "6"))             # BCM pin for ECHO (default 6)
SOUND_SPEED = float(os.environ.get("SOUND_SPEED", "343.0"))  # m/s (change to 1482 for underwater)
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "5.0"))   # seconds for POST timeout
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "10"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pi_sender")

# ---------- GPIO setup ----------
GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)
GPIO.output(TRIG, False)

# Make sure trigger is low for a short while before starting
time.sleep(0.05)

# Graceful shutdown
running = True
def handle_sigterm(signum, frame):
    global running
    log.info("Received termination signal, stopping...")
    running = False

signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)

# ---------- Sonar read function ----------
def read_distance():
    """
    Trigger the ultrasonic sensor and measure the echo time.
    Returns distance in meters (float) or None on timeout/no-echo.
    """
    # Trigger pulse
    GPIO.output(TRIG, True)
    time.sleep(0.00002)  # 20us
    GPIO.output(TRIG, False)

    start = time.perf_counter()
    timeout = start + 0.05  # 50ms timeout to wait for echo start
    # Wait for echo to go high
    while GPIO.input(ECHO) == 0 and time.perf_counter() < timeout:
        start = time.perf_counter()
    if time.perf_counter() >= timeout:
        return None

    # Wait for echo to go low
    end = time.perf_counter()
    timeout = end + 0.05
    while GPIO.input(ECHO) == 1 and time.perf_counter() < timeout:
        end = time.perf_counter()
    if time.perf_counter() >= timeout:
        return None

    pulse = end - start
    distance_m = (pulse * SOUND_SPEED) / 2.0
    return distance_m

# ---------- Publisher helper ----------
def publish_payload(payload):
    headers = {"Content-Type": "application/json"}
    if PUB_TOKEN:
        headers["x-pub-token"] = PUB_TOKEN
    try:
        r = requests.post(HTTP_PUBLISH_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        # treat non-2xx as error but log it
        if r.status_code >= 200 and r.status_code < 300:
            log.debug("Published ok: %s", r.status_code)
            return True
        else:
            log.warning("Publish returned %s: %s", r.status_code, r.text[:200])
            return False
    except requests.RequestException as e:
        log.warning("HTTP publish exception: %s", e)
        return False

# ---------- Main loop ----------
def main():
    log.info("Starting pi_sender. Publishing to: %s", HTTP_PUBLISH_URL)
    consecutive_failures = 0

    try:
        while running:
            d = read_distance()
            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            angle_deg = 0.0  # change if you have a rotating mount / angle sensor

            payload = {
                "distance_m": None if d is None else round(d, 3),
                "angle_deg": angle_deg,
                "timestamp": timestamp,
                "quality": "ok" if d is not None else "no_echo"
            }

            # Try to publish
            ok = publish_payload(payload)
            if ok:
                consecutive_failures = 0
                log.info("Published distance=%s m", payload["distance_m"] if payload["distance_m"] is not None else "None")
            else:
                consecutive_failures += 1
                log.warning("Publish failed (%d/%d).", consecutive_failures, MAX_CONSECUTIVE_FAILURES)

            # If many consecutive failures, back off a bit
            if consecutive_failures >= 3:
                # small backoff
                time.sleep(min(5, consecutive_failures))
            else:
                time.sleep(SAMPLE_INTERVAL)

            # If too many failures in a row, still keep trying but avoid tight loop
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("Maximum consecutive failures reached (%d). Sleeping 30s before retrying.", MAX_CONSECUTIVE_FAILURES)
                time.sleep(30)
                consecutive_failures = 0

    finally:
        # cleanup GPIO on exit
        try:
            GPIO.cleanup()
            log.info("GPIO cleaned up.")
        except Exception:
            pass
        log.info("pi_sender exiting.")

if __name__ == "__main__":
    main()













<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Live Sonar Viewer</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    html,body { height:100%; margin:0; background:#000; color:#0f0; font-family: Inter, Roboto, monospace; }
    #ui { position:fixed; left:10px; top:10px; color:#0f0; font-size:14px; z-index:3; }
    #cwrap { display:flex; align-items:center; justify-content:center; height:100vh; }
    canvas { background:transparent; max-width:100%; height:auto; display:block; }
    .badge { background: rgba(0,0,0,0.6); padding:6px 8px; border-radius:6px; border:1px solid rgba(0,255,0,0.12); }
    #legend { position:fixed; right:10px; top:10px; color:#0f0; font-size:13px; }
  </style>
</head>
<body>
  <div id="ui" class="badge">Status: <span id="status">disconnected</span> &nbsp; | &nbsp; Range: <span id="range">—</span> m</div>
  <div id="legend" class="badge">Red = detection / pulse = sudden change</div>
  <div id="cwrap">
    <canvas id="c" width="1000" height="700"></canvas>
  </div>

<script>
/* ---------- CONFIG ---------- */
const SERVER = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/viewer';
const MAX_RANGE_M = 6.0;                // max measurable/render range (meters)
const ARC_COUNT = 6;                    // number of Wi-Fi arcs (concentric semicircles)
const OUTER_MARGIN = 0.85;              // fraction of canvas half-width used for outermost arc
const SUDDEN_CHANGE_THRESHOLD_M = 0.35; // threshold (m) to declare a sudden change / fish event
const DETECTION_FADE_MS = 2000;         // how long a detection marker stays (ms)
const PULSE_DURATION_MS = 1200;         // pulse animation length for fish events
const KEEP_HISTORY = 20;                // keep last N detections for trail

/* ---------- STATE ---------- */
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const statusSpan = document.getElementById('status');
const rangeSpan = document.getElementById('range');

let ws = null;
let lastDistance = null;
let lastTimestamp = null;
let detections = []; // {x,y,radius_m,distance_m,angle_deg,timestamp,type: 'normal'|'pulse'}
let wsConnected = false;

/* ---------- UTILS ---------- */
function nowMs(){ return Date.now(); }
function clamp(v,a,b){ return Math.max(a, Math.min(b,v)); }

/* ---------- DRAW ---------- */
function drawBackground(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  // centered top origin
  const cx = canvas.width/2;
  const cy = canvas.height * 0.18; // origin near top
  const halfWidth = canvas.width * 0.5;
  const maxRadiusPx = halfWidth * OUTER_MARGIN;

  // draw faint central marker (origin)
  ctx.beginPath();
  ctx.fillStyle = "#0f0";
  ctx.arc(cx, cy, 4, 0, Math.PI*2);
  ctx.fill();

  // draw arcs (inverted Wi-Fi: semicircles opening downward, angle 0..PI)
  for(let i=ARC_COUNT; i>=1; --i){
    const t = i / ARC_COUNT;
    const r = t * maxRadiusPx;
    // stroke style: varying alpha
    ctx.beginPath();
    ctx.lineWidth = 3;
    const alpha = 0.14 + 0.5*(t*0.7);
    ctx.strokeStyle = `rgba(0,255,0,${alpha})`;
    // arc from 0 to PI (downwards semicircle)
    ctx.arc(cx, cy, r, 0, Math.PI);
    ctx.stroke();

    // inner soft fill for larger arcs
    if(i === ARC_COUNT){
      const g = ctx.createRadialGradient(cx, cy, 10, cx, cy + r, r);
      g.addColorStop(0, "rgba(0,255,0,0.06)");
      g.addColorStop(1, "rgba(0,0,0,0.0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI);
      ctx.lineTo(cx, cy);
      ctx.closePath();
      ctx.fill();
    }
  }

  // draw tick labels at each arc (distance)
  ctx.fillStyle = "#0f0";
  ctx.font = "13px monospace";
  ctx.textAlign = "center";
  for(let i=1;i<=ARC_COUNT;i++){
    const t = i / ARC_COUNT;
    const r = t * maxRadiusPx;
    const dist = (t * MAX_RANGE_M).toFixed(2);
    ctx.fillText(`${dist} m`, cx, cy + r + 16); // below arc
  }

  return {cx,cy,maxRadiusPx};
}

function render(){
  const {cx,cy,maxRadiusPx} = drawBackground();

  // draw historical detections (fade by age)
  const now = nowMs();
  for(let i=0;i<detections.length;i++){
    const d = detections[i];
    const age = now - d.timestamp;
    if(age > DETECTION_FADE_MS + PULSE_DURATION_MS) continue;
    const fade = 1 - clamp(age / (DETECTION_FADE_MS + PULSE_DURATION_MS), 0, 1);
    if(d.type === 'pulse'){
      // pulsing ring
      const t = clamp((now - d.timestamp) / PULSE_DURATION_MS, 0, 1);
      const pulseR = (1 + 0.5 * t) * (d.radius_px || 6);
      ctx.beginPath();
      ctx.lineWidth = 3 + 3*(1 - t);
      ctx.strokeStyle = `rgba(255,0,0,${0.9 * fade})`;
      ctx.arc(d.x, d.y, pulseR, 0, Math.PI*2);
      ctx.stroke();
      // small center
      ctx.beginPath();
      ctx.fillStyle = `rgba(255,0,0,${0.95 * fade})`;
      ctx.arc(d.x, d.y, 6, 0, Math.PI*2);
      ctx.fill();
    } else {
      // normal detection (small dot + fading label)
      ctx.beginPath();
      ctx.fillStyle = `rgba(255,0,0,${0.95 * fade})`;
      ctx.arc(d.x, d.y, 5, 0, Math.PI*2);
      ctx.fill();
      if(fade > 0.6){
        ctx.fillStyle = `rgba(0,255,0,${0.7 * fade})`;
        ctx.font = "12px monospace";
        ctx.fillText(`${d.distance_m.toFixed(2)} m`, d.x + 12, d.y - 8);
      }
    }
  }

  // request next frame
  requestAnimationFrame(render);
}

/* ---------- MAPPING helpers ---------- */
function distanceToRadiusPx(distance_m, maxRadiusPx){
  const frac = clamp(distance_m / MAX_RANGE_M, 0, 1);
  return frac * maxRadiusPx;
}

function polarToCanvas(cx, cy, radius_px, angle_deg){
  // angle_deg is relative to center line (0 = center-down). Convert: 0 corresponds to 90deg = PI/2
  const angle_rad = (Math.PI/2) + (angle_deg * Math.PI/180.0);
  const x = cx + radius_px * Math.cos(angle_rad);
  const y = cy + radius_px * Math.sin(angle_rad);
  return {x,y};
}

/* ---------- WebSocket and message handling ---------- */
function handlePayload(payload){
  // expected payload: {distance_m, angle_deg, timestamp, quality}
  try {
    const distance_m = (payload.distance_m === null || payload.distance_m === undefined) ? null : Number(payload.distance_m);
    const angle_deg = (payload.angle_deg === undefined || payload.angle_deg === null) ? 0 : Number(payload.angle_deg);
    const ts = payload.timestamp ? Date.parse(payload.timestamp) : nowMs();

    // update UI
    rangeSpan.textContent = MAX_RANGE_M.toFixed(2);

    // compute location
    // we need current canvas mapping (cx,cy,maxRadiusPx)
    const {cx,cy,maxRadiusPx} = drawBackground(); // drawBackground also returns geometry (but note: render loop will redraw)
    if(distance_m !== null){
      const radius_px = distanceToRadiusPx(distance_m, maxRadiusPx);
      const pos = polarToCanvas(cx, cy, radius_px, angle_deg);
      // check sudden change (fish event)
      let isPulse = false;
      if(lastDistance !== null){
        const delta = Math.abs(distance_m - lastDistance);
        if(delta >= SUDDEN_CHANGE_THRESHOLD_M){
          isPulse = true;
        }
      }
      lastDistance = distance_m;
      lastTimestamp = ts;

      const dobj = {
        x: pos.x, y: pos.y,
        radius_px,
        distance_m,
        angle_deg,
        timestamp: nowMs(),
        type: isPulse ? 'pulse' : 'normal'
      };

      detections.push(dobj);
      // keep history short
      if(detections.length > KEEP_HISTORY) detections.shift();
    } else {
      // no echo; treat as a special pulse at outer ring
      lastDistance = null;
      const {cx,cy,maxRadiusPx} = drawBackground();
      const pos = polarToCanvas(cx, cy, maxRadiusPx, 0);
      detections.push({x:pos.x,y:pos.y,distance_m:null,timestamp:nowMs(),type:'pulse',radius_px:10});
      if(detections.length > KEEP_HISTORY) detections.shift();
    }
  } catch(e){
    console.error("Bad payload:", e, payload);
  }
}

function connectWS(){
  try {
    ws = new WebSocket(SERVER);
  } catch(e){
    console.error("WS construct error", e);
    statusSpan.textContent = "error";
    setTimeout(connectWS, 3000);
    return;
  }

  ws.onopen = () => {
    wsConnected = true;
    statusSpan.textContent = "connected";
    // keepalive (simple)
    setInterval(()=>{ try{ ws.send('ping'); } catch(e){} }, 20000);
  };

  ws.onmessage = (ev) => {
    // ignore manual pings
    const s = ev.data;
    // try parse JSON
    try {
      const payload = JSON.parse(s);
      handlePayload(payload);
    } catch(e) {
      // not JSON — ignore
    }
  };

  ws.onclose = () => {
    wsConnected = false;
    statusSpan.textContent = "disconnected";
    setTimeout(connectWS, 2000);
  };

  ws.onerror = (e) => {
    wsConnected = false;
    statusSpan.textContent = "error";
    console.error("WebSocket error", e);
    ws.close();
  };
}

/* ---------- RESIZE handling ---------- */
function resizeCanvas(){
  // choose a responsive size keeping aspect ratio
  const parent = canvas.parentElement;
  const w = Math.min(window.innerWidth * 0.98, 1200);
  const h = Math.min(window.innerHeight * 0.9, 900);
  canvas.width = Math.round(w);
  canvas.height = Math.round(h);
}
window.addEventListener('resize', resizeCanvas);

/* ---------- START ---------- */
resizeCanvas();
render();
connectWS();

</script>
</body>
</html>











# server.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi import Request, HTTPException
import uvicorn
import os   
import asyncio
import json
from typing import List

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

class ConnectionManager:
    def __init__(self):
        self.viewers: List[WebSocket] = []
        self.publishers: List[WebSocket] = []
        self.lock = asyncio.Lock()

    async def connect_viewer(self, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.viewers.append(ws)

    async def connect_publisher(self, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.publishers.append(ws)

    async def disconnect(self, ws: WebSocket):
        async with self.lock:
            if ws in self.viewers: self.viewers.remove(ws)
            if ws in self.publishers: self.publishers.remove(ws)

    async def broadcast_to_viewers(self, message: str):
        async with self.lock:
            for v in list(self.viewers):
                try:
                    await v.send_text(message)
                except Exception:
                    await self.disconnect(v)

manager = ConnectionManager()

@app.post("/publish")
async def publish(request: Request):
    """
    Accept JSON from HTTP POST and broadcast to all connected viewers.
    Return {"status":"ok"} on success.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    # OPTIONAL: very basic validation (distance_m present or null)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")

    # Broadcast to viewers (existing manager.broadcast_to_viewers)
    # ensure payload is JSON string
    await manager.broadcast_to_viewers(json.dumps(payload))
    return {"status": "ok"}

@app.get("/")
async def index():
    return HTMLResponse(open("static/index.html","r",encoding="utf-8").read())

@app.websocket("/ws/publisher")
async def websocket_publisher(ws: WebSocket):
    await manager.connect_publisher(ws)
    try:
        while True:
            data = await ws.receive_text()
            await manager.broadcast_to_viewers(data)
    except WebSocketDisconnect:
        await manager.disconnect(ws)

@app.websocket("/ws/viewer")
async def websocket_viewer(ws: WebSocket):
    await manager.connect_viewer(ws)
    try:
        while True:
            # viewers may send keepalive pings; this keeps the connection open
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


