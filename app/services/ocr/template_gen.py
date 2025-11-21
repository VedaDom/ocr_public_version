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
        self._model = settings.gemini_model

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

    def _prompt(self, required_field_names: Optional[List[str]] | None = None) -> str:
        settings = get_settings()
        langs = getattr(settings, "document_languages", ["en"]) or ["en"]
        lang_hint = ", ".join(langs)
        base = (
            "You are a document template inference system. The document may be bilingual/multilingual. "
            f"Consider these languages: {lang_hint}. "
            "Analyze the uploaded PDF and infer a list of key-value fields that users would capture. "
            "Return ONLY JSON that conforms to the schema. Rules: "
            "1) fields is an array of objects with: name (snake_case), label (human readable), field_type (string|number|date|boolean), required (boolean), description (short guidance). "
            "2) Do not include example values, only field definitions. "
            "3) Use stable, machine-friendly names. "
            "4) Consider printed headings and handwritten inputs; include fields commonly filled by hand (e.g., signatures, handwritten notes). "
            "5) Where helpful, make the description include bilingual label synonyms observed on the page (e.g., FR/RW). "
        )

        if required_field_names:
            # Mention mandatory field names explicitly so the model includes them.
            mandatory_list = ", ".join(str(n).strip() for n in required_field_names if str(n).strip())
            if mandatory_list:
                base += (
                    "6) The following field names are MANDATORY and must appear at least once in the 'fields' array as the 'name' value, "
                    "spelled exactly as given (you may still add extra fields): "
                    f"{mandatory_list}. "
                )

        return base

    def generate(
        self,
        *,
        pdf_bytes: bytes,
        content_type: str = "application/pdf",
        required_field_names: Optional[List[str]] | None = None,
    ) -> Dict[str, Any]:
        parts: List[types.Part] = [types.Part.from_text(text=self._prompt(required_field_names))]
        parts.append(types.Part.from_bytes(data=pdf_bytes, mime_type=content_type))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=self._schema(),
            temperature=0.1,
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

        # Normalize required field names for matching while preserving the original spellings
        norm_required: set[str] = set()
        raw_required_map: dict[str, str] = {}
        if required_field_names:
            for raw in required_field_names:
                if not isinstance(raw, str):
                    continue
                raw_trim = raw.strip()
                if not raw_trim:
                    continue
                san = self._sanitize_name(raw_trim)
                if san in norm_required:
                    continue
                norm_required.add(san)
                # Preserve the original spelling for final field names
                raw_required_map[san] = raw_trim[:100]

        fields_out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for idx, f in enumerate(data["fields"]):
            if not isinstance(f, dict):
                continue
            raw_name = str(f.get("name") or f.get("label") or f"field_{idx+1}")
            san_name = self._sanitize_name(raw_name)

            # If this matches a required field (by sanitized form), keep the original spelling
            if san_name in raw_required_map:
                name = raw_required_map[san_name]
            else:
                name = san_name

            # Ensure uniqueness of names; avoid renaming mandatory ones, just skip duplicates
            if name in seen:
                # If this is a mandatory name, skip duplicate entries
                if san_name in norm_required:
                    continue
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

        # Ensure all mandatory fields are present at least once
        if norm_required:
            present_norm: set[str] = set()
            for f in fields_out:
                san = self._sanitize_name(str(f.get("name") or ""))
                if san in norm_required:
                    present_norm.add(san)

            missing = norm_required - present_norm
            for san in missing:
                orig = raw_required_map.get(san, san)
                label = orig.replace("_", " ").title()[:200]
                fields_out.append(
                    {
                        "name": orig[:100],
                        "label": label,
                        "field_type": "string",
                        "required": True,
                        "description": "",
                    }
                )

        return {"fields": fields_out}
