from collections.abc import Callable
from typing import TypedDict
from uuid import uuid4

import streamlit as st

from ui.pages.explore import explore_page
from ui.pages.loading import load_page
from ui.pages.results import results_page
from ui.theme import apply_theme, render_brand


class PageDefinition(TypedDict):
    label: str
    render: Callable[[], None]


PAGES: dict[str, PageDefinition] = {
    "Load data": {"label": "📂  Load data", "render": load_page},
    "Explore data": {"label": "🔎  Explore data", "render": explore_page},
    "Results": {"label": "📊  Results", "render": results_page},
}


def initialize_session() -> None:
    """Initialize state without overwriting existing user data."""
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid4().hex
    if "navigation" not in st.session_state:
        st.session_state["navigation"] = "Load data"


def main() -> None:
    """Configure the application and render the selected page."""
    st.set_page_config(page_title="dbtwin", page_icon="◫", layout="wide")
    apply_theme()
    initialize_session()

    # Update navigation before creating its widget.
    if st.session_state.pop("open_results", False):
        st.session_state["navigation"] = "Results"

    with st.sidebar:
        render_brand()
        selected = st.radio(
            "Navigation",
            options=list(PAGES),
            format_func=lambda name: PAGES[name]["label"],
            key="navigation",
            label_visibility="collapsed",
        )

    PAGES[selected]["render"]()


if __name__ == "__main__":
    main()
