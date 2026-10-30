"""Care-plan and outreach templates. Deterministic wording; every draft needs human approval.

Outreach uses minimum-necessary content: no diagnoses or results in patient messages.
"""

from __future__ import annotations

from app.vbc_poc.catalog import MEASURES

PRACTICE = "Northstar Primary Care"
PRACTICE_PHONE = "(555) 010-0100"
CHANNELS = ("SMS", "Phone", "Letter", "Portal")
GOALS = {
    "GSD-P": "Glycemic status assessed this period and HbA1c below 8.0%",
    "BPD-P": "Outpatient blood pressure below 140/90 confirmed",
    "BCS-P": "Screening mammogram completed and verified",
    "COL-P": "Colorectal screening completed and verified",
}
INTERVENTIONS = {
    "CLINICAL": "{action}; address barriers (transport, cost, language) before booking",
    "OUTCOME": "Clinician reviews the regimen and self-management plan; repeat measurement within {days} days",
    "DOCUMENTATION": "Request the source report; verify patient identity and service date before acceptance",
    "DATA_CORRECTION": "Correct the source record with the data steward, then re-run the measure",
    "FOLLOW_UP": "{action}; track the referral until the procedure is completed",
}
_MESSAGES = {
    ("GSD-P", "CLINICAL"): "you're due for a routine blood test. Reply 1 to book a lab visit, or call {phone}.",
    ("GSD-P", "OUTCOME"): "your care team would like to see you for a short check-in. Reply 1 to book a visit.",
    ("BPD-P", "CLINICAL"): "it's time for a quick blood pressure check. Reply 1 to book a nurse visit.",
    ("BPD-P", "OUTCOME"): "your care team would like a follow-up blood pressure check in the next two weeks. Reply 1 to book.",
    ("BCS-P", "CLINICAL"): "you're due for a routine screening mammogram. Reply 1 and we'll help you book a time.",
    ("COL-P", "CLINICAL"): "you're due for colon cancer screening. We can mail a simple home test kit. Reply 1 for a kit, 2 to talk about other options.",
    ("COL-P", "FOLLOW_UP"): "please contact us about the next step after your recent home test. Reply 1 and a nurse will call you.",
}
_DOCUMENT_SERVICE = {"GSD-P": "blood test", "BPD-P": "blood pressure check", "BCS-P": "mammogram", "COL-P": "colon screening"}


def plan_content(gaps: list[dict]) -> tuple[list, list, list]:
    goals, interventions, tasks = [], [], []
    for gap in gaps:
        goal = GOALS[gap["measure_id"]]
        if goal not in [item["goal"] for item in goals]:
            goals.append({"measure_id": gap["measure_id"], "goal": goal})
        interventions.append({"gap_id": gap["gap_id"], "measure_id": gap["measure_id"],
                              "text": INTERVENTIONS[gap["gap_type"]].format(action=gap["recommended_action"], days=14)})
        tasks.append({"gap_id": gap["gap_id"], "action": gap["recommended_action"], "owner_role": gap["owner_role"], "owner": gap.get("owner"),
                      "due_date": gap["due_date"], "status": gap["status"]})
    return goals, interventions, tasks


def outreach_text(patient, gap: dict, channel: str) -> str | None:
    """Plain-language draft for the gap, or None where outreach does not apply (internal data corrections)."""
    measure_id, gap_type = gap["measure_id"], gap["gap_type"]
    if gap_type == "DATA_CORRECTION":
        return None
    first = patient.name.split()[0]
    if gap_type == "DOCUMENTATION":
        body = f"if you had a {_DOCUMENT_SERVICE[measure_id]} at another clinic, reply 1 and tell us where so we can request the report."
    else:
        body = _MESSAGES.get((measure_id, gap_type)) or f"please contact us about {MEASURES[measure_id].short.lower()}. Reply 1 to book."
    body = body.format(phone=PRACTICE_PHONE)
    if channel == "Phone":
        text = f"Call script: Hello {first}, this is {PRACTICE}. {body[0].upper()}{body[1:]}"
    elif channel == "Letter":
        text = f"Dear {patient.name},\n\nThis is {PRACTICE}. {body[0].upper()}{body[1:]}\n\nCall {PRACTICE_PHONE} with any questions."
    else:
        text = f"Hi {first}, this is {PRACTICE}: {body}"
    if patient.language != "English":
        text += f"\n\n[Send through an approved {patient.language} translation or interpreter.]"
    return text
