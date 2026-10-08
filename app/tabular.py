"""표 → 제출값 변환. 엑셀 시트든 회신 문서(hwpx·docx·pdf) 안의 표든, 실무 서식 그대로 받아
'지표명·센터명·기준일·값' 긴 형식으로 만든다.

처리하는 서식:
- 제목 행("자기주도학습센터 개소 실적 (2026. 8. 30. 기준)"), 빈 행, 주석 행(※ …)을 건너뛴다. 제목의 날짜는 기준일 후보가 된다.
- 여러 줄 머리글(병합 셀)은 위 줄을 가로로 채운 뒤 아래 줄과 이어 붙인다: "개소" + "운영" → "개소 운영".
- 긴 형식(지표명·센터명·기준일·값 열이 있음)은 그대로 읽는다.
- 가로 펼침(행=센터·시군구, 열=지표)은 센터 열·값 열·기준일을 정해 한 줄 한 값으로 편다(melt). 합계·소계 행은 기본 제외.

원칙: 열 매핑은 규칙으로 추정하고(선택: Claude가 제안), 담당자가 화면에서 확정한다. 값을 계산하거나 고치지 않는다."""
from __future__ import annotations
import os, re
from dataclasses import dataclass, field
import pandas as pd
import compare
from compare import ALIASES, REQUIRED, clean_text

TOTAL_RE = re.compile(r"^(합\s*계|소\s*계|총\s*계|계|전\s*체|총\s*합|합|평\s*균)$")
CENTER_HDR_RE = re.compile(r"센터|시\s*·?\s*군\s*·?\s*구|기관|지역|구\s*분|지자체|시\s*·?\s*도|학교|운영기관|센터명")
DATE_RE = re.compile(r"'?\d{2,4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\.?")
NOTE_RE = re.compile(r"^(※|\*|주\s*[:)]|출처|자료\s*:)")
MONTH_RE = re.compile(r"(?<!\d)((?:19|20)\d{2}|\d{2})\s*[.\-/년]\s*(0?[1-9]|1[0-2])\s*월?\.?(?!\s*[.\-/]?\s*\d)")   # 2025.12 · 2025-12 · 2025년 12월 · 25.12 (일자가 이어지면 날짜이므로 제외)

def month_end(year: int, month: int) -> str:
    import calendar
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"

def month_in(text_: str) -> tuple[str, str] | None:
    """글 속의 월 표기 하나 → (월말 날짜, 월 표기를 뺀 나머지 글). 없으면 None."""
    m = MONTH_RE.search(text_ or "")
    if not m: return None
    y = int(m.group(1)); y = y + 2000 if y < 100 else y
    rest = re.sub(r"\s+", " ", (text_[:m.start()] + " " + text_[m.end():])).strip(" -·/")
    return month_end(y, int(m.group(2))), rest
UNIT_RE = re.compile(r"\(\s*(%|명|개|개소|원|천원|백만원|억원|건|회|시간|일)\s*\)")
MAX_HEADER_ROWS = 3

# ---------- 셀 판별 ----------
def is_blank(v) -> bool:
    if v is None: return True
    if isinstance(v, float) and pd.isna(v): return True
    if isinstance(v, str) and not v.strip(): return True
    return v is pd.NaT

def is_num(v) -> bool:
    """숫자로 볼 셀: 수치형, 또는 '67.8%' '1,234' '-' 같은 문자열. 날짜 셀·한글 문자열은 아님."""
    if isinstance(v, bool): return False
    if isinstance(v, (int, float)): return not pd.isna(v)
    if isinstance(v, str): return bool(re.fullmatch(r"[-+]?[\d,]*\.?\d+\s*(?:%|" + compare.UNIT_SUFFIX + r")?|-", v.strip()))
    return False

def is_date(v) -> bool:
    if hasattr(v, "strftime"): return True
    return isinstance(v, str) and bool(DATE_RE.fullmatch(v.strip())) or (isinstance(v, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip())))

