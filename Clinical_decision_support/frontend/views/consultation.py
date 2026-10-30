"""New Consultation wizard: patient selection (step 1) and recording/transcription (step 2)."""

import io
import uuid
from datetime import datetime
from html import escape

import requests
import speech_recognition as sr
import streamlit as st

from frontend import access_control as ac
from frontend.api_client import HealthcareApiClient
from frontend.clinical_text import parse_transcript_entries
from frontend.ehr_chart import active_drug_allergies, age_from_dob, primary_coverage
from frontend.settings import ANALYSIS_VERSION
from frontend.ui.components import badge, card_title, page_header, render_banner, render_stepper, render_summary
from frontend.ui.data import backend_error_message, fetch_ehr_patients, fetch_patient_chart, refresh_ehr_cache
from frontend.ui.shell import render_page
from frontend.ui.state import ACCESS, can, current_user, go_to, open_record, persist_access, save_db, start_new_consultation
from frontend.views.ocr_widgets import render_insurance_card_ocr


def has_first_and_last_name(name):
    return len(str(name or "").split()) >= 2


def register_consultation_in_ehr(record, client=None, patient=None, dob=None, encounter_type="Office Visit"):
    client = client or HealthcareApiClient()
    patient = patient or {}
    patient_id = str(
        record.get("ehr_patient_id") or record.get("patient_id") or patient.get("Patient_ID") or patient.get("patient_id") or ""
    )
    if not patient_id:
        patient_name = str(record.get("name", "")).strip()
        if not has_first_and_last_name(patient_name):
            raise ValueError("Enter the patient's first and last name before creating an EHR patient.")
        first_name, last_name = patient_name.split(maxsplit=1)
        if not dob:
            try:
                approximate_age = max(0, int(record.get("age", "")))
            except (TypeError, ValueError) as error:
                raise ValueError("A date of birth is required to create an EHR patient.") from error
            dob = f"{max(1900, datetime.now().year - approximate_age)}-01-01"
        patient = client.create_patient(
            {
                "First_Name": first_name,
                "Last_Name": last_name,
                "DOB": dob,
                "Sex_at_Birth": record.get("gender", "Other"),
            }
        )
        patient_id = str(patient.get("Patient_ID") or patient.get("patient_id") or "")
        if not patient_id:
            raise ValueError("The EHR patient response did not include a patient ID.")

    record["ehr_patient_id"] = patient_id
    record["patient_id"] = patient_id
    record["mrn"] = patient.get("MRN", record.get("mrn", ""))
    record["dob"] = patient.get("DOB", dob or record.get("dob", ""))
    try:
        service_date = datetime.strptime(record.get("date", ""), "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        service_date = datetime.now().date().isoformat()

    if not record.get("ehr_encounter_id"):
        encounter = client.create_patient_record(
            patient_id,
            "Encounters",
            {
                "Encounter_ID": f"ENC-{uuid.uuid4().hex[:10].upper()}",
                "Patient_ID": patient_id,
                "Date": service_date,
                "Type": encounter_type,
                "Department": "Primary Care",
                "Provider": record.get("doctor", "Unassigned"),
                "Facility": "NuuCare Demo",
                "Status": "In Progress",
                "Primary_Diagnosis": record.get("patient_summary", ""),
                "Priority": "Routine",
            },
        )
        record["ehr_encounter_id"] = encounter.get("Encounter_ID")
    if not record.get("ehr_encounter_id"):
        raise ValueError("The EHR encounter response did not include an encounter ID.")

    if not record.get("ehr_note_id"):
        note = client.create_patient_record(
            patient_id,
            "Clinical_Notes",
            {
                "Note_ID": f"NOTE-{uuid.uuid4().hex[:10].upper()}",
                "Patient_ID": patient_id,
                "Encounter_ID": record["ehr_encounter_id"],
                "Note_Type": "SOAP",
                "Service_Date": service_date,
                "Author": record.get("doctor", "Unassigned"),
                "Note_Summary": record.get("summary") or record.get("patient_summary", ""),
                "Status": "Draft" if record.get("status") == "Pending" else "Signed",
            },
        )
        record["ehr_note_id"] = note.get("Note_ID")
    return record


def update_ehr_clinical_note(record, approved=False):
    patient_id = record.get("ehr_patient_id") or record.get("patient_id")
    note_id = record.get("ehr_note_id")
    if not patient_id or not note_id:
        return
    client = HealthcareApiClient()
    chart = client.get_patient_chart(str(patient_id))
    notes = chart.get("records", {}).get("Clinical_Notes", [])
    for note in notes:
        if str(note.get("Note_ID", "")) == str(note_id):
            note["Note_Summary"] = record.get("summary", "")
            note["Status"] = "Signed" if approved else "Draft"
            break
    else:
        return
    client.replace_patient_records(str(patient_id), "Clinical_Notes", notes)


def _consult_step_patient():
    draft = st.session_state.consult_draft
    with st.container(border=True):
        card_title("Patient", "Select an EHR patient or register a new one for this consultation")
        ehr_patients = fetch_ehr_patients()
        if ehr_patients is None:
            render_banner(
                "EHR backend unavailable",
                "You can still enter patient details manually; EHR linking will be retried when the note is generated.",
                tone="warn",
                icon="&#9888;",
            )
            ehr_patients = []
        ehr_patient_options = {"Create new EHR patient": None}
        ehr_patient_by_id = {}
        for item in ehr_patients:
            patient_id = str(item.get("patient_id") or item.get("Patient_ID") or "")
            if patient_id:
                patient_label = item.get("display_name") or item.get("Legal_Name") or patient_id
                ehr_patient_options[f"{patient_label} · {patient_id} · {item.get('MRN', '')}"] = patient_id
                ehr_patient_by_id[patient_id] = item
        labels = list(ehr_patient_options)
        default_label = next(
            (label for label, value in ehr_patient_options.items() if value and value == draft.get("ehr_patient_id")), labels[0]
        )
        selected_ehr_patient_label = st.selectbox(
            "Patient source", labels, index=labels.index(default_label), key="consult_ehr_patient"
        )
        selected_ehr_patient_id = ehr_patient_options[selected_ehr_patient_label]
        selected_ehr_patient = ehr_patient_by_id.get(selected_ehr_patient_id, {})
        patient_widget_key = selected_ehr_patient_id or "manual"
        same_patient = draft.get("ehr_patient_id") == selected_ehr_patient_id and draft
        default_name = (
            draft.get("name")
            if same_patient
            else str(selected_ehr_patient.get("Legal_Name") or selected_ehr_patient.get("display_name") or "")
        )
        default_dob = draft.get("dob") if same_patient else str(selected_ehr_patient.get("DOB") or "").split("T")[0]
        default_age = draft.get("age") if same_patient else age_from_dob(default_dob)
        gender_options = ["Male", "Female", "Other"]
        default_gender = (
            draft.get("gender")
            if same_patient
            else str(selected_ehr_patient.get("Sex_at_Birth") or selected_ehr_patient.get("Gender") or "Male")
        )
        gender_index = gender_options.index(default_gender) if default_gender in gender_options else 2
        if selected_ehr_patient_id:
            chart = fetch_patient_chart(selected_ehr_patient_id)
            coverage = primary_coverage(chart)
            allergies = ", ".join(str(row.get("Substance")) for row in active_drug_allergies(chart)) or "NKDA"
            st.markdown(
                '<div class="nc-badge-row" style="margin:.2rem 0 .4rem">'
                + badge(coverage.get("Payer_Name") or "No coverage on file", "blue")
                + badge(
                    f"Eligibility {str(coverage.get('Status') or 'unknown').upper()}",
                    "green" if str(coverage.get("Status", "")).lower() == "active" else "amber",
                )
                + badge(f"Allergy: {allergies}", "red" if allergies != "NKDA" else "gray")
                + badge(f"PCP {selected_ehr_patient.get('PCP') or '—'}", "gray")
                + "</div>",
                unsafe_allow_html=True,
            )
            render_insurance_card_ocr(selected_ehr_patient_id, str(selected_ehr_patient.get("Legal_Name") or ""), chart)
        left, right = st.columns(2, gap="large")
        patient_name = left.text_input(
            "Full Name", value=default_name, key=f"consult_patient_name_{patient_widget_key}", placeholder="e.g. John Doe"
        )
        name_is_valid = bool(selected_ehr_patient_id) or has_first_and_last_name(patient_name)
        patient_age = right.text_input("Age", value=default_age, key=f"consult_patient_age_{patient_widget_key}")
        patient_gender = left.selectbox(
            "Gender", gender_options, index=gender_index, key=f"consult_patient_gender_{patient_widget_key}"
        )
        patient_dob = right.text_input(
            "Date of birth (YYYY-MM-DD)",
            value=default_dob,
            key=f"consult_patient_dob_{patient_widget_key}",
            disabled=bool(selected_ehr_patient_id),
            placeholder="Required to create a new EHR patient",
        )
        doctor_name = left.text_input(
            "Attending Doctor", value=draft.get("doctor", ""), placeholder="e.g. Dr. Sarah Jenkins", key="consult_doctor"
        )
        encounter_options = ["Outpatient", "Inpatient"]
        consultation_encounter_type = right.selectbox(
            "Encounter type",
            encounter_options,
            index=encounter_options.index(draft.get("encounter_type", "Outpatient")),
            key="consult_encounter_type",
        )
        if patient_name.strip() and not name_is_valid:
            st.info("Enter both the patient's first and last name, or select an existing EHR patient.")

    def proceed():
        st.session_state.consult_draft = {
            "ehr_patient_id": selected_ehr_patient_id,
            "ehr_patient": selected_ehr_patient,
            "name": patient_name.strip(),
            "age": patient_age.strip(),
            "gender": patient_gender,
            "dob": patient_dob.strip(),
            "doctor": doctor_name.strip(),
            "encounter_type": consultation_encounter_type,
        }
        st.session_state.consult_step = "record"

    back_col, _, next_col = st.columns([2, 5, 3])
    back_col.button("← Back to Overview", key="consult_back_overview", on_click=go_to, args=("dashboard",), width="stretch")
    next_col.button(
        "Proceed to Record →",
        key="consult_to_record",
        type="primary",
        on_click=proceed,
        width="stretch",
        disabled=not patient_name.strip() or not name_is_valid,
    )


def _audio_duration(audio_bytes):
    try:
        import wave

        with wave.open(io.BytesIO(audio_bytes)) as handle:
            seconds = handle.getnframes() / float(handle.getframerate() or 1)
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
    except Exception:
        return "--:--"


def _format_size(size):
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size} B"


