from __future__ import annotations

from html import escape
from typing import Any

import pandas as pd
import streamlit as st

from app.vbc.dashboard import get_population_dashboard
from app.vbc.dataset import (
    VbcDatasetNotFoundError,
    VbcDatasetRepository,
    VbcDatasetValidationError,
)


SYNTHETIC_NOTICE = (
    "Synthetic VBC POC data. HEDIS-inspired measures. "
    "Not for clinical care or official quality reporting."
)


def _filter_options() -> tuple[dict[str, list[str]], dict[str, str], str | None]:
    repository = VbcDatasetRepository()
    patients = repository.get_patients()
    providers = repository.get_providers()
    care_gaps = repository.get_care_gaps()
    summary = repository.get_population_summary()
    provider_names = {
        str(row.get("provider_id") or ""): str(row.get("provider_name") or "Unknown")
        for row in providers
        if row.get("provider_id")
    }
    options = {
        "population_segment": sorted(
            {
                repository.get_population_field(row)
                for row in patients
                if repository.get_population_field(row)
            },
            key=str.casefold,
        ),
        "provider_id": sorted(provider_names),
        "risk_category": sorted(
            {
                row["risk_category"]
                for row in repository.get_risk_scores()
                if row.get("risk_category")
            }
            | {
                row["risk_category"]
                for row in patients
                if row.get("risk_category")
            },
            key=str.casefold,
        ),
        "measure_id": sorted(
            repository.list_supported_measures()
        ),
        "gap_type": sorted(
            {row["gap_type"] for row in care_gaps if row.get("gap_type")},
            key=str.casefold,
        ),
        "priority": sorted(
            {row["priority"] for row in care_gaps if row.get("priority")},
            key=str.casefold,
        ),
        "owner": sorted(
            {row["owner"] for row in care_gaps if row.get("owner")},
            key=str.casefold,
        ),
        "status": sorted(
            {row["status"] for row in care_gaps if row.get("status")},
            key=str.casefold,
        ),
    }
    if not patients:
        options = {name: [] for name in options}
    return options, provider_names, summary.get("as_of_date")


def _select_filter(label: str, key: str, options: list[str]) -> str | None:
    values = ["All"] + options
    selected = st.selectbox(
        label,
        values,
        key=f"vbc_dashboard_{key}",
    )
    return None if selected == "All" else selected


