"""Supplied versus calculated QA comparison (Evidence & Quality workstream).

The extract arrives with measure results already computed by the supplier. Before they are trusted,
every (member, measure) result is recalculated from the normalized source evidence under the active
measure version and compared:

  AGREE                       same state
  DENOMINATOR_MISMATCH        one side has the member in the denominator, the other does not
  SUPPLIED_UNSUPPORTED        supplied compliant, but the source evidence does not support it
  SUPPLIED_UNDERCOUNT         source evidence satisfies the measure, supplied result is not compliant
  STATUS_DIFFERENCE           both non-compliant, different reason (e.g. open vs evidence review)

Each measure passes QA when state agreement and the rate difference are inside tolerance. A run is
recorded with the dataset hash and measure version; a second person signs it off.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.hedis import calculator, normalizer, sources, store, versions
from app.hedis.errors import Conflict, HedisError, NotFound, SegregationOfDuties

TOLERANCE = {"agreement_min": 95.0, "rate_delta_max": 2.0}
CATEGORIES = {
    "AGREE": ("Agree", "gray"),
    "DENOMINATOR_MISMATCH": ("Denominator mismatch", "violet"),
    "SUPPLIED_UNSUPPORTED": ("Supplied compliant, evidence does not support", "red"),
    "SUPPLIED_UNDERCOUNT": ("Evidence compliant, supplied not", "amber"),
    "STATUS_DIFFERENCE": ("Both non-compliant, different status", "blue"),
}


def _supplied() -> dict:
    out = {}
    for row in sources.table("measure_results"):
        state = calculator.LABEL_TO_STATE.get(row["state"].strip().lower())
        if state is None:
            raise HedisError(f"Supplied result {row['result_id']} has an unknown state '{row['state']}'.")
        out[(row["patient_id"], row["measure_id"])] = {"state": state, "label": row["state"], "reason": row["reason"],
                                                       "result_id": row["result_id"], "rule_version": row["rule_version"],
                                                       "source": row["result_source"]}
    return out


def _classify(supplied: dict, calculated: dict) -> str:
    if supplied["state"] == calculated["state"]:
        return "AGREE"
    supplied_in = supplied["state"] not in calculator.OUTSIDE_DENOMINATOR
    if supplied_in != calculated["in_denominator"]:
        return "DENOMINATOR_MISMATCH"
    supplied_num = supplied["state"] in calculator.NUMERATOR_STATES
    if supplied_num and not calculated["numerator_met"]:
        return "SUPPLIED_UNSUPPORTED"
    if calculated["numerator_met"] and not supplied_num:
        return "SUPPLIED_UNDERCOUNT"
    return "STATUS_DIFFERENCE"


def compare(path: Path | None = None) -> dict:
    """Recalculate everything and compare with the supplied results. Pure: nothing is stored."""
    state = store.load(path)
    version_id, spec = versions.active_spec(path)
    normalized = normalizer.normalize(versions._workflow_evidence(state))
    members = normalizer.patient_demographics()
    calculated = calculator.calculate(spec, normalized, members)
    supplied = _supplied()
    evidence = {e.evidence_id: e for e in normalized["evidence"]}
    items, measures = [], {}
    for key in sorted(set(supplied) | set(calculated)):
        patient_id, measure_id = key
        theirs, ours = supplied.get(key), calculated.get(key)
        if theirs is None or ours is None:
            items.append({"patient_id": patient_id, "measure_id": measure_id, "category": "DENOMINATOR_MISMATCH",
                          "supplied": theirs and theirs["state"], "calculated": ours and ours["state"],
                          "explanation": "Present on only one side", "evidence": []})
            continue
        category = _classify(theirs, ours)
        entry = measures.setdefault(measure_id, {"compared": 0, "agree": 0, "categories": Counter(), "matrix": Counter(),
                                                 "supplied": {"numerator": 0, "denominator": 0}, "calculated": {"numerator": 0, "denominator": 0}})
        entry["compared"] += 1
        entry["agree"] += category == "AGREE"
        entry["categories"][category] += 1
        entry["matrix"][(theirs["state"], ours["state"])] += 1
        entry["supplied"]["denominator"] += theirs["state"] not in calculator.OUTSIDE_DENOMINATOR
        entry["supplied"]["numerator"] += theirs["state"] in calculator.NUMERATOR_STATES
        entry["calculated"]["denominator"] += ours["in_denominator"]
        entry["calculated"]["numerator"] += ours["numerator_met"]
        if category != "AGREE":
            items.append({"patient_id": patient_id, "measure_id": measure_id, "category": category,
                          "category_label": CATEGORIES[category][0], "tone": CATEGORIES[category][1],
                          "supplied": theirs["state"], "supplied_label": theirs["label"], "supplied_reason": theirs["reason"],
                          "calculated": ours["state"], "calculated_label": ours["label"], "calculated_reason": ours["reason"],
                          "evidence": [{"evidence_id": eid, "concept": evidence[eid].concept, "date": evidence[eid].service_date,
                                        "source": evidence[eid].source, "value": evidence[eid].value, "value2": evidence[eid].value2,
                                        "result_flag": evidence[eid].result_flag, "verification": evidence[eid].verification,
                                        "lineage": [f"{l['source']}:{l['record_id']}" for l in evidence[eid].lineage]}
                                       for eid in ours["evidence_ids"] if eid in evidence]})
    for measure_id, entry in measures.items():
        for side in ("supplied", "calculated"):
            counts = entry[side]
            counts["rate"] = round(counts["numerator"] / counts["denominator"] * 100, 1) if counts["denominator"] else None
        entry["agreement"] = round(entry["agree"] / entry["compared"] * 100, 1)
        entry["rate_delta"] = (round(entry["calculated"]["rate"] - entry["supplied"]["rate"], 1)
                               if entry["calculated"]["rate"] is not None and entry["supplied"]["rate"] is not None else None)
        entry["pass"] = entry["agreement"] >= TOLERANCE["agreement_min"] and abs(entry["rate_delta"] or 0) <= TOLERANCE["rate_delta_max"]
        entry["categories"] = dict(entry["categories"])
        entry["matrix"] = [{"supplied": s, "calculated": c, "count": n} for (s, c), n in entry["matrix"].most_common()]
    compared = sum(m["compared"] for m in measures.values())
    agree = sum(m["agree"] for m in measures.values())
    return {"version_id": version_id, "dataset_sha256": normalized["dataset_sha256"], "normalization": normalized["summary"],
            "measures": {m: measures[m] for m in calculator.MEASURE_ORDER if m in measures}, "items": items,
            "summary": {"compared": compared, "agree": agree, "agreement": round(agree / compared * 100, 1) if compared else None,
                        "categories": dict(Counter(item["category"] for item in items)),
                        "measures_passing": sum(m["pass"] for m in measures.values()), "measures": len(measures)},
            "tolerance": TOLERANCE, "categories": CATEGORIES}


def record_run(actor: str, path: Path | None = None) -> dict:
    result = compare(path)
    with store.transaction(path) as state:
        run = {"run_id": store.next_id(state, "QA"), "at": store.now_iso(), "by": actor, "version_id": result["version_id"],
               "dataset_sha256": result["dataset_sha256"], "summary": result["summary"],
               "measures": {m: {k: v for k, v in e.items() if k in ("agreement", "rate_delta", "pass", "categories", "supplied", "calculated")}
                            for m, e in result["measures"].items()},
               "signoff": None}
        state["quality"]["runs"].append(run)
        del state["quality"]["runs"][:-50]
        store.audit(state, actor, "qa.run", "qa_run", run["run_id"], new=f"{result['summary']['agreement']}% agree", trigger="qa",
                    details=f"Version {result['version_id']} · {result['summary']['measures_passing']}/{result['summary']['measures']} measures within tolerance")
    return {**result, "run": run}


def sign_off(run_id: str, actor: str, decision: str, note: str, path: Path | None = None) -> dict:
    """Second-person review of a QA run: ACCEPTED (results usable) or REJECTED (back to the supplier)."""
    if decision not in ("ACCEPTED", "REJECTED"):
        raise HedisError("Decision must be ACCEPTED or REJECTED.")
    if len(note.strip()) < 10:
        raise HedisError("Record the basis for the decision (at least 10 characters).")
    with store.transaction(path) as state:
        run = next((r for r in state["quality"]["runs"] if r["run_id"] == run_id), None)
        if not run:
            raise NotFound(f"QA run {run_id} not found.")
        if run["signoff"]:
            raise Conflict(f"{run_id} was already signed off.")
        if actor == run["by"] or actor.startswith("agent:"):
            raise SegregationOfDuties("The person who ran the QA cannot sign it off.")
        failing = [m for m, e in run["measures"].items() if not e["pass"]]
        if decision == "ACCEPTED" and failing:
            raise Conflict(f"{', '.join(failing)} are outside tolerance; they can only be rejected back to the supplier.")
        run["signoff"] = {"by": actor, "at": store.now_iso(), "decision": decision, "note": note.strip()}
        store.audit(state, actor, "qa.signed_off", "qa_run", run_id, new=decision, trigger="signoff", details=note.strip())
        return run


def history(path: Path | None = None) -> list[dict]:
    return list(reversed(store.load(path)["quality"]["runs"]))
