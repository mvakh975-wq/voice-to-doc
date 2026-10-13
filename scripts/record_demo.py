"""Record docs/images/demo.gif: upload a short synthetic lecture and show the result.

Uses the real Groq key from .env (about one minute of audio). Usage:
    .venv/bin/python scripts/record_demo.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8766
URL = f"http://127.0.0.1:{PORT}"
OUT = ROOT / "docs" / "images"
LECTURE = (
    "Добрый день, коллеги. Сегодня поговорим о том, как устроен интернет. "
    "Начнём с истории. В конце шестидесятых годов появилась сеть ARPANET, она связала несколько университетов. "
    "В семидесятых годах были созданы протоколы TCP и IP, и разные сети смогли обмениваться данными. "
    "Теперь о том, как интернет работает сегодня. Когда вы открываете сайт, браузер сначала узнаёт адрес сервера через DNS. "
    "Затем он отправляет запрос по протоколу HTTP, и сервер возвращает страницу. "
    "Подведём итог: интернет — это договорённости о том, как компьютеры передают друг другу данные."
)


def wait_for_server():
    for _ in range(50):
        try:
            urllib.request.urlopen(URL, timeout=1)  # nosec B310 — fixed local URL
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    audio = tmp / "Лекция — как устроен интернет.m4a"
    subprocess.run(["say", "-v", "Milena", "-o", str(audio), "--data-format=aac", LECTURE], check=True)
    env = {**os.environ, "DATA_DIR": str(tmp / "data")}
    server = subprocess.Popen(
        [str(ROOT / ".venv/bin/uvicorn"), "--factory", "voice_to_doc.app:create_app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env,
    )
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 1000, "height": 720}, color_scheme="light",
                                          record_video_dir=str(tmp / "video"),
                                          record_video_size={"width": 1000, "height": 720})
            page = context.new_page()
            page.goto(URL)
            page.wait_for_timeout(1200)
            page.set_input_files("#file-input", str(audio))
            page.wait_for_timeout(800)
            page.click("#upload-btn")
            expect(page.locator("#doc h1")).to_be_visible(timeout=180000)
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(2500)
            video = page.video.path()
            context.close()
            browser.close()
        page_shot = OUT / "demo.gif"
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        palette = tmp / "palette.png"
        filters = "fps=8,scale=800:-1:flags=lanczos"
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-i", video, "-vf", f"{filters},palettegen", str(palette)], check=True)
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-i", video, "-i", str(palette),
                        "-lavfi", f"{filters}[x];[x][1:v]paletteuse", str(page_shot)], check=True)
        print(f"OK — {page_shot} ({page_shot.stat().st_size // 1024} KB)")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    sys.exit(main())
