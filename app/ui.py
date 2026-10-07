"""화면 공통(보이는 것만): 색·글꼴·여백, 사이드바 메뉴, 머리글, 단계 표시, 숫자 카드.
기준으로 삼은 것
- 색·글자 크기: 대한민국 디자인 시스템(KRDS) 토큰 — 단일 주색 Primary-50 #256EF4, 본문 글자 gray-90 #1E2124, 연한 면 secondary-5 #EEF2F7, 본문 17px에 가까운 16px
- 절제: 토스 디자인 원칙 '한 화면에 강조되는 행동은 하나' — 파란 버튼은 화면당 하나, 나머지는 흰 버튼·글자 버튼
- 글꼴: Pretendard(온라인이면 CDN에서 받고, 사내망에서 막히면 맑은 고딕으로 자동 대체)
장식 아이콘·이모지 없음. 아이콘은 Material Symbols(선 아이콘)만, 메뉴에서만 쓴다."""
import streamlit as st

PRIMARY, TEXT, MUTED, LINE, SOFT, BG = "#256EF4", "#1E2124", "#6D7882", "#E4E7EB", "#EEF2F7", "#F5F6F8"

CSS = f"""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], .stMarkdown, p, li, label, input, textarea, button, table {{
  font-family: Pretendard, "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif !important; }}
#MainMenu, footer, [data-testid="stStatusWidget"], [data-testid="stToolbar"] {{visibility: hidden; height: 0;}}
header[data-testid="stHeader"] {{background: transparent; height: 0;}}
.block-container {{padding: 1.6rem 2.4rem 4rem; max-width: 1180px;}}
html {{font-size: 16px;}}
p, li, label, .stMarkdown {{line-height: 1.6; color: {TEXT};}}
h1 {{font-size: 1.5rem !important; font-weight: 700 !important; letter-spacing: -0.02em; color: {TEXT}; margin: 0 0 .25rem !important; padding: 0 !important;}}
h2 {{font-size: 1.15rem !important; font-weight: 700 !important; letter-spacing: -0.01em; margin: 1.6rem 0 .6rem !important;}}
h3 {{font-size: 1rem !important; font-weight: 700 !important; margin: 1.2rem 0 .4rem !important;}}
h4 {{font-size: .95rem !important; font-weight: 700 !important; color: {TEXT}; margin: 1.1rem 0 .3rem !important;}}
[data-testid="stCaptionContainer"] p {{color: {MUTED}; font-size: .86rem;}}
/* ---- 사이드바: 흰 바탕, 글자 버튼 메뉴 ---- */
[data-testid="stSidebar"] {{border-right: 1px solid {LINE};}}
[data-testid="stSidebar"] > div:first-child {{padding-top: 1.2rem;}}
[data-testid="stSidebar"] .block-container {{padding: 0 .9rem 1rem;}}
.brand {{display: flex; gap: 10px; align-items: center; padding: 2px 6px 16px;}}
.brand .mark {{width: 34px; height: 34px; border-radius: 9px; background: {PRIMARY}; color: #fff; font-weight: 800; font-size: 15px; display: flex; align-items: center; justify-content: center; letter-spacing: -0.02em;}}
.brand .t {{font-weight: 700; font-size: .98rem; color: {TEXT}; letter-spacing: -0.01em; line-height: 1.2;}}
.brand .s {{font-size: .74rem; color: {MUTED}; margin-top: 2px;}}
.navsec {{font-size: .72rem; font-weight: 700; color: #99A2AC; letter-spacing: .06em; height: 34px; display: flex; align-items: flex-end; padding: 0 10px 6px; margin: 0;}}
[data-testid="stSidebar"] [data-testid="stMarkdown"] {{overflow: visible;}}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{gap: .3rem;}}
[data-testid="stSidebar"] .stButton {{margin: 0;}}
[data-testid="stSidebar"] [class*="st-key-nav"] button {{
  justify-content: flex-start !important; text-align: left; width: 100%; padding: 7px 10px; border: 0; border-radius: 8px; background: transparent;
  color: #3B4350; font-weight: 500; min-height: 0; line-height: 1.3;}}
[data-testid="stSidebar"] [class*="st-key-nav"] button > div {{justify-content: flex-start !important; width: 100%; gap: 10px;}}
[data-testid="stSidebar"] [class*="st-key-nav"] button p {{font-size: .93rem; text-align: left;}}
[data-testid="stSidebar"] [class*="st-key-nav"] button span[data-testid="stIconMaterial"] {{font-size: 20px; color: #6D7882;}}
[data-testid="stSidebar"] [class*="st-key-nav"] button:hover {{background: #F1F3F5; color: {TEXT};}}
[data-testid="stSidebar"] [class*="st-key-navon_"] button {{background: {SOFT}; color: {PRIMARY}; font-weight: 700;}}
[data-testid="stSidebar"] [class*="st-key-navon_"] button:hover {{background: {SOFT}; color: {PRIMARY};}}
[data-testid="stSidebar"] [class*="st-key-navon_"] button span[data-testid="stIconMaterial"] {{color: {PRIMARY};}}
.sidefoot {{border-top: 1px solid {LINE}; margin-top: 14px; padding: 12px 6px 0; font-size: .8rem; color: {MUTED};}}
.sidefoot .who {{color: {TEXT}; font-weight: 600; font-size: .86rem;}}
/* ---- 머리글·안내문 ---- */
.eyebrow {{font-size: .78rem; font-weight: 700; color: {PRIMARY}; letter-spacing: .04em; margin-bottom: 4px;}}
.lead {{color: {MUTED}; font-size: .95rem; margin: 2px 0 18px;}}
.topbar {{display: flex; justify-content: flex-end; gap: 8px; margin-bottom: -6px;}}
.pill {{display: inline-flex; align-items: center; gap: 6px; border: 1px solid {LINE}; background: #fff; border-radius: 999px; padding: 3px 10px; font-size: .78rem; color: #3B4350;}}
.pill i {{width: 7px; height: 7px; border-radius: 50%; background: #B8C0C8; display: inline-block;}}
.pill i.on {{background: #1F9D55;}}
/* ---- 단계 표시 ---- */
.stepper {{display: flex; align-items: center; margin: 4px 0 22px;}}
.stepper .st {{display: flex; align-items: center; gap: 10px; color: {MUTED}; font-size: .9rem; white-space: nowrap;}}
.stepper .st .n {{width: 26px; height: 26px; border-radius: 50%; border: 1.5px solid #C9D0D8; display: flex; align-items: center; justify-content: center; font-size: .8rem; font-weight: 700; color: {MUTED}; background: #fff;}}
.stepper .st.on {{color: {TEXT}; font-weight: 700;}}
.stepper .st.on .n {{background: {PRIMARY}; border-color: {PRIMARY}; color: #fff;}}
.stepper .st.done .n {{background: #fff; border-color: {PRIMARY}; color: {PRIMARY};}}
.stepper .st.done {{color: #3B4350;}}
.stepper .ln {{flex: 1; height: 1.5px; background: #D9DEE4; margin: 0 14px;}}
.stepper .ln.done {{background: {PRIMARY};}}
/* ---- 카드·숫자 ---- */
[data-testid="stVerticalBlockBorderWrapper"] {{border-radius: 12px !important; border-color: {LINE} !important; background: #fff; box-shadow: 0 1px 2px rgba(16, 24, 40, .04);}}
.kpi {{background: #fff; border: 1px solid {LINE}; border-radius: 12px; padding: 16px 18px 14px; min-height: 92px; box-shadow: 0 1px 2px rgba(16,24,40,.04); position: relative; overflow: hidden;}}
.kpi:before {{content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: #C9D0D8;}}
.kpi.ok:before {{background: {PRIMARY};}} .kpi.warn:before {{background: #F59E0B;}} .kpi.bad:before {{background: #DC2626;}}
.kpi .v {{font-size: 1.75rem; font-weight: 800; color: {TEXT}; line-height: 1.1; letter-spacing: -0.02em;}}
.kpi .l {{color: {MUTED}; font-size: .84rem; margin-top: 6px;}}
.card-t {{font-weight: 700; font-size: 1.02rem; color: {TEXT}; margin: 2px 0 4px; letter-spacing: -0.01em;}}
.card-d {{color: {MUTED}; font-size: .9rem; min-height: 70px; line-height: 1.55;}}
div[data-testid="stMetric"] {{background: #fff; border: 1px solid {LINE}; border-radius: 12px; padding: 12px 16px;}}
div[data-testid="stMetric"] label p {{color: {MUTED} !important; font-size: .84rem !important;}}
div[data-testid="stMetricValue"] {{font-size: 1.6rem !important; font-weight: 800 !important; letter-spacing: -0.02em;}}
/* ---- 입력·버튼 ---- */
.stButton > button, .stDownloadButton > button {{border-radius: 8px; font-weight: 600; box-shadow: none;}}
.stButton > button[kind="secondary"], .stDownloadButton > button {{border: 1px solid #CBD2DA; color: {TEXT}; background: #fff;}}
.stButton > button[kind="secondary"]:hover {{border-color: {PRIMARY}; color: {PRIMARY}; background: #fff;}}
.stButton > button[kind="primary"] {{background: {PRIMARY}; border-color: {PRIMARY};}}
.stButton > button[kind="primary"]:hover {{background: #1D5FD6; border-color: #1D5FD6;}}
.stButton > button[kind="tertiary"] {{color: #3B4350;}}
div[data-testid="stExpander"] details {{border-radius: 10px; border-color: {LINE};}}
div[data-testid="stExpander"] summary p {{font-weight: 600;}}
[data-testid="stFileUploaderDropzone"] {{border-radius: 10px; border: 1.5px dashed #C9D0D8; background: #FAFBFC;}}
div[data-testid="stAlert"] {{border-radius: 10px;}}
.small-muted {{color: {MUTED}; font-size: .84rem;}}
hr {{margin: 1.4rem 0 1.1rem; border-color: {LINE};}}
</style>
"""

