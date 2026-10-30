"""OCR widgets: supporting documents, insurance cards and paper superbills."""

import uuid
from datetime import datetime
from html import escape

import streamlit as st

from frontend import ocr_service as ocr
from frontend.api_client import HealthcareApiClient
from frontend.ehr_chart import chart_rows
from frontend.ui.components import badge, card_title, render_html_table
from frontend.ui.data import BACKEND_ERRORS, backend_error_message, refresh_ehr_cache
from frontend.ui.state import can, current_user, log_action, save_record, user_label

OCR_UPLOAD_TYPES = list(ocr.SUPPORTED_TYPES)

DOCUMENT_TYPES = ("Clinical note", "Lab report", "Referral", "Imaging report", "Other")


def run_ocr(uploaded):
    """Extract text from an uploaded file once per session; reruns reuse the result."""
    data = uploaded.getvalue()
    cache = st.session_state.setdefault("ocr_cache", {})
    key = (uploaded.name, len(data))
    if key not in cache:
        with st.spinner(f"Reading {uploaded.name} (local OCR)…"):
            cache[key] = ocr.extract_text(data, uploaded.name)
    return cache[key]


def ocr_summary(result):
    method = {"ocr": "OCR", "pdf-text": "PDF text layer", "mixed": "PDF text + OCR"}.get(result["method"], result["method"])
    confidence = f" · confidence {result['confidence']:.0%}" if result.get("confidence") is not None else ""
    return f"{method} · {result['pages']} page(s){confidence}"


def _upload_key(prefix, scope):
    revision = st.session_state.setdefault("ocr_upload_rev", {}).get(f"{prefix}_{scope}", 0)
    return f"{prefix}_{scope}_{revision}"


def _reset_upload(prefix, scope):
    revisions = st.session_state.setdefault("ocr_upload_rev", {})
    revisions[f"{prefix}_{scope}"] = revisions.get(f"{prefix}_{scope}", 0) + 1


def _ocr_or_error(uploaded):
    try:
        result = run_ocr(uploaded)
    except (ValueError, RuntimeError, OSError) as error:
        st.error(f"Could not read {uploaded.name}: {error}")
        return None
    for warning in result["warnings"]:
        st.warning(warning)
    return result


def render_document_ocr(record, can_edit):
    """Attach OCR'd supporting documents to an encounter; their text feeds coding and CDS."""
    documents = list(record.get("documents") or [])
    record_id = record.get("id")
    with st.container(border=True):
        card_title(
            "Supporting documents (OCR)",
            "Scanned notes, lab reports or referrals — attached text is used by coding and CDS",
            trailing=badge(f"{len(documents)} attached", "violet" if documents else "gray"),
        )
        for index, doc in enumerate(documents):
            with st.expander(f"{doc['name']} · {doc.get('type', 'Document')} · added {doc['at']} by {user_label(doc.get('by'))}"):
                st.caption(doc.get("method", ""))
                st.text(doc["text"][:6000])
                if can_edit and st.button("Remove from encounter", key=f"doc_remove_{record_id}_{index}", type="tertiary"):
                    documents.pop(index)
                    record["documents"] = documents
                    save_record(record)
                    log_action("ocr.document_removed", f"{record_id}: {doc['name']}")
                    st.rerun()
        if not can_edit:
            st.caption("Attaching documents requires Clinical Review · Edit.")
            return
        uploaded = st.file_uploader(
            "Add a scanned document",
            type=OCR_UPLOAD_TYPES,
            key=_upload_key("doc", record_id),
            help="PDF, PNG, JPG, TIFF, BMP or WEBP. Text is extracted on this machine.",
        )
        if not uploaded:
            return
        result = _ocr_or_error(uploaded)
        if result is None:
            return
        st.caption(ocr_summary(result))
        doc_type = st.selectbox("Document type", DOCUMENT_TYPES, key=f"doc_type_{record_id}_{uploaded.name}")
        text = st.text_area(
            "Review and correct the extracted text before attaching",
            value=result["text"],
            height=200,
            key=f"doc_text_{record_id}_{uploaded.name}",
        )
        if st.button("Attach to encounter", type="primary", disabled=not text.strip(), key=f"doc_attach_{record_id}"):
            record.setdefault("documents", []).append(
                {
                    "name": uploaded.name,
                    "type": doc_type,
                    "text": text.strip(),
                    "method": ocr_summary(result),
                    "at": datetime.now().strftime("%d %b %Y %H:%M"),
                    "by": current_user()["username"],
                }
            )
            save_record(record)
            log_action("ocr.document", f"{record_id}: {uploaded.name} ({doc_type})")
            _reset_upload("doc", record_id)
            st.toast(f"{uploaded.name} attached")
            st.rerun()


def _iso_date(value):
    for date_format in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), date_format).date().isoformat()
        except ValueError:
            continue
    return value


