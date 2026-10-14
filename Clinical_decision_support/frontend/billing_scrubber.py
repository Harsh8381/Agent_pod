from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from frontend.chargemaster import price_cpt_bill_lines
from cpt_coder import ADDITIONAL_CPT_FALLBACKS, COMMON_CPT_FALLBACKS, load_cpt_data


IMAGING_CODES = {"71045", "71046", "70450", "70460", "71250"}
DOCUMENTED_CPT_TERMS = {
    "71045": ("chest x ray", "chest radiograph"),
    "71046": ("chest x ray", "chest radiograph"),
    "70450": ("ct head", "ct scan of head", "ct scan head", "computed tomography head"),
    "70460": ("ct head", "ct scan of head", "ct scan head", "computed tomography head"),
    "71250": ("ct chest", "ct scan of chest", "computed tomography chest"),
    "83036": ("a1c", "hba1c", "hemoglobin a1c"),
    "80061": ("lipid panel", "cholesterol panel", "cholesterol testing"),
    "80053": ("comprehensive metabolic panel", "cmp"),
}
CHRONIC_CONDITION_PATTERNS = {
    "diabetes": r"\bdiabetes\b|\bdiabetic\b",
    "hypertension": r"\bhypertension\b|\bhigh blood pressure\b",
    "hyperlipidemia": r"\b(?:hyperlipidemia|high cholesterol|dyslipidemia)\b",
    "asthma": r"\basthma\b",
    "chronic kidney disease": r"\b(?:chronic kidney disease|ckd)\b",
    "copd": r"\b(?:copd|chronic obstructive pulmonary disease)\b",
    "heart failure": r"\b(?:heart failure|chf)\b",
}


def _code(value: Any) -> str:
    return re.sub(r"[\s.-]", "", str(value or "")).upper()