def inject():
    st.markdown(CSS, unsafe_allow_html=True)

def brand(title: str, sub: str, mark: str = "요"):
    st.markdown(f'<div class="brand"><div class="mark">{mark}</div><div><div class="t">{title}</div><div class="s">{sub}</div></div></div>', unsafe_allow_html=True)

def nav(sections: list[tuple[str, list[tuple[str, str]]]], current: str) -> str | None:
    """사이드바 메뉴. sections: [(구역 이름, [(메뉴 이름, material 아이콘)])]. 눌린 메뉴 이름을 돌려준다(없으면 None)."""
    clicked = None
    for sec, items in sections:
        if sec: st.markdown(f'<div class="navsec">{sec}</div>', unsafe_allow_html=True)
        for i, (name, icon) in enumerate(items):
            key = f"navon_{sec}_{i}" if name == current else f"nav_{sec}_{i}"
            if st.button(name, key=key, icon=f":material/{icon}:", type="tertiary", width="stretch"): clicked = name
    return clicked

def sidefoot(user: str, ai_on: bool, ai_text: str, extra: str = ""):
    tail = f'<div style="margin-top:6px">{extra}</div>' if extra else ""
    dot = "on" if ai_on else ""
    st.markdown(f'<div class="sidefoot"><div class="who">{user}</div><div><span class="pill"><i class="{dot}"></i>{ai_text}</span></div>{tail}</div>', unsafe_allow_html=True)