def render_insurance_card_ocr(patient_id, patient_name, chart):
    """Read an insurance card photo and save it as the patient's primary EHR coverage."""
    with st.expander("Scan insurance card (OCR)"):
        if not can("EHR Demo Data", "Edit"):
            st.caption("Updating coverage requires EHR Demo Data · Edit.")
            return
        uploaded = st.file_uploader("Front of the insurance card", type=OCR_UPLOAD_TYPES, key=_upload_key("card", patient_id))
        if not uploaded:
            st.caption(
                "Payer, member ID, group, Rx BIN/PCN and copay are read locally; you confirm every field before it is saved."
            )
            return
        result = _ocr_or_error(uploaded)
        if result is None:
            return
        fields = ocr.parse_insurance_card(result["text"])
        st.caption(ocr_summary(result) + " · verify every field against the card before saving")
        if (
            fields["member_name"]
            and patient_name
            and fields["member_name"].lower().split()[-1:] != patient_name.lower().split()[-1:]
        ):
            st.warning(
                f"Card member name “{fields['member_name']}” does not match {patient_name}. Check you have the right card."
            )
        with st.form(f"card_form_{patient_id}"):
            left, right = st.columns(2)
            payer = left.text_input("Payer", fields["payer_name"])
            plan = right.text_input("Plan type", fields["plan_type"])
            member_id = left.text_input("Member ID", fields["member_id"])
            group = right.text_input("Group number", fields["group_number"])
            rx_bin = left.text_input("Rx BIN", fields["rx_bin"])
            rx_pcn = right.text_input("Rx PCN", fields["rx_pcn"])
            effective = left.text_input("Effective date", fields["effective_date"])
            copay = right.text_input("PCP copay ($)", fields["copay_pcp"])
            save = st.form_submit_button("Save as primary coverage", type="primary")
        with st.expander("Extracted text"):
            st.text(result["text"])
        if not save:
            return
        if not payer.strip() or not member_id.strip():
            st.error("Payer and member ID are required.")
            return
        rows = [dict(row) for row in chart_rows(chart, "Insurance")]
        primary = next((row for row in rows if str(row.get("Priority", "")).lower() == "primary"), None)
        coverage = ocr.coverage_from_card(
            {
                "payer_name": payer,
                "plan_type": plan,
                "member_id": member_id,
                "group_number": group,
                "rx_bin": rx_bin,
                "rx_pcn": rx_pcn,
                "effective_date": effective,
                "copay_pcp": copay,
            },
            patient_id,
            (primary or {}).get("Coverage_ID") or f"COV-OCR-{uuid.uuid4().hex[:8].upper()}",
        )
        coverage["Effective_Date"] = _iso_date(coverage["Effective_Date"])
        if primary:
            primary.update({key: value for key, value in coverage.items() if value not in (None, "")})
        else:
            rows.append(coverage)
        try:
            HealthcareApiClient().replace_patient_records(patient_id, "Insurance", rows)
        except BACKEND_ERRORS as error:
            st.error(f"Coverage could not be saved to the EHR: {backend_error_message(error)}")
            return
        refresh_ehr_cache()
        log_action("ocr.insurance_card", f"{patient_id}: {payer} · member {member_id}")
        _reset_upload("card", patient_id)
        st.toast("Primary coverage updated from the insurance card")
        st.rerun()


def render_superbill_import(record, can_code):
    """Read a paper superbill and add its CPT/HCPCS and ICD-10-CM codes to the encounter."""
    imported = record.get("imported_codes") or {}
    count = len(imported.get("icd10", [])) + len(imported.get("cpt", []))
    with st.expander("Import paper superbill (OCR)" + (f" · {count} code(s) imported" if count else "")):
        if not can_code:
            st.caption("Importing codes requires Codes · Edit.")
            return
        record_id = record.get("id")
        uploaded = st.file_uploader("Scanned superbill", type=OCR_UPLOAD_TYPES, key=_upload_key("superbill", record_id))
        if not uploaded:
            st.caption(
                "Codes are read locally and checked against the ICD-10-CM and CPT/chargemaster references; imported codes then go through post-coding validation."
            )
            return
        result = _ocr_or_error(uploaded)
        if result is None:
            return
        parsed = ocr.parse_superbill(result["text"])
        st.caption(ocr_summary(result) + (f" · date of service {parsed['date_of_service']}" if parsed["date_of_service"] else ""))
        rows = [
            [
                badge("ICD-10-CM", "violet"),
                f"<code>{escape(item['code'])}</code>",
                escape(item["description"]),
                "—",
                badge("Verified", "green"),
            ]
            for item in parsed["icd10"]
        ] + [
            [
                badge("CPT/HCPCS", "blue"),
                f"<code>{escape(item['code'])}</code>",
                escape(item["description"] or "—"),
                str(item["units"]),
                badge("Verified", "green") if item["verified"] else badge("Not in reference", "amber"),
            ]
            for item in parsed["cpt"]
        ]
        if rows:
            render_html_table(["Type", "Code", "Description", "Units", "Reference"], rows)
        else:
            st.info("No ICD-10-CM or CPT/HCPCS codes were recognised in this document.")
        with st.expander("Extracted text"):
            st.text(result["text"])
        if st.button(
            f"Add {len(rows)} code(s) to this encounter", type="primary", disabled=not rows, key=f"superbill_add_{record_id}"
        ):
            record["imported_codes"] = ocr.merge_code_results(record.get("imported_codes"), ocr.superbill_code_results(parsed))
            record["superbill_import"] = {
                "file": uploaded.name,
                "date_of_service": parsed["date_of_service"],
                "at": datetime.now().strftime("%d %b %Y %H:%M"),
                "by": current_user()["username"],
            }
            save_record(record)
            codes = ", ".join(item["code"] for item in parsed["icd10"] + parsed["cpt"])
            log_action("ocr.superbill", f"{record_id}: {codes}")
            _reset_upload("superbill", record_id)
            st.toast(f"{len(rows)} superbill code(s) added for review")
            st.rerun()