def _create_consultation_record():
    draft = st.session_state.consult_draft
    encounter = st.session_state.scribe_result or {}
    try:
        encounter = HealthcareApiClient().add_cds(encounter)
    except (RuntimeError, requests.exceptions.HTTPError) as error:
        if encounter.get("recommendations"):
            st.warning(
                f"CDS could not be refreshed; keeping the existing recommendations. Details: {backend_error_message(error)}"
            )
        else:
            st.warning(
                f"CDS is unavailable; the consultation will be saved without recommendations. Details: {backend_error_message(error)}"
            )
    transcript = encounter.get("transcript", "")
    doctor, now = draft.get("doctor") or "Doctor", datetime.now()
    selected_ehr_patient_id = draft.get("ehr_patient_id")
    record = {
        "id": f"PT-{str(uuid.uuid4().int)[:6]}",
        "name": draft.get("name", ""),
        "age": draft.get("age", ""),
        "gender": draft.get("gender", "Other"),
        "doctor": doctor,
        "date": now.strftime("%d/%m/%Y"),
        "time": now.strftime("%H:%M"),
        "status": "Pending",
        "transcript": transcript,
        "transcript_data": parse_transcript_entries(transcript, doctor, draft.get("name", "Patient")),
        "summary": encounter.get("soap_note", ""),
        "patient_summary": encounter.get("patient_summary", ""),
        "key_highlights": encounter.get("key_highlights", []),
        "retrieved_guidelines": encounter.get("retrieved_guidelines", ""),
        "recommendations": encounter.get("recommendations", ""),
        "ehr_patient_id": selected_ehr_patient_id,
        "ehr_encounter_type": draft.get("encounter_type", "Outpatient"),
        "dob": draft.get("dob", ""),
        "cds_requested": bool(encounter.get("recommendations")),
        "analysis_version": ANALYSIS_VERSION,
    }
    try:
        register_consultation_in_ehr(
            record,
            patient=draft.get("ehr_patient") or {},
            dob=draft.get("dob") or None,
            encounter_type=draft.get("encounter_type", "Outpatient"),
        )
        refresh_ehr_cache()
    except (RuntimeError, requests.exceptions.RequestException, ValueError, KeyError) as error:
        st.warning(f"Consultation saved locally, but EHR registration was unavailable: {error}")
    creator = current_user()["username"]
    record["created_by"] = creator
    ac.assign_record(ACCESS["store"], record, creator, creator, "Consultation created")
    persist_access()
    st.session_state.records.insert(0, record)
    save_db(st.session_state.records)
    st.session_state.consult_step = "patient"
    st.session_state.consult_draft = {}
    st.session_state.scribe_result = None
    st.session_state.consult_audio_signature = None
    open_record(record)


