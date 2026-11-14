"""Real same-origin HTTP auth smoke; creates a synthetic account without logging credentials."""

import json
import secrets
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    base = "http://localhost:8080"
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    name = "http_" + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)

    def call(path, body=None, csrf=None):
        headers = {"Origin": base}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-CSRF-Token"] = csrf
        request = Request(
            base + "/api/auth/" + path,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            response = opener.open(request)
        except HTTPError as error:
            response = error
        content = response.read()
        return response.status, json.loads(content) if content and content.startswith(
            b"{"
        ) else None

    status, result = call(
        "register", {"username": name, "email": name + "@example.com", "password": password}
    )
    assert status == 201, status
    assert call("session")[0] == 200
    assert call("logout", {})[0] == 403
    assert call("logout", {}, result["csrf_token"])[0] == 204
    assert call("session")[0] == 401
    status, result = call("login", {"email": name + "@example.com", "password": password})
    assert status == 200, status
    assert call("logout", {}, result["csrf_token"])[0] == 204
    print("OK real HTTP register/session/CSRF/login/logout")
    statuses = [
        call("login", {"email": "absent@example.com", "password": password})[0] for _ in range(6)
    ]
    assert 429 in statuses, statuses
    print("OK Nginx login rate limit: 429")


if __name__ == "__main__":
    main()
