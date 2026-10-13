"""Turn a raw transcript into a structured document with the help of an LLM."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from voice_to_doc.groq_client import InvalidJSON
from voice_to_doc.ratelimit import RateLimiter, call_with_retries

SPEAKERS = ("Интервьюер", "Собеседник")


def fmt_ts(seconds: float, long: bool = False) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if long else f"{h * 60 + m:02d}:{s:02d}"


def parse_ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return float(total)


def estimate_tokens(text: str, chars_per_token: float) -> int:
    return int(len(text) / chars_per_token) + 1


def pack_blocks(segments: list[dict], budget_tokens: int, chars_per_token: float) -> list[list[dict]]:
    blocks, current, used = [], [], 0
    for seg in segments:
        cost = estimate_tokens(seg["text"], chars_per_token) + 4  # +4 for the "[MM:SS] " prefix
        if current and used + cost > budget_tokens:
            blocks.append(current)
            current, used = [], 0
        current.append(seg)
        used += cost
    if current:
        blocks.append(current)
    return blocks


def render_lines(block: list[dict], long: bool) -> str:
    return "\n".join(f"[{fmt_ts(s['start'], long)}] {s['text']}" for s in block)


def snap_start(value, allowed: list[float]) -> float:
    """Model timestamps are untrusted: map them onto the nearest real segment start."""
    t = parse_ts(value)
    if t is None:
        return allowed[0]
    return min(allowed, key=lambda a: abs(a - t))


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _monotonic(items: list[dict]) -> list[dict]:
    prev = 0.0
    for item in items:
        item["start"] = prev = max(item["start"], prev)
    return items


def validate_lecture(data, allowed: list[float]) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return None
    sections = []
    for s in data["sections"]:
        if not isinstance(s, dict):
            return None
        raw = s.get("paragraphs") if isinstance(s.get("paragraphs"), list) else []
        paragraphs = [p for p in map(_clean, raw) if p]
        if paragraphs:
            sections.append({"title": _clean(s.get("title")) or "Без названия",
                             "start": snap_start(s.get("start"), allowed), "paragraphs": paragraphs})
    if not sections:
        return None
    return {"continues_previous": data.get("continues_previous") is True, "sections": _monotonic(sections)}


def validate_interview(data, allowed: list[float]) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("utterances"), list):
        return None
    utterances = []
    for u in data["utterances"]:
        if not isinstance(u, dict):
            return None
        text = _clean(u.get("text"))
        if text:
            speaker = u.get("speaker") if u.get("speaker") in SPEAKERS else None
            utterances.append({"speaker": speaker, "start": snap_start(u.get("start"), allowed), "text": text})
    if not utterances:
        return None
    return {"utterances": _monotonic(utterances)}


def output_text(result: dict) -> str:
    if "sections" in result:
        return " ".join(p for s in result["sections"] for p in s["paragraphs"])
    return " ".join(u["text"] for u in result["utterances"])


def is_too_short(block: list[dict], result: dict, min_ratio: float = 0.7, min_chars: int = 200) -> bool:
    source = sum(len(s["text"]) for s in block)
    return source >= min_chars and len(output_text(result)) < min_ratio * source


SYSTEM_LECTURE = """# task: lecture
Ты редактор расшифровок лекций на русском языке. На входе — фрагмент автоматической расшифровки: строки вида [таймкод] текст.
Сделай из него читаемый текст.
- НЕ пересказывай и НЕ сокращай по смыслу. Сохрани все мысли, примеры, числа и формулировки лектора.
- Можно только: убрать слова-паразиты («э-э», «ну», «как бы», «вот»), повторы и оговорки; расставить знаки препинания; исправить явные ошибки распознавания; разбить текст на абзацы.
- Раздели фрагмент на тематические разделы с короткими заголовками (2–6 слов).
- start раздела — таймкод первой строки раздела, в точности как во входе.
- continues_previous = true, если первый раздел продолжает тему предыдущего фрагмента (его заголовок дан в контексте); иначе false.
Ответь только JSON:
{"continues_previous": bool, "sections": [{"title": str, "start": str, "paragraphs": [str]}]}"""

SYSTEM_INTERVIEW = """# task: interview
Ты редактор расшифровок интервью на русском языке. На входе — фрагмент автоматической расшифровки: строки вида [таймкод] текст. Говорят двое: «Интервьюер» задаёт вопросы, «Собеседник» отвечает.
- Раздели текст на реплики; новая реплика — при смене говорящего. Определи говорящего по смыслу; учти последние реплики из контекста.
- НЕ пересказывай и НЕ сокращай по смыслу. Можно только убрать слова-паразиты, повторы и оговорки, расставить знаки препинания, исправить явные ошибки распознавания.
- start реплики — таймкод её первой строки, в точности как во входе.
Ответь только JSON:
{"utterances": [{"speaker": "Интервьюер" | "Собеседник", "start": str, "text": str}]}"""

SYSTEM_SUMMARY = """# task: summary
На входе — содержание документа: строки с таймкодом, заголовком раздела или ролью говорящего и началом текста.
Придумай короткое название документа (до 8 слов) и выдели 3–7 главных мыслей.
Опирайся только на входной текст, ничего не добавляй от себя.
Ответь только JSON:
{"title": str, "summary": [str]}"""

STRICT_NOTE = (
    "\n\nВАЖНО: прошлый ответ был неполным или неверным. Верни ВЕСЬ текст фрагмента в указанном JSON, "
    "удалив только слова-паразиты и повторы."
)


@dataclass
class LLM:
    client: object
    limiter: RateLimiter
    max_output_tokens: int
    chars_per_token: float
    sleep: Callable = time.sleep
    log: Callable = field(default=lambda message: None)

    def ask(self, system: str, user: str):
        estimate = estimate_tokens(system + user, self.chars_per_token) + self.max_output_tokens

        def call():
            self.limiter.acquire(estimate)
            return self.client.chat_json(system, user, self.max_output_tokens)

        try:
            return json.loads(call_with_retries(call, sleep=self.sleep, log=self.log))
        except (InvalidJSON, json.JSONDecodeError) as e:
            self.log(f"Модель вернула не-JSON: {str(e)[:200]}")
            return None


def context_for(kind: str, items: list[dict]) -> str:
    if not items:
        return ""
    if kind == "lecture":
        return f"Последний раздел: «{items[-1]['title']}»"
    return "\n".join(f"{u['speaker'] or 'Неизвестно'}: {u['text'][:300]}" for u in items[-2:])


def build_user(block: list[dict], context: str, long: bool) -> str:
    parts = [f"Контекст предыдущего фрагмента:\n{context}"] if context else []
    parts.append(f"Фрагмент:\n{render_lines(block, long)}")
    return "\n\n".join(parts)


def fallback_block(block: list[dict], kind: str) -> dict:
    text = " ".join(s["text"] for s in block)
    start = block[0]["start"]
    if kind == "lecture":
        return {"fallback": True, "continues_previous": False,
                "sections": [{"title": "Не оформлено", "start": start, "paragraphs": [text], "raw": True}]}
    return {"fallback": True, "utterances": [{"speaker": None, "start": start, "text": text, "raw": True}]}


def structure_block(block: list[dict], kind: str, context: str, llm: LLM, long: bool) -> dict:
    allowed = [s["start"] for s in block]
    system = SYSTEM_LECTURE if kind == "lecture" else SYSTEM_INTERVIEW
    validate = validate_lecture if kind == "lecture" else validate_interview
    user = build_user(block, context, long)
    for attempt in range(2):
        result = validate(llm.ask(system + (STRICT_NOTE if attempt else ""), user), allowed)
        if result and not is_too_short(block, result):
            return result
        llm.log(f"Блок с {fmt_ts(block[0]['start'], long)}: ответ не прошёл проверку (попытка {attempt + 1})")
    return fallback_block(block, kind)


def merge_blocks(kind: str, results: list[dict], long: bool) -> tuple[list[dict], list[str]]:
    items, warnings = [], []
    for r in results:
        parts = [dict(x) for x in (r["sections"] if kind == "lecture" else r["utterances"])]
        if r.get("fallback"):
            warnings.append(f"Фрагмент с {fmt_ts(parts[0]['start'], long)} не оформлен — показан исходный текст распознавания.")
        if kind == "lecture" and r.get("continues_previous") and items and not items[-1].get("raw"):
            items[-1] = {**items[-1], "paragraphs": items[-1]["paragraphs"] + parts[0]["paragraphs"]}
            parts = parts[1:]
        items.extend(parts)
    return items, warnings


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def structure_transcript(job_dir: Path, kind: str, llm: LLM, block_tokens: int, *,
                         on_progress=lambda done, total: None) -> dict:
    segments = _read_json(job_dir / "transcript.json")
    duration = _read_json(job_dir / "chunks.json")["duration_s"]
    long = duration >= 3600
    blocks = pack_blocks(segments, block_tokens, llm.chars_per_token)
    out_dir = job_dir / "blocks"
    out_dir.mkdir(exist_ok=True)
    results = []
    for i, block in enumerate(blocks):
        path = out_dir / f"{i:03d}.json"
        if path.exists():
            result = _read_json(path)
        else:
            previous, _ = merge_blocks(kind, results[-1:], long)
            result = structure_block(block, kind, context_for(kind, previous), llm, long)
            _write_json(path, result)
        results.append(result)
        on_progress(i + 1, len(blocks))
    items, warnings = merge_blocks(kind, results, long)
    if not segments:
        warnings.append("В записи не найдено речи.")
    structured = {"kind": kind, "duration_s": duration, "items": items, "warnings": warnings}
    _write_json(job_dir / "structured.json", structured)
    return structured


def summary_input(structured: dict, long: bool, max_chars: int) -> str:
    lines = []
    for it in structured["items"]:
        ts = fmt_ts(it["start"], long)
        if structured["kind"] == "lecture":
            lines.append(f"[{ts}] {it['title']}: {' '.join(it['paragraphs'])[:300]}")
        else:
            lines.append(f"[{ts}] {it['speaker'] or 'Неизвестно'}: {it['text'][:200]}")
    step = 1
    while step < len(lines) and len("\n".join(lines[::step])) > max_chars:
        step += 1
    return "\n".join(lines[::step])


def make_document(structured: dict, llm: LLM, fallback_title: str, block_tokens: int) -> dict:
    doc = {**structured, "title": fallback_title, "summary": [], "warnings": list(structured["warnings"])}
    if not structured["items"]:
        return doc
    long = structured["duration_s"] >= 3600
    data = llm.ask(SYSTEM_SUMMARY, summary_input(structured, long, int(block_tokens * llm.chars_per_token)))
    data = data if isinstance(data, dict) else {}
    title, summary = data.get("title"), data.get("summary")
    if isinstance(title, str) and title.strip():
        doc["title"] = title.strip()
    if isinstance(summary, list):
        doc["summary"] = [s.strip() for s in summary if isinstance(s, str) and s.strip()][:7]
    if not doc["summary"]:
        doc["warnings"].append("Не удалось составить выжимку.")
    return doc
