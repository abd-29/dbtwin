import duckdb
import streamlit as st

from src.exceptions import DbtwinError
from ui.components import PREVIEW_ROWS, csv_preview, parquet_preview


def explore_page() -> None:
    """Display a preview of the loaded source or a target dataset."""
    st.title("Explore data")
    inputs = st.session_state.get("inputs")
    if inputs is None:
        st.info("Load files first.")
        return

    # Keep internal identifiers separate from uploaded filenames.
    options = ["source", *inputs["targets"]]
    selected = st.selectbox(
        "Dataset",
        options,
        format_func=lambda value: (
            f"Source · {inputs['source_name']}"
            if value == "source"
            else f"Target · {value}"
        ),
    )
    request = (
        inputs["source"] if selected == "source"
        else inputs["targets"][selected]
    )

    job = st.session_state.get("job")
    prepared = None
    if job is not None:
        if selected == "source":
            prepared = job.source
        else:
            outcome = job.targets[selected]
            prepared = outcome.prepared
            if outcome.error:
                st.warning(outcome.error)

    try:
        if prepared is not None:
            dataset = prepared.dataset
            settings = prepared.settings
            left, right = st.columns(2)
            left.metric("Rows", dataset.row_count)
            right.metric("Columns", dataset.column_count)

            rows = parquet_preview(dataset.parquet_files)
            if rows:
                st.dataframe(
                    [
                        dict(zip(dataset.column_names, row, strict=True))
                        for row in rows
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.info("This dataset contains no records.")
                st.write("Columns:", list(dataset.column_names))
        else:
            preview, settings = csv_preview(request)
            st.metric("Columns", preview.num_columns)
            st.dataframe(
                preview,
                hide_index=True,
                use_container_width=True,
            )
            st.caption("Total row count is available after comparison.")

        st.caption(f"First {PREVIEW_ROWS} rows at most.")
        with st.expander("Reading settings"):
            st.write("Encoding:", settings.encoding)
            separator = {
                ",": "Comma",
                ";": "Semicolon",
                "\t": "Tab",
                "|": "Pipe",
            }.get(settings.delimiter, repr(settings.delimiter))
            st.write("Separator:", separator)

    except (DbtwinError, duckdb.Error, OSError) as error:
        st.error(str(error))