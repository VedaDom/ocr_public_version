from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.domain.models.document_template_field import DocumentTemplateField
from .provider import OcrProvider


class GeminiProvider(OcrProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = "gemini-1.5-flash-latest"

    def _map_field_type(self, ft: str) -> types.Schema:
        t = (ft or "").lower()
        if t in ("number", "float", "int", "integer"):
            return types.Schema(type=types.Type.NUMBER)
        if t in ("boolean", "bool"):
            return types.Schema(type=types.Type.BOOLEAN)
        return types.Schema(type=types.Type.STRING)

    def build_schema_from_fields(self, fields: List[DocumentTemplateField]) -> types.Schema:
        props: Dict[str, types.Schema] = {}
        required: List[str] = []
        for f in fields:
            props[f.name] = self._map_field_type(f.field_type)
            if f.required:
                required.append(f.name)
        return types.Schema(type=types.Type.OBJECT, properties=props, required=required)

    def build_system_prompt(self, fields: Optional[List[DocumentTemplateField]] = None) -> str:
        if fields:
            names = ", ".join([f.name for f in fields])
            return (
                "You are an OCR extraction system. Read the provided page (image or PDF). "
                "Extract the following fields strictly and return ONLY JSON that conforms to the schema. "
                "Do not include keys that are not defined. If a value is missing, return an empty string. "
                "Use numeric types for numbers and ISO format for dates where applicable. Fields: "
                + names
            )
        return (
            "You are an OCR extraction system. Read the provided page (image or PDF) and return structured JSON. "
            "Return only valid JSON."
        )

    def extract(
        self,
        *,
        page_bytes: bytes,
        content_type: str,
        schema: Optional[types.Schema] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompt = system_prompt or self.build_system_prompt()
        parts: List[types.Part] = [types.Part.from_text(text=prompt)]
        parts.append(types.Part.from_bytes(data=page_bytes, mime_type=content_type))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        text = getattr(resp, "text", None)
        if not text:
            # Some SDK versions expose aggregated candidates differently
            if hasattr(resp, "candidates") and resp.candidates:
                text = resp.candidates[0].content.parts[0].text
        if not text:
            raise RuntimeError("No text response from Gemini")
        try:
            data = json.loads(text)
        except Exception:
            # Best effort: try to trim non-json content
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start : end + 1])
            else:
                raise
        if not isinstance(data, dict):
            raise RuntimeError("Gemini returned non-object JSON")
        return data
