# voice-to-doc

[![CI](https://github.com/OWNER/voice-to-doc/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/voice-to-doc/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Turns a recorded lecture or interview into a structured document** — a short summary, a near-verbatim text split into titled sections with timestamps, or speaker-labelled replies for interviews. Download as Word or Markdown.

Runs locally on a modest laptop and costs nothing: speech recognition and text editing use Groq's free tier.

[Русская версия](README.ru.md)

![Demo: upload a lecture, watch progress, read the document](docs/images/demo.gif)

## Why

Transcripts straight out of speech recognition are a wall of text with "um"s and no structure. Turning an hour-long lecture into readable notes by hand takes hours. This tool does it in minutes while keeping the speaker's words — it cleans, it does not paraphrase.

## How it works

```
upload ─► ffmpeg: mono 16 kHz opus, 10-min chunks
       ─► Groq Whisper large-v3: text + timestamps per chunk
       ─► Groq gpt-oss-120b: cleanup, sections / speaker turns (block by block)
       ─► summary ─► Markdown + .docx
```

Every step saves its result to `data/<job>/`, so an interrupted job resumes from the first unfinished step without spending quota twice.

## Engineering highlights

- **Free-tier aware.** A client-side limiter tracks requests and tokens per minute; the output budget is sized per block because Groq counts `max_completion_tokens` toward the limit. Minute limits are waited out; a daily limit pauses the job with a "Continue" button.
- **Untrusted model output.** JSON is validated against a schema; invented timestamps are snapped to real segment starts; an answer much shorter than the input (a sign of paraphrasing) is retried with a stricter prompt, then falls back to the raw text with a visible note.
- **Resumable and crash-safe.** Atomic file writes, per-chunk and per-block checkpoints, atomic status transitions, a single worker that survives failing jobs.
- **Tested.** ~100 offline tests (pytest, Groq replaced by a fake), a live end-to-end test against the real API, and a Playwright browser check in light/dark themes at desktop and phone widths.

## Security

Designed as a local single-user tool:

- the server listens on `127.0.0.1` only;
- requests with a foreign `Host` (DNS rebinding) or from another site (CSRF) are rejected;
- strict Content-Security-Policy with no third-party scripts (the Markdown renderer is vendored);
- model output is HTML-escaped, and only `http(s)` links survive;
- uploads: extension allowlist, 2 GB / 6 h limits, ffmpeg without playlist demuxers (no reading other local files);
- the API key lives in `.env` with owner-only permissions and is never logged or returned by the API;
- all dependency versions pinned; CI runs `pip-audit` (known CVEs) and `bandit` (static analysis);
- an independent security audit found no critical, high or medium issues; all low findings fixed.

See [SECURITY.md](SECURITY.md).

## Quick start

Requirements: macOS or Linux, Python 3.12+, a free [Groq API key](https://console.groq.com/keys).

```bash
git clone https://github.com/OWNER/voice-to-doc.git
cd voice-to-doc
./run.sh            # creates .venv, installs dependencies, opens http://127.0.0.1:8000
```

On first launch the page asks for the Groq key. ffmpeg is bundled — nothing else to install.

Free-tier capacity (Groq, October 2026): about 8 hours of audio and 200k LLM tokens per day — roughly 3–4 hours of recordings.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                          # offline tests
.venv/bin/pytest -m live -s               # real Groq round trip (macOS: uses `say`)
.venv/bin/python scripts/ui_check.py      # browser check + screenshots
```

Design notes: [specification](docs/design/spec.md) and [implementation plan](docs/design/implementation-plan.md) written before the code.

## Limitations

- Speaker labels in interviews are inferred from content, not from voices; reliable for two people only.
- Rare terms may be misrecognized by Whisper (the demo turns "ARPANET" into "Orpnet").
- Built for Russian; other languages need a different `LANGUAGE` and prompts.

## License

[MIT](LICENSE)
