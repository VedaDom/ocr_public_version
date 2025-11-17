from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import threading
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.api.v1.router import api_router

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



app.include_router(api_router, prefix=settings.api_v1_prefix)

os.makedirs("app/static/admin", exist_ok=True)
os.makedirs("app/uploads", exist_ok=True)
os.makedirs("app/tmp", exist_ok=True)
app.mount("/admin", StaticFiles(directory="app/static/admin", html=True), name="admin")
app.mount("/uploads", StaticFiles(directory="app/uploads"), name="uploads")


def _cleanup_temp_files_once():
    if not settings.temp_cleanup_enabled:
        return
    tmp_dir = os.path.abspath(os.path.join("app", "tmp"))
    now = datetime.now(timezone.utc).timestamp()
    ttl = max(60, int(settings.temp_cleanup_ttl_seconds))

    def _is_under(path: str, base: str) -> bool:
        try:
            rp = os.path.realpath(path)
            rb = os.path.realpath(base)
            return os.path.commonpath([rp, rb]) == rb
        except Exception:
            return False

    try:
        from app.infrastructure.db import SessionLocal
        from app.domain.models.ocr_job import OcrJob
        from app.domain.models.document import Document
        from app.domain.models.template_gen_job import TemplateGenJob
    except Exception:
        # If DB not ready, skip this run
        return

    try:
        db = SessionLocal()
        try:
            for root, _dirs, files in os.walk(tmp_dir):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        if not _is_under(path, tmp_dir):
                            continue
                        st = os.stat(path)
                        age = now - st.st_mtime
                        if age < ttl:
                            continue
                        file_url = f"file://{os.path.abspath(path)}"
                        # Skip if referenced by active jobs
                        active = False
                        # Active OCR jobs referencing this document file
                        q1 = (
                            db.query(OcrJob)
                              .join(Document)
                              .filter(Document.url == file_url)
                              .filter(OcrJob.status.in_([OcrJob.Status.queued, OcrJob.Status.running]))
                              .first()
                        )
                        if q1 is not None:
                            active = True
                        # Active template-gen jobs referencing this file
                        if not active:
                            q2 = (
                                db.query(TemplateGenJob)
                                  .filter(TemplateGenJob.pdf_url == file_url)
                                  .filter(TemplateGenJob.status.in_(["queued", "running"]))
                                  .first()
                            )
                            if q2 is not None:
                                active = True
                        if active:
                            continue
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                    except Exception:
                        continue
        finally:
            db.close()
        # Optionally remove empty directories under tmp
        for root, dirs, _files in os.walk(tmp_dir, topdown=False):
            for d in dirs:
                p = os.path.join(root, d)
                try:
                    if _is_under(p, tmp_dir) and not os.listdir(p):
                        os.rmdir(p)
                except Exception:
                    pass
    except Exception:
        pass


def _start_temp_cleanup_thread_once():
    if getattr(app.state, "_temp_cleanup_started", False):
        return
    app.state._temp_cleanup_started = True

    def _runner():
        # Stagger initial run slightly to allow app to finish startup
        time.sleep(5)
        interval = max(60, int(settings.temp_cleanup_interval_seconds))
        while True:
            try:
                _cleanup_temp_files_once()
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_runner, name="temp-cleaner", daemon=True)
    t.start()


@app.on_event("startup")
def _on_startup_temp_cleanup():
    if settings.temp_cleanup_enabled:
        _start_temp_cleanup_thread_once()

@app.get("/")
def root():
    return {"status": "ok"}
