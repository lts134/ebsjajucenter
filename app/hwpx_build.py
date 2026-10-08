"""답변자료 양식 HWPX 생성기 — 부서가 실제로 내는 '○○ 의원실 답변자료' 틀대로 문서를 처음부터 만든다(python-hwpx).

틀(실제 회신 문서 4건에서 확인한 공통 구조):
  제목  "{요청 주체} 답변자료"                              ← 가운데, 굵게
  날짜  "2026. 10."                                        ← 오른쪽
  항목마다
    "N. {요구 항목 원문}"                                   ← 굵게
    "【확인 : {부서장} ☎ {내선}】"                           ← 오른쪽, 작게. 값이 없으면 [확인 필요]
    본문 문단(초안의 해당 번호 문단)
    수치 표(센터 × 기준일 — 기준일이 하나면 센터|값), 머리글 음영
    "※ 산출 근거: 정의 / 집계기간 / 추출시점 / 원자료 버전"
    "※ 지난 제출값과 차이: 센터 … (사유)"                    ← 대조에서 차이 난 행만
    자료가 없으면 "[확인 필요] 보유 자료 없음 — 별도 산출 필요"
값은 그대로 옮기고 계산하지 않는다(합계 행을 만들지 않음)."""
from __future__ import annotations
import datetime as dt, re
import pandas as pd
from hwpx.document import HwpxDocument

ITEM_RE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.*)$")

def split_body(body: str, n_items: int, item_texts: list[str] | None = None) -> tuple[list[str], dict[int, list[str]]]:
    """초안 본문을 항목 번호별 문단으로 나눈다. 번호 문단 앞의 글은 머리말(preamble)로. (머리말, {번호: [문단…]})
    규칙 초안처럼 '1.'이 인사 문단이고 항목이 2.부터 시작하면(번호 문단이 항목 수 + 1개) 한 칸 당겨 맞춘다. 항목 원문을 되풀이한 머리('항목: ')는 뗀다."""
    lines = [l.rstrip() for l in (body or "").splitlines() if l.strip()]
    nums = [int(ITEM_RE.match(l).group(1)) for l in lines if ITEM_RE.match(l)]
    intro = bool(lines) and ITEM_RE.match(lines[0]) is not None and re.search(r"요구하신|요청하신|제출합니다|회신합니다|알려\s*드립니다", lines[0]) is not None   # 첫 문단이 '1. 귀 ○○에서 요구하신 … 제출합니다' 꼴
    shift = 1 if (intro and nums == list(range(1, n_items + 2))) else 0
    pre, cur, by = [], None, {}
    for line in lines:
        m = ITEM_RE.match(line)
        if m and 1 <= int(m.group(1)) - shift <= n_items:
            cur = int(m.group(1)) - shift; by.setdefault(cur, []); text = m.group(2).strip()
            if item_texts and cur - 1 < len(item_texts) and text.startswith(item_texts[cur - 1]): text = text[len(item_texts[cur - 1]):].lstrip(" :：-—").strip()
            if text: by[cur].append(text)
            continue
        if m and shift and int(m.group(1)) == 1: pre.append(m.group(2).strip()); cur = None; continue
        (by[cur] if cur else pre).append(line.strip())
    return pre, by

def _index_of(doc, p) -> int:
    for i, q in enumerate(doc.paragraphs):
        if q is p or getattr(q, "element", None) is getattr(p, "element", object()): return i
    raise ValueError("문단을 찾지 못했습니다.")

def _fmt(v, as_int: bool | None = None) -> str:
    """값 표기: 표 전체가 정수면 정수로(학생 수 188), 소수가 섞여 있으면 소수 1자리 유지(등원율 61.0). 계산은 하지 않는다."""
    if v is None or (isinstance(v, float) and pd.isna(v)): return "-"
    if isinstance(v, (int, float)):
        if as_int is None: as_int = float(v) == int(v)
        return f"{int(v):,}" if as_int and float(v) == int(v) else f"{v:,.1f}"
    return str(v)

def _all_int(series) -> bool:
    vs = [x for x in series.tolist() if x is not None and not (isinstance(x, float) and pd.isna(x))]
    return all(isinstance(x, (int, float)) and float(x) == int(x) for x in vs) if vs else True

def _table_for(df: pd.DataFrame) -> tuple[list[str], list[list[str]]]:
    """한 지표의 값 → (머리글, 행들). 기준일이 둘 이상이면 센터 × 기준일로 펼친다."""
    dates = sorted(df["base_date"].dropna().unique().tolist())
    centers = list(dict.fromkeys(df["center"].tolist())); as_int = _all_int(df["value"])
    if len(dates) <= 1:
        return ["구분", f"값({dates[0]} 기준)" if dates else "값"], [[c, _fmt(df[df["center"] == c]["value"].iloc[0], as_int)] for c in centers]
    piv = {(r["center"], r["base_date"]): r["value"] for _, r in df.iterrows()}
    return ["구분"] + dates, [[c] + [_fmt(piv.get((c, d)), as_int) for d in dates] for c in centers]

