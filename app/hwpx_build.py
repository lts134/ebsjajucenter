"""답변자료 양식 HWPX 생성기 — 부서가 실제로 내는 '○○ 의원실 답변자료' 틀대로 문서를 처음부터 만든다(python-hwpx).

틀(실제 회신 문서 4건에서 확인한 공통 구조):
  제목  "{요청 주체} 답변자료"                              ← 가운데, 굵게
  날짜  "2026. 10."                                        ← 오른쪽
  항목마다
    "N. {요구 항목 원문}"                                   ← 굵게
    "【확인 : {부서장} ☎ {내선}】"                           ← 오른쪽, 작게. 값이 없으면 [확인 필요]
    본문 문단(초안의 해당 번호 문단)
    수치 표(센터 × 기준일 — 기준일이 하나면 센터|값), 머리글 음영·반복, 첫 열은 이름이 한 줄에 들어가는 폭, 24행 넘으면 쪽 단위로 나눔
    "※ 산출 근거: 정의 / 집계기간 / 추출시점 / 원자료 버전"
    "※ 지난 제출값과 차이: 센터 … (사유)"                    ← 대조에서 차이 난 행만
    자료가 없으면 "[확인 필요] 보유 자료 없음 — 별도 산출 필요"
값은 그대로 옮기고 계산하지 않는다(합계 행을 만들지 않음)."""
from __future__ import annotations
import datetime as dt, re
import pandas as pd
from hwpx.document import HwpxDocument

ITEM_RE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.*)$")
DROP_RE = re.compile(r"^\s*(붙임|첨부)\s*[:：]?\s*.*|^\s*끝\.?\s*$")
BIG_TABLE_ROWS = 20     # 이보다 긴 표는 새 쪽에서 시작(한글이 표를 통째로 다음 쪽에 보내 앞 쪽이 비는 것을 피함)

def split_body(body: str, n_items: int, item_texts: list[str] | None = None) -> tuple[list[str], dict[int, list[str]]]:
    """초안 본문을 항목 번호별 문단으로 나눈다. 번호 문단 앞의 글은 머리말(preamble)로. (머리말, {번호: [문단…]})
    규칙 초안처럼 '1.'이 인사 문단이고 항목이 2.부터 시작하면(번호 문단이 항목 수 + 1개) 한 칸 당겨 맞춘다. 항목 원문을 되풀이한 머리('항목: ')는 뗀다."""
    lines = [l.rstrip() for l in (body or "").splitlines() if l.strip()]
    nums = [int(ITEM_RE.match(l).group(1)) for l in lines if ITEM_RE.match(l)]
    intro = bool(lines) and ITEM_RE.match(lines[0]) is not None and re.search(r"요구하신|요청하신|제출합니다|회신합니다|알려\s*드립니다", lines[0]) is not None   # 첫 문단이 '1. 귀 ○○에서 요구하신 … 제출합니다' 꼴
    shift = 1 if (intro and nums == list(range(1, n_items + 2))) else 0
    pre, cur, by = [], None, {}
    for line in lines:
        if DROP_RE.match(line): continue                                            # '붙임: … 1부. 끝.' 같은 줄은 양식이 따로 넣으므로 뺀다
        m = ITEM_RE.match(line)
        if m and 1 <= int(m.group(1)) - shift <= n_items:
            cur = int(m.group(1)) - shift; by.setdefault(cur, []); text = m.group(2).strip()
            if item_texts and cur - 1 < len(item_texts) and text.startswith(item_texts[cur - 1]): text = text[len(item_texts[cur - 1]):].lstrip(" :：-—").strip()
            if text: by[cur].append(text)
            continue
        if m and shift and int(m.group(1)) == 1: pre.append(m.group(2).strip()); cur = None; continue
        text = m.group(2).strip() if (m and cur) else line.strip()                 # 항목 수를 넘는 번호('2.' '3.'를 문단 번호로 쓴 경우)는 번호만 뗀다
        (by[cur] if cur else pre).append(text)
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

