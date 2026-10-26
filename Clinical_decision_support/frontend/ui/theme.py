"""Pastel design system: one palette, one stylesheet, injected once per script run.

Every color in the app comes from PALETTE. Each pastel family has a soft background
(100), a border tone (300) and a dark "ink" for text, so pastel surfaces stay readable.
"""

import streamlit as st

PALETTE = {
    # neutrals
    "bg": "#F6F7FB",
    "surface": "#FFFFFF",
    "surface_soft": "#FAFBFE",
    "surface_tint": "#F5F3FF",
    "border": "#E8E9F2",
    "border_strong": "#D6D9E6",
    "text": "#262B3D",
    "muted": "#6B7186",
    "faint": "#9DA3B5",
    # pastel families: 50 (wash) · 100 (fill) · 300 (border) · 400 (strong fill) · ink (text)
    "mint_50": "#EEF9F4",
    "mint_100": "#D9F2E7",
    "mint_300": "#A9E0CB",
    "mint_400": "#8BD3B8",
    "mint_ink": "#1D684F",
    "sky_50": "#EEF5FF",
    "sky_100": "#DDEBFF",
    "sky_300": "#B3D0F5",
    "sky_400": "#9CC1F0",
    "sky_ink": "#264F86",
    "lav_50": "#F5F3FF",
    "lav_100": "#EAE6FF",
    "lav_300": "#CCC3F8",
    "lav_400": "#ABA0EF",
    "lav_ink": "#4F3FA3",
    "peach_50": "#FFF5EF",
    "peach_100": "#FFE7D9",
    "peach_300": "#F6C6A9",
    "peach_ink": "#94461F",
    "butter_50": "#FFFBEA",
    "butter_100": "#FFF2C6",
    "butter_300": "#EFD98C",
    "butter_ink": "#6E5600",
    "rose_50": "#FFF3F5",
    "rose_100": "#FFE1E7",
    "rose_300": "#F3B4C2",
    "rose_ink": "#9E2440",
    "gray_100": "#EFF0F5",
    "gray_300": "#D9DCE6",
    "gray_ink": "#4A5065",
}

# Chart series colors (Altair) taken from the same palette.
# Chart series colors: the pastel families stepped deep enough to pass the categorical palette
# checks (lightness band, chroma, CVD separation, normal-vision floor, 3:1 contrast) on white.
CHART_COLORS = ["#2A9D74", "#6B5BD6", "#D9773F", "#3D86D6"]
CHART_NEUTRAL = "#9DA3B5"

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {
    __TOKENS__
    --font: "Inter", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    --radius: 14px;
    --radius-sm: 10px;
    --shadow: 0 1px 2px rgba(38, 43, 61, .04), 0 6px 20px rgba(79, 63, 163, .05);
    --shadow-lift: 0 2px 4px rgba(38, 43, 61, .05), 0 12px 28px rgba(79, 63, 163, .09);
}

/* ---------- Base ---------- */
/* Streamlit pulls every markdown block up by 1rem to cancel a trailing <p> margin. Our HTML
   components end in a <div>, so that pull would make stacked blocks touch; cancel it. */
