import pandas as pd
import streamlit as st
from config import FILETYPE, INGESTION_MODES
from src.csv_comp import DataComparator

st.set_page_config(page_title="dbtwin", page_icon="🔎", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1400px;}
[data-testid="stSidebar"] {background-color: #eef3f8;}
h1, h2, h3 {letter-spacing: -0.035em;}
.stButton > button {border-radius: 10px; font-weight: 600;}
</style>
""", unsafe_allow_html=True)

DEFAULTS = {
    "source_data": None,
    "source_name": None,
    "target_data": {},
    "results": None,
    "result_key": None,
}
for name, value in DEFAULTS.items():
    if name not in st.session_state:
        st.session_state[name] = value


def read_file(file, filetype: str, separator: str = ",") -> pd.DataFrame:
    """Convert an uploaded file into a dataframe."""
    file.seek(0)
    if filetype == "csv":
        return pd.read_csv(file, sep=separator, dtype="string", keep_default_na=False)
    if filetype == "excel":
        return pd.read_excel(file, dtype="string", keep_default_na=False)
    if filetype == "parquet":
        return pd.read_parquet(file).astype("string").fillna("")
    raise ValueError(f"No reader is implemented for '{filetype}'.")


def ingestion_controls(prefix: str, multiple: bool):
    """Build ingestion controls from the application configuration."""
    available_modes = [name for name, settings in INGESTION_MODES.items() if settings["enabled"]]
    if not available_modes:
        st.error("No ingestion mode is enabled.")
        return None

    mode = st.selectbox(
        "Ingestion mode", available_modes,
        format_func=lambda value: INGESTION_MODES[value]["label"],
        key=f"{prefix}_mode",
    )
    if mode != "file":
        st.info("This ingestion mode is not implemented yet.")
        return None

    filetype = st.selectbox(
        "File type", list(FILETYPE),
        format_func=lambda value: FILETYPE[value]["label"],
        key=f"{prefix}_filetype",
    )
    separator = ","
    if filetype == "csv":
        separators = {"Comma": ",", "Semicolon": ";", "Tab": "\t", "Pipe": "|"}
        selected = st.selectbox("Separator", list(separators), key=f"{prefix}_separator")
        separator = separators[selected]
    elif filetype == "excel":
        st.caption("The first worksheet will be loaded.")

    files = st.file_uploader(
        "Select files" if multiple else "Select a file",
        type=FILETYPE[filetype]["extensions"],
        accept_multiple_files=multiple,
        key=f"{prefix}_upload_{filetype}",
    )
    return files, filetype, separator


def load_page():
    st.title("Load data")
    st.caption("Choose one reference dataset and one or more targets.")
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Reference source")
        source_settings = ingestion_controls("source", multiple=False)
    with right:
        st.subheader("Target datasets")
        target_settings = ingestion_controls("target", multiple=True)

    if st.button("Load datasets", type="primary", use_container_width=True):
        if source_settings is None or target_settings is None:
            st.warning("Configure both ingestion sources.")
        else:
            source_file, source_type, source_separator = source_settings
            target_files, target_type, target_separator = target_settings
            if source_file is None or not target_files:
                st.warning("Select a source file and at least one target.")
            else:
                try:
                    # Replace the current batch only after every file loads.
                    source = read_file(source_file, source_type, source_separator)
                    targets = {}
                    for index, file in enumerate(target_files, start=1):
                        name = f"{index:02d} · {file.name}"
                        targets[name] = read_file(file, target_type, target_separator)
                except Exception as error:
                    st.error(f"Loading failed. Previous datasets were kept. Details: {error}")
                else:
                    st.session_state.update({
                        "source_data": source,
                        "source_name": source_file.name,
                        "target_data": targets,
                        "results": None,
                        "result_key": None,
                    })
                    st.success(f"Loaded one reference and {len(targets)} target dataset(s).")

    source = st.session_state["source_data"]
    if source is None:
        st.info("Load datasets to configure the comparison.")
        return

    st.divider()
    st.subheader("Comparison")
    st.caption(
        f"Loaded reference: {st.session_state['source_name']} · "
        f"{len(st.session_state['target_data'])} target(s)"
    )
    st.caption("Click Load datasets again to apply changes to your file selection.")
    if source.columns.empty:
        st.warning("The reference must contain at least one column.")
        return

    key = st.selectbox("Unique record identifier", list(source.columns))
    if st.button("Compare files", type="primary", use_container_width=True):
        comparator = DataComparator(key)
        try:
            with st.spinner("Comparing datasets..."):
                comparator.set_source(source)
                for name, target in st.session_state["target_data"].items():
                    comparator.add_target(name, target)
                results = comparator.compare_all()
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state["results"] = results
            st.session_state["result_key"] = key
            st.session_state["open_results"] = True
            st.rerun()


def explore_page():
    st.title("Explore data")
    if st.session_state["source_data"] is None:
        st.info("Load datasets first.")
        return

    datasets = {"Reference": st.session_state["source_data"]}
    datasets.update({
        f"Target · {name}": frame
        for name, frame in st.session_state["target_data"].items()
    })
    selected = st.selectbox("Dataset", list(datasets))
    frame = datasets[selected]
    left, right = st.columns(2)
    left.metric("Rows", len(frame))
    right.metric("Columns", len(frame.columns))
    columns = st.multiselect("Columns", list(frame.columns), default=list(frame.columns))
    st.dataframe(frame[columns], hide_index=True, use_container_width=True)


def results_page():
    st.title("Results")
    results = st.session_state["results"]
    if results is None:
        st.info("Run a comparison from Load data first.")
        return

    st.caption(f"Record identifier: {st.session_state['result_key']}")
    successful = [item for item in results.values() if item["status"] == "success"]
    metrics = st.columns(3)
    metrics[0].metric("Compared targets", len(successful))
    metrics[1].metric(
        "Identical targets",
        sum(item["comparison"]["summary"]["identical"] for item in successful),
    )
    metrics[2].metric("Invalid targets", len(results) - len(successful))

    for index, (name, item) in enumerate(results.items()):
        st.divider()
        st.subheader(name)
        if item["status"] == "error":
            st.error(item["message"])
            continue

        result = item["comparison"]
        summary = result["summary"]
        if summary["identical"]:
            st.success("The target matches the reference.")
        else:
            st.warning("Differences detected.")

        dimensions = pd.DataFrame({
            "Dataset": ["Source", "Target"],
            "Rows": [summary["source_rows"], summary["target_rows"]],
            "Columns": [summary["source_columns"], summary["target_columns"]],
        })
        st.dataframe(dimensions, hide_index=True, use_container_width=True)

        metrics = st.columns(4)
        for column, label, field in zip(
            metrics,
            ["Missing records", "Added records", "Modified records", "Modified cells"],
            ["missing_rows", "added_rows", "modified_rows", "modified_cells"],
        ):
            column.metric(label, summary[field])

        tabs = st.tabs(["Missing records", "Added records", "Modified values", "Schema"])
        for tab, field in zip(tabs[:3], ["missing_rows", "added_rows", "modified_values"]):
            with tab:
                frame = result[field]
                if frame.empty:
                    st.info("No differences in this category.")
                else:
                    st.dataframe(frame, hide_index=True, use_container_width=True)
                    st.download_button(
                        "Download CSV",
                        data=frame.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"target_{index + 1}_{field}.csv",
                        mime="text/csv",
                        key=f"download_{index}_{field}",
                    )
        with tabs[3]:
            st.write("Common columns:", summary["common_columns"])
            st.write("Missing columns:", result["missing_columns"] or "None")
            st.write("Extra columns:", result["extra_columns"] or "None")


# Redirect before creating the sidebar navigation widget.
if st.session_state.pop("open_results", False):
    st.session_state["navigation"] = "Results"

with st.sidebar:
    st.title("dbtwin")
    st.caption("Data comparison workspace")
    page = st.radio(
        "Navigation", ["Load data", "Explore data", "Results"],
        key="navigation", label_visibility="collapsed",
    )
    st.divider()
    st.caption("One reference. Multiple targets.")

if page == "Load data":
    load_page()
elif page == "Explore data":
    explore_page()
else:
    results_page()