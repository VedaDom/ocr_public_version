from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.core.config import get_settings


class TemplateGenerator:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = "gemini-1.5-flash-latest"

    def _sanitize_name(self, s: str) -> str:
        s = s.strip().lower()
        s = re.sub(r"[^a-z0-9_\s-]", "", s)
        s = re.sub(r"[\s-]+", "_", s)
        if not s:
            s = "field"
        return s[:100]

    def _schema(self) -> types.Schema:
        field_obj = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "name": types.Schema(type=types.Type.STRING),
                "label": types.Schema(type=types.Type.STRING),
                "field_type": types.Schema(type=types.Type.STRING),
                "required": types.Schema(type=types.Type.BOOLEAN),
                "description": types.Schema(type=types.Type.STRING),
            },
            required=["name", "label", "field_type", "required"],
        )
        return types.Schema(
            type=types.Type.OBJECT,
            properties={
                "fields": types.Schema(type=types.Type.ARRAY, items=field_obj),
            },
            required=["fields"],
        )

    def _prompt(self) -> str:
        return (
            "You are a document template inference system. Analyze the uploaded PDF and infer a list of key-value fields "
            "that a business would want to capture (e.g., invoice_number, invoice_date, total_amount, vendor_name). "
            "Return ONLY JSON that conforms to the schema. Rules: "
            "1) fields should be an array of objects with: name (snake_case key), label (human readable), field_type (one of string, number, date, boolean), required (boolean), description (short guidance). "
            "2) Do not include values or example data, only field definitions. "
            "3) Use stable, machine-friendly names. "
            "4) Consider both printed and handwritten text; include fields commonly filled by hand (e.g., signature, handwritten totals, notes). "
        )

    def generate(self, *, pdf_bytes: bytes, content_type: str = "application/pdf") -> Dict[str, Any]:
        parts: List[types.Part] = [types.Part.from_text(text=self._prompt())]
        parts.append(types.Part.from_bytes(data=pdf_bytes, mime_type=content_type))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=self._schema(),
        )
        resp = self._client.models.generate_content(model=self._model, contents=contents, config=config)
        text = getattr(resp, "text", None)
        if not text and hasattr(resp, "candidates") and resp.candidates:
            text = resp.candidates[0].content.parts[0].text
        if not text:
            raise RuntimeError("No text from Gemini for template generation")
        try:
            data = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start : end + 1])
            else:
                raise
        if not isinstance(data, dict) or "fields" not in data or not isinstance(data["fields"], list):
            raise RuntimeError("Invalid schema response")
        fields_out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for idx, f in enumerate(data["fields"]):
            if not isinstance(f, dict):
                continue
            name = self._sanitize_name(str(f.get("name") or f.get("label") or f"field_{idx+1}"))
            if name in seen:
                name = f"{name}_{idx+1}"
            seen.add(name)
            label = str(f.get("label") or name.replace("_", " ").title())[:200]
            field_type = str(f.get("field_type") or "string").lower()
            required = bool(f.get("required") or False)
            description = str(f.get("description") or "")[:500]
            fields_out.append(
                {
                    "name": name,
                    "label": label,
                    "field_type": field_type,
                    "required": required,
                    "description": description,
                }
            )
        return {"fields": fields_out}
