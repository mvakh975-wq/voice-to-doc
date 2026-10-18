# Security

## Threat model

voice-to-doc is a **local, single-user** tool. The server binds to `127.0.0.1` and is not meant to be exposed to a network. The things it protects:

- your Groq API key (`.env`);
- your recordings and transcripts (`data/`);
- your Groq quota.

The adversaries it considers are **other web pages open in your browser** (they can send requests to `127.0.0.1`), **hostile media files**, and **untrusted model output**.

## Defenses

| Risk | Defense |
|---|---|
| Other sites driving the local API (CSRF) | Non-GET requests are rejected unless `Origin` matches the app; `Sec-Fetch-Site: cross-site/same-site` is rejected |
| DNS rebinding | `Host` must be `127.0.0.1` or `localhost` |
| Script injection via model output | Markdown is HTML-escaped before rendering; only `http(s)` links survive; images removed; strict CSP (`script-src 'self'`, no inline scripts, no third-party origins) |
| Clickjacking | `frame-ancestors 'none'`, `X-Frame-Options: DENY` |
| Path traversal | Job ids validated by pattern; uploads stored as `source<.ext>` with an allowlisted extension |
| Playlists reading other local files | ffmpeg runs with `-protocol_whitelist file` and a demuxer allowlist (no hls/concat) |
| Resource exhaustion | 2 GB upload limit enforced from `Content-Length` before the body is read; 6 h maximum duration; ffmpeg timeout |
| Malformed model output | Schema validation; control characters stripped; timestamps snapped to real ones |
| Key leakage | `.env` is owner-only (0600), never logged, never returned by the API; `data/` is 0700 |
| Vulnerable dependencies | All versions pinned (`constraints.txt`); CI runs `pip-audit` and `bandit` |

## Known limitations

- Any process running **on your machine** can call the API (no authentication), by design for a local tool.
- ffmpeg parses untrusted media; keep `imageio-ffmpeg` up to date.
- Audio and text are sent to Groq; see Groq's data policy before processing sensitive recordings.

## Reporting a vulnerability

Please open a [private security advisory](../../security/advisories/new) on GitHub instead of a public issue.