def build_reply(req: dict, items: list[dict], values: pd.DataFrame | None, draft: dict, reasons: dict | None = None, compare_df: pd.DataFrame | None = None,
                dept_head: str = "", phone: str = "", org: str = "지역교육협력부", today: dt.date | None = None) -> bytes:
    today = today or dt.date.today()
    doc = HwpxDocument.new()
    bold16 = doc.ensure_run_style(bold=True, size=16); bold12 = doc.ensure_run_style(bold=True, size=12); bold10 = doc.ensure_run_style(bold=True, size=10)
    body10 = doc.ensure_run_style(size=10); small9 = doc.ensure_run_style(size=9, color="#444444"); note9 = doc.ensure_run_style(size=9)
    border = doc.ensure_border_fill(border_color="#000000", border_width="0.12 mm")
    def fmt(p, align=None, before=0.0, after=0.0, indent=0.0):
        kw = {}
        if align: kw["alignment"] = align
        if before: kw["spacing_before_pt"] = before
        if after: kw["spacing_after_pt"] = after
        if indent: kw["indent_left_mm"] = indent
        if kw: doc.set_paragraph_format(paragraph_index=_index_of(doc, p), **kw)
    def para(text, cp, align=None, before=0.0, after=0.0, indent=0.0):
        p = doc.add_paragraph(text, char_pr_id_ref=cp, inherit_style=False)
        fmt(p, align, before, after, indent); return p
    # 제목·날짜
    first = doc.paragraphs[0]; first.text = f"{req.get('requester') or '[확인 필요: 요청 주체]'} 답변자료"
    for r in first.runs: r.char_pr_id_ref = bold16
    fmt(first, align="CENTER", after=6)
    para(f"{today.year}. {today.month}.", body10, align="RIGHT", after=10)
    if draft.get("제목"): para(draft["제목"], bold12, after=6)
    pre, by = split_body(draft.get("본문", ""), len(items), [it["item_text"] for it in items])
    for line in pre: para(line, body10, after=2)
    confirm = f"【확인 : {org}장 {dept_head or '[확인 필요]'} ☎ {phone or '[확인 필요]'}】"
    vals = values if values is not None else pd.DataFrame(columns=["indicator", "center", "base_date", "value"])
    reasons = reasons or {}
    for i, it in enumerate(items, 1):
        para(f"{i}. {it['item_text']}", bold12, before=10, after=2)
        para(confirm, small9, align="RIGHT", after=4)
        for line in by.get(i, []): para(line, body10, indent=3, after=2)
        ind = it.get("indicator")
        sub = vals[vals["indicator"] == ind] if ind else vals.iloc[0:0]
        if ind and it.get("base_date") and len(sub) and it["base_date"] in set(sub["base_date"]): sub = sub[sub["base_date"] == it["base_date"]]
        if len(sub):
            hdr, rows = _table_for(sub)
            para(f"□ {ind} (단위: {it.get('unit') or '-'})", bold10, before=4, after=2)
            t = doc.add_table(len(rows) + 1, len(hdr), border_fill_id_ref=border)
            for c, h in enumerate(hdr):
                t.set_cell_text(0, c, h); t.set_cell_shading(0, c, "#EDEDED")
            for r, row in enumerate(rows, 1):
                for c, v in enumerate(row): t.set_cell_text(r, c, v)
            t.equalize_column_widths()
            meta = sub.iloc[0]
            prov = [f"{k}: {meta.get(col)}" for k, col in (("정의", "definition"), ("집계기간", "calc_period"), ("추출시점", "extract_date"), ("원자료 버전", "source_version")) if meta.get(col) not in (None, "", "None") and not (isinstance(meta.get(col), float) and pd.isna(meta.get(col)))]
            if prov: para("※ 산출 근거 — " + " / ".join(prov), note9, after=2)
            diffs = [(k, v) for k, v in reasons.items() if k[0] == ind and v]
            if compare_df is not None and len(compare_df):
                d_rows = compare_df[(compare_df["indicator"] == ind) & (compare_df["판정"] == "차이")]
                if len(d_rows):
                    lines = [f"{r['center']} {_fmt(r['old_value'], False)}→{_fmt(r['new_value'], False)}" + (f"({reasons.get((r['indicator'], r['center'], r['base_date']))})" if reasons.get((r["indicator"], r["center"], r["base_date"])) else "") for _, r in d_rows.iterrows()]
                    para("※ 지난 제출값과 차이: " + ", ".join(lines), note9, after=2)
            elif diffs:
                para("※ 차이 사유: " + ", ".join(f"{k[1]} {v}" for k, v in diffs), note9, after=2)
        else:
            para("[확인 필요] 보유 자료 없음 — 별도 산출 필요", note9, indent=3, after=2)
    if draft.get("산출근거"):
        para("붙임. 산출 근거", bold10, before=10, after=2)
        for line in str(draft["산출근거"]).splitlines():
            if line.strip(): para(line.strip(), note9, indent=3, after=1)
    return doc.to_bytes()
