"""Drive the UI in Chromium against FakeGroq and save screenshots to screenshots/.

Usage: .venv/bin/python scripts/ui_check.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.conftest import make_media  # noqa: E402

MALICIOUS_MD = (
    "# Документ\n\n[клик](javascript:alert(1)) [сайт](https://example.com)\n\n"
    "<img src=x onerror=alert(1)> ![трекер](http://evil.example/p.png)\n\n<script>alert(1)</script>\n"
)
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
OUT = ROOT / "screenshots"


def wait_for_server():
    for _ in range(50):
        try:
            urllib.request.urlopen(URL, timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def check_hostile_document(browser, audio):
    """Model output is untrusted: links, images and scripts in it must be neutralised."""
    page = browser.new_page()
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.route("**/result.md", lambda route: route.fulfill(
        status=200, content_type="text/markdown; charset=utf-8", body=MALICIOUS_MD))
    page.goto(URL)
    page.set_input_files("#file-input", str(audio))
    page.click("#upload-btn")
    expect(page.locator("#doc h1")).to_have_text("Документ", timeout=30000)
    assert page.locator("#doc a[href^='javascript']").count() == 0
    assert page.locator("#doc a[href='https://example.com']").get_attribute("rel") == "noopener noreferrer"
    assert page.locator("#doc img, #doc script").count() == 0
    page.wait_for_timeout(500)
    assert not dialogs, dialogs
    page.close()


def main():
    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    audio = make_media(tmp / "Лекция по истории интернета.wav", 8)
    env = {**os.environ, "VTD_FAKE_GROQ": "1", "VTD_FAKE_DELAY": "1.5", "DATA_DIR": str(tmp / "data"),
           "CHUNK_SECONDS": "3", "LLM_TPM": "1000000"}
    server = subprocess.Popen(
        [str(ROOT / ".venv/bin/uvicorn"), "--factory", "voice_to_doc.app:create_app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT, env=env,
    )
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for scheme in ("light", "dark"):
                for name, viewport in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 375, "height": 812})):
                    page = browser.new_page(viewport=viewport, color_scheme=scheme)
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
                    page.goto(URL)
                    expect(page.locator("#upload-form")).to_be_visible()
                    page.screenshot(path=OUT / f"home-{name}-{scheme}.png", full_page=True)
                    page.set_input_files("#file-input", str(audio))
                    page.click("#upload-btn")
                    expect(page.locator("#steps li.current")).to_be_visible()
                    page.screenshot(path=OUT / f"progress-{name}-{scheme}.png", full_page=True)
                    expect(page.locator("#doc h1")).to_have_text("Тестовая запись", timeout=30000)
                    page.screenshot(path=OUT / f"result-{name}-{scheme}.png", full_page=True)
                    scroll_width = page.evaluate("document.documentElement.scrollWidth")
                    assert scroll_width <= viewport["width"], f"horizontal scroll on {name}: {scroll_width}px"
                    assert not errors, errors
                    page.close()
            check_hostile_document(browser, audio)
            browser.close()
        print(f"OK — screenshots in {OUT}")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