def text(v) -> str:
    if is_blank(v): return ""
    if hasattr(v, "strftime"): return clean_text(v) or ""
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return re.sub(r"\s+", " ", str(v)).strip()

# ---------- 원본 격자 ----------
@dataclass
class Grid:
    rows: list[list]                 # 원본 셀(헤더 포함). rows[i]의 실제 행 번호 = row_offset + i
    source_file: str = ""
    source_sheet: str = ""           # 엑셀 시트명 또는 '표 1'
    row_offset: int = 1
    kind: str = "sheet"              # sheet | document
    def width(self) -> int: return max((len(r) for r in self.rows), default=0)
    def cell(self, i: int, j: int):
        r = self.rows[i]; return r[j] if j < len(r) else None

def read_grid(file, sheet: str | int | None = None) -> Grid:
    """엑셀/CSV를 머리글 해석 없이(header=None) 셀 격자로 읽는다. 빈 행도 유지해 행 번호가 맞게 한다."""
    name = str(getattr(file, "name", file))
    data = compare._bytes(file)
    kind = compare.sheet_kind(name, data) if name.lower().endswith((".xlsx", ".xls", ".xlsm", ".csv", ".txt")) else "text"
    if kind in ("xlsx", "xls"):
        xl = compare.excel_file(data, kind)
        sheet_name = sheet if sheet is not None else xl.sheet_names[0]
        df = xl.parse(sheet_name, header=None)
    elif kind == "xmlss":                                 # XML Spreadsheet 2003(사내 시스템 내려받기)
        sheets = compare.spreadsheetml_sheets(data)
        if not sheets: raise ValueError("XML 스프레드시트에 시트가 없습니다.")
        pick = next((sh for sh in sheets if sh[0] == sheet), sheets[sheet] if isinstance(sheet, int) and sheet < len(sheets) else sheets[0])
        w = max((len(r) for r in pick[1]), default=0)
        df, sheet_name = pd.DataFrame([r + [None] * (w - len(r)) for r in pick[1]]), (pick[0] if len(sheets) > 1 else "")
    elif kind == "html":                                  # 이름만 .xls인 HTML 표(사내 시스템 내려받기)
        tables = compare.html_tables(data)
        idx = (int(str(sheet).split()[-1]) - 1) if isinstance(sheet, str) and sheet.startswith("표 ") else (sheet if isinstance(sheet, int) else 0)
        if not tables: raise ValueError("파일 안에 표가 없습니다.")
        df, sheet_name = tables[idx], (f"표 {idx + 1}" if len(tables) > 1 else "")
    else:
        df, sheet_name = compare._read_csv(data, header=None), ""
    rows = [[None if is_blank(v) else v for v in r] for r in df.itertuples(index=False, name=None)]
    return Grid(rows=rows, source_file=os.path.basename(name), source_sheet=str(sheet_name), row_offset=1, kind="sheet")

def _split_line(line: str) -> list[str] | None:
    """문서 줄 → 셀. '셀 | 셀'(hwpx·docx 표) 우선, 아니면 탭 또는 공백 2칸 이상(pdf 텍스트)으로 3칸 이상 갈라지면 표로 본다."""
    if " | " in line: return [c.strip() for c in line.split(" | ")]
    if "\t" in line:
        cells = [c.strip() for c in line.split("\t")]
        return cells if len(cells) >= 2 else None
    cells = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
    return cells if len(cells) >= 3 else None

def grids_from_text(doc_text: str, source_file: str = "") -> list[Grid]:
    """회신 문서 본문(docread.read 결과)에서 표를 찾는다. 연속한 표 줄(열 수 비슷)을 한 표로 묶는다. 2행 미만은 버린다."""
    lines = doc_text.splitlines()
    grids, cur, start = [], [], 0
    def flush():
        if len(cur) >= 2:
            w = max(len(r) for r in cur)
            grids.append(Grid(rows=[r + [None] * (w - len(r)) for r in cur], source_file=source_file,
                              source_sheet=f"표 {len(grids) + 1}", row_offset=start + 1, kind="document"))
        cur.clear()
    for i, line in enumerate(lines):
        cells = _split_line(line) if line.strip() else None
        if cells and (not cur or abs(len(cells) - len(cur[-1])) <= 1):
            if not cur: start = i
            cur.append([c if c else None for c in cells])
        else:
            flush()
            if cells: start = i; cur.append([c if c else None for c in cells])
    flush()
    return grids