def _transcribe_audio(audio_bytes):
    recognizer = sr.Recognizer()
    with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
        return recognizer.recognize_google(recognizer.record(source))


def _process_consultation(transcript=None, audio=None):
    """Transcribe (when audio is given) and generate the SOAP draft, with visible progress."""
    with st.status("Processing consultation…", expanded=True) as status:
        try:
            if audio is not None:
                st.write("Transcribing audio with Google Speech Recognition…")
                transcript = _transcribe_audio(audio["bytes"])
                st.write(f"Transcript captured · {len(transcript.split())} words")
            st.write("Generating SOAP draft and key highlights…")
            st.session_state.scribe_result = HealthcareApiClient().scribe_encounter(transcript)
            st.session_state.consult_audio_signature = audio["signature"] if audio else None
            status.update(label="Transcript and SOAP draft ready", state="complete", expanded=False)
            return True
        except sr.UnknownValueError:
            status.update(
                label="No speech could be recognised — record closer to the microphone or upload a clearer file", state="error"
            )
        except sr.RequestError as error:
            status.update(label=f"Speech recognition service unreachable: {error}", state="error")
        except ValueError as error:
            status.update(label=f"Unsupported or unreadable audio (use WAV, FLAC or AIFF): {error}", state="error")
        except (RuntimeError, requests.exceptions.RequestException) as error:
            status.update(label=f"Clinical summary service failed: {backend_error_message(error)}", state="error")
    return False


