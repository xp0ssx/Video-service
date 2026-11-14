"""Write/verify an infrastructure object across a real storage container restart."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.infrastructure.storage import make_s3_client

CONTENT = b"Video-service persistence probe\n" * 128


def run_probe(action: str) -> None:
    settings = get_settings()
    state_path = Path(os.environ.get("S3_PROBE_STATE", "/checks/s3-probe.json"))
    client = make_s3_client(settings)
    try:
        if action == "write" and not state_path.exists():
            state = {
                "bucket": settings.s3_bucket,
                "key": f"checks/persistence/{uuid4().hex}.bin",
                "sha256": hashlib.sha256(CONTENT).hexdigest(),
            }
            client.put_object(Bucket=state["bucket"], Key=state["key"], Body=CONTENT)
            with state_path.open("x") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(state, stream)
        state = json.loads(state_path.read_text())
        response = client.get_object(Bucket=state["bucket"], Key=state["key"])
        with response["Body"] as body:
            digest = hashlib.sha256(body.read()).hexdigest()
        if digest != state["sha256"]:
            raise RuntimeError("S3 persistence probe checksum mismatch")
        print(f"S3 persistence {action}: checksum verified")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["write", "verify"])
    run_probe(parser.parse_args().action)
