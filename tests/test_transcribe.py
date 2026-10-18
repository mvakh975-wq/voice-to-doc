import json

from voice_to_doc.devfake import FakeGroq
from voice_to_doc.ratelimit import RateLimiter
from voice_to_doc.transcribe import shift_segments, transcribe_chunks


def write_manifest(job_dir, starts):
    (job_dir / "chunks").mkdir(parents=True)
    chunks = [{"file": f"{i:03d}.ogg", "start": s, "end": s + 600} for i, s in enumerate(starts)]
    for c in chunks:
        (job_dir / "chunks" / c["file"]).write_bytes(b"x")
    (job_dir / "chunks.json").write_text(json.dumps({"duration_s": starts[-1] + 600, "chunks": chunks}))


def test_drops_empty_and_no_speech_segments():
    segs = [
        {"start": 1.0, "end": 2.0, "text": "  Привет.  ", "no_speech_prob": 0.1},
        {"start": 2.0, "end": 3.0, "text": "   ", "no_speech_prob": 0.1},
        {"start": 3.0, "end": 9.0, "text": "Продолжение следует...", "no_speech_prob": 0.95},
    ]
    assert shift_segments(segs, 600) == [{"start": 601.0, "end": 602.0, "text": "Привет."}]


def test_offsets_and_progress(tmp_path, clock):
    write_manifest(tmp_path, [0, 600])
    progress = []
    segs = transcribe_chunks(tmp_path, FakeGroq(), RateLimiter(20, clock=clock, sleep=clock.sleep), "ru",
                             on_progress=lambda d, t: progress.append((d, t)))
    assert len(segs) == 6
    assert segs[3] == {"start": 600.0, "end": 602.0, "text": "Фраза 1 из куска 001."}
    assert progress == [(1, 2), (2, 2)]
    assert json.loads((tmp_path / "transcript.json").read_text()) == segs


def test_resume_skips_done_chunks(tmp_path, clock):
    write_manifest(tmp_path, [0, 600])
    (tmp_path / "transcript").mkdir()
    (tmp_path / "transcript" / "000.json").write_text(json.dumps([{"start": 0.0, "end": 1.0, "text": "Готово."}]))
    fake = FakeGroq()
    segs = transcribe_chunks(tmp_path, fake, RateLimiter(20, clock=clock, sleep=clock.sleep), "ru")
    assert fake.calls == [("transcribe", "001.ogg")]
    assert segs[0]["text"] == "Готово." and len(segs) == 4
