import io

import pytest

from frontend.ocr_service import (
    coverage_from_card,
    extract_text,
    merge_code_results,
    ocr_available,
    parse_insurance_card,
    parse_superbill,
    superbill_code_results,
)

ICD = {"E11.9": "Type 2 diabetes mellitus without complications", "J45.901": "Unspecified asthma with (acute) exacerbation",
       "R50.9": "Fever, unspecified", "E11.65": "Type 2 diabetes mellitus with hyperglycemia"}
CPT = {"99214": "Office visit, established, moderate", "85025": "CBC with differential", "82947": "Glucose, quantitative"}

# Text exactly as the local OCR engine returned it for a synthetic card / superbill (spaces dropped).
CARD_OCR = """AthenaHealthInsurance
PPO Gold Plan
Member Name: Nandan Roy
MemberID:ATH-100053
Group No: ATH-GRP-2026
RxBIN:610014RxPCN:ATHRX
EffectiveDate:01/01/2026
Copay PCP $30 Specialist $50"""
SUPERBILL_OCR = """SUPERBILL-NorthstarMedical Center
Patient: Nandan Roy DOS: 10/07/2026
Diagnoses:E11.9J45.901R50.9
CPT 99214Establishedpatient officevisit x1
CPT85025CBCwithautomateddifferentialx1
CPT 82947 Gluc0se, quantitative, blood x2
Seattle WA 98101
Provider:Dr. Shashwat"""


def test_insurance_card_fields_survive_missing_spaces():
    fields = parse_insurance_card(CARD_OCR)
    assert fields["payer_name"] == "Athena Health Insurance"
    assert fields["plan_type"] == "PPO"
    assert fields["member_id"] == "ATH-100053"
    assert fields["group_number"] == "ATH-GRP-2026"
    assert fields["rx_bin"] == "610014"
    assert fields["effective_date"] == "01/01/2026"
    assert fields["copay_pcp"] == "30"


def test_card_maps_to_ehr_insurance_columns():
    row = coverage_from_card(parse_insurance_card(CARD_OCR), "P100053", "COV-OCR-1")
    assert row["Patient_ID"] == "P100053" and row["Priority"] == "Primary" and row["Copay_USD"] == 30.0


def test_superbill_splits_glued_icd_codes_and_reads_cpt_units():
    parsed = parse_superbill(SUPERBILL_OCR, ICD, CPT)
    assert [item["code"] for item in parsed["icd10"]] == ["E11.9", "J45.901", "R50.9"]
    cpt = {item["code"]: item for item in parsed["cpt"]}
    assert set(cpt) == {"99214", "85025", "82947"}
    assert cpt["82947"]["units"] == 2 and all(item["verified"] for item in cpt.values())
    assert parsed["date_of_service"] == "10/07/2026"


def test_zip_codes_and_ids_are_not_mistaken_for_cpt():
    parsed = parse_superbill("Seattle WA 98101\nMRN P100053\nMember ATH-100053", ICD, CPT)
    assert parsed["cpt"] == [] and parsed["icd10"] == []


def test_unknown_cpt_on_procedure_line_is_kept_but_unverified():
    parsed = parse_superbill("CPT 12345 Unknown service x1", ICD, CPT)
    assert parsed["cpt"][0]["code"] == "12345" and parsed["cpt"][0]["verified"] is False


def test_superbill_codes_merge_without_duplicates():
    parsed = parse_superbill(SUPERBILL_OCR, ICD, CPT)
    imported = superbill_code_results(parsed)
    base = {"icd10": [{"ICD-10 Code": "E11.9"}], "cpt": [{"CPT/HCPCS Code": "85025"}]}
    merged = merge_code_results(base, imported)
    assert [item["ICD-10 Code"] for item in merged["icd10"]] == ["E11.9", "J45.901", "R50.9"]
    assert [item["CPT/HCPCS Code"] for item in merged["cpt"]] == ["85025", "99214", "82947"]
    assert all(item.get("source") == "ocr_superbill" for item in merged["cpt"][1:])


def test_digital_pdf_uses_text_layer():
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = io.BytesIO()
    pdf = reportlab.Canvas(buffer)
    pdf.drawString(72, 720, "Discharge summary: community-acquired pneumonia treated with azithromycin.")
    pdf.drawString(72, 700, "Follow up with primary care in one week for repeat chest x-ray.")
    pdf.save()
    result = extract_text(buffer.getvalue(), "summary.pdf")
    assert result["method"] == "pdf-text" and "pneumonia" in result["text"]


def test_unsupported_file_type_is_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        extract_text(b"data", "notes.docx")


@pytest.mark.skipif(not ocr_available(), reason="rapidocr_onnxruntime not installed")
def test_image_ocr_reads_rendered_text():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 200), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((30, 70), "Hemoglobin A1c 8.4 percent", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = extract_text(buffer.getvalue(), "lab.png")
    assert result["method"] == "ocr" and "8.4" in result["text"] and result["confidence"] > 0.8