[data-testid="stMarkdownContainer"]:has(> div:last-child) { margin-bottom: 0 !important; }
html { font-size: 15px; }
.stApp { background: var(--bg); color: var(--text); font-family: var(--font); }
.stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button { font-family: var(--font); }
#MainMenu, footer, [data-testid="stAppDeployButton"], [data-testid="stToolbarActions"], [data-testid="stStatusWidget"],
[data-testid="stDecoration"] { display: none !important; }
header[data-testid="stHeader"] { height: 2.6rem; background: transparent !important; pointer-events: none; z-index: 1000; }
header[data-testid="stHeader"] button { pointer-events: auto; }
.block-container { max-width: 1560px; padding: 0 1.5rem 2rem !important; }
[data-testid="stVerticalBlock"] { gap: .7rem; }
[data-testid="stHorizontalBlock"] { gap: .75rem; }
hr { margin: .4rem 0 .7rem !important; border-color: var(--border) !important; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: var(--gray_300); border-radius: 999px; border: 2px solid var(--bg); }
h1, h2, h3, h4 { color: var(--text) !important; font-family: var(--font) !important; letter-spacing: -.01em; }
[data-testid="stMain"] h3 { font-size: 1.02rem !important; font-weight: 700 !important; margin: .35rem 0 .15rem !important; }
[data-testid="stMain"] [data-testid="stMarkdownContainer"] p { color: var(--text); }
[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaptionContainer"] p { color: var(--muted) !important; }

/* ---------- Top bar ---------- */
.st-key-nc_topbar {
    width: calc(100% + 3rem) !important; max-width: none !important; margin: 0 -1.5rem 1.1rem;
    padding: .6rem 1.5rem; background: var(--surface); border-bottom: 1px solid var(--border);
    box-shadow: 0 1px 0 rgba(38, 43, 61, .02);
}
.st-key-nc_topbar { flex-wrap: nowrap !important; }
.st-key-nc_topnav { gap: .15rem !important; }
.nc-brand { display: flex; align-items: center; gap: .6rem; white-space: nowrap; }
.nc-brand-mark {
    width: 1.55rem; height: 1.55rem; border-radius: 50%; flex: none;
    background: conic-gradient(from 210deg, var(--mint_400), var(--sky_400), var(--lav_400), var(--peach_300), var(--mint_400));
    -webkit-mask: radial-gradient(circle, transparent 42%, #000 44%); mask: radial-gradient(circle, transparent 42%, #000 44%);
}
.nc-brand-name { color: var(--text); font-weight: 800; font-size: 1.05rem; line-height: 1; letter-spacing: -.01em; }
.nc-brand-sub { color: var(--faint); font-size: .52rem; font-weight: 700; letter-spacing: .09rem; margin-top: .2rem; }
.nc-app-pills { display: flex; gap: .3rem; margin-left: .8rem; }
.nc-app-pill { padding: .24rem .65rem; border-radius: 999px; font-size: .68rem; font-weight: 600; color: var(--muted); background: var(--gray_100); white-space: nowrap; }
.nc-app-pill.on { background: var(--mint_100); color: var(--mint_ink); }
.nc-topbar-right { display: flex; justify-content: flex-end; align-items: center; gap: 1rem; color: var(--muted); font-size: .76rem; font-weight: 600; white-space: nowrap; }
.nc-count { display: inline-grid; place-items: center; min-width: 1.1rem; height: 1.1rem; margin-left: .25rem; padding: 0 .3rem; border-radius: 999px; background: var(--rose_100); color: var(--rose_ink); font-size: .62rem; font-weight: 700; }
[data-testid="stMain"] [class*="st-key-topnav_"] button {
    background: transparent !important; border: 0 !important; box-shadow: none !important; color: var(--muted) !important;
    min-height: 2rem !important; padding: .2rem .7rem !important; border-radius: 999px !important; white-space: nowrap;
}
[data-testid="stMain"] [class*="st-key-topnav_"] button p { font-size: .78rem !important; font-weight: 600 !important; }
[data-testid="stMain"] [class*="st-key-topnav_"] button:hover { background: var(--lav_50) !important; color: var(--lav_ink) !important; }
[data-testid="stMain"] [class*="st-key-topnav_on_"] button { background: var(--lav_100) !important; color: var(--lav_ink) !important; }
.st-key-nc_user_menu { gap: .35rem !important; }
[data-testid="stMain"] [class*="st-key-nc_tb_"] [data-testid="stPopover"] button {
    min-height: 2rem !important; padding: 0 .7rem !important; border-radius: 999px !important;
    background: transparent !important; border: 1px solid transparent !important;
}
[data-testid="stMain"] [class*="st-key-nc_tb_"] [data-testid="stPopover"] button * { color: var(--muted) !important; fill: var(--muted) !important; font-weight: 600 !important; font-size: .76rem !important; }
[data-testid="stMain"] [class*="st-key-nc_tb_"] [data-testid="stPopover"] button:hover { background: var(--lav_50) !important; }
[data-testid="stMain"] .st-key-nc_tb_avatar [data-testid="stPopover"] button {
    background: var(--lav_100) !important; border-color: var(--lav_300) !important;
}
[data-testid="stMain"] .st-key-nc_tb_avatar [data-testid="stPopover"] button * { color: var(--lav_ink) !important; fill: var(--lav_ink) !important; font-weight: 700 !important; }

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] { background: #FBFAFF !important; border-right: 1px solid var(--border); }
[data-testid="stSidebar"] > div:first-child { padding-top: .3rem; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .14rem; }
[data-testid="stSidebar"] .stButton > button {
    justify-content: flex-start; min-height: 2.15rem; padding: .32rem .75rem; background: transparent !important;
    border: 0 !important; border-radius: 10px !important; box-shadow: none !important; color: var(--gray_ink) !important;
}
[data-testid="stSidebar"] .stButton > button > div { justify-content: flex-start; width: 100%; }
[data-testid="stSidebar"] .stButton > button p { font-size: .82rem; font-weight: 600; text-align: left; color: inherit; }
[data-testid="stSidebar"] .stButton > button [data-testid="stIconMaterial"] { color: var(--faint); }
[data-testid="stSidebar"] .stButton > button:hover { background: var(--lav_50) !important; color: var(--lav_ink) !important; }
[data-testid="stSidebar"] [class*="st-key-nav_on_"] button { background: var(--lav_100) !important; color: var(--lav_ink) !important; box-shadow: inset 3px 0 0 var(--lav_400) !important; }
[data-testid="stSidebar"] [class*="st-key-nav_on_"] button [data-testid="stIconMaterial"] { color: var(--lav_ink); }
.shell-section-label { display: flex; align-items: center; gap: .45rem; margin: 1rem 0 .5rem; padding: 0 .5rem; color: var(--faint); font-size: .62rem; font-weight: 700; letter-spacing: .1rem; }
.shell-section-label.title { color: var(--lav_ink); font-size: .68rem; margin-top: .2rem; }
.shell-divider { height: 1px; margin: .7rem .3rem; background: var(--border); }
.shell-user { display: flex; align-items: center; gap: .6rem; margin: .4rem .2rem .3rem; padding: .65rem; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.shell-user b, .shell-user small { display: block; }
.shell-user b { color: var(--text); font-size: .8rem; }
.shell-user small { color: var(--muted); font-size: .66rem; }
.user-avatar { display: grid; place-items: center; flex: none; width: 2.1rem; height: 2.1rem; border-radius: 50%; background: var(--lav_100); color: var(--lav_ink); font-size: .7rem; font-weight: 800; }

/* ---------- Buttons ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button { min-height: 2.25rem; border-radius: 999px; font-weight: 600; transition: background .15s, border-color .15s, box-shadow .15s; }
[data-testid="stMain"] [data-testid="stBaseButton-primary"], [data-testid="stMain"] [data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stPopoverBody"] [data-testid="stBaseButton-primary"] {
    background: var(--mint_400) !important; border: 1px solid var(--mint_400) !important; color: #0F3B2E !important; box-shadow: none !important;
}
[data-testid="stMain"] [data-testid="stBaseButton-primary"] *, [data-testid="stMain"] [data-testid="stBaseButton-primaryFormSubmit"] * { color: #0F3B2E !important; }
[data-testid="stMain"] [data-testid="stBaseButton-primary"]:hover, [data-testid="stMain"] [data-testid="stBaseButton-primaryFormSubmit"]:hover {
    background: #74C8A9 !important; border-color: #74C8A9 !important; box-shadow: 0 4px 12px rgba(29, 104, 79, .16) !important;
}
[data-testid="stMain"] [data-testid="stBaseButton-primary"]:disabled { background: var(--mint_100) !important; border-color: var(--mint_100) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-primary"]:disabled * { color: #87AE9F !important; }
[data-testid="stMain"] [data-testid="stBaseButton-secondary"], [data-testid="stMain"] [data-testid="stBaseButton-secondaryFormSubmit"],
[data-testid="stMain"] .stDownloadButton > button {
    background: var(--surface) !important; border: 1px solid var(--lav_300) !important; color: var(--lav_ink) !important; box-shadow: none !important;
}
[data-testid="stMain"] [data-testid="stBaseButton-secondary"] *, [data-testid="stMain"] .stDownloadButton > button * { color: var(--lav_ink) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:hover, [data-testid="stMain"] .stDownloadButton > button:hover { background: var(--lav_50) !important; border-color: var(--lav_400) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:disabled, [data-testid="stMain"] .stDownloadButton > button:disabled { border-color: var(--border) !important; background: var(--surface_soft) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:disabled *, [data-testid="stMain"] .stDownloadButton > button:disabled * { color: var(--faint) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-tertiary"] { color: var(--lav_ink) !important; font-weight: 600; min-height: 1.9rem; }
[data-testid="stMain"] [data-testid="stBaseButton-tertiary"] * { color: var(--lav_ink) !important; }
[data-testid="stMain"] [data-testid="stBaseButton-tertiary"]:hover { text-decoration: underline; text-underline-offset: 3px; }
[data-testid="stMain"] [data-testid="stBaseButton-tertiary"]:disabled * { color: var(--faint) !important; }
[data-testid="stMain"] [class*="st-key-accent_"] button { background: var(--sky_300) !important; border: 1px solid var(--sky_300) !important; }
[data-testid="stMain"] [class*="st-key-accent_"] button * { color: #1B3F70 !important; }
[data-testid="stMain"] [class*="st-key-accent_"] button:hover { background: var(--sky_400) !important; border-color: var(--sky_400) !important; }
[data-testid="stMain"] [class*="st-key-accent_"] button:disabled { background: var(--sky_50) !important; border-color: var(--sky_100) !important; }
[data-testid="stMain"] [class*="st-key-accent_"] button:disabled * { color: #9DB2D0 !important; }
[data-testid="stMain"] [class*="st-key-danger_"] button { background: var(--surface) !important; border: 1px solid var(--rose_300) !important; }
[data-testid="stMain"] [class*="st-key-danger_"] button * { color: var(--rose_ink) !important; }
[data-testid="stMain"] [class*="st-key-danger_"] button:hover { background: var(--rose_50) !important; }
[data-testid="stMain"] [class*="st-key-danger_"] button:disabled { border-color: var(--border) !important; }
[data-testid="stMain"] [class*="st-key-danger_"] button:disabled * { color: var(--faint) !important; }
[data-testid="stMain"] [data-testid="stPopover"] button { background: var(--surface) !important; border: 1px solid var(--border_strong) !important; color: var(--text) !important; box-shadow: none !important; }
[data-testid="stMain"] [data-testid="stPopover"] button * { color: var(--text) !important; fill: var(--text) !important; }
[data-testid="stMain"] [data-testid="stPopover"] button:hover { border-color: var(--lav_300) !important; background: var(--lav_50) !important; }
[data-testid="stPopoverBody"] { background: var(--surface) !important; color: var(--text) !important; border: 1px solid var(--border) !important; border-radius: var(--radius) !important; box-shadow: var(--shadow-lift) !important; }
[data-testid="stPopoverBody"] p, [data-testid="stPopoverBody"] label { color: var(--text) !important; }
[data-testid="stPopoverBody"] [data-testid="stBaseButton-secondary"], [data-testid="stPopoverBody"] [data-testid="stBaseButton-secondaryFormSubmit"] {
    background: var(--mint_400) !important; border: 1px solid var(--mint_400) !important;
}
[data-testid="stPopoverBody"] button * { color: #0F3B2E !important; }
[data-testid="stPopoverBody"] button:disabled { background: var(--mint_100) !important; border-color: var(--mint_100) !important; }

/* ---------- Inputs (independent of the viewer's OS/browser theme) ---------- */
[data-testid="stMain"] [data-testid="stWidgetLabel"] p, [data-testid="stMain"] [data-testid="stWidgetLabel"] label,
[data-testid="stPopoverBody"] [data-testid="stWidgetLabel"] p { color: var(--text) !important; font-size: .76rem; font-weight: 600; }
[data-testid="stMain"] [data-testid="stTextInputRootElement"], [data-testid="stMain"] [data-testid="stTextAreaRootElement"],
[data-testid="stMain"] [data-testid="stNumberInputContainer"], [data-testid="stMain"] [data-testid="stDateInputField"],
[data-testid="stMain"] [data-testid="stSelectbox"] [role="group"], [data-testid="stMain"] [data-testid="stMultiSelect"] [role="group"],
[data-testid="stPopoverBody"] [data-testid="stTextAreaRootElement"], [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"],
[data-testid="stPopoverBody"] [data-testid="stSelectbox"] [role="group"] {
    background: var(--surface) !important; border: 1px solid var(--border_strong) !important; border-radius: var(--radius-sm) !important; color: var(--text) !important;
}
[data-testid="stMain"] [data-testid="stTextInputRootElement"] *, [data-testid="stMain"] [data-testid="stTextAreaRootElement"] *,
[data-testid="stMain"] [data-testid="stSelectbox"] [role="group"] *, [data-testid="stMain"] [data-testid="stNumberInputContainer"] input,
[data-testid="stPopoverBody"] [data-testid="stTextAreaRootElement"] *, [data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"] *,
[data-testid="stPopoverBody"] [data-testid="stSelectbox"] [role="group"] * {
    background-color: transparent !important; color: var(--text) !important; -webkit-text-fill-color: var(--text);
}
[data-testid="stMain"] [data-testid="stTextInputRootElement"]:focus-within, [data-testid="stMain"] [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stMain"] [data-testid="stSelectbox"] [role="group"]:focus-within, [data-testid="stPopoverBody"] [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stPopoverBody"] [data-testid="stTextInputRootElement"]:focus-within {
    border-color: var(--lav_400) !important; box-shadow: 0 0 0 3px var(--lav_100) !important;
}
[data-testid="stMain"] [data-testid="stTextInputRootElement"]:has(input:disabled) { background: var(--surface_soft) !important; }
[data-testid="stMain"] input:disabled, [data-testid="stMain"] textarea:disabled { -webkit-text-fill-color: var(--muted) !important; }
[data-testid="stMain"] input::placeholder, [data-testid="stMain"] textarea::placeholder { color: var(--faint) !important; -webkit-text-fill-color: var(--faint) !important; }
[data-testid="stMain"] [data-testid="stSelectbox"] svg { fill: var(--muted) !important; color: var(--muted) !important; }
[data-baseweb="popover"] ul[role="listbox"], [data-baseweb="popover"] [role="option"] { background: var(--surface) !important; color: var(--text) !important; }
[data-baseweb="popover"] [role="option"]:hover, [data-baseweb="popover"] [aria-selected="true"] { background: var(--lav_50) !important; }
[data-testid="stMain"] [data-testid="stCheckbox"] label > span + div { background: var(--surface) !important; border: 1.5px solid var(--border_strong) !important; border-radius: 5px !important; }
[data-testid="stMain"] [data-testid="stCheckbox"] label:has(input:checked) > span + div { background: var(--lav_400) !important; border-color: var(--lav_400) !important; }
[data-testid="stMain"] [data-testid="stCheckbox"] p { color: var(--text) !important; }
[data-testid="stFileUploaderDropzone"] { background: var(--lav_50) !important; border: 1.5px dashed var(--lav_300) !important; border-radius: var(--radius) !important; }
[data-testid="stFileUploaderDropzone"] * { color: var(--muted) !important; }
[data-testid="stFileUploaderDropzone"] button { background: var(--surface) !important; border: 1px solid var(--lav_300) !important; }
[data-testid="stFileUploaderDropzone"] button * { color: var(--lav_ink) !important; }
[data-testid="stFileUploaderFile"] * { color: var(--text) !important; }
[data-testid="stAudioInput"] > div { background: var(--surface) !important; border: 1px solid var(--border_strong) !important; border-radius: var(--radius) !important; }
[data-testid="stAudioInput"] button { color: var(--text) !important; opacity: 1 !important; }
[data-testid="stAudioInput"] button[aria-label="Record"] { color: var(--mint_ink) !important; }
[data-testid="stAudioInput"] button[aria-label="Stop recording"] { color: var(--rose_ink) !important; }
[data-testid="stAudioInput"] button svg { fill: currentColor !important; color: currentColor !important; }
[data-testid="stAudioInput"] span, [data-testid="stAudioInput"] p { background: transparent !important; color: var(--muted) !important; }

/* ---------- Containers, tabs, expanders, alerts ---------- */
div[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); border-color: var(--border) !important; border-radius: var(--radius) !important; box-shadow: var(--shadow); }
div[data-testid="stVerticalBlockBorderWrapper"] > div { padding: .95rem 1.05rem !important; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .3rem; border-bottom: 1px solid var(--border); }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background: var(--lav_400); height: 3px; border-radius: 3px; }
[data-testid="stTabs"] [data-baseweb="tab-border"] { background: transparent; }
[data-testid="stMain"] [data-testid="stTab"], [data-testid="stMain"] [data-testid="stTab"] p { color: var(--muted) !important; font-weight: 600; font-size: .8rem; }
[data-testid="stMain"] [data-testid="stTab"]:hover p { color: var(--lav_ink) !important; }
[data-testid="stMain"] [data-testid="stTab"][aria-selected="true"], [data-testid="stMain"] [data-testid="stTab"][aria-selected="true"] p { color: var(--text) !important; font-weight: 700; }
[data-testid="stMain"] [data-testid="stExpander"] details { background: var(--surface); border: 1px solid var(--border) !important; border-radius: var(--radius-sm) !important; }
[data-testid="stMain"] [data-testid="stExpander"] summary { background: var(--surface_soft) !important; color: var(--text) !important; border-radius: var(--radius-sm); }
[data-testid="stMain"] [data-testid="stExpander"] summary:hover { background: var(--lav_50) !important; }
[data-testid="stMain"] [data-testid="stExpander"] summary p, [data-testid="stMain"] [data-testid="stExpander"] summary span { color: var(--text) !important; font-weight: 600; }
[data-testid="stAlertContainer"] { border-radius: var(--radius-sm) !important; border: 1px solid transparent; }
[data-testid="stAlertContentInfo"], [data-testid="stAlertContentSuccess"], [data-testid="stAlertContentWarning"], [data-testid="stAlertContentError"] { color: var(--text) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) { background: var(--sky_50) !important; border-color: var(--sky_100); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { background: var(--mint_50) !important; border-color: var(--mint_100); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { background: var(--butter_50) !important; border-color: var(--butter_100); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) { background: var(--rose_50) !important; border-color: var(--rose_100); }
[data-testid="stAlertContainer"] p { color: var(--text) !important; }
[data-testid="stToast"] { background: var(--surface) !important; border: 1px solid var(--border); border-radius: var(--radius-sm); box-shadow: var(--shadow-lift); }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; }
[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: .7rem .9rem; }

/* ---------- Page headers ---------- */
.nc-eyebrow { color: var(--lav_ink); font-size: .64rem; font-weight: 700; letter-spacing: .12rem; text-transform: uppercase; opacity: .85; }
.page-heading { margin: .15rem 0 0; font-size: 1.62rem; line-height: 1.15; font-weight: 750; color: var(--text); letter-spacing: -.02em; }
.page-kicker { margin: .25rem 0 .5rem; font-size: .8rem; color: var(--muted); }
.section-title { margin: 0; font-size: 1rem; line-height: 1.3; font-weight: 700; color: var(--text); }
.section-subtitle { margin: .12rem 0 .25rem; font-size: .74rem; color: var(--muted); }
.nc-label { display: block; color: var(--faint); font-size: .6rem; font-weight: 700; letter-spacing: .08rem; text-transform: uppercase; margin-bottom: .3rem; }
.nc-meta { color: var(--muted); font-size: .68rem; line-height: 1.5; }

/* ---------- Badges ---------- */
.nc-badge { display: inline-flex; align-items: center; gap: .25rem; padding: .2rem .55rem; border-radius: 999px; border: 1px solid transparent; font-size: .68rem; font-weight: 600; line-height: 1.25; white-space: nowrap; }
.nc-b-green { background: var(--mint_100); border-color: var(--mint_300); color: var(--mint_ink); }
.nc-b-amber { background: var(--butter_100); border-color: var(--butter_300); color: var(--butter_ink); }
.nc-b-red { background: var(--rose_100); border-color: var(--rose_300); color: var(--rose_ink); }
.nc-b-violet { background: var(--lav_100); border-color: var(--lav_300); color: var(--lav_ink); }
.nc-b-blue { background: var(--sky_100); border-color: var(--sky_300); color: var(--sky_ink); }
.nc-b-peach { background: var(--peach_100); border-color: var(--peach_300); color: var(--peach_ink); }
.nc-b-gray { background: var(--gray_100); border-color: var(--gray_300); color: var(--gray_ink); }
.nc-badge-row { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; margin-bottom: .25rem; }
.status-pill { display: inline-flex; align-items: center; padding: .22rem .6rem; border-radius: 999px; font-size: .66rem; font-weight: 700; border: 1px solid transparent; }
.status-pending { background: var(--butter_100); border-color: var(--butter_300); color: var(--butter_ink); }
.status-approved { background: var(--mint_100); border-color: var(--mint_300); color: var(--mint_ink); }

/* ---------- Stepper ---------- */
.nc-stepper { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; margin-bottom: .35rem; padding: .5rem .6rem; background: var(--surface); border: 1px solid var(--border); border-radius: 999px; box-shadow: var(--shadow); }
.nc-step { display: flex; align-items: center; gap: .45rem; padding: .25rem .8rem .25rem .3rem; border-radius: 999px; color: var(--faint); font-size: .76rem; font-weight: 600; }
.nc-step i { display: grid; place-items: center; width: 1.45rem; height: 1.45rem; border-radius: 50%; background: var(--gray_100); font-style: normal; font-size: .66rem; font-weight: 700; color: var(--muted); }
.nc-step.done { color: var(--mint_ink); }
.nc-step.done i { background: var(--mint_100); color: var(--mint_ink); }
.nc-step.current { background: var(--lav_50); color: var(--lav_ink); }
.nc-step.current i { background: var(--lav_400); color: #fff; }

/* ---------- Context strip, banners ---------- */
.nc-context { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1rem; padding: .9rem 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }
.nc-context small { display: block; color: var(--faint); font-size: .6rem; font-weight: 700; letter-spacing: .07rem; text-transform: uppercase; }
.nc-context b { display: block; margin-top: .3rem; color: var(--text); font-size: .8rem; font-weight: 600; overflow-wrap: anywhere; }
.nc-context-foot { grid-column: 1 / -1; padding-top: .55rem; border-top: 1px dashed var(--border); color: var(--muted); font-size: .66rem; }
.nc-banner { display: flex; gap: .65rem; align-items: flex-start; padding: .7rem .95rem; border-radius: var(--radius-sm); border: 1px solid var(--sky_100); background: var(--sky_50); font-size: .76rem; color: var(--text); }
.nc-banner > span:first-child { color: var(--sky_ink); }
.nc-banner b { display: block; font-size: .78rem; font-weight: 700; }
.nc-banner small { display: block; color: var(--muted); font-size: .7rem; margin-top: .12rem; }
.nc-banner.warn { border-color: var(--butter_300); background: var(--butter_50); }
.nc-banner.warn > span:first-child { color: var(--butter_ink); }
.nc-banner.danger { border-color: var(--rose_300); background: var(--rose_50); align-items: center; }
.nc-banner.danger > span:first-child { color: var(--rose_ink); }
.nc-banner.ok { border-color: var(--mint_300); background: var(--mint_50); }
.nc-banner.ok > span:first-child { color: var(--mint_ink); }
.nc-grow { flex: 1; }

/* ---------- Recommendation cards ---------- */
[class*="st-key-rec_"] { gap: .45rem; padding: .85rem 1rem .75rem; background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--rose_300); border-radius: var(--radius-sm); }
[class*="st-key-rec_med_"] { border-left-color: var(--butter_300); }
[class*="st-key-rec_low_"] { border-left-color: var(--mint_300); }
.nc-rec { padding-bottom: .55rem; }
.nc-rec-title { margin: .4rem 0 .2rem; font-size: .9rem; font-weight: 700; color: var(--text); }
.nc-rec-body { color: var(--muted); font-size: .76rem; line-height: 1.55; white-space: pre-wrap; }
.nc-rec-source { margin-top: .3rem; color: var(--lav_ink); font-size: .68rem; font-weight: 600; }
.nc-meter { display: flex; align-items: center; gap: .5rem; font-size: .72rem; color: var(--text); }
.nc-meter span { display: block; width: 6.5rem; height: .38rem; border-radius: 999px; background: var(--gray_100); overflow: hidden; }
.nc-meter span em { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--lav_300), var(--lav_400)); }
.nc-decision { margin-top: .35rem; font-size: .68rem; color: var(--muted); }

/* ---------- Check lists ---------- */
.nc-checklist { display: flex; flex-direction: column; }
.nc-check { display: flex; justify-content: space-between; align-items: center; gap: .6rem; padding: .55rem 0; border-bottom: 1px solid var(--border); font-size: .78rem; color: var(--text); }
.nc-check:last-child { border-bottom: 0; }
.nc-check small { display: block; margin-left: 1.05rem; color: var(--muted); font-size: .66rem; }
.nc-dot { display: inline-block; width: .5rem; height: .5rem; margin-right: .55rem; border-radius: 50%; background: var(--gray_300); }
.nc-dot.tone-green { background: var(--mint_400); } .nc-dot.tone-amber { background: var(--butter_300); }
.nc-dot.tone-red { background: var(--rose_300); } .nc-dot.tone-violet { background: var(--lav_400); }
.nc-dot.tone-blue { background: var(--sky_400); } .nc-dot.tone-gray { background: var(--gray_300); }

/* ---------- Tables ---------- */
.nc-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); }
.nc-table { width: 100%; border-collapse: collapse; font-size: .76rem; color: var(--text); }
.nc-table, .nc-table tr, .nc-table th, .nc-table td { border-left: 0 !important; border-right: 0 !important; border-top: 0 !important; }
.nc-table { border: 0 !important; margin: 0 !important; }
.nc-table th { padding: .55rem .65rem; text-align: left; color: var(--faint); font-size: .6rem; font-weight: 700; letter-spacing: .06rem; text-transform: uppercase; background: var(--surface_tint); border-bottom: 1px solid var(--border); }
.nc-table td { padding: .6rem .65rem; border-bottom: 1px solid var(--border); vertical-align: middle; }
.nc-table tr:last-child td { border-bottom: 0; }
.nc-table tbody tr:hover td { background: var(--surface_soft); }
.nc-table tr.sel td { background: var(--lav_50); }
.nc-table tr.blocked td { background: var(--rose_50); color: var(--rose_ink); }
.nc-table code { font-family: var(--font); font-weight: 700; color: var(--text); background: none; padding: 0; }
.nc-cell { font-size: .76rem; color: var(--text); line-height: 1.3; overflow-wrap: anywhere; }
.nc-cell.muted { color: var(--muted); }
.nc-cell b { font-weight: 700; }
.nc-rule { height: 1px; background: var(--border); margin: .1rem 0; }
[class*="st-key-coderow_"], [class*="st-key-encrow_"] { padding: .15rem .3rem; border-bottom: 1px solid var(--border); border-radius: 8px; }
[class*="st-key-coderow_sel_"], [class*="st-key-encrow_sel_"] { background: var(--lav_50); }
[class*="st-key-dir_"] { border-radius: var(--radius-sm) !important; transition: border-color .15s, background .15s; }
[class*="st-key-dir_on_"] { border-color: var(--lav_300) !important; background: var(--lav_50) !important; }
[class*="st-key-dir_"] > div { padding: .5rem .6rem !important; }

