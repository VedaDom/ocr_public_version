from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime, timezone
import time
import io
import re
import unicodedata
import os
import logging

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.infrastructure.db import SessionLocal
from app.domain.models.ocr_job import OcrJob
from app.domain.models.document import Document
from app.domain.models.template import DocumentTemplate
from app.domain.models.extracted_field import ExtractedField
from app.domain.models.credit_usage import CreditUsage
from app.services.ocr.gemini import GeminiProvider
from app.core.config import get_settings
import httpx
from pypdf import PdfReader
from app.services.rate_limit import get_limiter
from app.services.analytics import send_analytics

UTC = timezone.utc


_CYR_TO_LAT = {
    "А": "A", "В": "B", "С": "C", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "Т": "T", "У": "Y", "Х": "X",
    "а": "a", "с": "c", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "т": "t", "у": "y", "х": "x",
    "І": "I", "і": "i", "Ј": "J", "ј": "j", "П": "P",
}


def _to_latin_lookalike(s: str) -> str:
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in s)


def _contains_non_latin_alpha(s: str) -> bool:
    for ch in s:
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            if "LATIN" not in name and not ("GREEK" not in name and "CYRILLIC" not in name):
                return True
    return False


def _normalize_sex(s: str) -> str | None:
    t = s.strip().lower()
    mapping = {
        "m": "Gabo", "male": "Gabo", "homme": "Gabo", "masculin": "Gabo", "gabo": "Gabo",
        "f": "Gore", "female": "Gore", "femme": "Gore", "feminin": "Gore", "féminin": "Gore", "gore": "Gore",
    }
    return mapping.get(t)


def _normalize_field_value(raw: str, field: DocumentTemplateField) -> tuple[str, float]:
    s = str(raw or "")
    penalty = 0.0
    if _contains_non_latin_alpha(s):
        s = _to_latin_lookalike(s)
        penalty = max(penalty, 0.6)
    # Standardize whitespace
    s = " ".join(s.split())
    fname = (getattr(field, "name", "") or "").lower()
    ftype = (getattr(field, "field_type", "") or "").lower()
    # Sex normalization
    if "sex" in fname:
        norm = _normalize_sex(s)
        if norm and norm != s:
            s = norm
            penalty = max(penalty, 0.2)
    # Year/number normalization: strip trailing .0
    if "year" in fname or ftype in ("number", "int", "integer"):
        if re.fullmatch(r"\d+\.0", s):
            s = s[:-2]
            penalty = max(penalty, 0.2)
    return s, penalty


def _guess_content_type_from_url(url: str) -> str:
    ct, _ = mimetypes.guess_type(url)
    return ct or "application/pdf"