def grids_from_document(name: str, data: bytes) -> tuple[list[Grid], str]:
    """회신 문서 → 표 격자 목록과 본문 텍스트. hwpx·docx는 표 개체(셀 주소·병합 반영)에서 직접 꺼내고,
    표 개체가 없거나 pdf·txt면 본문 텍스트에서 표 모양 줄을 찾는다. 2행·2열 미만 표는 버린다."""
    import docread
    if name.lower().endswith(".hwp"):                      # 구형식은 한 번만 변환해서 본문·표 모두 꺼낸다
        data, name = docread.hwp_to_hwpx(data), name[:-4] + ".hwpx"
    text = docread.read(name, data)
    tables = docread.tables(name, data)
    grids = []
    if tables:
        for t in tables:
            w = max((len(r) for r in t), default=0)
            rows = [[(c if not is_blank(c) else None) for c in r] + [None] * (w - len(r)) for r in t]
            if len(rows) >= 2 and w >= 2 and any(not is_blank(c) for r in rows for c in r):
                grids.append(Grid(rows=rows, source_file=os.path.basename(name), source_sheet=f"표 {len(grids) + 1}", row_offset=1, kind="document"))
    if not grids:
        grids = grids_from_text(text, os.path.basename(name))
    return grids, text

def numeric_cells(grid: Grid) -> int:
    """격자 안 숫자 셀 수 — 여러 표 중 값 표(기본 선택)를 고르는 데 쓴다."""
    return sum(is_num(c) for r in grid.rows for c in r)

# ---------- 표 구조 분석 ----------
@dataclass
class TableInfo:
    title: str = ""
    notes: list[str] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    data_rows: list[int] = field(default_factory=list)
    base_date_hint: str | None = None
    shape: str = "empty"             # long | wide | empty
    center_col: int | None = None
    center_cols: list[int] = field(default_factory=list)      # 둘 이상이면 이어 붙여 센터명(예: 시·도 + 시·군·구, 연도 + 세부항목)
    value_cols: list[int] = field(default_factory=list)
    total_rows: list[int] = field(default_factory=list)
    col_dates: dict[int, str] = field(default_factory=dict)     # 머리글에 월이 있는 열: 열 번호 → 월말 기준일('2025.12 진행' → 2025-12-31)
    def col_label(self, j: int) -> str: return self.header[j] if j < len(self.header) and self.header[j] else f"열{j + 1}"
    def col_indicator(self, j: int) -> str:
        """지표명 기본값용 머리글: 월 표기를 뺀 나머지('2025.12 진행' → '진행')."""
        m = month_in(self.col_label(j)) if j in self.col_dates else None
        return (m[1] if m and m[1] else self.col_label(j))

def _norm_header(h: str) -> str: return re.sub(r"\s+", " ", h).strip()

def _alias_hits(cells: list) -> int:
    """긴 형식 머리글 판정용: 서로 다른 표준 열(지표·센터·기준일·값…)로 매핑되는 셀 수. '지표 | 지표'처럼 병합 반복은 1로 센다."""
    return len({ALIASES[_norm_header(text(c))] for c in cells if _norm_header(text(c)) in ALIASES})

def _looks_like_header(cells: list) -> bool:
    """'시군구 | 2024 | 2025 | 2026'처럼 연도·월 숫자가 머리글에 있는 행: 첫 칸이 센터류 이름이고 숫자는 모두 연도(1900~2100) 또는 1~12."""
    nb = [c for c in cells if not is_blank(c)]
    if len(nb) < 2 or is_num(nb[0]) or not (CENTER_HDR_RE.search(text(nb[0])) or _norm_header(text(nb[0])) in ALIASES): return False
    for c in nb[1:]:
        if is_num(c):
            v = compare.clean_number(c)
            if not (v == int(v) and (1900 <= v <= 2100 or 1 <= v <= 12)): return False
    return True

