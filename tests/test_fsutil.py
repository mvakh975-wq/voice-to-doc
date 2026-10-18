import pytest

from voice_to_doc.fsutil import write_text_atomic


def test_write_text_atomic_writes(tmp_path):
    path = tmp_path / "a.json"
    write_text_atomic(path, "новое")
    assert path.read_text(encoding="utf-8") == "новое"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_write_keeps_old_content(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    path.write_text("старое", encoding="utf-8")

    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.replace", boom)
    with pytest.raises(OSError):
        write_text_atomic(path, "новое")
    assert path.read_text(encoding="utf-8") == "старое"
