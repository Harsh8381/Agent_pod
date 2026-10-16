"""Local OCR for scanned clinical documents, insurance cards and paper superbills.

Text extraction runs entirely on this machine (RapidOCR on ONNX Runtime); no
document image is sent to an external service. Digital PDFs use their embedded
text layer and only fall back to OCR when a page has no text.
"""
from __future__ import annotations

import io
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_TYPES = ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp")
SUPPORTED_TYPES = (*IMAGE_TYPES, "pdf")
MAX_PDF_PAGES = 15
MIN_TEXT_CHARS_PER_PAGE = 40
PDF_RENDER_SCALE = 2.0


# ---------- Text extraction ----------
@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def ocr_available() -> bool:
    from importlib.util import find_spec

    return find_spec("rapidocr_onnxruntime") is not None


def _group_lines(boxes: list[Any]) -> list[tuple[str, float]]:
    """Merge OCR boxes on the same visual row into one line, left to right."""
    items = []
    for box, text, score in boxes or []:
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append({"top": min(ys), "bottom": max(ys), "left": min(xs), "text": text, "score": float(score)})
    items.sort(key=lambda item: (item["top"], item["left"]))
    rows: list[list[dict[str, Any]]] = []
    for item in items:
        center = (item["top"] + item["bottom"]) / 2
        row = rows[-1] if rows else None
        if row and abs(center - sum((i["top"] + i["bottom"]) / 2 for i in row) / len(row)) <= (item["bottom"] - item["top"]) * 0.6:
            row.append(item)
        else:
            rows.append([item])
    lines = []
    for row in rows:
        row.sort(key=lambda item: item["left"])
        lines.append((" ".join(item["text"] for item in row), sum(item["score"] for item in row) / len(row)))
    return lines


def _ocr_image(image) -> tuple[str, float | None]:
    import numpy as np

    result, _ = _engine()(np.array(image.convert("RGB")))
    lines = _group_lines(result)
    if not lines:
        return "", None
    return "\n".join(text for text, _ in lines), sum(score for _, score in lines) / len(lines)


def extract_text(data: bytes, filename: str) -> dict[str, Any]:
    """Return {"text", "pages", "method", "confidence", "warnings"} for an image or PDF."""
    extension = Path(filename or "").suffix.lower().lstrip(".")
    if extension not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported file type .{extension}. Use PDF, PNG, JPG, TIFF, BMP or WEBP.")
    warnings: list[str] = []
    if extension == "pdf":
        return _extract_pdf(data, warnings)
    if not ocr_available():
        raise RuntimeError("OCR engine is not installed. Run: pip install rapidocr_onnxruntime")
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    text, confidence = _ocr_image(image)
    if not text.strip():
        warnings.append("No text was recognised in the image.")
    return {"text": text, "pages": 1, "method": "ocr", "confidence": confidence, "warnings": warnings}


def _extract_pdf(data: bytes, warnings: list[str]) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        warnings.append(f"Only the first {MAX_PDF_PAGES} of {page_count} pages were processed.")
    texts, confidences, methods = [], [], set()
    pdfium_document = None
    for index, page in enumerate(reader.pages[:MAX_PDF_PAGES]):
        layer = (page.extract_text() or "").strip()
        if len(layer) >= MIN_TEXT_CHARS_PER_PAGE:
            texts.append(layer)
            methods.add("pdf-text")
            continue
        if not ocr_available():
            warnings.append(f"Page {index + 1} is scanned but the OCR engine is not installed.")
            texts.append(layer)
            continue
        import pypdfium2 as pdfium

        pdfium_document = pdfium_document or pdfium.PdfDocument(data)
        image = pdfium_document[index].render(scale=PDF_RENDER_SCALE).to_pil()
        text, confidence = _ocr_image(image)
        texts.append(text)
        methods.add("ocr")
        if confidence is not None:
            confidences.append(confidence)
    text = "\n\n".join(part for part in texts if part)
    if not text.strip():
        warnings.append("No text could be extracted from the PDF.")
    method = "mixed" if len(methods) > 1 else (methods.pop() if methods else "pdf-text")
    confidence = sum(confidences) / len(confidences) if confidences else None
    return {"text": text, "pages": min(page_count, MAX_PDF_PAGES), "method": method, "confidence": confidence, "warnings": warnings}


# ---------- Insurance card parsing ----------
PLAN_TYPES = ("PPO", "HMO", "EPO", "POS", "HDHP", "Medicare Advantage", "Medicaid", "Medicare")
PAYER_HINT = re.compile(r"insurance|health|healthcare|medical|blue|aetna|cigna|united|humana|kaiser|medicare|medicaid|anthem|care", re.IGNORECASE)


