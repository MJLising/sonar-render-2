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
HTTP_PUBLISH_URL = os.environ.get("HTTP_PUBLISH_URL", "https://sonar-render-2-1.onrender.com")
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
