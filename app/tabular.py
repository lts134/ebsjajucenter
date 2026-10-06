"""표 → 제출값 변환. 엑셀 시트든 회신 문서(hwpx·docx·pdf) 안의 표든, 실무 서식 그대로 받아
'지표명·센터명·기준일·값' 긴 형식으로 만든다.

처리하는 서식:
- 제목 행("자기주도학습센터 개소 실적 (2026. 8. 30. 기준)"), 빈 행, 주석 행(※ …)을 건너뛴다. 제목의 날짜는 기준일 후보가 된다.
- 여러 줄 머리글(병합 셀)은 위 줄을 가로로 채운 뒤 아래 줄과 이어 붙인다: "개소" + "운영" → "개소 운영".
- 긴 형식(지표명·센터명·기준일·값 열이 있음)은 그대로 읽는다.
- 가로 펼침(행=센터·시군구, 열=지표)은 센터 열·값 열·기준일을 정해 한 줄 한 값으로 편다(melt). 합계·소계 행은 기본 제외.

원칙: 열 매핑은 규칙으로 추정하고(선택: Claude가 제안), 담당자가 화면에서 확정한다. 값을 계산하거나 고치지 않는다."""
from __future__ import annotations
import io, os, re
from dataclasses import dataclass, field
import pandas as pd
import compare
from compare import ALIASES, REQUIRED, clean_text

TOTAL_RE = re.compile(r"^(합\s*계|소\s*계|총\s*계|계|전\s*체|총\s*합|합|평\s*균)$")
CENTER_HDR_RE = re.compile(r"센터|시\s*·?\s*군\s*·?\s*구|기관|지역|구\s*분|지자체|시\s*·?\s*도|학교|운영기관|센터명")
DATE_RE = re.compile(r"'?\d{2,4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\.?")
NOTE_RE = re.compile(r"^(※|\*|주\s*[:)]|출처|자료\s*:)")
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
    if isinstance(v, str): return bool(re.fullmatch(r"[-+]?[\d,]*\.?\d+\s*%?|-", v.strip()))
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
    if name.lower().endswith((".xlsx", ".xls", ".xlsm")):
        xl = pd.ExcelFile(io.BytesIO(data))
        sheet_name = sheet if sheet is not None else xl.sheet_names[0]
        df = xl.parse(sheet_name, header=None)
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
    value_cols: list[int] = field(default_factory=list)
    total_rows: list[int] = field(default_factory=list)
    def col_label(self, j: int) -> str: return self.header[j] if j < len(self.header) and self.header[j] else f"열{j + 1}"

def _norm_header(h: str) -> str: return re.sub(r"\s+", " ", h).strip()

def _alias_hits(cells: list) -> int:
    return sum(1 for c in cells if _norm_header(text(c)) in ALIASES)

def _looks_like_header(cells: list) -> bool:
    """'시군구 | 2024 | 2025 | 2026'처럼 연도·월 숫자가 머리글에 있는 행: 첫 칸이 센터류 이름이고 숫자는 모두 연도(1900~2100) 또는 1~12."""
    nb = [c for c in cells if not is_blank(c)]
    if len(nb) < 2 or is_num(nb[0]) or not (CENTER_HDR_RE.search(text(nb[0])) or _norm_header(text(nb[0])) in ALIASES): return False
    for c in nb[1:]:
        if is_num(c):
            v = compare.clean_number(c)
            if not (v == int(v) and (1900 <= v <= 2100 or 1 <= v <= 12)): return False
    return True

def _row_kind(cells: list) -> str:
    nb = [c for c in cells if not is_blank(c)]
    if not nb: return "blank"
    if len(nb) == 1 and not is_num(nb[0]) and (len(cells) >= 3 or NOTE_RE.match(text(nb[0]))): return "single"
    if any(is_num(c) or is_date(c) for c in nb): return "data"
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
            header.append(" ".join(parts))
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
    cands = [j for j in cols if CENTER_HDR_RE.search(info.header[j] or "") and num_ratio(j) < 0.5]
    if not cands: cands = [j for j in cols if num_ratio(j) < 0.5 and any(not is_blank(c) for c in col_cells(j))]
    info.center_col = cands[0] if cands else None
    info.value_cols = [j for j in cols if j != info.center_col and num_ratio(j) >= 0.5]
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

def wide_to_long(grid: Grid, info: TableInfo, center_col: int, value_cols: list[int], base_date: str,
                 indicators: dict[int, str] | None = None, drop_totals: bool = True, source_version: str | None = None) -> pd.DataFrame:
    """가로 펼침 표 → 한 줄 한 값. indicators: 값 열 번호 → 지표명(없으면 default_indicator). 값은 그대로 옮기고 계산하지 않는다."""
    if center_col is None: raise ValueError("센터(행 이름) 열을 고르세요.")
    if not value_cols: raise ValueError("값 열을 하나 이상 고르세요.")
    if not base_date: raise ValueError("기준일을 입력하세요(예: 2026-08-30). 표 제목에 '… 기준'이 있으면 자동으로 채워집니다.")
    indicators = indicators or {}
    recs = []
    for r in info.data_rows:
        center = text(grid.cell(r, center_col))
        if not center: continue
        if drop_totals and TOTAL_RE.match(center): continue
        for j in value_cols:
            v = grid.cell(r, j)
            if is_blank(v): continue
            recs.append({"indicator": indicators.get(j) or default_indicator(info.col_label(j)), "center": center,
                         "base_date": base_date, "value": v, "source_row": grid.row_offset + r,
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
              "기준일은 표에 적힌 것만 YYYY-MM-DD로 쓰고, 없으면 null. 지표명은 가능하면 다음 중에서 고르고 없으면 머리글을 다듬어 씁니다: %s")

def suggest_mapping(grid: Grid, info: TableInfo) -> dict:
    """Claude에 표 구조(제목·머리글·앞 5행)를 보내 열 매핑을 제안받는다. 담당자가 화면에서 확정하기 전의 초안일 뿐이다."""
    import llm, pii, normalize
    head = [{"col": j, "header": info.col_label(j)} for j in range(grid.width())]
    sample = [[text(grid.cell(r, j)) for j in range(grid.width())] for r in info.data_rows[:5]]
    payload = {"title": info.title, "notes": info.notes[:3], "columns": head, "rows": sample}
    prompt = ("다음 표의 구조를 판단해 JSON으로 답하세요. center_col은 센터·시군구 같은 행 이름 열 번호, value_cols는 값 열 번호와 지표명, "
              "base_date는 제목·주석에 적힌 기준일.\n" + str(pii.redact_obj(payload)))
    res = llm.ask_json(prompt, MAP_SYSTEM % ", ".join(normalize.CANON), 800, purpose="표 열 매핑 제안", schema=MAP_SCHEMA)
    w = grid.width()
    out = {"center_col": res.get("center_col") if isinstance(res.get("center_col"), int) and 0 <= res.get("center_col") < w else None,
           "value_cols": [(v["col"], v["indicator"]) for v in res.get("value_cols") or []
                          if isinstance(v, dict) and isinstance(v.get("col"), int) and 0 <= v["col"] < w and v.get("indicator")],
           "base_date": None, "note": res.get("note")}
    if res.get("base_date"):
        from extract import _norm_date
        out["base_date"] = _norm_date(str(res["base_date"]))
    return out