def _split_camel(text: str) -> str:
    """OCR sometimes drops spaces ("AthenaHealthInsurance"); restore word breaks."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)


def _find(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parse_insurance_card(text: str) -> dict[str, str]:
    lines = [_split_camel(line.strip()) for line in str(text or "").splitlines() if line.strip()]
    flat = "\n".join(lines)
    payer = next((line for line in lines[:4] if PAYER_HINT.search(line) and not re.search(r"\d{3,}", line)), lines[0] if lines else "")
    plan = next((plan for plan in PLAN_TYPES if re.search(rf"\b{re.escape(plan)}\b", flat, re.IGNORECASE)), "")
    copay = _find(r"(?:copay|co-pay)\D{0,12}\$\s*(\d+(?:\.\d{2})?)", flat)
    return {
        "payer_name": payer,
        "plan_type": plan,
        "member_name": _find(r"member\s*name\s*[:#]?\s*([A-Za-z][A-Za-z .'-]+)", flat),
        "member_id": _find(r"(?:member|subscriber|ID)\s*(?:ID|No\.?|number|#)?\s*[:#]\s*([A-Z0-9][A-Z0-9-]{3,})", flat)
        or _find(r"member\s*id\s*([A-Z0-9][A-Z0-9-]{3,})", flat),
        "group_number": _find(r"group\s*(?:no\.?|number|#|id)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{2,})", flat),
        "rx_bin": _find(r"rx\s*bin\s*[:#]?\s*(\d{6})", flat),
        "rx_pcn": _find(r"rx\s*pcn\s*[:#]?\s*([A-Z0-9]{2,10})", flat),
        "effective_date": _find(r"effective(?:\s*date)?\s*[:#]?\s*(\d{1,2}/\d{1,2}/\d{2,4})", flat),
        "copay_pcp": copay,
    }


# ---------- Superbill parsing ----------
@lru_cache(maxsize=1)
def icd10_reference() -> dict[str, str]:
    path = PROJECT_ROOT / "ICD10CM_2022_Codes.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(row.get("icd10_code", "")).upper(): str(row.get("description", "")) for row in rows if row.get("icd10_code")}


@lru_cache(maxsize=1)
def cpt_reference() -> dict[str, str]:
    """CPT/HCPCS descriptions from the CPT dataset, coder fallbacks and the chargemaster."""
    reference: dict[str, str] = {}
    try:
        from frontend.chargemaster import load_chargemaster

        frame, status = load_chargemaster()
        if status == "Found":
            for row in frame.to_dict("records"):
                reference.setdefault(str(row["code"]).upper(), str(row.get("description") or ""))
    except Exception:
        pass
    try:
        from cpt_coder import ADDITIONAL_CPT_FALLBACKS, COMMON_CPT_FALLBACKS, load_cpt_data

        data = load_cpt_data()
        for row in data.to_dict("records") if data is not None else []:
            reference[str(row["code"]).upper()] = str(row.get("description") or "")
        for entry in (*COMMON_CPT_FALLBACKS.values(), *ADDITIONAL_CPT_FALLBACKS.values()):
            if entry.get("code"):
                reference.setdefault(str(entry["code"]).upper(), str(entry.get("description") or ""))
    except Exception:
        pass
    return reference


ICD_START = re.compile(r"(?<![A-Za-z])[A-TV-Z]\d[0-9A-Z]")
CPT_CANDIDATE = re.compile(r"(?<![\d.])(\d{4}[0-9FTU]|[A-V]\d{4})(?![\d.])")
PROCEDURE_CONTEXT = re.compile(r"\b(?:cpt|hcpcs|procedure|proc|service|svc)\b", re.IGNORECASE)


def _normalize_ocr_codes(text: str) -> str:
    # Common OCR confusions inside code-like tokens: letter O for zero after a digit.
    return re.sub(r"(?<=\d)[Oo](?=\d)", "0", text)


def _scan_icd(line: str, icd_lookup: dict[str, str]) -> list[str]:
    """Find ICD-10 codes even when OCR has glued them together (E11.9J45.901R50.9)."""
    found, position = [], 0
    while True:
        match = ICD_START.search(line, position)
        if not match:
            return found
        start = match.start()
        best = None
        tail = re.match(r"\.([0-9A-Z]{1,4})", line[match.end():])
        candidates = [line[start:match.end()] + "." + tail.group(1)[:length] for length in range(len(tail.group(1)), 0, -1)] if tail else []
        candidates.append(line[start:match.end()])
        for candidate in candidates:
            following = line[start + len(candidate): start + len(candidate) + 1]
            if candidate.upper() in icd_lookup and not following.isdigit():
                best = candidate.upper()
                break
        if best:
            found.append(best)
            position = start + len(best)
        else:
            position = start + 1


def parse_superbill(
    text: str,
    icd_lookup: dict[str, str] | None = None,
    cpt_lookup: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract ICD-10-CM and CPT/HCPCS codes, units and date of service from superbill text."""
    icd_lookup = icd10_reference() if icd_lookup is None else icd_lookup
    cpt_lookup = cpt_reference() if cpt_lookup is None else cpt_lookup
    icd_codes: dict[str, dict[str, Any]] = {}
    cpt_codes: dict[str, dict[str, Any]] = {}
    for raw_line in _normalize_ocr_codes(str(text or "")).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        procedure_line = bool(PROCEDURE_CONTEXT.search(line))
        for code in _scan_icd(line, icd_lookup):
            icd_codes.setdefault(code, {"code": code, "description": icd_lookup.get(code, ""), "verified": True})
        for match in CPT_CANDIDATE.finditer(line):
            code = match.group(1).upper()
            known = code in cpt_lookup
            if not (known or procedure_line) or code in icd_codes:
                continue
            units = _find(r"(?:\bx\s*|units?\s*[:#]?\s*|qty\s*[:#]?\s*)(\d{1,3})\b", line[match.end():]) or "1"
            cpt_codes.setdefault(code, {
                "code": code,
                "description": cpt_lookup.get(code, ""),
                "verified": known,
                "units": int(units),
            })
    return {
        "icd10": list(icd_codes.values()),
        "cpt": list(cpt_codes.values()),
        "date_of_service": _find(r"\bDOS\b\s*[:#]?\s*(\d{1,2}/\d{1,2}/\d{2,4})", text or "")
        or _find(r"date\s*of\s*service\s*[:#]?\s*(\d{1,2}/\d{1,2}/\d{2,4})", text or ""),
    }


