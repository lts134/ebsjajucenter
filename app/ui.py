"""화면 공통: 색·글꼴·여백(CSS), 머리띠, 단계 표시, 숫자 카드. 기능 코드는 app.py에 두고 여기는 보이는 것만 다룬다.
원칙: 관공서 실무자가 쓰는 도구답게 차분한 색 하나(남색), 흰 카드, 넉넉한 여백, 짧은 문장. 장식 아이콘·이모지 없음."""
import streamlit as st

PRIMARY = "#1B4F8A"
CSS = """
<style>
#MainMenu, footer, [data-testid="stStatusWidget"] {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
.block-container {padding-top: 1.4rem; padding-bottom: 4rem; max-width: 1240px;}
h1 {font-size: 1.55rem !important; font-weight: 700 !important; letter-spacing: -0.01em; margin-bottom: .2rem !important;}
h2 {font-size: 1.2rem !important; font-weight: 700 !important; margin-top: 1.1rem !important;}
h3 {font-size: 1.02rem !important; font-weight: 650 !important;}
p, li, label, .stMarkdown {line-height: 1.55;}
[data-testid="stSidebar"] {border-right: 1px solid #E5E9F0;}
[data-testid="stSidebar"] .stRadio > label {display: none;}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label {padding: 6px 8px; border-radius: 8px; margin: 1px 0;}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] > label:hover {background: #F1F5FA;}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] p {font-size: 0.95rem;}
[data-testid="stSidebar"] .stRadio div[role="radiogroup"] [data-testid="stCaptionContainer"] p {font-size: 0.78rem; color: #6B7A90;}
.brand {padding: 4px 4px 10px; border-bottom: 1px solid #E5E9F0; margin-bottom: 10px;}
.brand .t {font-weight: 700; font-size: 1.02rem; color: #1B4F8A; letter-spacing: -0.01em;}
.brand .s {font-size: 0.78rem; color: #6B7A90; margin-top: 2px;}
.lead {color: #4B5A6E; font-size: 0.96rem; margin: -2px 0 16px;}
.steps {display: flex; gap: 8px; margin: 6px 0 20px;}
.step {flex: 1; padding: 9px 12px; border-radius: 10px; background: #FFFFFF; border: 1px solid #E2E8F0; color: #6B7A90; font-size: 0.92rem;}
.step b {display: block; font-size: 0.72rem; font-weight: 600; letter-spacing: .02em; margin-bottom: 2px; opacity: .85;}
.step.on {border-color: #1B4F8A; color: #1B4F8A; background: #EEF4FB; font-weight: 600;}
.step.done {color: #1F7A4D; border-color: #BFE3CD; background: #F1FAF4;}
.kpi {background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 14px 16px; min-height: 84px;}
.kpi .v {font-size: 1.7rem; font-weight: 700; color: #0F172A; line-height: 1.1;}
.kpi .l {color: #6B7A90; font-size: 0.84rem; margin-top: 4px;}
.kpi.warn .v {color: #B45309;} .kpi.bad .v {color: #B91C1C;}
.actioncard {padding: 4px 2px 2px; min-height: 96px;}
.actioncard .t {font-weight: 700; font-size: 1.05rem; color: #0F172A; margin-bottom: 6px;}
.actioncard .d {color: #4B5A6E; font-size: 0.9rem; min-height: 44px;}
.status {display: inline-flex; align-items: center; gap: 6px; font-size: 0.82rem; color: #4B5A6E;}
.status i {width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: #9AA6B8;}
.status i.on {background: #1F9D55;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 10px 14px;}
div[data-testid="stExpander"] details {border-radius: 10px;}
.stButton > button {border-radius: 8px; font-weight: 600;}
.stDownloadButton > button {border-radius: 8px;}
.small-muted {color: #6B7A90; font-size: 0.84rem;}
</style>
"""

def inject():
    st.markdown(CSS, unsafe_allow_html=True)

def brand(title: str, sub: str):
    st.markdown(f'<div class="brand"><div class="t">{title}</div><div class="s">{sub}</div></div>', unsafe_allow_html=True)

def page_title(title: str, lead: str = ""):
    st.markdown(f"# {title}")
    if lead: st.markdown(f'<div class="lead">{lead}</div>', unsafe_allow_html=True)

def steps(labels: list[tuple[str, str]], current: int, done_upto: int = 0):
    """labels: [(짧은 번호글, 이름)], current: 1부터. done_upto: 끝난 단계 수."""
    parts = []
    for i, (k, name) in enumerate(labels, 1):
        cls = "step on" if i == current else ("step done" if i <= done_upto else "step")
        parts.append(f'<div class="{cls}"><b>{k}</b>{name}</div>')
    st.markdown('<div class="steps">' + "".join(parts) + "</div>", unsafe_allow_html=True)

def kpi(col, value, label, tone: str = ""):
    col.markdown(f'<div class="kpi {tone}"><div class="v">{value}</div><div class="l">{label}</div></div>', unsafe_allow_html=True)

def action_card(col, title: str, desc: str, button: str, key: str) -> bool:
    with col, st.container(border=True):
        st.markdown(f'<div class="actioncard"><div class="t">{title}</div><div class="d">{desc}</div></div>', unsafe_allow_html=True)
        return st.button(button, key=key, width="stretch", type="primary" if key.endswith("new") else "secondary")

def status_line(on: bool, text: str):
    st.markdown(f'<span class="status"><i class="{"on" if on else ""}"></i>{text}</span>', unsafe_allow_html=True)