def page_title(title: str, lead: str = "", eyebrow: str = ""):
    if eyebrow: st.markdown(f'<div class="eyebrow">{eyebrow}</div>', unsafe_allow_html=True)
    st.markdown(f"# {title}")
    if lead: st.markdown(f'<div class="lead">{lead}</div>', unsafe_allow_html=True)

def stepper(labels: list[str], current: int, done_upto: int = 0):
    """labels: 단계 이름들. current: 1부터. done_upto: 끝난 단계 수."""
    parts = []
    for i, name in enumerate(labels, 1):
        cls = "st on" if i == current else ("st done" if i <= done_upto else "st")
        parts.append(f'<div class="{cls}"><div class="n">{i}</div>{name}</div>')
        if i < len(labels): parts.append(f'<div class="ln {"done" if i <= done_upto else ""}"></div>')
    st.markdown('<div class="stepper">' + "".join(parts) + "</div>", unsafe_allow_html=True)

def kpi(col, value, label, tone: str = ""):
    col.markdown(f'<div class="kpi {tone}"><div class="v">{value}</div><div class="l">{label}</div></div>', unsafe_allow_html=True)

def action_card(col, title: str, desc: str, button: str, key: str, icon: str, primary: bool = False) -> bool:
    with col, st.container(border=True):
        st.markdown(f'<div class="card-t">{title}</div><div class="card-d">{desc}</div>', unsafe_allow_html=True)
        return st.button(button, key=key, width="stretch", type="primary" if primary else "secondary", icon=f":material/{icon}:")

def status_line(on: bool, text: str):
    st.markdown(f'<span class="pill"><i class="{"on" if on else ""}"></i>{text}</span>', unsafe_allow_html=True)