def _short_date(d: str) -> str:
    """표 머리글용 짧은 날짜: 월말이면 '25.12 (연.월), 아니면 '26.9.15. 열이 좁아도 한 줄에 들어가게."""
    try:
        import calendar
        y, m, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
        return f"'{y % 100:02d}.{m}" if dd == calendar.monthrange(y, m)[1] else f"'{y % 100:02d}.{m}.{dd}"
    except (ValueError, TypeError): return str(d)

def _table_for(df: pd.DataFrame) -> tuple[list[str], list[list[str]]]:
    """한 지표의 값 → (머리글, 행들). 기준일이 둘 이상이면 센터 × 기준일로 펼친다(머리글은 짧은 날짜)."""
    dates = sorted(df["base_date"].dropna().unique().tolist())
    centers = list(dict.fromkeys(df["center"].tolist())); as_int = _all_int(df["value"])
    if len(dates) <= 1:
        return ["구분", f"값({dates[0]} 기준)" if dates else "값"], [[c, _fmt(df[df["center"] == c]["value"].iloc[0], as_int)] for c in centers]
    piv = {(r["center"], r["base_date"]): r["value"] for _, r in df.iterrows()}
    return ["구분"] + [_short_date(d) for d in dates], [[c] + [_fmt(piv.get((c, d)), as_int) for d in dates] for c in centers]

# ---- 표 치수(HWPUNIT = 1/7200인치, 1pt = 100) ----
TEXT_W = 42520          # A4 본문 폭(210mm - 좌우 30mm씩)
CELL_MARGIN = 280       # 셀 좌우 안쪽 여백(≈1mm) — 열이 많은 표도 들어가게 기본값(510)보다 좁게
CELL_PAD = CELL_MARGIN * 2

def _text_w(text: str, pt: float = 9) -> int:
    """글자 폭 추정(HWPUNIT): 한글·전각은 1em, 숫자·영문·기호는 0.55em."""
    return int(sum((1.0 if ord(ch) > 0x2E7F else 0.55) for ch in str(text)) * pt * 100)

def _line_h(pt: float) -> int: return int(pt * 100 * 1.6) + 160          # 줄 간격 160% + 셀 위아래 여백

def _fit_pt(hdr: list[str], rows: list[list[str]]) -> float:
    """표 글자 크기: 첫 열 이름과 수치 열이 모두 한 줄에 들어가는 가장 큰 크기(9 → 8 → 7). 7로도 안 되면 7(첫 열이 두 줄로 접힘)."""
    n = len(hdr)
    for pt in (9, 8, 7):
        label = max([_text_w(hdr[0], pt)] + [_text_w(r[0], pt) for r in rows]) + CELL_PAD
        num = max([_text_w(h, pt) for h in hdr[1:]] + [_text_w(r[c], pt) for r in rows for c in range(1, n)] + [0]) + CELL_PAD
        if label + num * (n - 1) <= TEXT_W: return pt
    return 7

def _col_widths(hdr: list[str], rows: list[list[str]], pt: float = 9) -> list[int]:
    """열 폭: 첫 열(구분)은 가장 긴 이름이 한 줄에 들어가게(본문 폭의 45%까지), 나머지는 남는 폭을 같게. 수치 열은 머리글·값이 들어갈 최소 폭을 지킨다."""
    n = len(hdr)
    if n == 1: return [TEXT_W]
    label_need = max([_text_w(hdr[0], pt)] + [_text_w(r[0], pt) for r in rows]) + CELL_PAD
    label_w = min(label_need, int(TEXT_W * 0.45))
    num_need = max([_text_w(h, pt) for h in hdr[1:]] + [_text_w(r[c], pt) for r in rows for c in range(1, n)]) + CELL_PAD
    rest = TEXT_W - label_w
    if rest // (n - 1) < num_need: label_w = max(int(TEXT_W * 0.25), TEXT_W - num_need * (n - 1)); rest = TEXT_W - label_w
    each = rest // (n - 1)
    ws = [label_w] + [each] * (n - 1); ws[-1] += TEXT_W - sum(ws)
    return ws

