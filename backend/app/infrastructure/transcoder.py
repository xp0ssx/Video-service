import hashlib
import json
import math
import subprocess
import time
from fractions import Fraction
from pathlib import Path


class ProcessingError(Exception):
    def __init__(self, code):
        self.code = code


class LostLease(Exception):
    pass


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def execute(args, cancel, timeout, output=None):
    # No inherited application credentials, stdin, shell or unbounded stderr buffers.
    with open(output, "wb") if output else open("/dev/null", "wb") as stdout:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel.wait(0.2):
                    raise LostLease
                if time.monotonic() > deadline:
                    raise ProcessingError("processing_timeout")
                if output and Path(output).stat().st_size > 2 * 1024 * 1024:
                    raise ProcessingError("probe_output_limit")
            if output and Path(output).stat().st_size > 2 * 1024 * 1024:
                raise ProcessingError("probe_output_limit")
            if process.returncode:
                raise ProcessingError("invalid_media")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def probe(path, cancel):
    output = Path(path).with_name(Path(path).name + ".probe.json")
    execute(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        cancel,
        30,
        output,
    )
    try:
        data = json.loads(output.read_text())
        streams = data["streams"]
        video = next(row for row in streams if row["codec_type"] == "video")
        duration = float(data["format"]["duration"])
        width, height = int(video["width"]), int(video["height"])
        fps = float(Fraction(video["avg_frame_rate"]))
        rotation = float(video.get("tags", {}).get("rotate", 0))
        for side in video.get("side_data_list", []):
            rotation = float(side.get("rotation", rotation))
        if round(rotation) % 180:
            width, height = height, width
        if (
            not math.isfinite(duration)
            or not 1 <= duration <= 600
            or not 1 <= width <= 3840
            or not 1 <= height <= 2160
            or not 0 < fps <= 60
            or len(streams) > 16
        ):
            raise ProcessingError("media_limits")
        if not set(data["format"]["format_name"].split(",")) & {"mov", "mp4", "matroska", "webm"}:
            raise ProcessingError("unsupported_format")
        brand = data["format"].get("tags", {}).get("major_brand", "").lower()
        if brand.startswith(("3g", "m4a")) or any(
            row.get("codec_tag_string") in ("encv", "enca") for row in streams
        ):
            raise ProcessingError("unsupported_format")
        return {
            "duration_ms": round(duration * 1000),
            "width": width,
            "height": height,
            "audio": any(row["codec_type"] == "audio" for row in streams),
        }
    except (KeyError, ValueError, TypeError, StopIteration, ZeroDivisionError, OverflowError):
        raise ProcessingError("invalid_media") from None


def transcode(source, directory, cancel):
    info = probe(source, cancel)
    playback, thumbnail = Path(directory) / "playback.mp4", Path(directory) / "thumbnail.jpg"
    execute(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-vf",
            "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-maxrate",
            "2500k",
            "-bufsize",
            "5000k",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(playback),
        ],
        cancel,
        1200,
    )
    result = probe(playback, cancel)
    if result["width"] > 1280 or result["height"] > 720:
        raise ProcessingError("invalid_output")
    execute(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-threads",
            "2",
            "-protocol_whitelist",
            "file,pipe",
            "-ss",
            str(min(1, info["duration_ms"] / 2000)),
            "-i",
            str(playback),
            "-frames:v",
            "1",
            "-vf",
            "scale=w='min(640,iw)':h='min(360,ih)':force_original_aspect_ratio=decrease",
            "-threads",
            "2",
            str(thumbnail),
        ],
        cancel,
        30,
    )
    if (
        not thumbnail.is_file()
        or thumbnail.stat().st_size <= 0
        or thumbnail.read_bytes()[:2] != b"\xff\xd8"
    ):
        raise ProcessingError("invalid_thumbnail")
    return result, playback, thumbnail
