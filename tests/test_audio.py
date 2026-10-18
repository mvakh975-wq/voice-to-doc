import json

import pytest

from tests.conftest import make_media
from voice_to_doc.audio import AudioError, READ_ERROR, parse_segment_list, prepare_audio


def test_parse_segment_list():
    assert parse_segment_list("000.ogg,0.000000,3.000000\n001.ogg,3.000000,5.7365\n") == [
        {"file": "000.ogg", "start": 0.0, "end": 3.0},
        {"file": "001.ogg", "start": 3.0, "end": 5.7365},
    ]


def test_splits_audio_into_contiguous_chunks(tmp_path):
    src = make_media(tmp_path / "in.wav", 7)
    manifest = prepare_audio(src, tmp_path / "job", chunk_seconds=3)
    chunks = manifest["chunks"]
    assert [c["file"] for c in chunks] == ["000.ogg", "001.ogg", "002.ogg"]
    assert chunks[0]["start"] == 0.0
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt["start"] == pytest.approx(prev["end"])
    assert manifest["duration_s"] == pytest.approx(7, abs=0.1)
    assert all((tmp_path / "job" / "chunks" / c["file"]).stat().st_size > 0 for c in chunks)
    assert json.loads((tmp_path / "job" / "chunks.json").read_text()) == manifest


def test_accepts_video(tmp_path):
    src = make_media(tmp_path / "in.mp4", 4, video=True)
    manifest = prepare_audio(src, tmp_path / "job", chunk_seconds=600)
    assert len(manifest["chunks"]) == 1
    assert manifest["duration_s"] == pytest.approx(4, abs=0.2)


def test_rerun_removes_stale_chunks(tmp_path):
    job = tmp_path / "job"
    (job / "chunks").mkdir(parents=True)
    (job / "chunks" / "009.ogg").write_bytes(b"old")
    prepare_audio(make_media(tmp_path / "in.wav", 2), job, chunk_seconds=600)
    assert not (job / "chunks" / "009.ogg").exists()


def test_garbage_file_raises_readable_error(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_text("not audio")
    with pytest.raises(AudioError) as exc:
        prepare_audio(bad, tmp_path / "job", chunk_seconds=600)
    assert str(exc.value) == READ_ERROR
    assert exc.value.detail
    assert not (tmp_path / "job" / "chunks.json").exists()


def test_playlist_cannot_pull_in_other_local_files(tmp_path):
    secret = make_media(tmp_path / "secret.wav", 3)
    for name, body in [
        ("evil.m3u8", f"#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nfile://{secret}\n#EXT-X-ENDLIST\n"),
        ("evil", f"ffconcat version 1.0\nfile '{secret}'\n"),
    ]:
        src = tmp_path / name
        src.write_text(body)
        with pytest.raises(AudioError):
            prepare_audio(src, tmp_path / f"job-{name}", chunk_seconds=600)


def test_accepts_common_formats(tmp_path):
    for ext in ("mp3", "m4a", "ogg", "flac", "aiff", "webm"):
        src = make_media(tmp_path / f"in.{ext}", 1)
        assert prepare_audio(src, tmp_path / f"job-{ext}", chunk_seconds=600)["chunks"]


def test_too_long_recording_is_rejected(tmp_path):
    src = make_media(tmp_path / "in.wav", 5)
    with pytest.raises(AudioError, match="длиннее"):
        prepare_audio(src, tmp_path / "job", chunk_seconds=600, max_seconds=3)


def test_ffmpeg_timeout_is_an_audio_error(tmp_path):
    src = make_media(tmp_path / "in.wav", 5)
    with pytest.raises(AudioError, match="слишком долго"):
        prepare_audio(src, tmp_path / "job", chunk_seconds=600, timeout=0.001)
