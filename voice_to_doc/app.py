"""HTTP API and static page. Run: uvicorn --factory voice_to_doc.app:create_app"""

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from voice_to_doc.config import load_settings, save_api_key
from voice_to_doc.groq_client import make_client
from voice_to_doc.jobs import KINDS, JobStore, Pipeline, Worker

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 2 * 1024**3
UPLOAD_OVERHEAD = 1024**2  # multipart boundaries and form fields
MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "; ".join([
        "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:",
        "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'",
        "frame-ancestors 'none'",
    ]),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _hostname(netloc: str) -> str:
    return urlsplit(f"//{netloc}").hostname or ""


def _is_cross_site(request: Request) -> bool:
    # Browsers attach Origin / Sec-Fetch-Site to every cross-site POST; non-browser clients may omit them.
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
        return True
    return request.headers.get("sec-fetch-site") in ("cross-site", "same-site")


class UploadTooLarge(Exception):
    pass


def save_upload(store: JobStore, filename: str, kind: str, fileobj) -> dict:
    """Copy an upload into a new job; on any failure no half-written job is left behind."""
    job = store.create(filename, kind)
    try:
        size = 0
        with open(store.dir(job["id"]) / job["source"], "wb") as out:
            while chunk := fileobj.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise UploadTooLarge
                out.write(chunk)
    except BaseException:
        store.delete(job["id"])
        raise
    return store.update(job["id"], status="queued")


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

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host check stops DNS rebinding; the cross-site check stops other web pages
        # in the user's browser from driving this local API (CSRF).
        length = request.headers.get("content-length", "")
        if _hostname(request.headers.get("host", "")) not in ALLOWED_HOSTS:
            response = PlainTextResponse("Unknown host", status_code=400)
        elif request.method not in SAFE_METHODS and _is_cross_site(request):
            response = JSONResponse({"detail": "Запрос с чужого сайта отклонён"}, status_code=403)
        elif length.isdigit() and int(length) > MAX_UPLOAD + UPLOAD_OVERHEAD:
            # Starlette spools the whole body to disk before the handler runs: refuse early.
            response = JSONResponse({"detail": "Файл больше 2 ГБ"}, status_code=413)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

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
    def create_job(file: UploadFile = File(...), kind: str = Form(...)):
        # Sync handler: FastAPI runs it in a thread pool, so copying a big file
        # does not block the event loop.
        check_kind(kind)
        try:
            job = save_upload(store, file.filename or "запись", kind, file.file)
        except UploadTooLarge:
            raise HTTPException(413, "Файл больше 2 ГБ") from None
        worker.submit(job["id"])
        return {"id": job["id"]}

    @app.get("/api/jobs")
    def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        job_or_404(job_id)
        try:
            deleted = store.delete_if_idle(job_id)
        except KeyError:
            raise HTTPException(404, "Запись не найдена") from None
        if not deleted:
            raise HTTPException(409, "Запись ещё обрабатывается — удалить можно после остановки")
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        job_or_404(job_id)
        if not store.transition(job_id, ("paused", "error"), status="queued", message="", reason=None):
            raise HTTPException(409, "Запись уже обрабатывается или готова")
        worker.submit(job_id)
        return store.get(job_id)

    @app.post("/api/jobs/{job_id}/restructure")
    def restructure(job_id: str, body: KindIn):
        job_or_404(job_id)
        check_kind(body.kind)
        if not (store.dir(job_id) / "transcript.json").exists():
            raise HTTPException(409, "Сначала нужна расшифровка")
        if not store.transition(job_id, ("done", "paused", "error"), status="queued"):
            raise HTTPException(409, "Запись ещё обрабатывается")
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
