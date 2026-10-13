import json

from voice_to_doc.devfake import FakeGroq
from voice_to_doc.groq_client import InvalidJSON
from voice_to_doc.ratelimit import RateLimiter
from voice_to_doc.structure import (
    LLM, STRICT_NOTE, context_for, make_document, merge_blocks, structure_block, structure_transcript,
)


class Scripted:
    """Returns queued answers; records (system, user) of every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def chat_json(self, system, user, max_tokens):
        self.prompts.append((system, user))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def make_llm(client, clock):
    return LLM(client, RateLimiter(30, 8000, clock=clock, sleep=clock.sleep), 4000, 2.5, sleep=clock.sleep)


BLOCK = [{"start": 0.0, "end": 5.0, "text": "Ну, э-э, сегодня про сети."}, {"start": 5.0, "end": 9.0, "text": "Начнём с истории."}]
GOOD = {"continues_previous": False, "sections": [{"title": "Сети", "start": "00:00", "paragraphs": ["Сегодня про сети. Начнём с истории."]}]}


def test_structure_block_success_and_prompt(clock):
    client = Scripted(GOOD)
    result = structure_block(BLOCK, "lecture", "Последний раздел: «X»", make_llm(client, clock), long=False)
    assert result["sections"][0]["title"] == "Сети" and "fallback" not in result
    system, user = client.prompts[0]
    assert system.startswith("# task: lecture")
    assert "Последний раздел: «X»" in user and "[00:05] Начнём с истории." in user


def test_invalid_then_strict_retry(clock):
    client = Scripted("не json", GOOD)
    result = structure_block(BLOCK, "lecture", "", make_llm(client, clock), long=False)
    assert "fallback" not in result
    assert client.prompts[1][0].endswith(STRICT_NOTE)


def test_two_failures_give_fallback(clock):
    client = Scripted(InvalidJSON("bad"), {"sections": []})
    result = structure_block(BLOCK, "interview", "", make_llm(client, clock), long=False)
    assert result == {"fallback": True, "utterances": [
        {"speaker": None, "start": 0.0, "text": "Ну, э-э, сегодня про сети. Начнём с истории.", "raw": True}]}


def test_too_short_answer_is_retried(clock):
    long_block = [{"start": 0.0, "end": 60.0, "text": "слово " * 100}]
    short = {"utterances": [{"speaker": "Собеседник", "start": "00:00", "text": "Кратко."}]}
    full = {"utterances": [{"speaker": "Собеседник", "start": "00:00", "text": "слово " * 90}]}
    client = Scripted(short, full)
    result = structure_block(long_block, "interview", "", make_llm(client, clock), long=False)
    assert len(client.prompts) == 2 and result["utterances"][0]["text"].startswith("слово")


def test_merge_continues_previous_and_warnings():
    a = {"continues_previous": False, "sections": [{"title": "A", "start": 0.0, "paragraphs": ["p1"]}]}
    b = {"continues_previous": True, "sections": [{"title": "A2", "start": 50.0, "paragraphs": ["p2"]},
                                                  {"title": "B", "start": 70.0, "paragraphs": ["p3"]}]}
    c = {"fallback": True, "continues_previous": False,
         "sections": [{"title": "Не оформлено", "start": 90.0, "paragraphs": ["raw"], "raw": True}]}
    items, warnings = merge_blocks("lecture", [a, b, c], long=False)
    assert [(i["title"], i["paragraphs"]) for i in items] == [("A", ["p1", "p2"]), ("B", ["p3"]), ("Не оформлено", ["raw"])]
    assert warnings == ["Фрагмент с 01:30 не оформлен — показан исходный текст распознавания."]


def test_context_for():
    assert context_for("lecture", []) == ""
    assert context_for("lecture", [{"title": "Итоги"}]) == "Последний раздел: «Итоги»"
    items = [{"speaker": "Интервьюер", "text": "a"}, {"speaker": None, "text": "b"}, {"speaker": "Собеседник", "text": "c"}]
    assert context_for("interview", items) == "Неизвестно: b\nСобеседник: c"


def write_job(job_dir, segments, duration=120.0):
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "transcript.json").write_text(json.dumps(segments, ensure_ascii=False))
    (job_dir / "chunks.json").write_text(json.dumps({"duration_s": duration, "chunks": []}))


def test_structure_transcript_resumes_and_passes_context(tmp_path, clock):
    segments = [{"start": float(i * 10), "end": float(i * 10 + 9), "text": f"Фраза номер {i}. " * 10} for i in range(4)]
    write_job(tmp_path, segments)
    fake = FakeGroq()
    progress = []
    structured = structure_transcript(tmp_path, "lecture", make_llm(fake, clock), block_tokens=100,
                                      on_progress=lambda d, t: progress.append((d, t)))
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert structured["kind"] == "lecture" and len(structured["items"]) == 4
    assert json.loads((tmp_path / "structured.json").read_text()) == structured
    (tmp_path / "blocks" / "003.json").unlink()
    again = FakeGroq()
    structure_transcript(tmp_path, "lecture", make_llm(again, clock), block_tokens=100)
    assert len(again.calls) == 1


def test_empty_transcript_gives_warning(tmp_path, clock):
    write_job(tmp_path, [])
    structured = structure_transcript(tmp_path, "interview", make_llm(FakeGroq(), clock), block_tokens=2500)
    assert structured["items"] == [] and structured["warnings"] == ["В записи не найдено речи."]
    doc = make_document(structured, make_llm(FakeGroq(), clock), "файл", 2500)
    assert doc["title"] == "файл" and doc["summary"] == []


def test_make_document_uses_summary_and_falls_back(clock):
    structured = {"kind": "lecture", "duration_s": 60.0, "warnings": [],
                  "items": [{"title": "A", "start": 0.0, "paragraphs": ["Текст."]}]}
    doc = make_document(structured, make_llm(FakeGroq(), clock), "файл", 2500)
    assert doc["title"] == "Тестовая запись" and doc["summary"] == ["Первый тезис", "Второй тезис"]
    bad = make_document(structured, make_llm(Scripted({"title": 5}), clock), "файл", 2500)
    assert bad["title"] == "файл" and bad["warnings"] == ["Не удалось составить выжимку."]