def _lines(text: str, width: int, pt: float = 9) -> int:
    inner = max(width - CELL_PAD, 500)
    return max(1, -(-_text_w(text, pt) // inner))

def _emit_table(doc, hdr: list[str], rows: list[list[str]], border, pt: float, ws: list[int]):
    """표 하나를 문서에 넣는다: 머리글 음영·굵게, 열 폭(ws)·글자 크기(pt)는 전체 표 기준으로 미리 정한 것(나눈 조각이 같은 모양이 되게), 줄 수에 맞춘 행 높이, 첫 열 왼쪽·수치 가운데 정렬, 머리글 반복."""
    cp_head = doc.ensure_run_style(bold=True, size=pt); cp_body = doc.ensure_run_style(size=pt); lh = _line_h(pt)
    t = doc.add_table(len(rows) + 1, len(hdr), border_fill_id_ref=border)
    t.element.set("repeatHeader", "1")                                             # 쪽이 넘어가면 머리글 다시
    t.set_column_widths(ws)
    left, center = [], []
    heights = []
    for r, row in enumerate([hdr] + rows):
        h = max(_lines(v, ws[c], pt) for c, v in enumerate(row)) * lh; heights.append(h)
        for c, v in enumerate(row):
            t.set_cell_text(r, c, v)
            cell = t.cell(r, c); cell.set_size(height=h); cell.set_margins(left=CELL_MARGIN, right=CELL_MARGIN, top=141, bottom=141)
            for p in cell.paragraphs:
                for run in p.runs: run.char_pr_id_ref = cp_head if r == 0 else cp_body
                (center if (r == 0 or c > 0) else left).append(p)
            if r == 0: t.set_cell_shading(r, c, "#EDEDED")
    sz = t.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}sz")
    if sz is not None: sz.set("height", str(sum(heights)))
    if left: doc.styles.apply_paragraph_format(paragraphs=left, alignment="LEFT")
    if center: doc.styles.apply_paragraph_format(paragraphs=center, alignment="CENTER")
    return t

def _unit_for(indicator: str | None) -> str | None:
    """표 제목의 단위: 지표 이름에서 분명한 것만(율·률 → %, 학생 수·인원 → 명, 센터 수 → 개소). 모르면 적지 않는다."""
    s = indicator or ""
    if re.search(r"[율률]$", s): return "%"
    if re.search(r"학생\s*수|인원", s): return "명"
    if re.search(r"센터\s*수|개소", s): return "개소"
    return None

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
        for line in by.get(i, []): para(line, body10, align="LEFT", indent=3, after=2)      # 양쪽 정렬은 긴 낱말에서 글자 사이가 벌어져 왼쪽 정렬
        ind = it.get("indicator")
        sub = vals[vals["indicator"] == ind] if ind else vals.iloc[0:0]
        if ind and it.get("base_date") and len(sub) and it["base_date"] in set(sub["base_date"]): sub = sub[sub["base_date"] == it["base_date"]]
        if len(sub):
            hdr, rows = _table_for(sub)
            unit = _unit_for(ind); dates = sorted(sub["base_date"].dropna().unique().tolist())
            cap = para(f"□ {ind}" + (f" (단위: {unit})" if unit else "") + (f" — 기준일 {dates[0]} ~ {dates[-1]}, 각 월 말일" if len(dates) > 1 else ""), bold10, before=4, after=2)
            if len(rows) > BIG_TABLE_ROWS: doc.styles.apply_paragraph_format(paragraphs=[cap], page_break_before=True)   # 긴 표는 새 쪽에서(별지처럼)
            pt = _fit_pt(hdr, rows); ws = _col_widths(hdr, rows, pt)
            per_page = max(1, 60000 // _line_h(pt) - 1)                            # 한 쪽에 들어가는 자료 행 수(본문 높이 약 60000 HWPUNIT ≈ 212mm 기준)
            n_chunks = -(-len(rows) // per_page); size = -(-len(rows) // n_chunks)   # 조각 수를 정한 뒤 고르게 나눈다(45+3 대신 24+24)
            for k in range(0, len(rows), size):                                     # 긴 표는 쪽 단위로 나눠 넣는다(머리글 반복, 같은 열 폭·글자 크기)
                _emit_table(doc, hdr, rows[k:k + size], border, pt, ws)
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
