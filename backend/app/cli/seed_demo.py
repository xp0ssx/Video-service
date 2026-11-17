"""Resumable demo fixtures through existing services and the real HTTP upload/worker pipeline."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.session import Database
from app.infrastructure.storage import Storage
from app.modules.admin.schemas import RoleChange
from app.modules.admin.service import AdminService
from app.modules.auth.models import Channel, User
from app.modules.auth.schemas import Register
from app.modules.auth.service import AuthService
from app.modules.channels.schemas import Metadata
from app.modules.channels.service import ChannelService
from app.modules.interactions.models import Comment
from app.modules.interactions.schemas import CreateComment
from app.modules.interactions.service import InteractionService
from app.modules.moderation.schemas import CreateReport, Decision
from app.modules.moderation.service import ModerationService
from app.modules.playlists.service import PlaylistService
from app.modules.videos.models import Video, VideoAsset
from app.modules.videos.schemas import CreateVideo
from app.modules.videos.service import VideoService


def private_json(path, value):
    if path.is_symlink():
        raise ValueError("Refusing symlink")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".seed-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        if os.fstat(stream.fileno()).st_mode & 0o077:
            raise ValueError("Demo file must have mode 0600")
        return json.load(stream)


class Client:
    def __init__(self, base, origin, account):
        self.base, self.origin = base, origin
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf = ""
        self.csrf = self.call(
            "/api/auth/login",
            "POST",
            {
                "email": account["email"],
                "password": account["password"],
            },
        )["csrf_token"]

    def call(self, path, method="GET", body=None):
        headers = {"Origin": self.origin, "X-CSRF-Token": self.csrf}
        if isinstance(body, bytes):
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        request = Request(self.base + path, data=body, method=method, headers=headers)
        with self.opener.open(request, timeout=120) as response:
            content = response.read()
            return json.loads(content) if content else None

    def close(self):
        self.call("/api/auth/logout", "POST")


class Demo:
    def __init__(self, database, directory, base, fail_after=None):
        self.database, self.directory, self.base = database, directory, base
        self.fail_after = fail_after
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise ValueError("Refusing symlink demo directory")
        self.state_path = directory / "state.json"
        self.credentials_path = directory / "credentials.json"
        if not self.credentials_path.exists():
            # Missing credentials for a previous seed must never reset its accounts.
            if self.state_path.exists():
                raise ValueError("Restore original demo credentials before retrying")
            suffix = secrets.token_hex(5)
            accounts = {}
            for name in ("admin", "moderator", "author", "viewer", "outsider"):
                username = f"demo_{name}_{suffix}"
                accounts[name] = {
                    "username": username,
                    "email": username + "@example.com",
                    "password": secrets.token_urlsafe(24),
                }
            private_json(self.credentials_path, {"version": 1, "accounts": accounts})
        self.accounts = read_private(self.credentials_path)["accounts"]
        self.state = (
            read_private(self.state_path)
            if self.state_path.exists()
            else {
                "resources": {},
                "uploads": [],
                "complete": False,
            }
        )

    def save(self):
        private_json(self.state_path, self.state)

    def failpoint(self, name):
        if self.fail_after == name:
            raise RuntimeError("Injected demo interruption")

    def once(self, key, model, create):
        entry = self.state["resources"].get(key)
        if entry:
            identifier = UUID(entry["id"])
            with self.database.session_factory() as db:
                exists = db.get(model, identifier) is not None
            if entry["done"] or exists:
                if not exists:
                    raise ValueError("A completed demo resource was removed; not recreated")
                entry["done"] = True
                self.save()
                return identifier
        # Service commits are savepoints in this outer transaction. Persist its result
        # BEFORE outer commit: an interruption on either side of commit is recoverable.
        with self.database.engine.begin() as connection:
            factory = sessionmaker(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            identifier = create(factory)
            self.state["resources"][key] = {"id": str(identifier), "done": False}
            self.save()
            self.failpoint("prepared:" + key)
        self.failpoint("committed:" + key)
        self.state["resources"][key]["done"] = True
        self.save()
        print("Demo checkpoint: " + key, flush=True)
        return identifier

    def user(self, key):
        def create(factory):
            with factory() as db:
                account = self.accounts[key]
                user = AuthService(db).register(
                    Register(**account),
                    role="admin" if key == "admin" else "user",
                )
                channel = db.scalar(select(Channel).where(Channel.user_id == user.id))
                channel.title = "Demo studio: " + key
                channel.description = "Synthetic Video-service demonstration channel"
                if key == "moderator":
                    admin = db.scalar(
                        select(User).where(User.username == self.accounts["admin"]["username"])
                    )
                    AdminService(db).set_role(
                        admin,
                        user.id,
                        RoleChange(
                            role="moderator",
                            reason="Demo moderator bootstrap",
                        ),
                    )
                else:
                    db.commit()
                return user.id

        return self.once("user-" + key, User, create)

    def video(self, index, user):
        def create(factory):
            return VideoService(factory).create(
                user,
                CreateVideo(
                    title=f"Demo clip {index + 1}",
                    description="Synthetic FFmpeg test pattern with generated audio, 6 seconds",
                    visibility="private" if index == 4 else "public",
                ),
            )["id"]

        return self.once("video-" + str(index), Video, create)

    def upload(self, index, identifier, owner):
        if index in self.state["uploads"]:
            return
        deadline = time.monotonic() + 300
        client = None
        try:
            while time.monotonic() < deadline:
                with self.database.session_factory() as db:
                    video = db.get(Video, identifier)
                    status, stage = video.status, video.error_stage
                    accepted = (
                        db.scalar(
                            select(VideoAsset.id).where(
                                VideoAsset.video_id == identifier,
                                VideoAsset.kind == "source",
                            )
                        )
                        is not None
                    )
                if status == "ready":
                    self.state["uploads"].append(index)
                    self.save()
                    return
                if status == "failed" and accepted:
                    raise RuntimeError(
                        "Demo processing failed; inspect Studio/worker, data retained"
                    )
                if not accepted and (
                    status == "awaiting_upload" or (status == "failed" and stage == "upload")
                ):
                    if client is None:
                        client = Client(self.base, get_settings().app_origin, self.accounts[owner])
                    with tempfile.TemporaryDirectory(prefix="demo-clip-") as temporary:
                        source = Path(temporary) / "input.mp4"
                        subprocess.run(
                            [
                                "ffmpeg",
                                "-nostdin",
                                "-v",
                                "error",
                                "-f",
                                "lavfi",
                                "-i",
                                f"testsrc2=size=320x180:rate=24:duration=6, hue=h={index * 45}",
                                "-f",
                                "lavfi",
                                "-i",
                                f"sine=frequency={330 + index * 55}:duration=6",
                                "-c:v",
                                "libx264",
                                "-preset",
                                "ultrafast",
                                "-pix_fmt",
                                "yuv420p",
                                "-c:a",
                                "aac",
                                "-shortest",
                                str(source),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=60,
                        )
                        client.call(f"/api/videos/{identifier}/source", "PUT", source.read_bytes())
                    self.failpoint("uploaded:" + str(index))
                time.sleep(1)
            raise TimeoutError("Demo processing deadline exceeded")
        finally:
            if client:
                client.close()

    def extras(self, users, videos):
        def create(factory):
            channels, interactions = ChannelService(factory), InteractionService(factory)
            for follower, author in [("viewer", "author"), ("outsider", "moderator")]:
                channels.subscribe(self.accounts[author]["username"], users[follower], True)
            VideoService(factory).grants(
                users["moderator"], videos[4], self.accounts["viewer"]["username"]
            )
            for key in ("viewer", "outsider"):
                interactions.like(videos[0], users[key], True)
            marker = interactions.add_comment(
                videos[0],
                users["viewer"],
                CreateComment(
                    body="Synthetic demo comment: generated test pattern",
                ),
            )["id"]
            interactions.add_comment(
                videos[3],
                users["author"],
                CreateComment(
                    body="A second demo channel with generated audio",
                ),
            )
            playlists = PlaylistService(factory)
            playlist = playlists.create(
                users["author"],
                Metadata(
                    title="Demo playlist",
                    description="Three synthetic clips",
                ),
            )["id"]
            for identifier in videos[:3]:
                playlists.mutate(playlist, users["author"], "add", video_id=identifier)
            moderation = ModerationService(factory)
            for index, status in [(5, "actioned"), (1, "dismissed")]:
                report = moderation.create(
                    videos[index],
                    users["viewer"],
                    CreateReport(
                        reason="other",
                        body="Synthetic demo review example",
                    ),
                )["id"]
                moderation.resolve(
                    users["moderator"],
                    report,
                    Decision(
                        status=status,
                        reason="Synthetic demonstration decision",
                    ),
                )
            return marker

        self.once("extras", Comment, create)

    def verify(self):
        storage = Storage(get_settings())
        try:
            public = []
            with self.database.session_factory() as db:
                for index in range(6):
                    identifier = UUID(self.state["resources"]["video-" + str(index)]["id"])
                    video = db.get(Video, identifier)
                    if video is None or video.status != "ready":
                        raise ValueError("Demo video not ready; existing edits were not reset")
                    assets = db.scalars(
                        select(VideoAsset).where(VideoAsset.video_id == identifier)
                    ).all()
                    if {asset.kind for asset in assets} != {"source", "playback", "thumbnail"}:
                        raise ValueError("Demo assets incomplete")
                    for asset in assets:
                        if storage.head(asset.object_key)["ContentLength"] != asset.size_bytes:
                            raise ValueError("Demo stored asset mismatch")
                    if video.visibility == "public" and video.moderation_state == "visible":
                        public.append(identifier)
            if not public:
                raise ValueError("No accessible public demo playback; existing edits preserved")
            request = Request(
                self.base + f"/api/videos/{public[0]}/media", headers={"Range": "bytes=0-31"}
            )
            with urlopen(request, timeout=15) as response:
                if response.status != 206 or len(response.read()) != 32:
                    raise ValueError("Real media Range failed")
        finally:
            storage.close()
        print(
            "Demo verified: six real processed videos, previews, authorized HTTP Range", flush=True
        )

    def run(self):
        if not self.state["complete"]:
            users = {
                key: self.user(key)
                for key in ("admin", "moderator", "author", "viewer", "outsider")
            }
            videos = []
            for index in range(6):
                owner = "author" if index < 3 else "moderator"
                identifier = self.video(index, users[owner])
                videos.append(identifier)
                self.upload(index, identifier, owner)
            self.extras(users, videos)
            self.state["complete"] = True
            self.save()
        self.verify()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/demo"))
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--fail-after", help="Test-only interruption checkpoint; data are retained")
    args = parser.parse_args()
    database = Database(get_settings())
    try:
        with database.engine.connect() as lock:
            if not lock.scalar(text("SELECT pg_try_advisory_lock(762830014)")):
                raise ValueError("Another demo seed is running")
            try:
                Demo(database, args.directory, args.base_url, args.fail_after).run()
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(762830014)"))
    except Exception:
        raise SystemExit(
            "Demo seed failed; credentials hidden; checkpoints/data retained for retry"
        ) from None
    finally:
        database.close()


if __name__ == "__main__":
    main()