def _amount(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


def _documentation_text(documentation: str) -> str:
    text = str(documentation or "").casefold()
    text = re.sub(r"\bx[ -]?ray\b|\bxray\b", "x ray", text)
    text = re.sub(r"\bchest radiographs?\b", "chest x ray", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _expected_imaging(documentation: str) -> list[tuple[str, str]]:
    text = _documentation_text(documentation)
    expected = []
    chest_xray = "chest x ray" in text
    if chest_xray and re.search(r"\b(?:two|2)\s*(?:views?|view)\b", text):
        expected.append(("71046", "Two-view chest X-ray"))
    elif chest_xray and re.search(r"\b(?:one|1)\s*(?:views?|view)\b", text):
        expected.append(("71045", "Single-view chest X-ray"))
    if "ct head" in text or ("ct scan" in text and "head" in text):
        if "without contrast" in text:
            expected.append(("70450", "CT head without contrast"))
        elif "with contrast" in text:
            expected.append(("70460", "CT head with contrast"))
    return expected


def _imaging_family(code: str) -> str | None:
    if code in {"71045", "71046"}:
        return "chest_xray"
    if code in {"70450", "70460", "71250"}:
        return "ct_imaging"
    return None


def _valid_cpt_codes() -> set[str]:
    dataframe = load_cpt_data()
    codes = set(dataframe["code"].astype(str).map(_code)) if dataframe is not None and not dataframe.empty else set()
    for entry in (*COMMON_CPT_FALLBACKS.values(), *ADDITIONAL_CPT_FALLBACKS.values()):
        if entry.get("code"):
            codes.add(_code(entry["code"]))
    return codes


def _price_rows_by_code(priced_lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_code(line.get("cpt_hcpcs")): line for line in priced_lines if _code(line.get("cpt_hcpcs"))}


def _replacement_price(code: str, quantity: Any, encounter_type: str) -> Decimal | None:
    try:
        rows = price_cpt_bill_lines(
            [{"CPT/HCPCS Code": code, "quantity": quantity}],
            encounter_type=encounter_type,
        )
    except (OSError, ValueError, KeyError):
        return None
    return _amount(rows[0].get("gross_line_total_usd")) if rows else None


def scrub_claim(
    documentation: str,
    services: list[dict[str, Any]],
    priced_lines: list[dict[str, Any]] | None = None,
    encounter_type: str = "OPD",
    valid_cpt_codes: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate billed CPTs and return a claim-scrub report without modifying services."""
    priced_lines = priced_lines if priced_lines is not None else price_cpt_bill_lines(services, encounter_type)
    prices = _price_rows_by_code(priced_lines)
    valid_codes = _valid_cpt_codes()
    if valid_cpt_codes:
        valid_codes.update(_code(value) for value in valid_cpt_codes if _code(value))
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    billed_codes = [_code(service.get("CPT/HCPCS Code") or service.get("CPT_HCPCS") or service.get("code")) for service in services]
    billed_codes = [code for code in billed_codes if code]

    for service in services:
        code = _code(service.get("CPT/HCPCS Code") or service.get("CPT_HCPCS") or service.get("code"))
        if not code:
            errors.append({"type": "invalid_cpt", "code": "", "message": "A billed service is missing its CPT/HCPCS code."})
            continue
        if code not in valid_codes:
            errors.append({"type": "invalid_cpt", "code": code, "message": f"CPT {code} is not present in the active CPT reference."})
        price = prices.get(code)
        if price is None or price.get("unit_charge_usd") is None or price.get("rate_source_status") != "Found":
            status = price.get("rate_source_status", "No chargemaster result") if price else "No chargemaster result"
            errors.append({"type": "unpriced_cpt", "code": code, "message": f"CPT {code} has no valid chargemaster price ({status})."})

    expected_imaging = _expected_imaging(documentation)
    for expected_code, service_name in expected_imaging:
        expected_family = _imaging_family(expected_code)
        family_codes = [code for code in billed_codes if _imaging_family(code) == expected_family]
        if expected_code in billed_codes:
            wrong_codes = [code for code in family_codes if code != expected_code]
            for wrong_code in wrong_codes:
                errors.append({
                    "type": "imaging_mismatch",
                    "code": wrong_code,
                    "expected_code": expected_code,
                    "message": f"Documentation supports {service_name} (CPT {expected_code}), but CPT {wrong_code} is billed.",
                })
                service = next((item for item in services if _code(item.get("CPT/HCPCS Code") or item.get("CPT_HCPCS") or item.get("code")) == wrong_code), {})
                old_line = prices.get(wrong_code, {})
                quantity = service.get("quantity", service.get("Quantity", 1))
                new_total = _replacement_price(expected_code, quantity, encounter_type)
                old_total = _amount(old_line.get("gross_line_total_usd"))
                delta = new_total - old_total if new_total is not None and old_total is not None else None
                recommendations.append({
                    "from_code": wrong_code,
                    "to_code": expected_code,
                    "reason": f"Use the CPT matching documented {service_name}.",
                    "estimated_revenue_delta_usd": float(delta) if delta is not None else None,
                })
        else:
            imaging_codes = family_codes
            if imaging_codes:
                wrong_code = imaging_codes[0]
                errors.append({
                    "type": "imaging_mismatch",
                    "code": wrong_code,
                    "expected_code": expected_code,
                    "message": f"Documentation supports {service_name} (CPT {expected_code}), but billed imaging CPT(s) are {', '.join(imaging_codes)}.",
                })
                service = next((item for item in services if _code(item.get("CPT/HCPCS Code") or item.get("CPT_HCPCS") or item.get("code")) == wrong_code), {})
                old_total = _amount(prices.get(wrong_code, {}).get("gross_line_total_usd"))
                new_total = _replacement_price(
                    expected_code,
                    service.get("quantity", service.get("Quantity", 1)),
                    encounter_type,
                )
                delta = new_total - old_total if new_total is not None and old_total is not None else None
                recommendations.append({
                    "from_code": wrong_code,
                    "to_code": expected_code,
                    "reason": f"Replace the imaging CPT with the code matching documented {service_name}.",
                    "estimated_revenue_delta_usd": float(delta) if delta is not None else None,
                })
            else:
                errors.append({
                    "type": "missing_imaging_cpt",
                    "code": expected_code,
                    "expected_code": expected_code,
                    "message": f"Documentation supports {service_name} (CPT {expected_code}), but no matching imaging CPT is billed.",
                })
                recommendations.append({
                    "from_code": None,
                    "to_code": expected_code,
                    "reason": f"Consider adding CPT {expected_code} for documented {service_name} after coding review.",
                    "estimated_revenue_delta_usd": None,
                })

    normalized_documentation = _documentation_text(documentation)
    for code in dict.fromkeys(billed_codes):
        support_terms = DOCUMENTED_CPT_TERMS.get(code)
        if support_terms and not any(term in normalized_documentation for term in support_terms):
            errors.append({
                "type": "unsupported_cpt",
                "code": code,
                "message": f"Billed CPT {code} is not supported by the supplied provider documentation; review for possible over-coding.",
            })

    chronic_conditions = [
        name for name, pattern in CHRONIC_CONDITION_PATTERNS.items()
        if re.search(pattern, normalized_documentation)
    ]
    has_diagnostic_workup = bool(re.search(
        r"\b(?:x ray|ct scan|ct head|mri|ultrasound|imaging|laboratory|lab test|blood test|a1c|hba1c|lipid panel|metabolic panel|cbc)\b",
        normalized_documentation,
    ))
    if len(chronic_conditions) >= 2 and has_diagnostic_workup:
        em_codes = [code for code in billed_codes if code in {"99213", "99214"}]
        warnings.append({
            "type": "em_level_review",
            "code": ", ".join(em_codes),
            "message": (
                "Documentation includes multiple chronic conditions plus diagnostic workup. "
                "Review whether 99213 or 99214 is supported; do not automatically upcode."
            ),
        })
        em_delta = None
        if em_codes:
            current_code = em_codes[0]
            alternate_code = "99214" if current_code == "99213" else "99213"
            service = next((item for item in services if _code(item.get("CPT/HCPCS Code") or item.get("CPT_HCPCS") or item.get("code")) == current_code), {})
            current_total = _amount(prices.get(current_code, {}).get("gross_line_total_usd"))
            alternate_total = _replacement_price(
                alternate_code,
                service.get("quantity", service.get("Quantity", 1)),
                encounter_type,
            )
            if current_total is not None and alternate_total is not None:
                em_delta = float(alternate_total - current_total)
        recommendations.append({
            "from_code": em_codes[0] if em_codes else None,
            "to_code": "99213 vs 99214",
            "reason": "Manual E/M level review recommended based on documented complexity and medical decision-making.",
            "estimated_revenue_delta_usd": em_delta,
        })
    if not str(documentation or "").strip():
        warnings.append({
            "type": "missing_documentation",
            "code": "",
            "message": "Provider documentation was not supplied; documentation-to-CPT validation is limited.",
        })

    billed_total = sum(
        (_amount(line.get("gross_line_total_usd")) or Decimal("0"))
        for line in priced_lines
    )
    estimated_deltas = [
        _amount(item.get("estimated_revenue_delta_usd"))
        for item in recommendations
        if item.get("estimated_revenue_delta_usd") is not None
    ]
    revenue_delta = sum(estimated_deltas, Decimal("0")) if estimated_deltas else Decimal("0")
    errors_count = len(errors)
    warnings_count = len(warnings)
    scrub_score = max(0, 100 - 30 * errors_count - 10 * warnings_count)
    return {
        "errors": errors,
        "warnings": warnings,
        "recommended_cpt_changes": recommendations,
        "revenue_impact": {
            "billed_priced_total_usd": float(billed_total),
            "estimated_change_usd": float(revenue_delta),
            "unpriced_code_count": sum(issue["type"] == "unpriced_cpt" for issue in errors),
        },
        "scrub_score": scrub_score,
        "blocking": bool(errors),
    }