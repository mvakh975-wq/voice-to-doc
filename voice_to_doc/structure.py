"""Turn a raw transcript into a structured document with the help of an LLM."""

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
