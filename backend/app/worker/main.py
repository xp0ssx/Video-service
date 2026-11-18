import logging
import shutil
import signal
import tempfile
import threading
import time
from pathlib import Path

from app.core.config import get_settings
from app.db.session import Database
from app.infrastructure.storage import Storage
from app.infrastructure.transcoder import LostLease, ProcessingError, checksum, transcode
from app.modules.videos.deletion import storage_writer
from app.modules.videos.views import ViewService
from app.worker.cleanup import Cleanup
from app.worker.deletions import Deletions
from app.worker.jobs import Jobs, Lease

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, factory, storage):
        self.settings, self.storage = settings, storage
        self.jobs = Jobs(factory)
        self.deletions = Deletions(factory, storage)
        self.cancel = threading.Event()

    def run_once(self):
        self.jobs.heartbeat()
        self.jobs.recover()
        self.deletions.run()
        claim = self.jobs.claim()
        if claim is None:
            return False
        self.cancel.clear()
        try:
            with Lease(self.jobs, claim, self.cancel):
                root = Path(self.settings.scratch_dir)
                root.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(root).free < self.settings.scratch_min_free_bytes:
                    raise OSError("Scratch unavailable")
                with tempfile.TemporaryDirectory(
                    prefix=f"job-{claim['token']}-", dir=root
                ) as directory:
                    source = Path(directory) / "source"
                    self.storage.download(claim["key"], source)
                    if (
                        source.stat().st_size != claim["size"]
                        or checksum(source) != claim["sha256"]
                    ):
                        raise ProcessingError("source_corrupt")
                    info, playback, thumbnail = transcode(source, directory, self.cancel)
                    with storage_writer(self.jobs.factory, claim["video_id"]):
                        assets = []
                        for kind, path, mime in [
                            ("playback", playback, "video/mp4"),
                            ("thumbnail", thumbnail, "image/jpeg"),
                        ]:
                            if self.cancel.is_set():
                                raise LostLease
                            key = f"processed/{claim['video_id']}/{claim['token']}/{path.name}"
                            self.storage.upload(path, key, mime)
                            if self.storage.head(key)["ContentLength"] != path.stat().st_size:
                                raise OSError("Stored output mismatch")
                            assets.append(
                                {
                                    "kind": kind,
                                    "object_key": key,
                                    "size_bytes": path.stat().st_size,
                                    "sha256": checksum(path),
                                    "content_type": mime,
                                }
                            )
                        if self.cancel.is_set():
                            raise LostLease
                        self.jobs.publish(claim, info, assets)
        except LostLease:
            logger.warning("Processing lease lost; publication rejected")
        except ProcessingError as error:
            try:
                self.jobs.fail(claim, error.code, True)
            except LostLease:
                logger.warning("Processing lease lost; failure result rejected")
        except Exception:
            # Credentials/SQL/subprocess diagnostics are deliberately not logged.
            try:
                self.jobs.fail(claim, "dependency_unavailable")
            except Exception:
                logger.warning("Job persistence unavailable; lease recovery required")
        return True


def main():
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    database, storage = Database(settings), Storage(settings)
    worker = Worker(settings, database.session_factory, storage)
    stop = threading.Event()

    def shutdown(*args):
        stop.set()
        worker.cancel.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    def pulse():
        while not stop.wait(15):
            try:
                worker.jobs.heartbeat()
            except Exception:
                logger.warning("Worker heartbeat unavailable")

    heartbeat = threading.Thread(target=pulse, daemon=True)
    heartbeat.start()
    cleanup_at = 0
    try:
        while not stop.is_set():
            try:
                # Single worker/main thread: run_once (including publication)
                # returns before S3 cleanup. Heartbeat threads never publish.
                worker.run_once()
                if time.monotonic() >= cleanup_at:
                    cleanup_at = time.monotonic() + 3600
                    Cleanup(database.session_factory, settings, storage).run()
                    ViewService(database.session_factory).cleanup()
            except Exception:
                logger.warning("Worker iteration unavailable; will retry")
            stop.wait(2)
    finally:
        storage.close()
        database.close()


if __name__ == "__main__":
    main()