def _is_month(c) -> bool: return isinstance(c, str) and MONTH_RE.fullmatch(c.strip()) is not None

def _row_kind(cells: list) -> str:
    nb = [c for c in cells if not is_blank(c)]
    if not nb: return "blank"
    if len(nb) == 1 and not is_num(nb[0]) and (len(cells) >= 3 or NOTE_RE.match(text(nb[0]))): return "single"
    if any((is_num(c) and not _is_month(c)) or is_date(c) for c in nb): return "data"
    return "text"

def analyze(grid: Grid) -> TableInfo:
    info = TableInfo()
    rows = grid.rows; n = len(rows)
    kinds = [_row_kind(r) for r in rows]
    i = 0
    # 1) 제목·빈 행: 맨 위의 한 칸짜리 행들
    while i < n and kinds[i] in ("blank", "single"):
        if kinds[i] == "single":
            t = text(next(c for c in rows[i] if not is_blank(c)))
            if NOTE_RE.match(t): info.notes.append(t)
            else: info.title = (info.title + " " + t).strip()
        i += 1
    if i >= n: return info
    # 2) 머리글: 별칭이 3개 이상 들어 있는 행이 있으면 그 행(긴 형식), 아니면 첫 자료 행 전까지(최대 3줄)
    alias_row = next((k for k in range(i, min(n, i + 10)) if _alias_hits(rows[k]) >= 3), None)
    if alias_row is not None:
        info.header_rows = [alias_row]
    else:
        k = i
        while k < n and len(info.header_rows) < MAX_HEADER_ROWS and (kinds[k] == "text" or (not info.header_rows and _looks_like_header(rows[k]))):
            info.header_rows.append(k); k += 1
    w = grid.width()
    if info.header_rows:
        filled = []
        for idx, r in enumerate(info.header_rows):
            cells = [text(grid.cell(r, j)) for j in range(w)]
            if idx < len(info.header_rows) - 1:                 # 위 줄은 병합 셀을 가로로 채운다
                for j in range(1, w):
                    if not cells[j]: cells[j] = cells[j - 1]
            filled.append(cells)
        header = []
        for j in range(w):
            parts = []
            for cells in filled:
                if cells[j] and cells[j] not in parts: parts.append(cells[j])
            h = " ".join(parts)
            header.append(h if len(h) <= 60 else h[:58] + "…")          # 머리글 칸에 긴 주석이 들어간 경우 표시용으로 자른다
        info.header = header
        start = info.header_rows[-1] + 1
    else:
        info.header = [f"열{j + 1}" for j in range(w)]; start = i
    # 3) 자료 행: 머리글 다음부터. 빈 행·한 칸 주석 행은 건너뛴다
    for k in range(start, n):
        if kinds[k] == "blank": continue
        if kinds[k] == "single":
            t = text(next(c for c in rows[k] if not is_blank(c)))
            info.notes.append(t); continue
        info.data_rows.append(k)
    if not info.data_rows: return info
    # 4) 기준일 후보: 제목 → 머리글 → 주석에서 날짜('기준' 가까이 있는 것 우선)
    from extract import _norm_date
    for src in ([info.title] + info.header + info.notes):
        if not src: continue
        ms = list(DATE_RE.finditer(src))
        if not ms: continue
        pref = [m for m in ms if "기준" in src[m.end():m.end() + 6] or "기준" in src[max(0, m.start() - 6):m.start()]]
        d = _norm_date((pref or ms)[0].group(0))
        if d: info.base_date_hint = d; break
    for j, h in enumerate(info.header):                        # 열 머리글의 월 → 열별 기준일(월별 펼침 표)
        mi = month_in(h) if h and not h.startswith("열") else None
        if mi: info.col_dates[j] = mi[0]
    if not info.base_date_hint and info.title:                  # 제목의 '2025.12 ~ 2026.09' 같은 기간은 끝 월을 기준일 후보로
        months = [m for m in MONTH_RE.finditer(info.title)]
        if months:
            y = int(months[-1].group(1)); y = y + 2000 if y < 100 else y
            info.base_date_hint = month_end(y, int(months[-1].group(2)))
    # 5) 모양 판정
    mapped = {_norm_header(h): ALIASES.get(_norm_header(h)) for h in info.header}
    if all(req in mapped.values() for req in REQUIRED):
        info.shape = "long"; return info
    info.shape = "wide"
    cols = list(range(w))
    def col_cells(j): return [grid.cell(r, j) for r in info.data_rows]
    def num_ratio(j):
        cs = [c for c in col_cells(j) if not is_blank(c)]
        return (sum(is_num(c) for c in cs) / len(cs)) if cs else 0.0
    def nonblank(j): return any(not is_blank(c) for c in col_cells(j))
    def year_like(j):                                           # 연도 열(1900~2100 정수만): 숫자지만 행 이름의 일부로 쓸 수 있다
        vs = [compare.clean_number(c) if is_num(c) else float("nan") for c in col_cells(j) if not is_blank(c)]
        return bool(vs) and all(v == v and v == int(v) and 1900 <= v <= 2100 for v in vs)
    label_cols = [j for j in cols if nonblank(j) and (num_ratio(j) < 0.5 or year_like(j))]
    cands = [j for j in label_cols if CENTER_HDR_RE.search(info.header[j] or "") and not year_like(j)] or [j for j in label_cols if not year_like(j)] or label_cols
    def labels(js): return [" ".join(t for t in (text(grid.cell(r, j)) for j in js) if t) for r in info.data_rows]
    def distinct(j): return len({t for t in labels([j]) if t and not TOTAL_RE.match(t)})
    def has_dup(js):
        seen = [t for t in labels(js) if t and not TOTAL_RE.match(t)]
        return len(seen) != len(set(seen))
    # 후보가 여럿이면 고유값이 가장 많은 열(시·도보다 시·군·구). 그래도 행 이름이 겹치면(여러 시·도에 같은 구 이름, 연도별 같은 항목)
    # 다른 이름 열을 왼쪽부터 이어 붙여 겹치지 않을 때까지 늘린다
    base_rows = [r for r in info.data_rows if not any(TOTAL_RE.match(text(grid.cell(r, j))) for j in label_cols)]   # 합계 행은 빼고 채움 비율 계산
    def fill(j): return sum(1 for r in base_rows if not is_blank(grid.cell(r, j))) / max(1, len(base_rows))
    full = [j for j in cands if fill(j) >= 0.8] or cands                     # 비고처럼 드문드문 적힌 열은 행 이름이 아니다
    info.center_col = max(full, key=distinct) if full else None             # 고유값이 같으면 왼쪽 열
    if info.center_col is not None:
        chosen = [info.center_col]
        for j in label_cols:
            if not has_dup(sorted(chosen)): break
            if j not in chosen: chosen.append(j)
        info.center_cols = sorted(chosen)
    info.value_cols = [j for j in cols if j not in info.center_cols and num_ratio(j) >= 0.5]
    if info.center_col is not None:
        info.total_rows = [r for r in info.data_rows if TOTAL_RE.match(text(grid.cell(r, info.center_col)))]
    return info