def superbill_code_results(parsed: dict[str, Any], source_label: str = "Superbill (OCR)") -> dict[str, list[dict[str, Any]]]:
    """Convert parsed superbill codes into the coding workspace's code_results shape."""
    return {
        "icd10": [
            {"Extracted Condition": source_label, "Matched Disease/Injury": item["description"], "ICD-10 Code": item["code"], "source": "ocr_superbill"}
            for item in parsed.get("icd10", [])
        ],
        "cpt": [
            {
                "Extracted Procedure": item["description"] or item["code"],
                "Matched Procedure/Service": item["description"] or "Not in CPT reference — verify",
                "CPT/HCPCS Code": item["code"],
                "quantity": item.get("units", 1),
                "source": "ocr_superbill",
            }
            for item in parsed.get("cpt", [])
        ],
    }


def merge_code_results(base: dict[str, Any] | None, imported: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """Matcher results plus imported codes, without duplicating a code already present."""
    base = base or {}
    merged = {"icd10": list(base.get("icd10") or []), "cpt": list(base.get("cpt") or [])}
    keys = {"icd10": "ICD-10 Code", "cpt": "CPT/HCPCS Code"}
    for kind, key in keys.items():
        present = {str(item.get(key, "")).upper() for item in merged[kind]}
        for item in (imported or {}).get(kind, []):
            if str(item.get(key, "")).upper() not in present:
                merged[kind].append(item)
                present.add(str(item.get(key, "")).upper())
    return merged


def coverage_from_card(fields: dict[str, str], patient_id: str, coverage_id: str) -> dict[str, Any]:
    """Map parsed card fields onto the EHR Insurance sheet columns."""
    copay = fields.get("copay_pcp") or ""
    return {
        "Coverage_ID": coverage_id,
        "Patient_ID": patient_id,
        "Priority": "Primary",
        "Payer_Name": fields.get("payer_name", ""),
        "Member_ID": fields.get("member_id", ""),
        "Group_Number": fields.get("group_number", ""),
        "Relationship_to_Subscriber": "Self",
        "Effective_Date": fields.get("effective_date", ""),
        "Status": "Active",
        "Plan_Type": fields.get("plan_type", ""),
        "Copay_USD": float(copay) if re.fullmatch(r"\d+(?:\.\d+)?", copay) else None,
        "Rx_BIN": fields.get("rx_bin", ""),
        "Rx_PCN": fields.get("rx_pcn", ""),
        "Data_Source": "OCR insurance card (verified by user)",
    }