def _consult_step_record():
    draft = st.session_state.consult_draft
    context_badges = (
        '<div class="nc-badge-row" style="justify-content:flex-end">'
        + badge(draft.get("name") or "Patient", "gray")
        + badge(draft.get("ehr_patient_id") or "New EHR patient", "blue" if draft.get("ehr_patient_id") else "amber")
        + badge(draft.get("encounter_type", "Outpatient"), "violet")
        + badge(draft.get("doctor") or "Attending not set", "gray")
        + "</div>"
    )
    with st.container(border=True):
        card_title(
            "Record Consultation",
            "Capture the clinical conversation with the microphone, or upload a recording",
            trailing=context_badges,
        )
        render_banner(
            "Use synthetic data only in this environment",
            "Avoid recording or uploading real PHI unless your environment is explicitly approved.",
            tone="warn",
            icon="&#9888;",
        )
        record_col, upload_col = st.columns(2, gap="medium")
        with record_col:
            st.markdown(
                '<div class="nc-label" style="margin-top:.5rem">&#127908; Record with microphone</div>', unsafe_allow_html=True
            )
            recorded = st.audio_input("Record consultation", key="consult_audio_mic", label_visibility="collapsed")
            st.caption("Click the microphone to start and again to stop. Re-record any time before transcribing.")
        with upload_col:
            st.markdown(
                '<div class="nc-label" style="margin-top:.5rem">&#128193; Upload audio file</div>', unsafe_allow_html=True
            )
            uploaded = st.file_uploader(
                "Upload consultation audio",
                type=["wav", "flac", "aif", "aiff"],
                key="consult_audio_upload",
                label_visibility="collapsed",
                help="Supported: WAV, FLAC, AIFF",
            )

        if recorded and uploaded:
            source = st.radio(
                "Audio to transcribe", ["Microphone recording", "Uploaded file"], horizontal=True, key="consult_audio_source"
            )
            audio_file = recorded if source == "Microphone recording" else uploaded
        else:
            audio_file = recorded or uploaded
        audio = None
        if audio_file:
            audio_bytes = audio_file.getvalue()
            source_name = "Microphone recording" if audio_file is recorded else getattr(audio_file, "name", "Uploaded file")
            audio = {"bytes": audio_bytes, "name": source_name, "signature": (source_name, len(audio_bytes))}

        result = st.session_state.scribe_result
        transcribed_this = bool(result and audio and st.session_state.consult_audio_signature == audio["signature"])
        if transcribed_this or (result and not audio):
            status_text = "Transcribed"
        elif audio:
            status_text = "Ready to transcribe"
        else:
            status_text = "Awaiting audio"
        st.markdown(
            '<div class="nc-upload-tiles" style="margin-top:.4rem">'
            f"<div><small>Source</small><b>{escape(audio['name']) if audio else 'No audio yet'}</b></div>"
            f"<div><small>Duration</small><b>{_audio_duration(audio['bytes']) if audio else '--:--'}</b></div>"
            f"<div><small>Size</small><b>{_format_size(len(audio['bytes'])) if audio else '0 B'}</b></div>"
            f"<div><small>Status</small><b>{status_text}</b></div></div>",
            unsafe_allow_html=True,
        )
        if st.button(
            "Re-transcribe and regenerate summary" if transcribed_this else "Transcribe and Generate Summary",
            key="consult_transcribe",
            type="secondary" if transcribed_this else "primary",
            icon=":material/graphic_eq:",
            width="stretch",
            disabled=audio is None,
        ):
            if _process_consultation(audio=audio):
                st.rerun()

        with st.expander("No audio? Paste a transcript instead"):
            pasted = st.text_area(
                "Transcript",
                height=150,
                placeholder="Doctor: ...\nPatient: ...",
                key="consult_transcript_paste",
                label_visibility="collapsed",
            )
            if st.button("Generate summary from transcript", key="consult_paste_go", disabled=not pasted.strip()):
                if _process_consultation(transcript=pasted.strip()):
                    st.rerun()

        if result:
            transcript_col, summary_col = st.columns(2, gap="medium")
            with transcript_col:
                st.markdown('<div class="nc-label" style="margin-top:.6rem">TRANSCRIPT</div>', unsafe_allow_html=True)
                st.text_area(
                    "Generated transcript",
                    value=result.get("transcript", ""),
                    height=220,
                    disabled=True,
                    label_visibility="collapsed",
                )
            with summary_col:
                st.markdown('<div class="nc-label" style="margin-top:.6rem">DRAFT SUMMARY</div>', unsafe_allow_html=True)
                render_summary(result.get("soap_note") or result.get("patient_summary", "Not available"))
                for highlight in result.get("key_highlights", []):
                    st.markdown(f"- {highlight}")

    back_col, _, next_col = st.columns([2, 4.5, 3.5])
    if back_col.button("← Back to Patient", key="consult_back_patient", width="stretch"):
        st.session_state.consult_step = "patient"
        st.rerun()
    if next_col.button(
        "Proceed to Clinical Review & CDS →",
        key="consult_to_cds",
        type="primary",
        width="stretch",
        disabled=not st.session_state.scribe_result,
    ):
        with st.spinner("Running clinical decision support and saving the consultation..."):
            _create_consultation_record()
        st.rerun()


def show_new_consultation():
    render_page("new_consult")
    step = st.session_state.consult_step
    actions = page_header(
        "CONSULTATIONS", "New Consultation", "Record encounter details and transcribe consultation audio.", action_weight=2.4
    )
    actions.button("Start over", key="consult_reset", type="tertiary", on_click=start_new_consultation)
    render_stepper(0 if step == "patient" else 1)
    if not can("Clinical Review", "Edit"):
        render_banner(
            "Your role can view consultations but not create them.",
            "Creating a consultation requires Clinical Review · Edit. Ask a Super Admin to update your role.",
            tone="warn",
            icon="&#128274;",
        )
        return
    if step == "record" and st.session_state.consult_draft:
        _consult_step_record()
    else:
        st.session_state.consult_step = "patient"
        _consult_step_patient()
