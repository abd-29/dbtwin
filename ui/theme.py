from base64 import b64encode

import streamlit as st

CSS = """
<style>
.block-container {
    max-width: 1320px;
    padding-top: 2.5rem;
    padding-bottom: 3rem;
}
h1 {
    font-size: 2rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.04em;
}
h2, h3 {letter-spacing: -0.025em;}
section[data-testid="stSidebar"] {background: #10213D;}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: #EAF0FA;
}
section[data-testid="stSidebar"] [role="radiogroup"] {gap: 8px;}
section[data-testid="stSidebar"] [role="radiogroup"] > label {
    padding: 12px 14px;
    border-radius: 9px;
    margin: 0;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label:hover {
    background: #1B3355;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {
    background: #2459B8;
}
section[data-testid="stSidebar"] [role="radiogroup"] > label > div:first-child {
    display: none;
}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}
[data-testid="stFileUploader"] section {
    border: 1px dashed #B8C8DF;
    border-radius: 10px;
    background: #FAFCFF;
}
.stButton > button, .stDownloadButton > button {
    border-radius: 8px;
    font-weight: 600;
}
[data-testid="stMetric"] {
    padding: 16px;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
}
.brand {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 4px 0 30px;
}
.brand span {
    color: white;
    font-size: 28px;
    font-weight: 750;
    letter-spacing: -1px;
}
</style>
"""

LOGO_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none">
    <rect x="5" y="5" width="25" height="25" rx="3"
          stroke="white" stroke-width="3.5"/>
    <rect x="18" y="18" width="25" height="25" rx="3"
          stroke="white" stroke-width="3.5"/>
</svg>
"""


def apply_theme() -> None:
    """Apply interface styles."""
    st.markdown(CSS, unsafe_allow_html=True)


def render_brand() -> None:
    """Render the sidebar logo and wordmark."""
    encoded = b64encode(LOGO_SVG.encode("utf-8")).decode("ascii")
    st.markdown(
        '<div class="brand">'
        f'<img src="data:image/svg+xml;base64,{encoded}" '
        'width="38" height="38" alt="">'
        "<span>dbtwin</span>"
        "</div>",
        unsafe_allow_html=True,
    )
