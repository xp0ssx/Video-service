"""Check the running production entry point using only the Python standard library."""

import argparse
import json
import re
from urllib.error import HTTPError
from urllib.request import urlopen


def check_http(base_url: str) -> None:
    def read(path: str) -> tuple[bytes, str]:
        with urlopen(base_url.rstrip("/") + path, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"Unexpected HTTP status at {path}")
            return response.read(), response.headers.get("Content-Type", "")

    content, content_type = read("/")
    html = content.decode()
    if "text/html" not in content_type or "<title>Video-service</title>" not in html:
        raise RuntimeError("Frontend index was not served")
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    if not assets:
        raise RuntimeError("Production frontend assets were not found")
    for asset in assets:
        _, asset_type = read(asset)
        if "text/html" in asset_type:
            raise RuntimeError("A missing production asset fell through to the SPA")
    print("OK frontend: production HTML, JavaScript and CSS served")

    docs, _ = read("/docs")
    if b"SwaggerUIBundle" not in docs:
        raise RuntimeError("Swagger page was not served")
    schema, _ = read("/openapi.json")
    if json.loads(schema)["info"]["title"] != "Video-service API":
        raise RuntimeError("OpenAPI was not proxied to FastAPI")
    print("OK Swagger /docs and OpenAPI /openapi.json")

    live, _ = read("/health/live")
    if json.loads(live).get("status") != "ok":
        raise RuntimeError("Liveness failed")
    ready, _ = read("/health/ready")
    if json.loads(ready).get("checks") != {"database": "ok", "storage": "ok", "worker": "ok"}:
        raise RuntimeError("PostgreSQL/S3/worker readiness failed")
    print("OK liveness and real PostgreSQL/migration/S3/worker readiness")

    try:
        read("/api/__missing__")
    except HTTPError as error:
        if error.code != 404 or "application/json" not in error.headers.get("Content-Type", ""):
            raise RuntimeError("Unknown API path was not handled by FastAPI") from None
    else:
        raise RuntimeError("Unknown API path fell through to the SPA")
    print("OK unknown API path returns JSON 404")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    check_http(parser.parse_args().base_url)