def process_ocr_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
        if not job:
            return

        # Transition to running if queued; track if this invocation started the job
        just_started = False
        try:
            if hasattr(OcrJob, "Status"):
                if job.status in (OcrJob.Status.succeeded, OcrJob.Status.failed, OcrJob.Status.cancelled):
                    return
                if job.status == OcrJob.Status.queued:
                    job.status = OcrJob.Status.running
                    just_started = True
            else:
                if str(job.status) in ("succeeded", "failed", "cancelled"):
                    return
                if str(job.status) == "queued":
                    job.status = "running"  # type: ignore
                    just_started = True
        except Exception:
            pass

        if not job.started_at:
            job.started_at = datetime.now(UTC)
        job.provider = "gemini"
        db.add(job)
        db.commit()

        # Callback will be fired after marking success

        # No credits logic in trimmed OCR service

        # Load document
        doc = db.query(Document).filter(Document.id == job.document_id).first()
        if not doc:
            raise RuntimeError("Document not found for job")

        page_bytes: bytes
        content_type: str | None = None
        tmp_path: str | None = None
        try:
            if isinstance(doc.url, str) and doc.url.startswith("file://"):
                tmp_path = doc.url[len("file://"):]
                with open(tmp_path, "rb") as f:
                    page_bytes = f.read()
                content_type = mimetypes.guess_type(tmp_path)[0]
            else:
                with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                    resp = client.get(doc.url)
                    resp.raise_for_status()
                    page_bytes = resp.content
                    content_type = resp.headers.get("content-type")
        except Exception as e:
            raise RuntimeError(f"failed to download document: {e}")
        if not content_type:
            content_type = _guess_content_type_from_url(tmp_path or doc.url)

        # Determine page count for credits
        credits_used = 1
        try:
            if (content_type or "").lower().startswith("application/pdf") or doc.url.lower().endswith(".pdf"):
                reader = PdfReader(io.BytesIO(page_bytes))
                credits_used = max(1, len(reader.pages))
        except Exception:
            credits_used = 1

        # Prepare provider
        provider = GeminiProvider()

        schema = None
        system_prompt = None
        fields = []
        if job.template_id is not None:
            tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == job.template_id).first()
            if tpl:
                fields = list(tpl.fields)
                schema = provider.build_schema_from_fields(fields)
                system_prompt = provider.build_system_prompt(fields)
        if schema is None:
            system_prompt = provider.build_system_prompt()

        # Extract with rate limiting
        limiter = get_limiter()
        queue_size = limiter.acquire()
        t0 = time.monotonic()
        try:
            result = provider.extract(
                page_bytes=page_bytes,
                content_type=content_type,
                schema=schema,
                system_prompt=system_prompt,
            )
        finally:
            limiter.release()

        # Persist extracted fields if template present
        if fields:
            for f in fields:
                val = result.get(f.name, "") if isinstance(result, dict) else ""
                conf = None
                if isinstance(val, dict):
                    v = val.get("value", "")
                    c = val.get("confidence", None)
                    try:
                        conf = float(c) if c is not None else None
                    except Exception:
                        conf = None
                    val = v
                # Normalize value and adjust confidence
                norm_val, penalty = _normalize_field_value(str(val), f)
                if conf is None:
                    conf = 0.5
                if penalty > 0:
                    conf = max(0.0, min(1.0, conf * (1.0 - penalty)))
                rec = ExtractedField(
                    document_id=doc.id,
                    template_field_id=f.id,
                    extracted_value=norm_val,
                    value=norm_val,
                    confidence=conf,
                )
                db.add(rec)

        # Mark success
        try:
            if hasattr(OcrJob, "Status"):
                job.status = OcrJob.Status.succeeded
            else:
                job.status = "succeeded"  # type: ignore
        except Exception:
            pass
        job.completed_at = datetime.now(UTC)
        db.add(job)
        db.commit()

        # Cleanup local temp file on success (if input was a file:// URL)
        try:
            if tmp_path and os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

        # Record credit usage (success)
        try:
            duration_ms = int((time.monotonic() - t0) * 1000)
            cu = CreditUsage(
                job_id=job.id,
                document_id=doc.id,
                template_id=job.template_id,
                credits_used=int(credits_used),
                status="succeeded",
                error_message="",
                queue_size=int(queue_size),
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                duration_ms=duration_ms,
            )
            db.add(cu)
            db.commit()

            # Analytics
            total_credits = db.query(func.coalesce(func.sum(CreditUsage.credits_used), 0)).scalar() or 0
            payload = {
                "type": "ocr_job",
                "job_id": str(job.id),
                "document_id": str(doc.id),
                "template_id": (str(job.template_id) if job.template_id else None),
                "provider": job.provider,
                "status": "succeeded",
                "error_message": None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "duration_ms": duration_ms,
                "queue_size": int(queue_size),
                "credits_used": int(credits_used),
                "total_credits": int(total_credits),
                "document": {
                    "reference_id": doc.reference_id,
                    "url": doc.url,
                    "content_type": content_type,
                },
            }
            send_analytics(payload)
        except Exception:
            pass

        # Fire callback if configured on template (after success)
        try:
            if job.template_id is not None:
                tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == job.template_id).first()
                if tpl and getattr(tpl, "callback_url", None):
                    payload: dict = {
                        "job_id": str(job.id),
                        "status": str(job.status.value) if hasattr(job.status, "value") else str(job.status),
                        "document": {
                            "id": str(doc.id),
                            "reference_id": doc.reference_id,
                            "url": doc.url,
                        },
                        "template_id": str(tpl.id),
                        "extracted": (result if isinstance(result, dict) else {}),
                        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    }
                    try:
                        with httpx.Client(timeout=10.0) as client:
                            resp = client.post(tpl.callback_url, json=payload)
                            logging.info(
                                "callback success url=%s status=%s job_id=%s template_id=%s payload=%s",
                                tpl.callback_url,
                                getattr(resp, "status_code", None),
                                str(job.id),
                                str(tpl.id),
                                payload,
                            )
                    except Exception as e:
                        logging.error(
                            "callback error url=%s job_id=%s template_id=%s error=%s payload=%s",
                            tpl.callback_url,
                            str(job.id),
                            str(tpl.id),
                            str(e),
                            payload,
                        )
        except Exception:
            pass
    except Exception as e:
        # Mark failure
        try:
            job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
            if job:
                try:
                    if hasattr(OcrJob, "Status"):
                        job.status = OcrJob.Status.failed
                    else:
                        job.status = "failed"  # type: ignore
                except Exception:
                    pass
                job.error_message = (str(e) or "error")[:2000]
                job.completed_at = datetime.now(UTC)
                db.add(job)
                db.commit()
                # Failure callback
                try:
                    if job.template_id is not None:
                        tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == job.template_id).first()
                        doc = db.query(Document).filter(Document.id == job.document_id).first()
                        if tpl and getattr(tpl, "callback_url", None) and doc:
                            payload: dict = {
                                "job_id": str(job.id),
                                "status": str(job.status.value) if hasattr(job.status, "value") else str(job.status),
                                "error_message": job.error_message,
                                "document": {
                                    "id": str(doc.id),
                                    "reference_id": doc.reference_id,
                                    "url": doc.url,
                                },
                                "template_id": str(tpl.id),
                                "extracted": {},
                                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                            }
                            try:
                                with httpx.Client(timeout=10.0) as client:
                                    resp = client.post(tpl.callback_url, json=payload)
                                    logging.info(
                                        "callback failure url=%s status=%s job_id=%s template_id=%s payload=%s",
                                        tpl.callback_url,
                                        getattr(resp, "status_code", None),
                                        str(job.id),
                                        str(tpl.id),
                                        payload,
                                    )
                            except Exception as e:
                                logging.error(
                                    "callback failure error url=%s job_id=%s template_id=%s error=%s payload=%s",
                                    tpl.callback_url,
                                    str(job.id),
                                    str(tpl.id),
                                    str(e),
                                    payload,
                                )
                except Exception:
                    pass
                # Record credit usage (failure -> 0)
                try:
                    duration_ms = None
                    try:
                        if job.started_at and job.completed_at:
                            duration_ms = int((job.completed_at - job.started_at).total_seconds() * 1000)
                    except Exception:
                        pass
                    cu = CreditUsage(
                        job_id=job.id,
                        document_id=job.document_id,
                        template_id=job.template_id,
                        credits_used=0,
                        status="failed",
                        error_message=job.error_message,
                        queue_size=0,
                        created_at=job.created_at,
                        started_at=job.started_at,
                        completed_at=job.completed_at,
                        duration_ms=duration_ms,
                    )
                    db.add(cu)
                    db.commit()

                    total_credits = db.query(func.coalesce(func.sum(CreditUsage.credits_used), 0)).scalar() or 0
                    payload = {
                        "type": "ocr_job",
                        "job_id": str(job.id),
                        "document_id": str(job.document_id),
                        "template_id": (str(job.template_id) if job.template_id else None),
                        "provider": job.provider,
                        "status": "failed",
                        "error_message": job.error_message,
                        "created_at": job.created_at.isoformat() if job.created_at else None,
                        "started_at": job.started_at.isoformat() if job.started_at else None,
                        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                        "duration_ms": duration_ms,
                        "queue_size": 0,
                        "credits_used": 0,
                        "total_credits": int(total_credits),
                    }
                    send_analytics(payload)
                except Exception:
                    pass
        finally:
            pass
    finally:
        db.close()