def preview(grid: Grid, info: TableInfo, limit: int = 8) -> pd.DataFrame:
    """화면 확인용: 머리글을 붙인 자료 행 앞부분."""
    rows = [[text(grid.cell(r, j)) for j in range(grid.width())] for r in info.data_rows[:limit]]
    cols = [info.col_label(j) for j in range(grid.width())]
    return pd.DataFrame(rows, columns=_unique(cols))

def _unique(cols: list[str]) -> list[str]:
    seen, out = {}, []
    for c in cols:
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c}({seen[c]})")
    return out

def default_indicator(header: str) -> str:
    """값 열 머리글 → 지표명 기본값. 동의어 사전에 있으면 정규 지표명, 아니면 단위 괄호를 뗀 머리글."""
    import normalize
    canon, _ = normalize.normalize(header)
    return canon or UNIT_RE.sub("", header).strip() or header

# ---------- 긴 형식으로 ----------
def long_table(grid: Grid, info: TableInfo) -> pd.DataFrame:
    """긴 형식 표(별칭 머리글)를 표준 제출값 표로."""
    if info.shape != "long":
        missing = [r for r in REQUIRED if r not in {ALIASES.get(_norm_header(h)) for h in info.header}]
        raise ValueError(f"필수 컬럼 없음: {missing} (현재 컬럼: {info.header}). 허용 컬럼명: {sorted(set(ALIASES))}. "
                         "행=센터·열=지표인 가로 펼침 표라면 화면의 '열 매핑'에서 센터 열·값 열·기준일을 지정하세요.")
    cols = _unique([_norm_header(h) for h in info.header])
    df = pd.DataFrame([[grid.cell(r, j) for j in range(grid.width())] for r in info.data_rows], columns=cols)
    df["source_row"] = [grid.row_offset + r for r in info.data_rows]
    return compare.finalize(df, grid.source_file, grid.source_sheet)

