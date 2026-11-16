from shutil import rmtree
from uuid import uuid4

import streamlit as st

from config import JOBS_DIR, WORKSPACE_DIR
from src.exceptions import DbtwinError
from src.services.comparison_service import ComparisonService, CsvInput
from ui.components import (
    ENCODINGS,
    SEPARATORS,
    csv_preview,
    directory_key,
    ingestion_controls,
    save_upload,
)


def load_page() -> None:
    """Load CSV inputs, configure comparison and open its results."""
    st.title("Load data")
    source_column, target_column = st.columns(2, gap="large")

    with source_column:
        with st.container(border=True):
            st.subheader("Source")
            source_settings = ingestion_controls("source", multiple=False)

    with target_column:
        with st.container(border=True):
            st.subheader("Targets")
            target_settings = ingestion_controls("targets", multiple=True)

    available = source_settings is not None and target_settings is not None
    load_clicked = st.button(
        "Load files",
        disabled=not available,
        use_container_width=True,
    )

    if load_clicked and source_settings is not None and target_settings is not None:
        source_upload, source_encoding, source_separator = source_settings
        target_uploads, target_encoding, target_separator = target_settings

        if source_upload is None or not target_uploads:
            st.warning("Select one source and at least one target.")
        else:
            directory = (
                WORKSPACE_DIR
                / "uploads"
                / st.session_state["session_id"]
                / uuid4().hex
            )
            try:
                directory.mkdir(parents=True, exist_ok=False)
                source = CsvInput(
                    path=save_upload(source_upload, directory),
                    encoding=source_encoding,
                    delimiter=source_separator,
                )
                preview, settings = csv_preview(source)
                source = CsvInput(
                    path=source.path,
                    encoding=settings.encoding,
                    delimiter=settings.delimiter,
                )

                targets = {}
                for index, upload in enumerate(target_uploads, start=1):
                    name = f"{index}. {upload.name}"
                    targets[name] = CsvInput(
                        path=save_upload(upload, directory),
                        encoding=target_encoding,
                        delimiter=target_separator,
                    )
            except (DbtwinError, OSError) as error:
                if directory.exists():
                    rmtree(directory)
                st.error(str(error))
            else:
                st.session_state["inputs"] = {
                    "source": source,
                    "source_name": source_upload.name,
                    "targets": targets,
                    "columns": preview.column_names,
                    "settings": settings,
                }
                st.session_state.pop("job", None)
                st.success("Files loaded.")

    if not available:
        return

    inputs = st.session_state.get("inputs")
    if inputs is None:
        return

    st.divider()
    with st.container(border=True):
        left, right = st.columns(2)

        with left:
            st.markdown("**Loaded source**")
            st.write(inputs["source_name"])
            settings = inputs["settings"]
            separator_name = next(
                (
                    name
                    for name, value in SEPARATORS.items()
                    if value == settings.delimiter
                ),
                repr(settings.delimiter),
            )
            st.caption(f"{settings.encoding} · {separator_name}")

        with right:
            st.markdown("**Loaded targets**")
            for name in inputs["targets"]:
                st.write(name)

    st.caption("Changed your file selection? Click Load files to apply it.")

    overrides = {}
    with st.expander("Target reading settings"):
        for index, (name, request) in enumerate(inputs["targets"].items()):
            st.markdown(f"**{name}**")
            left, right = st.columns(2)
            token = f"{directory_key(inputs)}_{index}"

            with left:
                separator = st.selectbox(
                    "Separator",
                    list(SEPARATORS),
                    index=list(SEPARATORS.values()).index(request.delimiter),
                    key=f"loaded_separator_{token}",
                )

            with right:
                encoding = st.selectbox(
                    "Encoding",
                    list(ENCODINGS),
                    index=list(ENCODINGS.values()).index(request.encoding),
                    key=f"loaded_encoding_{token}",
                )

            overrides[name] = CsvInput(
                path=request.path,
                encoding=ENCODINGS[encoding],
                delimiter=SEPARATORS[separator],
            )

    left, right = st.columns([3, 1], vertical_alignment="bottom")

    with left:
        key = st.selectbox(
            "Record identifier",
            [None, *inputs["columns"]],
            format_func=lambda value: (
                "No key — compare complete rows" if value is None else value
            ),
            key=f"comparison_key_{directory_key(inputs)}",
        )

    with right:
        compare_clicked = st.button(
            "Compare →",
            type="primary",
            use_container_width=True,
        )

    if compare_clicked:
        # Keep effective target settings available to the exploration page.
        st.session_state["inputs"] = {**inputs, "targets": overrides}
        st.session_state.pop("job", None)

        try:
            with st.spinner("Comparing files…"):
                job = ComparisonService(JOBS_DIR).run(
                    source=inputs["source"],
                    targets=overrides,
                    key=key,
                )
        except DbtwinError as error:
            st.error(str(error))
        else:
            st.session_state["job"] = job
            st.session_state["open_results"] = True
            st.rerun()