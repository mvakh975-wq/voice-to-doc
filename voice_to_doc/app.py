"""HTTP API and static page. Run: uvicorn --factory voice_to_doc.app:create_app"""

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from voice_to_doc.config import load_settings, save_api_key
from voice_to_doc.groq_client import make_client
from voice_to_doc.jobs import KINDS, JobStore, Pipeline, Worker

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 2 * 1024**3
MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class KeyIn(BaseModel):
    groq_api_key: str


class KindIn(BaseModel):
    kind: str


def create_app(settings_loader=load_settings, client_factory=make_client, inline: bool = False) -> FastAPI:
    store = JobStore(settings_loader().data_dir)
    pipeline = Pipeline(store, settings_loader, client_factory)
    worker = Worker(pipeline, inline=inline)
    queued = store.recover_interrupted()
    if not inline:
        worker.start()
    for job_id in queued:
        worker.submit(job_id)

    app = FastAPI(title="voice-to-doc")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def job_or_404(job_id: str) -> dict:
        try:
            return store.get(job_id)
        except KeyError:
            raise HTTPException(404, "Запись не найдена") from None

    def check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise HTTPException(400, "Тип записи должен быть «лекция» или «интервью»")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/settings")
    def get_settings():
        s = settings_loader()
        return {"has_key": bool(s.groq_api_key) or s.fake_groq}

    @app.post("/api/settings")
    def set_settings(body: KeyIn):
        try:
            save_api_key(body.groq_api_key, settings_loader().env_path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"has_key": True}

    @app.post("/api/jobs")
    async def create_job(file: UploadFile = File(...), kind: str = Form(...)):
        check_kind(kind)
        job = store.create(file.filename or "запись", kind)
        size = 0
        with open(store.dir(job["id"]) / job["source"], "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    break
                out.write(chunk)
        if size > MAX_UPLOAD:
            store.delete(job["id"])
            raise HTTPException(413, "Файл больше 2 ГБ")
        worker.submit(job["id"])
        return {"id": job["id"]}

    @app.get("/api/jobs")
    def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id)

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        if job_or_404(job_id)["status"] not in ("paused", "error"):
            raise HTTPException(409, "Запись уже обрабатывается или готова")
        store.update(job_id, status="queued", message="", reason=None)
        worker.submit(job_id)
        return store.get(job_id)

    @app.post("/api/jobs/{job_id}/restructure")
    def restructure(job_id: str, body: KindIn):
        job = job_or_404(job_id)
        check_kind(body.kind)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "Запись ещё обрабатывается")
        if not (store.dir(job_id) / "transcript.json").exists():
            raise HTTPException(409, "Сначала нужна расшифровка")
        pipeline.restructure(job_id, body.kind)
        worker.submit(job_id)
        return store.get(job_id)

    def result(job_id: str, fmt: str):
        job = job_or_404(job_id)
        path = store.dir(job_id) / f"result.{fmt}"
        if job["status"] != "done" or not path.exists():
            raise HTTPException(404, "Документ ещё не готов")
        return FileResponse(path, media_type=MEDIA_TYPES[fmt], filename=f"{Path(job['filename']).stem}.{fmt}")

    @app.get("/api/jobs/{job_id}/result.md")
    def result_md(job_id: str):
        return result(job_id, "md")

    @app.get("/api/jobs/{job_id}/result.docx")
    def result_docx(job_id: str):
        return result(job_id, "docx")

    return app
