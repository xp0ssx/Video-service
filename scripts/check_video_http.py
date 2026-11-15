"""Real HTTP video smoke; optional stdin handshake to test a worker restart.

Run inside the host network namespace with a synthetic MP4 outside the repository.
No credentials are printed or written to disk.
"""

import argparse
import json
import sys
import time
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--restart-handshake", action="store_true")
    args = parser.parse_args()
    jar = CookieJar()
    http = build_opener(HTTPCookieProcessor(jar))
    csrf = ""

    def request(path, method="GET", body=None, raw=False, headers=None):
        extra = {"Origin": args.base_url, "X-CSRF-Token": csrf, **(headers or {})}
        if body is not None:
            extra["Content-Type"] = "application/octet-stream" if raw else "application/json"
            body = body if raw else json.dumps(body).encode()
        with http.open(
            Request(args.base_url + path, data=body, method=method, headers=extra), timeout=20
        ) as response:
            content = response.read()
            return response.status, content

    name = "restart_" + uuid.uuid4().hex[:12]
    _, body = request(
        "/api/auth/register",
        "POST",
        {"username": name, "email": name + "@example.com", "password": uuid.uuid4().hex},
    )
    csrf = json.loads(body)["csrf_token"]
    _, body = request(
        "/api/videos", "POST", {"title": "Worker restart synthetic clip", "visibility": "private"}
    )
    video_id = json.loads(body)["id"]
    code, _ = request(f"/api/videos/{video_id}/source", "PUT", args.file.read_bytes(), True)
    if code != 202:
        raise RuntimeError("Source not accepted")
    if args.restart_handshake:
        claim_deadline = time.monotonic() + 25
        while time.monotonic() < claim_deadline:
            _, state_body = request(f"/api/me/videos/{video_id}")
            state = json.loads(state_body)["status"]
            if state == "processing":
                break
            if state in ("ready", "failed"):
                raise RuntimeError("Use a longer clip to interrupt active processing")
            time.sleep(0.05)
        else:
            raise RuntimeError("Worker did not claim the video")
        print("RESTART_WORKER_NOW", flush=True)
        if sys.stdin.readline().strip() != "continue":
            raise RuntimeError("Restart handshake failed")
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        _, body = request(f"/api/me/videos/{video_id}")
        state = json.loads(body)["status"]
        if state == "ready":
            break
        if state == "failed":
            raise RuntimeError("Processing failed; inspect Studio status")
        time.sleep(0.5)
    else:
        raise RuntimeError("Video readiness timed out")
    code, body = request(f"/api/videos/{video_id}/media", headers={"Range": "bytes=0-31"})
    if code != 206 or len(body) != 32:
        raise RuntimeError("Playback Range failed after worker restart")
    code, body = request(f"/api/videos/{video_id}/thumbnail")
    if code != 200 or not body.startswith(b"\xff\xd8"):
        raise RuntimeError("Thumbnail failed after worker restart")
    request("/api/auth/logout", "POST")
    print("OK real HTTP upload/worker recovery/MP4 Range/JPEG/logout", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Video HTTP check failed; sensitive diagnostics suppressed") from None