def wide_to_long(grid: Grid, info: TableInfo, center_col, value_cols: list[int], base_date: str,
                 indicators: dict[int, str] | None = None, drop_totals: bool = True, source_version: str | None = None,
                 col_dates: dict[int, str] | None = None) -> pd.DataFrame:
    """가로 펼침 표 → 한 줄 한 값. center_col: 열 번호 하나 또는 여러 개(이어 붙여 센터명). indicators: 값 열 번호 → 지표명(없으면 default_indicator).
    col_dates: 열 번호 → 기준일(월별 펼침 표. 없는 열은 base_date). 값은 그대로 옮기고 계산하지 않는다."""
    ccols = [c for c in (center_col if isinstance(center_col, (list, tuple)) else [center_col]) if c is not None]
    if not ccols: raise ValueError("센터(행 이름) 열을 고르세요.")
    if not value_cols: raise ValueError("값 열을 하나 이상 고르세요.")
    col_dates = dict(col_dates if col_dates is not None else info.col_dates)
    if not base_date and any(j not in col_dates for j in value_cols):
        raise ValueError("기준일을 입력하세요(예: 2026-08-30). 표 제목에 '… 기준'이 있으면 자동으로 채워집니다." +
                         (" 월이 적힌 열은 그 월말이 기준일이 되고, 월이 없는 열(예: 전체 합계)에만 이 기준일이 쓰입니다." if col_dates else ""))
    indicators = indicators or {}
    names = {j: (indicators.get(j) or default_indicator(info.col_indicator(j))) for j in value_cols}
    seen: dict[tuple, int] = {}
    for j in value_cols:                                     # 같은 지표명·같은 기준일인 값 열이 둘 이상이면(예: 사업별 '편성액' 반복) 열 번호를 붙여 구분
        k_ = (names[j], col_dates.get(j, base_date)); seen[k_] = seen.get(k_, 0) + 1
    dup = {k_ for k_, n in seen.items() if n > 1}
    for j in value_cols:
        if (names[j], col_dates.get(j, base_date)) in dup: names[j] = f"{names[j]} ({j + 1}열)"
    recs = []
    for r in info.data_rows:
        parts = [text(grid.cell(r, c)) for c in ccols]
        if drop_totals and any(TOTAL_RE.match(t) for t in parts if t): continue
        center = " ".join(t for t in parts if t)
        if not center: continue
        for j in value_cols:
            v = grid.cell(r, j)
            if is_blank(v): continue
            recs.append({"indicator": names[j], "center": center,
                         "base_date": col_dates.get(j, base_date), "value": v, "source_row": grid.row_offset + r,
                         "source_version": source_version})
    if not recs: raise ValueError("변환된 값이 없습니다. 센터 열·값 열 선택을 확인하세요.")
    return compare.finalize(pd.DataFrame(recs), grid.source_file, grid.source_sheet)

