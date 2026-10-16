"""Pure helpers for transcripts, SOAP notes and CDS recommendation text."""

import re
from html import unescape


def parse_transcript_entries(transcript, doctor_name="Doctor", patient_name="Patient"):
    text = (transcript or "").strip()
    if not text:
        return []
    entries, current_speaker, current_text = [], doctor_name, ""

    def flush():
        nonlocal current_text
        if current_text.strip():
            entries.append({"Speaker": current_speaker, "Text": current_text.strip()})
        current_text = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(doctor|patient)\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if match:
            flush()
            current_speaker = doctor_name if match.group(1).lower() == "doctor" else patient_name
            current_text = match.group(2).strip()
        else:
            current_text = f"{current_text} {line}".strip()
    flush()
    return entries or [{"Speaker": doctor_name, "Text": text}]


def append_selected_insights_to_note(current_text, selected_insights):
    updated = current_text or ""
    values = [value for value in selected_insights if value and value != "None"]
    if not values:
        return updated
    additions = "\n".join(f"• {value}" for value in values)
    heading = "MAJOR DIAGNOSIS / ISSUES"
    if "Awaiting manual input." in updated:
        return updated.replace("Awaiting manual input.", additions, 1)
    if heading in updated:
        return f"{updated.rstrip()}\n{additions}"
    return f"{updated.rstrip()}\n\n{heading}\n{additions}".strip()


def append_to_plan(current_text, line):
    text = (current_text or "").rstrip()
    bullet = f"• {line.strip()}"
    if bullet in text:
        return text
    if re.search(r"(?im)^\s*#*\s*plan\s*:?", text):
        return f"{text}\n{bullet}"
    return f"{text}\n\nPLAN\n{bullet}".strip()


EXCERPT_STOPWORDS = {
    "the",
    "and",
    "with",
    "without",
    "for",
    "patient",
    "established",
    "new",
    "office",
    "visit",
    "test",
    "exam",
    "unspecified",
    "automated",
    "complete",
    "single",
    "view",
    "views",
    "type",
    "blood",
    "level",
}


def best_excerpt(term, text, max_chars=260):
    """Return the sentence from the documentation that best supports a term."""
    words = {
        word for word in re.findall(r"[a-z0-9]+", str(term or "").lower()) if len(word) > 2 and word not in EXCERPT_STOPWORDS
    }
    if not words:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", clean_clinical_text(text)) if part.strip()]
    scored = []
    for sentence in sentences:
        lowered = sentence.lower()
        score = sum(1 for word in words if word in lowered)
        if score:
            scored.append((score, -len(sentence), sentence))
    if not scored:
        return ""
    excerpt = max(scored)[2]
    excerpt = re.sub(r"^(doctor|patient)\s*:\s*", "", excerpt, flags=re.IGNORECASE)
    return excerpt if len(excerpt) <= max_chars else excerpt[: max_chars - 1].rstrip() + "…"


def get_insight_category(insight):
    lowered = insight.lower()
    if any(x in lowered for x in ("diabetes", "hypertension", "cholesterol", "asthma", "arthritis")):
        return "Diagnoses", "insight-diagnosis"
    if any(x in lowered for x in ("smok", "alcohol", "obesity")):
        return "Risk Factors", "insight-risk"
    if any(
        x in lowered
        for x in (
            "x-ray",
            "ct scan",
            "blood",
            "cbc",
            "a1c",
            "mri",
            "ultrasound",
            "test",
            "panel",
            "visit",
            "therapy",
            "injection",
        )
    ):
        return "Tests/Procedures", "insight-procedure"
    return "Symptoms", "insight-symptom"


def clean_clinical_text(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_cds_sections(recommendations):
    text = clean_clinical_text(recommendations)
    sections = {"Recommendation": "", "Reasoning": "", "Safety Note": ""}
    pattern = r"(?im)^\s*#{0,6}\s*\**\s*(?:Clinical\s+)?(Recommendations?|Reasoning|Safety Note)\s*\**\s*:?\s*\**\s*$"
    matches = list(re.finditer(pattern, text))
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            label = (
                match.group(1).title().rstrip("s")
                if match.group(1).lower().startswith("recommendation")
                else match.group(1).title()
            )
            sections[label] = text[match.end() : end].strip()
    else:
        sections["Recommendation"] = text
    return sections


HIGH_PRIORITY_TERMS = (
    "urgent",
    "immediate",
    "emergency",
    "avoid",
    "contraindicat",
    "allerg",
    "severe",
    "sepsis",
    "red flag",
    "hospital",
    "admit",
)

MEDIUM_PRIORITY_TERMS = ("consider", "assess", "evaluate", "monitor", "adjust", "escalat", "start", "order")


def recommendation_items(recommendation_text):
    """Split the CDS recommendation section into individual actionable items."""
    text = re.sub(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", recommendation_text or "").strip()
    if not text:
        return []
    bullet_pattern = r"(?m)^\s*(?:[-*•]|\d+[.)])\s+"
    if re.search(bullet_pattern, text):
        chunks = [chunk.strip() for chunk in re.split(bullet_pattern, text) if chunk.strip()]
    else:
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    items = []
    for chunk in chunks:
        plain = re.sub(r"\*\*|__", "", chunk).strip()
        if plain.endswith(":") and "\n" not in plain:
            continue  # lead-in sentence or empty heading, not a recommendation
        bold = re.match(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$", chunk, flags=re.DOTALL)
        if bold:
            title, body = bold.group(1).strip().rstrip(":"), re.sub(r"\*\*|__", "", bold.group(2)).strip()
        else:
            sentence = re.match(r"^(.{12,160}?[.:;])\s+(.*)$", plain, flags=re.DOTALL)
            title, body = (sentence.group(1).rstrip(".:;"), sentence.group(2).strip()) if sentence else (plain, "")
        lowered = plain.lower()
        if any(term in lowered for term in HIGH_PRIORITY_TERMS):
            priority = "HIGH"
        elif any(term in lowered for term in MEDIUM_PRIORITY_TERMS):
            priority = "MED"
        else:
            priority = "ROUTINE"
        items.append({"title": title, "body": body, "priority": priority})
    return items


def guideline_passages(retrieved_guidelines):
    text = str(retrieved_guidelines or "").strip()
    if not text or text.lower().startswith("no highly relevant context"):
        return []
    passages = []
    for block in re.split(r"(?m)^(?=Source:)", text):
        block = block.strip()
        if not block:
            continue
        first, _, rest = block.partition("\n")
        if first.startswith("Source:"):
            passages.append({"source": first.replace("Source:", "", 1).strip(), "text": rest.strip()})
        else:
            passages.append({"source": "Knowledge base", "text": block})
    return passages