/* ---------- Evidence and tiles ---------- */
.nc-excerpt { padding: .7rem .8rem; background: var(--surface_tint); border: 1px solid var(--lav_100); border-radius: var(--radius-sm); }
.nc-excerpt small { display: block; color: var(--faint); font-size: .6rem; font-weight: 700; letter-spacing: .07rem; text-transform: uppercase; margin-bottom: .3rem; }
.nc-excerpt q { font-style: italic; font-size: .78rem; color: var(--text); line-height: 1.5; }
.nc-cdi { padding: .7rem .8rem; background: var(--butter_50); border: 1px solid var(--butter_300); border-radius: var(--radius-sm); font-size: .76rem; }
.nc-cdi b { display: block; margin-bottom: .2rem; }
.nc-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); gap: .6rem; }
.nc-tile { padding: .7rem .85rem; background: var(--surface_soft); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.nc-tile small { display: block; color: var(--faint); font-size: .6rem; font-weight: 700; letter-spacing: .07rem; text-transform: uppercase; }
.nc-tile b { display: block; margin-top: .35rem; font-size: 1rem; font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.nc-upload-tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; background: var(--surface_soft); }
.nc-upload-tiles div { padding: .6rem .8rem; border-right: 1px solid var(--border); }
.nc-upload-tiles div:last-child { border-right: 0; }
.nc-upload-tiles small { display: block; color: var(--faint); font-size: .62rem; font-weight: 600; }
.nc-upload-tiles b { display: block; margin-top: .15rem; font-size: .78rem; }

/* ---------- People ---------- */
.nc-patient { display: flex; align-items: center; gap: .65rem; }
.nc-initials { display: grid; place-items: center; flex: none; width: 2.2rem; height: 2.2rem; border-radius: 50%; background: var(--sky_100); color: var(--sky_ink); font-size: .68rem; font-weight: 700; }
.nc-initials.lg { width: 3rem; height: 3rem; background: var(--lav_100); color: var(--lav_ink); font-size: .85rem; }
.nc-patient b { display: block; font-size: .84rem; color: var(--text); }
.nc-patient small { display: block; color: var(--muted); font-size: .68rem; }
.nc-banner-card { display: flex; flex-wrap: wrap; align-items: center; gap: 1.2rem; padding: .9rem 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }

/* ---------- KPI cards ---------- */
.kpi-card { min-height: 6.6rem; padding: .95rem 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); position: relative; overflow: hidden; }
.kpi-card::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 4px; background: var(--kpi-accent, var(--lav_300)); }
.kpi-card.tone-mint { --kpi-accent: var(--mint_300); } .kpi-card.tone-sky { --kpi-accent: var(--sky_300); }
.kpi-card.tone-lav { --kpi-accent: var(--lav_300); } .kpi-card.tone-peach { --kpi-accent: var(--peach_300); }
.kpi-card.tone-butter { --kpi-accent: var(--butter_300); } .kpi-card.tone-rose { --kpi-accent: var(--rose_300); }
.kpi-title { color: var(--faint); font-size: .62rem; font-weight: 700; letter-spacing: .07rem; text-transform: uppercase; }
.kpi-value { margin-top: .4rem; color: var(--text); font-size: 1.85rem; line-height: 1; font-weight: 750; font-variant-numeric: tabular-nums; }
.kpi-context { margin-top: .5rem; color: var(--muted); font-size: .7rem; }
.overview-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: .7rem; padding: .3rem 0; }
.overview-grid div { padding-left: .7rem; border-left: 3px solid var(--lav_100); }
.overview-grid small { display: block; color: var(--muted); font-size: .68rem; }
.overview-grid b { display: block; margin-top: .25rem; font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.amber-text { color: var(--butter_ink); } .green-text { color: var(--mint_ink); } .blue-text { color: var(--sky_ink); }

/* ---------- Patient queue ---------- */
.patient-table-header { display: grid; grid-template-columns: 2.2fr 1.15fr 1.7fr 1.55fr 1.05fr 1.15fr; min-width: 790px; padding: .5rem .55rem; background: var(--surface_tint); border-radius: 10px; color: var(--faint); font-size: .6rem; font-weight: 700; letter-spacing: .06rem; text-transform: uppercase; }
.table-two-line { display: flex; min-height: 2.45rem; flex-direction: column; justify-content: center; gap: .2rem; line-height: 1.15; }
.table-two-line small, .table-meta { color: var(--muted); font-size: .72rem; }
.table-row-rule { height: 1px; background: var(--border); }
.pagination-label { padding-top: .55rem; color: var(--muted); font-size: .72rem; text-align: center; }

/* ---------- Clinical summary, insights, graph ---------- */
.audit-note { padding-bottom: .3rem; color: var(--muted); font-size: .7rem; }
.summary-text { margin: .1rem 0 .65rem; white-space: pre-wrap; color: var(--text); font-size: .8rem; line-height: 1.6; }
.insight-scroll { max-height: 17rem; overflow-y: auto; padding-right: .2rem; }
.insight-category { margin: .55rem 0; }
.insight-category-heading { margin-bottom: .3rem; color: var(--faint); font-size: .62rem; font-weight: 700; letter-spacing: .06rem; text-transform: uppercase; }
.insight-category-items { display: flex; flex-wrap: wrap; gap: .35rem; }
.insight-chip { display: inline-flex; padding: .3rem .6rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface_soft); }
.insight-chip b { font-size: .7rem; font-weight: 600; }
.insight-diagnosis { background: var(--lav_50); border-color: var(--lav_300); color: var(--lav_ink); }
.insight-symptom { background: var(--butter_50); border-color: var(--butter_300); color: var(--butter_ink); }
.insight-risk { background: var(--peach_50); border-color: var(--peach_300); color: var(--peach_ink); }
.insight-procedure { background: var(--sky_50); border-color: var(--sky_300); color: var(--sky_ink); }
.clinical-graph-v2 { padding: 1rem; background: var(--surface_soft); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.graph-hierarchy-v2 { display: flex; flex-direction: column; align-items: center; gap: .6rem; }
.graph-patient-card { max-width: 80%; padding: .5rem .9rem; border-radius: 999px; background: var(--lav_100); color: var(--lav_ink); font-size: .74rem; font-weight: 700; text-align: center; }
.graph-connector-main { width: 2px; height: .8rem; background: var(--lav_300); }
.graph-categories { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); width: 100%; gap: .5rem; }
.graph-category-header { margin-bottom: .35rem; color: var(--faint); font-size: .58rem; font-weight: 700; text-align: center; text-transform: uppercase; letter-spacing: .05rem; }
.graph-category-content { display: flex; flex-direction: column; gap: .3rem; }
.graph-node-card, .graph-empty-state { padding: .5rem .45rem; border-radius: 10px; font-size: .7rem; line-height: 1.3; text-align: center; overflow-wrap: anywhere; }
.graph-node-diagnosis { background: var(--lav_100); color: var(--lav_ink); }
.graph-node-symptom { background: var(--sky_100); color: var(--sky_ink); }
.graph-node-risk { background: var(--peach_100); color: var(--peach_ink); }
.graph-node-procedure { background: var(--mint_100); color: var(--mint_ink); }
.graph-empty-state { color: var(--faint); border: 1px dashed var(--border_strong); }
.code-empty { padding: 1.25rem; text-align: center; color: var(--muted) !important; background: var(--surface_soft); border: 1px dashed var(--border_strong); border-radius: var(--radius-sm); font-size: .78rem; }


