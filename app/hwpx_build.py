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

ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*[.)](?=\s|[가-힣\[【(])\s*(.*)$")       # '1. …' '2) …' '1.등원율'. '2025. 12. 31. 기준…' '6.30 기준'(날짜)은 번호가 아니다
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

def _decimals(series) -> int:
    """표 전체에서 가장 긴 소수 자릿수(최대 3): 받은 값을 반올림해 바꾸지 않고, 한 표 안에서는 자릿수를 맞춘다(188 / 61.0 / 61.25)."""
    n = 0
    for x in (series.tolist() if hasattr(series, "tolist") else list(series)):
        if isinstance(x, (int, float)) and not isinstance(x, bool) and not (isinstance(x, float) and pd.isna(x)):
            s = f"{float(x):.6f}".rstrip("0"); n = max(n, len(s.split(".")[1]) if "." in s else 0)
    return min(n, 3)

def _fmt(v, dec: int | None = None) -> str:
    """값 표기: dec 자릿수(없으면 그 값에 필요한 만큼). 계산은 하지 않는다."""
    if v is None or (isinstance(v, float) and pd.isna(v)): return "-"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if dec is None: dec = _decimals([v])
        return f"{v:,.{dec}f}"
    return str(v)

def _is_month_end(d: str) -> bool:
    try:
        import calendar
        return int(d[8:10]) == calendar.monthrange(int(d[:4]), int(d[5:7]))[1]
    except (ValueError, TypeError): return False

def item_groups(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """같은 원문의 항목(지표·기준일 분리로 여러 개)을 한 번호로 묶는다 — 초안의 번호 매김(draft.unique_texts)과 같은 순서라 본문 번호와 표가 어긋나지 않는다."""
    out: list[tuple[str, list[dict]]] = []
    for it in items:
        t = it.get("item_text") or ""
        g = next((g for g in out if g[0] == t), None)
        if g: g[1].append(it)
        else: out.append((t, [it]))
    return out

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
    centers = list(dict.fromkeys(df["center"].tolist())); dec = _decimals(df["value"])
    if len(dates) <= 1:
        return ["구분", f"값({dates[0]} 기준)" if dates else "값"], [[c, _fmt(df[df["center"] == c]["value"].iloc[0], dec)] for c in centers]
    piv = {(r["center"], r["base_date"]): r["value"] for _, r in df.iterrows()}
    return ["구분"] + [_short_date(d) for d in dates], [[c] + [_fmt(piv.get((c, d)), dec) for d in dates] for c in centers]

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

def build_model(req: dict, items: list[dict], values: pd.DataFrame | None, draft: dict, reasons: dict | None = None, compare_df: pd.DataFrame | None = None,
                dept_head: str = "", phone: str = "", org: str = "지역교육협력부", today: dt.date | None = None, doc_label: str = "답변자료", show_confirm: bool = True) -> list[dict]:
    """답변자료의 내용 모형(블록 목록). HWPX(build_reply)와 화면 미리보기(preview_html)가 같은 모형을 그리므로 둘이 어긋나지 않는다.
    블록: title·date·subtitle·pre·heading·confirm·body·table(caption·hdr·rows·big)·note·missing·attach_title·attach"""
    today = today or dt.date.today(); out = []
    out.append({"kind": "title", "text": f"{req.get('requester') or '[확인 필요: 요청 주체]'} {doc_label}"})
    out.append({"kind": "date", "text": f"{today.year}. {today.month}."})
    if draft.get("제목"): out.append({"kind": "subtitle", "text": draft["제목"]})
    groups = item_groups(items)
    pre, by = split_body(draft.get("본문", ""), len(groups), [t for t, _ in groups])
    out += [{"kind": "pre", "text": line} for line in pre]
    confirm = f"【확인 : {org}장 {dept_head or '[확인 필요]'} ☎ {phone or '[확인 필요]'}】"
    vals = values if values is not None else pd.DataFrame(columns=["indicator", "center", "base_date", "value"])
    reasons = reasons or {}
    for i, (text, its) in enumerate(groups, 1):
        out.append({"kind": "heading", "text": f"{i}. {text}"})
        if show_confirm: out.append({"kind": "confirm", "text": confirm})
        out += [{"kind": "body", "text": line} for line in by.get(i, [])]
        shown = 0
        for ind in list(dict.fromkeys(it.get("indicator") for it in its if it.get("indicator"))):      # 한 원문에 지표가 여럿이면 지표마다 표 하나
            bds = [it.get("base_date") for it in its if it.get("indicator") == ind]
            sub = vals[vals["indicator"] == ind]
            if len(sub) and all(bds) and set(bds) & set(sub["base_date"]): sub = sub[sub["base_date"].isin(bds)]   # 요구 기준일이 있고 그 값이 있으면 그 기준일만
            if not len(sub): continue
            shown += 1
            hdr, rows = _table_for(sub); dec = _decimals(sub["value"])
            unit = _unit_for(ind); dates = sorted(sub["base_date"].dropna().unique().tolist())
            tail = (f" — 기준일 {dates[0]} ~ {dates[-1]}" + (", 각 월 말일" if all(_is_month_end(d) for d in dates) else f" ({len(dates)}개 기준일)")) if len(dates) > 1 else ""
            out.append({"kind": "table", "caption": f"□ {ind}" + (f" (단위: {unit})" if unit else "") + tail, "hdr": hdr, "rows": rows, "big": len(rows) > BIG_TABLE_ROWS})
            meta = sub.iloc[0]
            prov = [f"{k}: {meta.get(col)}" for k, col in (("정의", "definition"), ("집계기간", "calc_period"), ("추출시점", "extract_date"), ("원자료 버전", "source_version")) if meta.get(col) not in (None, "", "None") and not (isinstance(meta.get(col), float) and pd.isna(meta.get(col)))]
            if prov: out.append({"kind": "note", "text": "※ 산출 근거 — " + " / ".join(prov)})
            diffs = [(k, v) for k, v in reasons.items() if k[0] == ind and v]
            if compare_df is not None and len(compare_df):
                d_rows = compare_df[(compare_df["indicator"] == ind) & (compare_df["판정"] == "차이")]
                if len(d_rows):
                    lines = [f"{r['center']} {_fmt(r['old_value'], dec)}→{_fmt(r['new_value'], dec)}" + (f"({reasons.get((r['indicator'], r['center'], r['base_date']))})" if reasons.get((r["indicator"], r["center"], r["base_date"])) else "") for _, r in d_rows.iterrows()]
                    out.append({"kind": "note", "text": "※ 지난 제출값과 차이: " + ", ".join(lines)})
            elif diffs:
                out.append({"kind": "note", "text": "※ 차이 사유: " + ", ".join(f"{k[1]} {v}" for k, v in diffs)})
        if not shown: out.append({"kind": "missing", "text": "[확인 필요] 보유 자료 없음 — 별도 산출 필요"})
    if draft.get("산출근거"):
        out.append({"kind": "attach_title", "text": "붙임. 산출 근거"})
        out += [{"kind": "attach", "text": line.strip()} for line in str(draft["산출근거"]).splitlines() if line.strip()]
    return out

def build_reply(req: dict, items: list[dict], values: pd.DataFrame | None, draft: dict, reasons: dict | None = None, compare_df: pd.DataFrame | None = None,
                dept_head: str = "", phone: str = "", org: str = "지역교육협력부", today: dt.date | None = None, doc_label: str = "답변자료", show_confirm: bool = True) -> bytes:
    """답변자료 HWPX. 내용은 build_model이 정하고 여기서는 글꼴·정렬·표 치수만 입힌다."""
    model = build_model(req, items, values, draft, reasons, compare_df, dept_head, phone, org, today, doc_label, show_confirm)
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
    first = doc.paragraphs[0]
    for b in model:
        k = b["kind"]
        if k == "title":
            first.text = b["text"]
            for r in first.runs: r.char_pr_id_ref = bold16
            fmt(first, align="CENTER", after=6)
        elif k == "date": para(b["text"], body10, align="RIGHT", after=10)
        elif k == "subtitle": para(b["text"], bold12, after=6)
        elif k == "pre": para(b["text"], body10, after=2)
        elif k == "heading": para(b["text"], bold12, before=10, after=2)
        elif k == "confirm": para(b["text"], small9, align="RIGHT", after=4)
        elif k == "body": para(b["text"], body10, align="LEFT", indent=3, after=2)      # 양쪽 정렬은 긴 낱말에서 글자 사이가 벌어져 왼쪽 정렬
        elif k == "table":
            hdr, rows = b["hdr"], b["rows"]
            cap = para(b["caption"], bold10, before=4, after=2)
            if b["big"]: doc.styles.apply_paragraph_format(paragraphs=[cap], page_break_before=True)   # 긴 표는 새 쪽에서(별지처럼)
            pt = _fit_pt(hdr, rows); ws = _col_widths(hdr, rows, pt)
            per_page = max(1, 60000 // _line_h(pt) - 1)                            # 한 쪽에 들어가는 자료 행 수(본문 높이 약 60000 HWPUNIT ≈ 212mm 기준)
            n_chunks = -(-len(rows) // per_page); size = -(-len(rows) // n_chunks)   # 조각 수를 정한 뒤 고르게 나눈다(45+3 대신 24+24)
            for j in range(0, len(rows), size):                                     # 긴 표는 쪽 단위로 나눠 넣는다(머리글 반복, 같은 열 폭·글자 크기)
                _emit_table(doc, hdr, rows[j:j + size], border, pt, ws)
        elif k == "note": para(b["text"], note9, after=2)
        elif k == "missing": para(b["text"], note9, indent=3, after=2)
        elif k == "attach_title": para(b["text"], bold10, before=10, after=2)
        elif k == "attach": para(b["text"], note9, indent=3, after=1)
    return doc.to_bytes()

def preview_html(model: list[dict]) -> str:
    """같은 모형을 화면용 HTML로(종이 모양). 한글을 열지 않고 구조·표·근거를 확인하는 용도라 치수는 근사치다."""
    import html as _h
    e = _h.escape; parts = []
    for b in model:
        k = b["kind"]
        if k == "title": parts.append(f'<h1>{e(b["text"])}</h1>')
        elif k == "date": parts.append(f'<div class="dt">{e(b["text"])}</div>')
        elif k == "subtitle": parts.append(f'<h2>{e(b["text"])}</h2>')
        elif k == "pre": parts.append(f'<p>{e(b["text"])}</p>')
        elif k == "heading": parts.append(f'<h3>{e(b["text"])}</h3>')
        elif k == "confirm": parts.append(f'<div class="cf">{e(b["text"])}</div>')
        elif k == "body": parts.append(f'<p class="bd">{e(b["text"])}</p>')
        elif k == "table":
            rows = "".join("<tr>" + "".join(f'<td class="{"c0" if j == 0 else "n"}">{e(c)}</td>' for j, c in enumerate(r)) + "</tr>" for r in b["rows"])
            parts.append(f'<div class="cap">{e(b["caption"])}</div><table><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in b["hdr"]) + f"</tr></thead><tbody>{rows}</tbody></table>")
        elif k == "note": parts.append(f'<p class="nt">{e(b["text"])}</p>')
        elif k == "missing": parts.append(f'<p class="ms">{e(b["text"])}</p>')
        elif k == "attach_title": parts.append(f'<h4>{e(b["text"])}</h4>')
        elif k == "attach": parts.append(f'<p class="at">{e(b["text"])}</p>')
    css = ("<style>.paper{background:#fff;border:1px solid #D9DEE4;border-radius:6px;padding:34px 40px;max-width:820px;margin:0 auto;font-family:'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#111;font-size:13px;line-height:1.55}"
           ".paper h1{text-align:center;font-size:21px;margin:0 0 6px}.paper .dt{text-align:right;margin-bottom:14px}.paper h2{font-size:15px;margin:0 0 8px}.paper h3{font-size:15px;margin:16px 0 2px}"
           ".paper .cf{text-align:right;font-size:11px;color:#444;margin-bottom:6px}.paper p{margin:0 0 4px}.paper .bd{margin-left:12px}.paper .cap{font-weight:700;margin:8px 0 3px}"
           ".paper table{border-collapse:collapse;width:100%;margin:0 0 6px;font-size:12px}.paper th,.paper td{border:1px solid #333;padding:3px 6px}.paper th{background:#EEF2F7;text-align:center}.paper td.n{text-align:right}.paper td.c0{text-align:left}"
           ".paper .nt{font-size:12px;color:#222}.paper .ms{font-size:12px;color:#B42318;margin-left:12px}.paper h4{font-size:13px;margin:14px 0 4px}.paper .at{font-size:12px;margin-left:12px}</style>")
    return css + '<div class="paper">' + "".join(parts) + "</div>"
