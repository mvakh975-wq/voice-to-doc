import pytest

from voice_to_doc.structure import (
    estimate_tokens, fmt_ts, is_too_short, pack_blocks, parse_ts, render_lines,
    snap_start, validate_interview, validate_lecture,
)


def seg(start, text):
    return {"start": start, "end": start + 1, "text": text}


@pytest.mark.parametrize("seconds,long,expected", [
    (0, False, "00:00"), (65.9, False, "01:05"), (3599, False, "59:59"),
    (3725, True, "1:02:05"), (5, True, "0:00:05"),
])
def test_fmt_ts(seconds, long, expected):
    assert fmt_ts(seconds, long) == expected


@pytest.mark.parametrize("value,expected", [
    ("01:05", 65.0), ("1:02:05", 3725.0), (" 00:07 ", 7.0), ("7", None), ("ab:cd", None), (None, None), (12, None),
])
def test_parse_ts(value, expected):
    assert parse_ts(value) == expected


def test_estimate_tokens():
    assert estimate_tokens("a" * 25, 2.5) == 11


def test_pack_blocks_respects_budget_and_order():
    segments = [seg(i, "x" * 50) for i in range(10)]  # 21 + 4 = 25 tokens each
    blocks = pack_blocks(segments, budget_tokens=60, chars_per_token=2.5)
    assert [len(b) for b in blocks] == [2, 2, 2, 2, 2]
    assert [s for b in blocks for s in b] == segments


def test_oversized_segment_gets_own_block():
    segments = [seg(0, "a"), seg(1, "x" * 1000), seg(2, "b")]
    blocks = pack_blocks(segments, budget_tokens=60, chars_per_token=2.5)
    assert blocks == [[segments[0]], [segments[1]], [segments[2]]]


def test_render_lines():
    assert render_lines([seg(0, "Раз."), seg(3725, "Два.")], long=True) == "[0:00:00] Раз.\n[1:02:05] Два."


def test_snap_start():
    allowed = [0.0, 12.6, 30.2]
    assert snap_start("00:12", allowed) == 12.6
    assert snap_start("00:25", allowed) == 30.2
    assert snap_start("мусор", allowed) == 0.0


def test_validate_lecture_snaps_orders_and_drops_empty():
    allowed = [0.0, 10.0, 20.0]
    data = {"continues_previous": True, "sections": [
        {"title": " Введение ", "start": "00:10", "paragraphs": ["  Текст. ", ""]},
        {"title": "Пусто", "start": "00:20", "paragraphs": []},
        {"title": "", "start": "00:00", "paragraphs": ["Ещё."]},
    ]}
    assert validate_lecture(data, allowed) == {"continues_previous": True, "sections": [
        {"title": "Введение", "start": 10.0, "paragraphs": ["Текст."]},
        {"title": "Без названия", "start": 10.0, "paragraphs": ["Ещё."]},
    ]}


@pytest.mark.parametrize("data", [None, [], {"sections": "x"}, {"sections": [1]}, {"sections": []}])
def test_validate_lecture_rejects_bad_shapes(data):
    assert validate_lecture(data, [0.0]) is None


def test_validate_interview():
    data = {"utterances": [
        {"speaker": "Интервьюер", "start": "00:00", "text": "Вопрос?"},
        {"speaker": "Ведущий", "start": "00:05", "text": "Ответ."},
        {"speaker": "Собеседник", "start": "00:09", "text": " "},
    ]}
    assert validate_interview(data, [0.0, 5.0]) == {"utterances": [
        {"speaker": "Интервьюер", "start": 0.0, "text": "Вопрос?"},
        {"speaker": None, "start": 5.0, "text": "Ответ."},
    ]}
    assert validate_interview({"utterances": []}, [0.0]) is None


def test_is_too_short():
    block = [seg(0, "x" * 300)]
    short = {"sections": [{"title": "t", "start": 0.0, "paragraphs": ["x" * 100]}]}
    ok = {"utterances": [{"speaker": None, "start": 0.0, "text": "x" * 250}]}
    assert is_too_short(block, short)
    assert not is_too_short(block, ok)
    assert not is_too_short([seg(0, "Э-э, ну.")], {"utterances": [{"speaker": None, "start": 0.0, "text": "Ну."}]})


@pytest.mark.parametrize("value,expected", [("[05:30]", 330.0), (" [1:02:05] ", 3725.0)])
def test_parse_ts_accepts_bracketed_timestamps(value, expected):
    assert parse_ts(value) == expected


def test_control_characters_are_stripped_from_model_output():
    data = {"sections": [{"title": "A\x0bB", "start": "00:00", "paragraphs": ["x\x00y\x1bz", "\x07"]}]}
    assert validate_lecture(data, [0.0])["sections"] == [{"title": "AB", "start": 0.0, "paragraphs": ["xyz"]}]
    data = {"utterances": [{"speaker": "Собеседник", "start": "00:00", "text": "ок\x0c\tда\n"}]}
    assert validate_interview(data, [0.0])["utterances"][0]["text"] == "ок\tда"


def test_prompts_treat_transcript_as_data_and_forbid_invented_summary():
    from voice_to_doc.structure import SYSTEM_INTERVIEW, SYSTEM_LECTURE, SYSTEM_SUMMARY

    for prompt in (SYSTEM_LECTURE, SYSTEM_INTERVIEW, SYSTEM_SUMMARY):
        assert "не инструкции" in prompt
    assert "не обобщай" in SYSTEM_SUMMARY
