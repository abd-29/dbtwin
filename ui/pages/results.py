import duckdb
import streamlit as st

from src.models import KeyedComparison
from ui.components import difference_table


def results_page() -> None:
    """Display comparison summaries, differences and downloads."""
    st.title("Results")
    job = st.session_state.get("job")
    if job is None:
        st.info("Run a comparison first.")
        return

    selected = st.selectbox("Target", list(job.targets))
    outcome = job.targets[selected]

    if outcome.error is not None:
        st.error(outcome.error)
        return

    result = outcome.comparison
    if result is None:
        st.info("No comparison result.")
        return

    structure = result.structure
    keyed = isinstance(result, KeyedComparison)
    st.caption(f"Record identifier: {result.key}" if keyed else "Complete-row comparison · No key")

    st.dataframe(
        [
            {
                "Dataset": "Source",
                "Rows": structure.source.row_count,
                "Columns": structure.source.column_count,
            },
            {
                "Dataset": "Target",
                "Rows": structure.target.row_count,
                "Columns": structure.target.column_count,
            },
        ],
        hide_index=True,
        use_container_width=True,
    )

    if result.status == "not_compared":
        st.warning(result.reason or "The datasets could not be compared.")
    elif result.status == "partial":
        st.warning(result.reason or "Only part of the datasets was compared.")
    elif result.identical:
        st.success("Identical.")
    else:
        st.warning("Differences found.")

    if result.status != "not_compared":
        metrics = st.columns(3)
        metrics[0].metric("Matched rows", result.matched_rows)
        metrics[1].metric("Missing rows", result.missing_rows)
        metrics[2].metric("Added rows", result.added_rows)

        if keyed:
            left, right = st.columns(2)
            left.metric("Modified rows", result.modified_rows)
            right.metric("Modified cells", result.modified_cells)
            with st.expander("Compared columns"):
                st.write(list(result.compared_columns))
        else:
            st.caption("Counts include duplicate occurrences.")

    labels = ["Missing rows", "Added rows"]
    if keyed:
        labels.append("Modified cells")
    labels.append("Schema")
    tabs = st.tabs(labels)
    target_index = list(job.targets).index(selected)
    token = f"{job.job_id}_{target_index}"

    try:
        with tabs[0]:
            difference_table(
                result.missing_rows_file,
                widget_key=f"{token}_missing",
            )

        with tabs[1]:
            difference_table(
                result.added_rows_file,
                widget_key=f"{token}_added",
            )

        if keyed:
            with tabs[2]:
                difference_table(
                    result.modified_cells_file,
                    widget_key=f"{token}_modified",
                )

    except (duckdb.Error, OSError) as error:
        st.error(f"Unable to open result files: {error}")

    with tabs[-1]:
        left, right = st.columns(2)

        with left:
            st.markdown("**Missing from target**")
            if structure.missing_columns:
                st.write(list(structure.missing_columns))
            else:
                st.write("None")

        with right:
            st.markdown("**Extra in target**")
            if structure.extra_columns:
                st.write(list(structure.extra_columns))
            else:
                st.write("None")

        st.markdown("**Type differences**")
        if structure.type_differences:
            st.dataframe(
                [
                    {
                        "Column": difference.name,
                        "Source type": difference.source_type,
                        "Target type": difference.target_type,
                    }
                    for difference in structure.type_differences
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.write("None")

        with st.expander("Common columns"):
            st.write(list(structure.common_columns))