MANUAL_COLS = ["지표명", "센터명", "기준일", "값", "지표 정의", "집계기간", "추출시점", "원자료 버전"]

def from_records(df: pd.DataFrame, source_file: str = "직접 입력") -> pd.DataFrame:
    """화면에서 직접 입력한 표(한글 컬럼) → 표준 제출값 표. 빈 행은 건너뛴다."""
    d = df.copy()
    d = d.dropna(how="all")
    d = d[~d.apply(lambda r: all(is_blank(v) for v in r), axis=1)]
    if d.empty: raise ValueError("입력된 행이 없습니다.")
    d["source_row"] = [int(i) + 1 for i in d.index]
    return compare.finalize(d, source_file, "")

# ---------- Claude 열 매핑 제안(선택) ----------
MAP_SCHEMA = {"type": "object", "additionalProperties": False,
              "properties": {"center_col": {"type": ["integer", "null"]},
                             "value_cols": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                        "properties": {"col": {"type": "integer"}, "indicator": {"type": "string"}},
                                                                        "required": ["col", "indicator"]}},
                             "base_date": {"type": ["string", "null"]}, "note": {"type": ["string", "null"]}},
              "required": ["center_col", "value_cols", "base_date", "note"]}
MAP_SYSTEM = ("당신은 공공기관 실적 표의 구조를 읽는 보조자입니다. 표의 제목·머리글·앞부분 행을 보고 어느 열이 센터(행 이름)이고 어느 열이 값인지, "
              "값 열의 지표명은 무엇이 적절한지, 기준일은 언제인지 JSON으로만 답합니다. 값을 계산하거나 추정하지 않습니다. "
              "기준일은 표에 적힌 것만 YYYY-MM-DD로 쓰고, 없으면 null. 머리글에 월(2025.12, '26.1)이 적힌 열은 열마다 그 월말이 기준일이므로 base_date에 쓰지 말고 note에 '월별 열'이라 적습니다. "
              "센터 이름이 교육청·센터처럼 여러 열로 나뉘면 가장 구체적인 열(센터)을 center_col로 고릅니다. 합계·소계 행은 값 열 판단에서 뺍니다. "
              "지표명은 가능하면 다음 중에서 고르고 없으면 머리글을 다듬어 씁니다: %s")

def suggest_mapping(grid: Grid, info: TableInfo) -> dict:
    """Claude에 표 구조(제목·머리글·앞 5행)를 보내 열 매핑을 제안받는다. 담당자가 화면에서 확정하기 전의 초안일 뿐이다."""
    import llm, pii, normalize
    head = [{"col": j, "header": info.col_label(j)} for j in range(grid.width())]
    sample = [[text(grid.cell(r, j)) for j in range(grid.width())] for r in info.data_rows[:5]]
    payload = {"title": info.title, "notes": info.notes[:3], "columns": head, "rows": sample}
    prompt = ("다음 표의 구조를 판단해 JSON으로 답하세요. center_col은 센터·시군구 같은 행 이름 열 번호, value_cols는 값 열 번호와 지표명, "
              "base_date는 제목·주석에 적힌 기준일.\n" + str(pii.redact_obj(payload)))
    res = llm.ask_json(prompt, MAP_SYSTEM % ", ".join(normalize.CANON), 4000, purpose="표 열 매핑 제안", schema=MAP_SCHEMA)
    w = grid.width()
    out = {"center_col": res.get("center_col") if isinstance(res.get("center_col"), int) and 0 <= res.get("center_col") < w else None,
           "value_cols": [(v["col"], v["indicator"]) for v in res.get("value_cols") or []
                          if isinstance(v, dict) and isinstance(v.get("col"), int) and 0 <= v["col"] < w and v.get("indicator")],
           "base_date": None, "note": res.get("note")}
    if res.get("base_date"):
        from extract import _norm_date
        out["base_date"] = _norm_date(str(res["base_date"]))
    return out
