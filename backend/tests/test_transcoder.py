import json
import threading

import pytest

from app.infrastructure import transcoder


@pytest.mark.parametrize(
    "change,code",
    [
        ({"duration": 0.5}, "media_limits"),
        ({"duration": 601}, "media_limits"),
        ({"width": 3842}, "media_limits"),
        ({"height": 2162}, "media_limits"),
        ({"avg_frame_rate": "61/1"}, "media_limits"),
        ({"codec_tag_string": "encv"}, "unsupported_format"),
        ({"format_name": "hls"}, "unsupported_format"),
    ],
)
def test_probe_limits_and_self_contained_formats(tmp_path, monkeypatch, change, code):
    video = {"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "30/1"}
    container = {"format_name": "mov,mp4", "duration": "2"}
    for key, value in change.items():
        (container if key in ("duration", "format_name") else video)[key] = value

    def execute(args, cancel, timeout, output=None):
        output.write_text(json.dumps({"streams": [video], "format": container}))

    monkeypatch.setattr(transcoder, "execute", execute)
    with pytest.raises(transcoder.ProcessingError) as error:
        transcoder.probe(tmp_path / "source", threading.Event())
    assert error.value.code == code