/* ---------- Workspace switch ---------- */
.st-key-nc_ws_switch { gap: .3rem !important; padding: .25rem; margin: .1rem 0 .4rem; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
[data-testid="stSidebar"] [class*="st-key-ws_"] button { justify-content: center !important; min-height: 1.9rem !important; border-radius: 9px !important; }
[data-testid="stSidebar"] [class*="st-key-ws_"] button > div { justify-content: center !important; }
[data-testid="stSidebar"] [class*="st-key-ws_"] button p { font-size: .74rem !important; text-align: center !important; }
[data-testid="stSidebar"] [class*="st-key-ws_on_"] button { background: var(--lav_100) !important; color: var(--lav_ink) !important; box-shadow: none !important; }

/* ---------- Value-based care ---------- */
.vbc-context { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; margin: -.2rem 0 .35rem; }
.vbc-bars { display: grid; grid-template-columns: minmax(8rem, 1.4fr) 3fr minmax(4.5rem, auto); gap: .45rem .8rem; align-items: center; font-size: .76rem; }
.vbc-bars .lbl { color: var(--text); }
.vbc-bars .lbl small { display: block; color: var(--muted); font-size: .66rem; }
.vbc-bars .val { text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--text); white-space: nowrap; }
.vbc-bars .val small { color: var(--muted); font-weight: 500; }
.vbc-track { height: .7rem; background: var(--gray_100); border-radius: 999px; overflow: hidden; }
.vbc-fill { height: 100%; border-radius: 999px; background: #6B5BD6; min-width: 2px; }
.vbc-fill.tone-green { background: #2A9D74; } .vbc-fill.tone-peach { background: #D9773F; } .vbc-fill.tone-blue { background: #3D86D6; }
.vbc-fill.tone-gray { background: var(--gray_300); } .vbc-fill.tone-red { background: #D65C78; } .vbc-fill.tone-amber { background: #C99A1E; }
.vbc-timeline { position: relative; display: flex; justify-content: space-between; gap: .4rem; padding: 1.4rem .4rem .2rem; }
.vbc-timeline::before { content: ""; position: absolute; left: .8rem; right: .8rem; top: 1.85rem; height: 2px; background: var(--lav_300); }
.vbc-tick { position: relative; flex: 1; min-width: 0; text-align: center; font-size: .66rem; color: var(--muted); }
.vbc-tick i { display: block; width: .85rem; height: .85rem; margin: 0 auto .4rem; border-radius: 50%; background: var(--text); border: 2px solid var(--surface); box-shadow: 0 0 0 1px var(--border_strong); }
.vbc-tick i.tone-green { background: #2A9D74; } .vbc-tick i.tone-peach { background: #D9773F; } .vbc-tick i.tone-blue { background: #3D86D6; }
.vbc-tick i.tone-violet { background: #6B5BD6; } .vbc-tick i.tone-red { background: #D65C78; }
.vbc-tick b { display: block; color: var(--text); font-size: .68rem; }
.vbc-lineage { display: flex; flex-wrap: wrap; align-items: stretch; gap: .35rem; }
.vbc-lineage .step { flex: 1; min-width: 7.5rem; padding: .55rem .65rem; background: var(--sky_50); border: 1px solid var(--sky_100); border-radius: 10px; font-size: .7rem; color: var(--sky_ink); text-align: center; }
.vbc-lineage .step b { display: block; color: var(--text); font-size: .74rem; margin-bottom: .15rem; }
.vbc-lineage .step.head { background: var(--lav_100); border-color: var(--lav_300); color: var(--lav_ink); }
.vbc-lineage .step.broken { background: var(--rose_50); border-color: var(--rose_300); color: var(--rose_ink); }
.vbc-lineage .arrow { align-self: center; color: var(--faint); font-weight: 700; }
.vbc-doc { padding: .75rem .9rem; background: var(--surface_soft); border: 1px solid var(--border); border-radius: 10px; font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: .74rem; line-height: 1.7; color: var(--muted); }
.vbc-doc mark { background: var(--butter_100); color: var(--text); font-weight: 700; padding: .05rem .2rem; border-radius: 4px; }
.vbc-doc .doc-head { font-family: var(--font); color: var(--faint); font-size: .62rem; font-weight: 700; letter-spacing: .07rem; text-transform: uppercase; margin-bottom: .35rem; }
.vbc-phone { max-width: 19rem; margin: 0 auto; padding: .7rem; background: #2F3346; border-radius: 26px; box-shadow: var(--shadow-lift); }
.vbc-phone .screen { background: #FFFFFF; border-radius: 18px; padding: .8rem; min-height: 15rem; }
.vbc-phone .meta { color: var(--faint); font-size: .62rem; text-align: center; margin-bottom: .6rem; }
.vbc-phone .bubble { background: var(--sky_100); color: var(--text); padding: .6rem .7rem; border-radius: 14px 14px 14px 4px; font-size: .74rem; line-height: 1.5; }
.vbc-phone .foot { color: #C9CCDA; font-size: .6rem; text-align: center; margin-top: .45rem; }
.vbc-tile { padding: .7rem .8rem; background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--gray_300); border-radius: 10px; }
.vbc-tile.tone-red { border-left-color: var(--rose_300); } .vbc-tile.tone-green { border-left-color: var(--mint_400); }
.vbc-tile.tone-blue { border-left-color: var(--sky_400); } .vbc-tile.tone-amber { border-left-color: var(--butter_300); }
.vbc-tile.tone-violet { border-left-color: var(--lav_400); }
.vbc-tile .top { display: flex; justify-content: space-between; align-items: center; gap: .4rem; }
.vbc-tile b { font-size: .84rem; color: var(--text); }
.vbc-tile p { margin: .35rem 0 0 !important; font-size: .72rem !important; color: var(--muted) !important; line-height: 1.45 !important; }
.vbc-callout { display: flex; gap: .7rem; padding: .7rem .9rem; border-radius: 12px; font-size: .74rem; line-height: 1.5; }
.vbc-callout b { display: block; margin-bottom: .1rem; }
.vbc-callout.guardrail { background: var(--butter_50); border: 1px solid var(--butter_300); color: var(--text); }
.vbc-callout.guardrail b { color: var(--butter_ink); }
.vbc-callout.handoff { background: var(--sky_50); border: 1px solid var(--sky_100); color: var(--text); }
.vbc-callout.handoff b { color: var(--sky_ink); }
.vbc-callout.story { background: var(--mint_50); border: 1px solid var(--mint_100); color: var(--text); }
.vbc-callout.story b { color: var(--mint_ink); }
.vbc-big { font-size: 2rem; font-weight: 750; color: var(--text); line-height: 1.05; font-variant-numeric: tabular-nums; }
.vbc-big small { font-size: .95rem; color: var(--muted); font-weight: 600; }
.vbc-check { display: grid; grid-template-columns: 1.4rem 1fr auto; gap: .5rem; align-items: center; padding: .38rem 0; border-bottom: 1px solid var(--border); font-size: .76rem; }
.vbc-check:last-child { border-bottom: 0; }
.vbc-check .ok, .vbc-check .no, .vbc-check .hold { display: grid; place-items: center; width: 1.3rem; height: 1.3rem; border-radius: 50%; font-size: .68rem; font-weight: 800; }
.vbc-check .ok { background: var(--mint_100); color: var(--mint_ink); }
.vbc-check .no { background: var(--rose_100); color: var(--rose_ink); }
.vbc-check .hold { background: var(--butter_100); color: var(--butter_ink); }
.vbc-check small { color: var(--muted); font-size: .68rem; text-align: right; }
.vbc-verdict { margin-top: .55rem; padding: .55rem .7rem; border-radius: 10px; text-align: center; font-size: .76rem; font-weight: 700; }
.vbc-verdict.ACCEPTED { background: var(--mint_100); color: var(--mint_ink); }
.vbc-verdict.ON_HOLD { background: var(--butter_100); color: var(--butter_ink); }
.vbc-verdict.REJECTED { background: var(--rose_100); color: var(--rose_ink); }
.vbc-verdict.REVIEW_REQUIRED { background: var(--lav_100); color: var(--lav_ink); }
.vbc-gap { padding: .75rem .85rem; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.vbc-gap.alert { border-color: var(--rose_300); box-shadow: inset 4px 0 0 var(--rose_300); }
.vbc-gap .head { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
.vbc-gap .head b { font-size: .86rem; }
.vbc-gap .rule { margin-top: .3rem; font-size: .74rem; color: var(--text); }
.vbc-gap .evidence { margin-top: .2rem; font-size: .7rem; color: var(--muted); font-style: italic; }
.vbc-person { display: flex; flex-wrap: wrap; align-items: center; gap: 1rem; padding: .9rem 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }
.vbc-person .who b { display: block; font-size: 1.05rem; color: var(--text); }
.vbc-person .who small { display: block; color: var(--muted); font-size: .7rem; margin-top: .15rem; }
.vbc-grow { flex: 1; }
.vbc-legend { display: flex; flex-wrap: wrap; gap: .3rem .9rem; font-size: .68rem; color: var(--muted); }
.vbc-legend span::before { content: ""; display: inline-block; width: .65rem; height: .65rem; border-radius: 3px; margin-right: .3rem; vertical-align: -1px; background: var(--legend); }

/* ---------- VBC POC (quality loop) ---------- */
.poc-stepper { border-radius: 14px; margin: .6rem 0 .2rem; justify-content: space-between; box-shadow: none; }
.poc-stepper .nc-step { padding: .2rem .55rem .2rem .25rem; font-size: .7rem; }
.poc-score { padding: .8rem .9rem; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; height: 100%; }
.poc-score .top { display: flex; justify-content: space-between; align-items: baseline; gap: .4rem; }
.poc-score .top b { font-size: .86rem; color: var(--text); }
.poc-score .top span { font-size: .64rem; font-weight: 700; color: var(--faint); letter-spacing: .05rem; }
.poc-score .rate { font-size: 1.9rem; font-weight: 750; color: var(--text); line-height: 1.1; margin-top: .3rem; font-variant-numeric: tabular-nums; }
.poc-score .meta { font-size: .68rem; color: var(--muted); margin-top: .15rem; }
.poc-score .track { height: .5rem; background: var(--gray_100); border-radius: 999px; overflow: hidden; margin: .45rem 0 .2rem; }
.poc-score .track i { display: block; height: 100%; background: #3D86D6; border-radius: 999px; }
.poc-score .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .25rem .6rem; margin-top: .55rem; }
.poc-score .grid span { display: flex; justify-content: space-between; font-size: .68rem; color: var(--muted); border-bottom: 1px dashed var(--border); padding-bottom: .1rem; }
.poc-score .grid b { color: var(--text); font-variant-numeric: tabular-nums; }
.poc-risk { margin-top: .6rem; padding: .65rem .8rem; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.poc-evidence { padding: .5rem .2rem .35rem; border-bottom: 1px solid var(--border); font-size: .76rem; color: var(--text); }
.poc-step-no { display: grid; place-items: center; width: 1.7rem; height: 1.7rem; border-radius: 50%; background: var(--lav_100); color: var(--lav_ink); font-weight: 800; font-size: .74rem; }

/* ---------- Responsive ---------- */
@media (max-width: 1640px) {
    .nc-app-pills { display: none; }
}
@media (max-width: 900px) {
    .block-container { padding: 0 .9rem 1.5rem !important; }
    .st-key-nc_topbar { width: calc(100% + 1.8rem) !important; margin: 0 -.9rem .9rem; padding: .55rem .9rem; }
    .page-heading { font-size: 1.35rem; }
    .nc-context { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .overview-grid { grid-template-columns: 1fr; }
    .nc-upload-tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .nc-app-pills { display: none; }
}
</style>
"""

# Extra rules for the full-screen sign-in pages (no sidebar, soft pastel backdrop).
AUTH_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] { display: none !important; }
.stApp {
    background:
        radial-gradient(circle at 12% 18%, var(--mint_100) 0, transparent 28%),
        radial-gradient(circle at 88% 12%, var(--lav_100) 0, transparent 30%),
        radial-gradient(circle at 80% 88%, var(--peach_100) 0, transparent 26%),
        var(--bg) !important;
}
[data-testid="stForm"] { background: var(--surface); border: 1px solid var(--border) !important; border-radius: 18px !important; box-shadow: var(--shadow-lift); padding: 1.2rem 1.3rem !important; }
.nc-auth-brand { display: flex; align-items: center; gap: .65rem; margin: 3.5rem 0 1.2rem; }
.nc-auth-brand .nc-brand-mark { width: 2rem; height: 2rem; }
.nc-auth-brand b { display: block; font-size: 1.4rem; color: var(--text); letter-spacing: -.01em; }
.nc-auth-brand small { display: block; color: var(--faint); font-size: .62rem; font-weight: 700; letter-spacing: .09rem; }
</style>
"""


def css_tokens():
    return "\n    ".join(f"--{name}: {value};" for name, value in PALETTE.items())


def inject_theme():
    st.markdown(_CSS.replace("__TOKENS__", css_tokens()), unsafe_allow_html=True)


def inject_auth_theme():
    st.markdown(AUTH_CSS, unsafe_allow_html=True)
