"""Tunnel Manager for FaceAttend.

Manages the secure permanent HTTPS tunnel pointing to localhost:5000,
allowing students to access the portal from any smartphone, laptop, or
network worldwide without the URL ever changing.
"""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import threading
import time

PERMANENT_STATIC_URL = "https://harddisk-calcium-petite.ngrok-free.dev"

_TUNNEL_PROC: subprocess.Popen | None = None
_GLOBAL_URL: str | None = None
_IS_STARTING: bool = False


def find_ngrok_path() -> str | None:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(current_dir, ".."))
    candidates = [
        os.path.join(current_dir, "ngrok.exe"),
        os.path.join(root_dir, "ngrok.exe"),
        "ngrok.exe",
        "ngrok",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def find_cloudflared_path() -> str | None:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(current_dir, ".."))
    candidates = [
        os.path.join(current_dir, "cloudflared.exe"),
        os.path.join(root_dir, "cloudflared.exe"),
        "cloudflared.exe",
        "cloudflared",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def get_global_url() -> str | None:
    global _GLOBAL_URL
    return _GLOBAL_URL or PERMANENT_STATIC_URL


def _sync_frontend_backend_url(url: str) -> None:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    files_to_update = [
        os.path.join(current_dir, "student.html"),
        os.path.join(current_dir, "app.js"),
    ]
    for fp in files_to_update:
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            updated = re.sub(
                r"DEFAULT_LAPTOP_BACKEND\s*=\s*['\"][^'\"]*['\"];",
                f"DEFAULT_LAPTOP_BACKEND = '{url}';",
                content,
            )
            if updated != content:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(updated)
                print(f"  [✓] Updated live backend in {os.path.basename(fp)} -> {url}", flush=True)
        except Exception as exc:
            print(f"  [!] Warning syncing backend url to {os.path.basename(fp)}: {exc}", flush=True)


def start_tunnel_async(port: int = 5000) -> None:
    global _IS_STARTING, _GLOBAL_URL
    if _GLOBAL_URL and _TUNNEL_PROC and _TUNNEL_PROC.poll() is None:
        return
    if _IS_STARTING:
        return

    _IS_STARTING = True

    def _worker():
        global _TUNNEL_PROC, _GLOBAL_URL, _IS_STARTING
        ngrok_path = find_ngrok_path()

        if ngrok_path:
            try:
                # Terminate any stray ngrok process to prevent ERR_NGROK_334
                subprocess.run(
                    ["taskkill", "/F", "/IM", "ngrok.exe"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                time.sleep(0.5)

                cmd = [
                    ngrok_path,
                    "http",
                    str(port),
                    "--url",
                    PERMANENT_STATIC_URL,
                    "--log=stdout",
                ]
                _TUNNEL_PROC = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                _GLOBAL_URL = PERMANENT_STATIC_URL
                _IS_STARTING = False
                _sync_frontend_backend_url(_GLOBAL_URL)

                print("\n" + "=" * 68, flush=True)
                print("  🌟 PERMANENT STATIC ONLINE LINK ACTIVE (4G / 5G / Home Wi-Fi)", flush=True)
                print("  This link will NEVER change even after restarting!", flush=True)
                print(f"  👉 {_GLOBAL_URL}/student", flush=True)
                print("=" * 68 + "\n", flush=True)

                # Keep reading pipe so buffer doesn't stall
                for line in iter(_TUNNEL_PROC.stdout.readline, ""):
                    if "ERR_NGROK" in line:
                        print(f"  [!] ngrok note: {line.strip()}", flush=True)
                return
            except Exception as exc:
                print(f"[!] ngrok start error: {exc}. Trying cloudflared fallback...", flush=True)

        # Fallback to cloudflared if ngrok fails
        cf_path = find_cloudflared_path()
        if not cf_path:
            print("[!] Note: Neither ngrok nor cloudflared found. Global tunnel offline.")
            _IS_STARTING = False
            return

        try:
            cmd = [
                cf_path,
                "tunnel",
                "--url",
                f"http://127.0.0.1:{port}",
                "--no-autoupdate",
            ]
            _TUNNEL_PROC = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            found_url = False
            for line in iter(_TUNNEL_PROC.stdout.readline, ""):
                if not found_url:
                    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                    if m:
                        _GLOBAL_URL = m.group(0).strip()
                        found_url = True
                        _IS_STARTING = False
                        _sync_frontend_backend_url(_GLOBAL_URL)
                        print("\n" + "=" * 64, flush=True)
                        print("  🌍 FALLBACK CLOUDFLARE LINK ACTIVE", flush=True)
                        print(f"  👉 {_GLOBAL_URL}/student", flush=True)
                        print("=" * 64 + "\n", flush=True)
        except Exception as exc:
            print(f"[!] Tunnel fallback warning: {exc}", flush=True)
        finally:
            _IS_STARTING = False

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def restart_tunnel(port: int = 5000) -> None:
    """Forces termination and restarts the tunnel on the specified port."""
    stop_tunnel()
    time.sleep(1)
    start_tunnel_async(port)


def stop_tunnel() -> None:
    global _TUNNEL_PROC, _GLOBAL_URL, _IS_STARTING
    if _TUNNEL_PROC and _TUNNEL_PROC.poll() is None:
        try:
            _TUNNEL_PROC.terminate()
            _TUNNEL_PROC.wait(timeout=2)
        except Exception:
            try:
                _TUNNEL_PROC.kill()
            except Exception:
                pass
    _TUNNEL_PROC = None
    _GLOBAL_URL = None
    _IS_STARTING = False


atexit.register(stop_tunnel)