def _render_header(result: dict[str, Any]) -> None:
    metadata = result["metadata"]
    dataset_name = escape(str(metadata.get("dataset_name") or "Synthetic VBC POC"))
    dataset_version = escape(str(metadata.get("dataset_version") or "Unknown"))
    patient_count = metadata.get("patient_count", "Unknown")
    measure_count = metadata.get("measure_count", "Unknown")
    period_start = metadata.get("measurement_period_start") or "Unknown"
    period_end = metadata.get("measurement_period_end") or "Unknown"
    as_of_date = escape(str(metadata.get("as_of_date") or "Unknown"))
    synthetic_notice = escape(
        str(
            metadata.get("synthetic_data_notice")
            or SYNTHETIC_NOTICE
        )
    )
    st.markdown(
        f"""
<div style="background:#111827;color:#f8fafc;padding:1.1rem 1.35rem;
border-radius:.7rem;margin-bottom:.8rem">
  <div style="font-size:.78rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:#99f6e4">Value-Based Care</div>
  <div style="font-size:1.65rem;font-weight:800;margin:.1rem 0 .35rem">
  Population Dashboard</div>
  <div style="font-size:.9rem;font-weight:700">{dataset_name} · v{dataset_version}</div>
  <div style="font-size:.82rem;color:#cbd5e1">
  {patient_count} patients · {measure_count} measures
  &nbsp; · &nbsp;
  Measurement period: {escape(str(period_start))} to {escape(str(period_end))}
  &nbsp; · &nbsp; As of {as_of_date}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.caption(f"Active dataset: {dataset_name}, {patient_count} patients")
    if metadata.get("is_legacy_dataset"):
        st.caption(
            "Legacy cohort labels are mapped to population segments for display "
            "compatibility; this is not an official clinical classification."
        )
    st.info(synthetic_notice)


def _render_kpis(kpis: dict[str, Any]) -> None:
    supporting_text = (
        (
            "Quality Performance",
            f"{kpis['quality_performance_rate']:.1f}%",
            f"{kpis['met_evaluations']} met of {kpis['eligible_evaluations']} eligible evaluations; POC only.",
        ),
        (
            "Open Care Gaps",
            str(kpis["open_actionable_care_gaps"]),
            "Non-terminal care-gap records after selected filters.",
        ),
        (
            "Evidence Completeness",
            f"{kpis['evidence_completeness_rate']:.1f}%",
            f"{kpis['verified_documents']} verified of "
            f"{kpis['documents_requiring_verification']} documents.",
        ),
        (
            "High-Risk Patients",
            str(kpis["high_risk_patients"]),
            "Distinct filtered patients categorized as High.",
        ),
    )
    columns = st.columns(4)
    for column, (label, value, detail) in zip(columns, supporting_text):
        with column:
            st.metric(label, value, border=True)
            st.caption(detail)


def _render_freshness(freshness: list[dict[str, Any]]) -> None:
    st.subheader("Synthetic dataset freshness")
    columns = st.columns(len(freshness))
    for column, item in zip(columns, freshness):
        with column:
            st.markdown(f"**{item['source_name'].title()}**")
            st.write(item["status"])
            latest = item["latest_valid_date"] or "No valid date"
            age = (
                f"{item['age_days']} day(s)"
                if item["age_days"] is not None
                else "Age unknown"
            )
            st.caption(f"{latest} · {age}")


def _render_measures(measures: list[dict[str, Any]]) -> None:
    st.subheader("Measure performance")
    st.caption(
        "HEDIS-inspired POC measures. Target and status bands are POC logic, "
        "not CMS cut points or official reporting."
    )
    if not measures:
        st.info("No eligible measure population is available for these filters.")
        return

    table_rows = [
        {
            "Measure ID": row["measure_id"],
            "Measure": row["measure_name"],
            "Initial Population": row.get(
                "initial_population_count", row["total_evaluations"]
            ),
            "Eligible": row["eligible_count"],
            "Excluded": row.get("excluded_count", 0),
            "Met": row["met_count"],
            "Open / Actionable": row["open_gap_count"],
            "Uncontrolled": row["uncontrolled_count"],
            "Evidence Review": row["evidence_review_count"],
            "Data Quality": row["data_quality_count"],
            "Current Performance": f"{row['current_performance_rate']:.1f}%",
            "POC Goal": f"{row['poc_target_rate']:.1f}%",
            "Gaps to Goal": row["gaps_to_goal"],
            "Status": row["status"],
        }
        for row in measures
    ]
    st.dataframe(pd.DataFrame(table_rows), hide_index=True, width="stretch")


def _render_measure_history(
    history: list[dict[str, Any]],
    measure_performance: list[dict[str, Any]],
) -> None:
    st.subheader("Monthly performance trend")
    if not history:
        st.info("Measure history is unavailable for this dataset.")
        return

    measure_ids = sorted(
        {str(row.get("measure_id") or "") for row in history if row.get("measure_id")},
        key=lambda value: (
            ("GSD", "BPD-E", "KED", "BCS-E", "COL-E", "TRC").index(value)
            if value in ("GSD", "BPD-E", "KED", "BCS-E", "COL-E", "TRC")
            else 6,
            value,
        ),
    )
    selected_measure = st.selectbox(
        "Trend measure",
        measure_ids,
        key="vbc_dashboard_history_measure",
    )
    selected_rows = [
        row for row in history if row.get("measure_id") == selected_measure
    ]
    if not selected_rows:
        st.info("No valid monthly history is available for this measure.")
        return

    history_frame = pd.DataFrame(
        [
            {
                "Snapshot": row["snapshot_date"],
                "Actual Performance": pd.to_numeric(
                    row.get("performance_rate"), errors="coerce"
                ),
                "Synthetic POC Projection": pd.to_numeric(
                    row.get("projected_performance_rate"), errors="coerce"
                ),
                "POC Target": pd.to_numeric(
                    row.get("poc_target_rate"), errors="coerce"
                ),
            }
            for row in selected_rows
        ]
    ).set_index("Snapshot")
    st.line_chart(history_frame, y=list(history_frame.columns))
    st.caption(
        "Projection is a synthetic POC projection only; it is not a CMS forecast. "
        "The target is a POC target, not an official cut point."
    )


def _render_charts(
    risk_distribution: dict[str, int],
    gap_distribution: dict[str, dict[str, int]],
) -> None:
    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.subheader("Risk distribution")
        if risk_distribution:
            risk_frame = pd.DataFrame(
                [
                    {"Risk Category": category, "Patients": count}
                    for category, count in risk_distribution.items()
                ]
            )
            st.bar_chart(risk_frame, x="Risk Category", y="Patients")
        else:
            st.info("No patients match the selected population filters.")
    with chart_columns[1]:
        st.subheader("Care gaps by type")
        gaps_by_type = gap_distribution["by_gap_type"]
        if gaps_by_type:
            gap_frame = pd.DataFrame(
                [
                    {"Gap Type": category, "Care Gaps": count}
                    for category, count in gaps_by_type.items()
                ]
            )
            st.bar_chart(gap_frame, x="Gap Type", y="Care Gaps")
        else:
            st.info("No care gaps match the selected filters.")


def _render_worklist(rows: list[dict[str, Any]]) -> None:
    st.subheader("Priority patient worklist")
    if not rows:
        st.info("No care gaps match the selected filters.")
        return
    display_rows = [
        {
            "Patient ID": row["patient_id"],
            "Masked Patient": row["masked_display_name"],
            "Population Segment": row["population_segment"],
            "Risk Category": row["risk_category"],
            "Risk Score": row["risk_score"],
            "Gap Count": row["care_gap_count"],
            "Highest Priority": row["highest_priority"],
            "Measures": ", ".join(row["measure_names"]),
            "Owner": ", ".join(row["owners"]),
            "Earliest Due Date": row["earliest_due_date"] or "Unknown",
            "Overdue": row["overdue_gap_count"],
        }
        for row in rows
    ]
    st.dataframe(pd.DataFrame(display_rows), hide_index=True, width="stretch")


def _render_quality_panel(
    result: dict[str, Any],
    warnings: list[str],
) -> None:
    quality = result["data_quality_summary"]
    kpis = result["kpis"]
    with st.expander("Data quality and evidence review", expanded=False):
        st.markdown(
            f"- Data-quality measure cases: **{kpis['data_quality_cases']}**\n"
            f"- Evidence-review measure cases: **{kpis['evidence_review_cases']}**\n"
            f"- Documents pending verification: "
            f"**{quality['pending_document_verification']}**\n"
            f"- Missing DOB: **{quality['missing_dob']}**\n"
            f"- Missing observation dates: "
            f"**{quality['missing_observation_dates']}**\n"
            f"- Invalid BP values: **{quality['invalid_bp_values']}**\n"
            f"- Conflicting same-day BP groups: "
            f"**{quality['conflicting_same_day_bp_readings']}**\n"
            f"- Duplicate laboratory results: "
            f"**{quality['duplicate_laboratory_results']}**\n"
            f"- Missing or malformed due dates: "
            f"**{quality['malformed_due_dates']}**\n"
            f"- Unresolved provider references: "
            f"**{quality['unresolved_provider_references']}**\n"
            f"- Unresolved patient references: "
            f"**{quality['unresolved_patient_references']}**"
        )
        if warnings:
            st.markdown("**Dashboard notes**")
            for warning in warnings:
                st.warning(warning)


def show_vbc_population_dashboard(render_sidebar) -> None:
    """Render the read-only population dashboard from the bundled VBC archive."""
    render_sidebar("vbc_dashboard")

    try:
        options, provider_names, summary_as_of_date = _filter_options()
    except VbcDatasetNotFoundError as error:
        st.error(str(error))
        return
    except VbcDatasetValidationError as error:
        if "population_dashboard_summary.json" in str(error):
            st.error(
                "The required population summary JSON is missing or malformed "
                "in the VBC archive."
            )
        else:
            st.error(f"VBC dataset configuration or archive error: {error}")
        return
    except (OSError, ValueError):
        st.error("The synthetic VBC dashboard could not load its source data.")
        return

    filter_columns = st.columns(4)
    with filter_columns[0]:
        selected_population_segment = _select_filter(
            "Population segment",
            "population_segment",
            options["population_segment"],
        )
    with filter_columns[1]:
        provider_options = [
            f"{provider_names[provider_id]} · {provider_id}"
            for provider_id in options["provider_id"]
        ]
        provider_selection = st.selectbox(
            "Attributed provider",
            ["All"] + provider_options,
            key="vbc_dashboard_provider_id",
        )
        selected_provider = (
            None
            if provider_selection == "All"
            else provider_selection.rsplit(" · ", 1)[-1]
        )
    with filter_columns[2]:
        selected_risk = _select_filter(
            "Risk category", "risk_category", options["risk_category"]
        )
    with filter_columns[3]:
        selected_measure = _select_filter(
            "Measure", "measure_id", options["measure_id"]
        )

    gap_filter_columns = st.columns(4)
    with gap_filter_columns[0]:
        selected_gap_type = _select_filter(
            "Gap type", "gap_type", options["gap_type"]
        )
    with gap_filter_columns[1]:
        selected_priority = _select_filter(
            "Priority", "priority", options["priority"]
        )
    with gap_filter_columns[2]:
        selected_owner = _select_filter("Owner", "owner", options["owner"])
    with gap_filter_columns[3]:
        selected_status = _select_filter(
            "Workflow status", "status", options["status"]
        )

    try:
        result = get_population_dashboard(
            as_of_date=summary_as_of_date,
            population_segment=selected_population_segment,
            provider_id=selected_provider,
            risk_category=selected_risk,
            measure_id=selected_measure,
            gap_type=selected_gap_type,
            priority=selected_priority,
            owner=selected_owner,
            status=selected_status,
        )
    except VbcDatasetNotFoundError as error:
        st.error(str(error))
        return
    except VbcDatasetValidationError as error:
        st.error(f"VBC dataset configuration or archive error: {error}")
        return
    except (OSError, ValueError):
        st.error("The synthetic VBC dashboard could not calculate these results.")
        return

    _render_header(result)
    _render_kpis(result["kpis"])
    _render_freshness(result["freshness_summary"])
    _render_measures(result["measure_performance"])
    _render_measure_history(
        result["measure_history"], result["measure_performance"]
    )
    _render_charts(result["risk_distribution"], result["gap_distribution"])
    _render_worklist(result["priority_worklist"])
    _render_quality_panel(result, result["warnings"])

    if result["kpis"]["total_patients"] == 0:
        st.info("No patients match the selected population filters.")
    elif not result["measure_performance"] or not result["kpis"]["eligible_evaluations"]:
        st.info("No eligible population is available for the selected measures.")
