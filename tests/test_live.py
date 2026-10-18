"""Real Groq round trip. Run: .venv/bin/pytest -m live -s"""

import shutil
import subprocess
from dataclasses import replace

import pytest

from voice_to_doc.config import ENV_PATH, load_settings
from voice_to_doc.groq_client import make_client
from voice_to_doc.jobs import JobStore, Pipeline

TEXT = (
    "Добрый день. Сегодня мы поговорим об истории интернета. "
    "Всё началось в конце шестидесятых годов с сети ARPANET, которую создали для обмена данными между университетами. "
    "Первое сообщение передали в октябре тысяча девятьсот шестьдесят девятого года. "
    "Потом появились протоколы TCP и IP, и разные сети смогли общаться между собой. "
    "В начале девяностых Тим Бернерс-Ли придумал всемирную паутину: страницы, ссылки и браузер. "
    "С этого момента интернет стал доступен обычным людям, а не только учёным. "
    "На следующей лекции мы обсудим, как устроены поисковые системы."
)


@pytest.mark.live
def test_real_groq_end_to_end(tmp_path):
    settings = replace(load_settings(ENV_PATH), data_dir=tmp_path / "data")
    if not settings.groq_api_key:
        pytest.skip("нет GROQ_API_KEY в .env")
    speech = tmp_path / "speech.aiff"
    subprocess.run(["say", "-v", "Milena", "-o", str(speech), TEXT], check=True)
    store = JobStore(settings.data_dir)
    job = store.create("speech.aiff", "lecture")
    shutil.copy(speech, store.dir(job["id"]) / job["source"])
    Pipeline(store, lambda: settings, make_client).run(job["id"])
    job = store.get(job["id"])
    log = (store.dir(job["id"]) / "log.txt").read_text()
    assert job["status"] == "done", f"{job['message']}\n{log}"
    md = (store.dir(job["id"]) / "result.md").read_text()
    print("\n" + md)
    for word in ("arpanet", "интернет", "паутин"):
        assert word in md.lower()
    assert "не оформлен" not in md
