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
        # Allow configuration via settings
        self._model = getattr(settings, "gemini_model", "gemini-2.5-pro")

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
        settings = get_settings()
        langs = getattr(settings, "document_languages", ["en"]) or ["en"]
        lang_hint = ", ".join(langs)
        base = (
            "You are an OCR extraction system. The document may be bilingual/multilingual. "
            f"Prioritize and understand content in these languages: {lang_hint}. "
            "The page may include printed headings and handwritten entries. Read both carefully. "
            "Return ONLY JSON that strictly conforms to the provided schema. Do not add extra keys. "
            "If a field is not present, return an empty string for that field. "
            "For numbers return numeric types where possible. For dates, normalize to ISO YYYY-MM-DD when feasible. "
            "Do NOT translate or normalize proper nouns (people or location names). Preserve original spelling, casing, "
            "and diacritics exactly as written, including Kinyarwanda orthography. Allow hyphens, apostrophes and spaces "
            "in names; do not spell-correct or anglicize. "
            "If multiple candidates exist for a field, choose the one closest to the labeled area on the form. "
        )

        if fields:
            # Provide per-field guidance using label and description to help map FR/RW labels
            lines: List[str] = []
            for f in fields:
                desc = (f.description or "").strip()
                lines.append(
                    "- Field '" + f.name + "' (type=" + (f.field_type or "string") + ")\n  "
                    "Label: '" + (f.label or f.name) + "'\n  "
                    + ("Hints: " + desc if desc else "")
                )
            guidance = "\n".join(lines)
            return (
                base
                + "\n\nExtract the following fields using their labels and hints (labels may appear in French/Kinyarwanda/English):\n"
                + guidance
            )
        return base + " Return only valid JSON."

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
            temperature=0.0,
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
