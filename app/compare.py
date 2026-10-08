"""수치 대조: 새 집계값 vs 과거 제출값. AI를 쓰지 않고 pandas로만 계산."""
import io, os, re
from pathlib import Path
import pandas as pd
from html.parser import HTMLParser

UNIT_SUFFIX = r"(?:명|개소|개|건|원|회|시간|일|점|천원|백만원|억원|％)"
KEY = ["indicator", "center", "base_date"]
REQUIRED = ["indicator", "center", "base_date", "value"]

ENCODINGS = ("utf-8-sig", "cp949", "utf-8", "euc-kr")   # 한국어 엑셀에서 내보낸 CSV는 cp949인 경우가 많다

def _bytes(file) -> bytes:
    if hasattr(file, "getvalue"): return file.getvalue()
    return Path(str(file)).read_bytes()

XLS_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

def sheet_kind(name: str, data: bytes) -> str:
    """파일 이름이 아니라 내용으로 종류를 판별한다. 'xlsx' | 'xls'(구형 바이너리) | 'html'(이름만 .xls인 HTML 표·웹 보관 파일(MHTML) — 사내 시스템 내려받기에 흔함) | 'text'(CSV·탭 구분)."""
    lower = name.lower()
    if data[:4] == b"PK\x03\x04": return "xlsx"
    if data[:8] == XLS_OLE_MAGIC: return "xls"
    try: head = decode_text(data[:20000]).lower()
    except ValueError: head = data[:20000].decode("latin-1").lower()
    if "urn:schemas-microsoft-com:office:spreadsheet" in head or "<workbook" in head: return "xmlss"
    if "<table" in head or "<html" in head or "<body" in head or "<tr" in head or "mime-version" in head or "content-type: multipart" in head: return "html"
    if lower.endswith((".xlsx", ".xlsm")): return "xlsx"
    return "text"                                   # 진짜 .xls는 항상 OLE 머리말이 있으므로, 없으면 텍스트(탭·쉼표 구분)로 본다

def decode_text(data: bytes) -> str:
    """UTF-16(BOM 또는 널 바이트가 많음) → utf-8-sig → cp949 → utf-8 → euc-kr 순으로 해독."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"): return data.decode("utf-16")
    if len(data) >= 64 and data[:400].count(b"\x00") > len(data[:400]) // 4:
        for enc in ("utf-16-le", "utf-16-be"):
            try: return data.decode(enc)
            except UnicodeDecodeError: pass
    last = None
    for enc in ENCODINGS:
        try: return data.decode(enc)
        except UnicodeDecodeError as e: last = e
    raise ValueError(f"글자 인코딩을 판별하지 못했습니다(시도: {', '.join(ENCODINGS)}). ({last})")

def _unwrap_mhtml(data: bytes) -> bytes | None:
    """웹 보관 파일(MHTML, 'Single File Web Page')이면 안의 text/html 부분들을 풀어 이어 붙인다(quoted-printable·base64 해제)."""
    head = data[:4000].lower()
    if b"mime-version" not in head and b"multipart/related" not in head and b"content-transfer-encoding" not in head: return None
    import email
    msg = email.message_from_bytes(data)
    parts = [p.get_payload(decode=True) for p in msg.walk() if p.get_content_type() == "text/html"]
    parts = [p for p in parts if p]
    return b"\n".join(parts) if parts else None

class _HtmlTables(HTMLParser):
    """표준 라이브러리만으로 <table>을 격자로. 깨진 HTML(닫히지 않은 태그, 머리말 없는 조각)도 읽고 rowspan·colspan을 펼친다.
    사내 시스템이 '.xls'로 내려 주는 HTML 표가 대상이라 외부 파서(lxml·html5lib)에 기대지 않는다."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list]] = []; self._stack: list[dict] = []
    def _cur(self): return self._stack[-1] if self._stack else None
    def handle_starttag(self, tag, attrs):
        t = tag.lower(); a = dict(attrs)
        if t == "table" or (t in ("tr", "td", "th") and not self._stack):              # <table> 없이 <tr>부터 오는 조각도 표로
            self._stack.append({"rows": [], "row": None, "cell": None, "span": {}})   # span: 열 번호 → (남은 행 수, 값)
            if t == "table": return
        cur = self._cur()
        if cur is None: return
        if t == "tr":
            self._end_row(cur); cur["row"] = []
        elif t in ("td", "th"):
            if cur["row"] is None: cur["row"] = []
            self._end_cell(cur)
            self._fill_span(cur)
            cur["cell"] = {"text": [], "colspan": int(a.get("colspan") or 1), "rowspan": int(a.get("rowspan") or 1)}
        elif t == "br" and cur["cell"] is not None: cur["cell"]["text"].append(" ")
    def handle_endtag(self, tag):
        t = tag.lower(); cur = self._cur()
        if cur is None: return
        if t in ("td", "th"): self._end_cell(cur)
        elif t == "tr": self._end_row(cur)
        elif t == "table":
            self._end_row(cur); self._stack.pop()
            if cur["rows"]: self.tables.append(cur["rows"])
    def handle_data(self, data):
        cur = self._cur()
        if cur is not None and cur["cell"] is not None: cur["cell"]["text"].append(data)
    def _fill_span(self, cur):
        while len(cur["row"]) in cur["span"]:                                        # 위 행에서 내려온 병합 셀 자리 채우기
            col = len(cur["row"]); left, val = cur["span"][col]; cur["row"].append(val)
            if left - 1 > 0: cur["span"][col] = (left - 1, val)
            else: del cur["span"][col]
    def _end_cell(self, cur):
        c = cur["cell"]
        if c is None: return
        val = re.sub(r"\s+", " ", "".join(c["text"])).strip() or None
        col0 = len(cur["row"])
        for k in range(c["colspan"]):
            cur["row"].append(val if k == 0 else None)                               # 가로 병합: 첫 칸만 값(머리글 합치기는 tabular가 처리)
            if c["rowspan"] > 1: cur["span"][col0 + k] = (c["rowspan"] - 1, val if k == 0 else None)
        cur["cell"] = None
    def _end_row(self, cur):
        self._end_cell(cur)
        if cur["row"] is not None:
            self._fill_span(cur)
            if any(v is not None for v in cur["row"]): cur["rows"].append(cur["row"])
            cur["row"] = None
    def close(self):
        super().close()
        while self._stack:                                                           # 닫히지 않은 <table>
            cur = self._stack.pop(); self._end_row(cur)
            if cur["rows"]: self.tables.append(cur["rows"])

SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"

def spreadsheetml_sheets(data: bytes) -> list[tuple[str, list[list]]]:
    """XML Spreadsheet 2003(SpreadsheetML, 사내 시스템이 '.xls'로 내려 주는 형식) → [(시트명, 격자)]. ss:Index 건너뜀·MergeAcross·MergeDown을 펼친다."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(data)
    q = lambda tag: f"{{{SS_NS}}}{tag}"
    out = []
    for ws in root.iter(q("Worksheet")):
        name = ws.get(q("Name")) or f"Sheet{len(out) + 1}"
        rows: list[list] = []; pending: dict[int, tuple[int, object]] = {}            # 세로 병합: 열 → (남은 행 수, 값)
        for tbl in ws.iter(q("Table")):
            r_i = 0
            for row in tbl.iter(q("Row")):
                idx = row.get(q("Index"))
                r_i = int(idx) if idx else r_i + 1
                while len(rows) < r_i - 1: rows.append([])                          # 비어 있는 행
                cells: list = []; c_i = 0
                for cell in row.findall(q("Cell")):
                    cidx = cell.get(q("Index"))
                    c_i = int(cidx) if cidx else c_i + 1
                    while len(cells) < c_i - 1:
                        col = len(cells)
                        if col in pending:
                            left, val = pending[col]; cells.append(val)
                            if left - 1 > 0: pending[col] = (left - 1, val)
                            else: pending.pop(col)                                  # 세로 병합이 끝나면 지운다(None을 다시 넣으면 다음 행에서 터진다)
                        else: cells.append(None)
                    d = cell.find(q("Data")); val = ("".join(d.itertext()).strip() if d is not None else None) or None
                    across = int(cell.get(q("MergeAcross")) or 0); down = int(cell.get(q("MergeDown")) or 0)
                    for k in range(across + 1):
                        cells.append(val if k == 0 else None)
                        if down > 0: pending[c_i - 1 + k] = (down, val if k == 0 else None)
                    c_i += across
                rows.append(cells)
        out.append((name, rows))
    return out

def html_tables(data: bytes) -> list[pd.DataFrame]:
    """HTML 안의 <table>을 전부 격자(머리글 해석 없음, 병합 셀 펼침)로. 숫자 글자는 그대로 두고 뒤 단계(clean_number)가 해석."""
    inner = _unwrap_mhtml(data)
    text = decode_text(inner if inner else data)
    parser = _HtmlTables(); parser.feed(text); parser.close()
    out = []
    for rows in parser.tables:
        w = max(len(r) for r in rows)
        out.append(pd.DataFrame([r + [None] * (w - len(r)) for r in rows]))
    if not out:
        head = re.sub(r"\s+", " ", text[:400]).strip()
        raise ValueError(f"HTML에서 표를 찾지 못했습니다. 파일 앞부분: 「{head}」 — 이 글을 개발자에게 전달하면 형식을 맞출 수 있습니다.")
    return out

def excel_file(data: bytes, kind: str) -> pd.ExcelFile:
    if kind == "xls":
        try: return pd.ExcelFile(io.BytesIO(data), engine="xlrd")
        except ImportError as e: raise ValueError("구형 엑셀(.xls)을 읽으려면 xlrd 패키지가 필요합니다: pip install xlrd") from e
    return pd.ExcelFile(io.BytesIO(data))

def list_sheets(file) -> list[str]:
    """시트 이름 목록. 엑셀은 시트명, HTML 표 파일은 '표 1…'(표가 둘 이상일 때), CSV·탭 구분은 빈 리스트."""
    name = str(getattr(file, "name", file)); data = _bytes(file)
    if not name.lower().endswith((".xlsx", ".xls", ".xlsm", ".csv", ".txt")): return []
    kind = sheet_kind(name, data)
    if kind in ("xlsx", "xls"): return excel_file(data, kind).sheet_names
    if kind == "xmlss":
        names = [n for n, _ in spreadsheetml_sheets(data)]; return names if len(names) > 1 else []
    if kind == "html":
        n = len(html_tables(data)); return [f"표 {i + 1}" for i in range(n)] if n > 1 else []
    return []

def _read_csv(data: bytes, header="infer") -> pd.DataFrame:
    last = None
    sep = "\t" if b"\t" in data[:4000] and data[:4000].count(b"\t") > data[:4000].count(b",") else ","   # 탭 구분 파일(이름만 .xls/.csv) 대응
    for enc in ENCODINGS:
        try: return pd.read_csv(io.BytesIO(data), encoding=enc, sep=sep, skip_blank_lines=False, header=header)   # 빈 줄도 행으로 읽어 엑셀 행 번호를 맞춘다
        except UnicodeDecodeError as e: last = e
    raise ValueError(f"CSV 인코딩을 판별하지 못했습니다(시도: {', '.join(ENCODINGS)}). 엑셀에서 'CSV UTF-8'로 다시 저장하세요. ({last})")

def normalize_date_str(v):
    """'2026. 6. 30.' / \"'26.6.30\" / '2026년 6월 30일' → '2026-06-30'(extract._norm_date 공유). 그 외는 그대로(pandas가 해석)."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or not isinstance(v, str): return v
    from extract import _norm_date
    return _norm_date(v) or v

def clean_text(v):
    """근거 필드(정의·집계기간·추출시점·원자료 버전): 엑셀 날짜 셀은 'YYYY-MM-DD'로, 문자열은 공백 정리. 비면 None.
    같은 날짜가 한 파일엔 글자로, 다른 파일엔 날짜 셀로 들어 있어도 '변경'으로 오판하지 않게 한다."""
    if v is None or (isinstance(v, float) and pd.isna(v)): return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d") if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.strftime("%Y-%m-%d %H:%M")
    s = re.sub(r"\s+", " ", str(v)).strip()
    s = re.sub(r"^(\d{4}-\d{2}-\d{2}) 00:00:00$", r"\1", s)
    return s or None

def clean_number(v):
    """'67.8%', '1,234', ' 60.3 ' → 숫자. 비어 있으면 NaN."""
    if v is None or (isinstance(v, float) and pd.isna(v)): return float("nan")
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[%,\s]", "", str(v))
    s = re.sub(UNIT_SUFFIX + r"$", "", s)                      # '120명', '3개소', '1,234천원' → 숫자만
    if s in ("", "-", "nan", "None", "NaN"): return float("nan")
    try: return float(s)
    except ValueError: return float("nan")

ALIASES = {"지표": "indicator", "지표명": "indicator", "센터": "center", "센터명": "center", "기준일": "base_date",
           "값": "value", "수치": "value", "지표 정의": "definition", "정의": "definition", "집계기간": "calc_period",
           "추출시점": "extract_date", "추출일": "extract_date", "원자료 버전": "source_version", "버전": "source_version"}

def load_values(file, sheet: str | int | None = None) -> pd.DataFrame:
    """엑셀/CSV → 표준 컬럼. 한글 컬럼명·cp949 CSV·'67.8%' 같은 값·제목 행·빈 행 허용. 출처(파일·시트·행)를 함께 기록.
    sheet: 엑셀 시트 이름/번호(None이면 첫 시트). 가로 펼침 표(행=센터, 열=지표)는 ValueError — 화면의 열 매핑(tabular.wide_to_long)으로 처리."""
    import tabular
    grid = tabular.read_grid(file, sheet)
    return tabular.long_table(grid, tabular.analyze(grid))

def finalize(df: pd.DataFrame, source_file: str = "", source_sheet: str = "") -> pd.DataFrame:
    """표준화·검증 공통부: 한글 컬럼명 → 표준명, 기준일 정규화, 값 숫자화, 근거 필드 정리, 키 중복 검사.
    df에는 source_row(원본 행 번호)가 있어야 오류 메시지에 행 번호가 붙는다."""
    df = df.dropna(how="all").copy()
    if "source_row" not in df.columns: df["source_row"] = [int(i) + 2 for i in df.index]
    df["source_file"] = os.path.basename(str(source_file)) if source_file else ""
    df["source_sheet"] = str(source_sheet or "")
    df = df.rename(columns={c: ALIASES.get(re.sub(r"\s+", " ", str(c)).strip(), str(c).strip()) for c in df.columns})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 없음: {missing} (현재 컬럼: {list(df.columns)}). 허용 컬럼명: {sorted(set(ALIASES))}")
    df = df[~(df["indicator"].isna() & df["center"].isna() & df["value"].isna())]       # 핵심 셀이 다 빈 행 제외
    for c in ("indicator", "center"):
        df[c] = df[c].map(lambda v: re.sub(r"\s+", " ", str(v)).strip() if v is not None and not (isinstance(v, float) and pd.isna(v)) else "")
    empty = df.loc[(df["indicator"] == "") | (df["center"] == ""), "source_row"].tolist()
    if empty:
        raise ValueError(f"지표명 또는 센터명이 빈 행: {empty[:10]}{' …' if len(empty) > 10 else ''}")
    parsed = pd.to_datetime(df["base_date"].map(normalize_date_str), errors="coerce")
    bad = df.loc[parsed.isna(), "source_row"].tolist()
    if bad:
        raise ValueError(f"기준일을 날짜로 읽지 못한 행: {bad[:10]}{' …' if len(bad) > 10 else ''} (예: 2026-06-30 또는 2026. 6. 30.)")
    df["base_date"] = parsed.dt.strftime("%Y-%m-%d")
    df["value"] = df["value"].map(clean_number)
    for c in ["definition", "calc_period", "extract_date", "source_version"]:
        if c not in df.columns: df[c] = None
        else: df[c] = df[c].map(clean_text)
    dup = df[df.duplicated(KEY, keep=False)]
    if len(dup):
        rows = sorted(int(r) for r in dup["source_row"].tolist())
        raise ValueError(f"같은 지표·센터·기준일이 중복된 행: {rows[:10]}{' …' if len(rows) > 10 else ''}. 한 기준일에 센터당 한 행만 두세요.")
    df["source_row"] = df["source_row"].astype(int)
    keep = REQUIRED + ["definition", "calc_period", "extract_date", "source_version", "source_file", "source_sheet", "source_row"]
    extra = [c for c in df.columns if c not in keep]                 # 메모 같은 추가 열은 뒤에 남긴다(개인정보 검사 대상)
    return df[keep + extra].reset_index(drop=True)

def compare(new_df: pd.DataFrame, old_df: pd.DataFrame, tol: float = 0.0) -> pd.DataFrame:
    """키(지표·센터·기준일)로 합쳐 차이 계산. 판정: 일치 / 차이 / 과거 없음 / 신규 없음"""
    for d in (new_df, old_df):
        for c in ["source_file", "source_sheet", "source_row"]:
            if c not in d.columns: d[c] = None
    EXTRA = ["value", "definition", "calc_period", "extract_date", "source_version", "source_file", "source_row"]
    import db as _db
    n = new_df[KEY + EXTRA].rename(columns={c: f"new_{c}" for c in EXTRA}); n["_ck"] = n["center"].map(_db.center_key)
    o = old_df[KEY + EXTRA].rename(columns={c: f"old_{c}" for c in EXTRA}); o["_ck"] = o["center"].map(_db.center_key)
    # 센터 이름은 공백·기호·EBS 접두를 무시한 키로 맞춘다('EBS 계룡 자기주도학습센터' = 'EBS계룡자기주도학습센터'). 표시 이름은 이번 값 쪽 표기.
    m = n.merge(o.rename(columns={"center": "_old_center"}), on=["indicator", "_ck", "base_date"], how="outer", indicator=True)
    m["center"] = m["center"].where(m["center"].notna(), m["_old_center"]); m = m.drop(columns=["_old_center", "_ck"])
    m["diff"] = m["new_value"] - m["old_value"]
    def judge(r):
        if r["_merge"] == "left_only": return "과거 제출값 없음"
        if r["_merge"] == "right_only": return "신규 집계값 없음"
        if pd.isna(r["diff"]): return "값 누락"
        return "차이" if abs(r["diff"]) > tol + 1e-9 else "일치"                 # 부동소수 오차(67.9-67.8 = 0.1000…0085)로 경계값이 '차이'가 되지 않게
    m["판정"] = m.apply(judge, axis=1)
    # 차이의 단서: 근거 필드가 다르면 표시
    def clue(r):
        if r["판정"] != "차이": return ""
        c = []
        for k, label in [("definition", "지표 정의"), ("calc_period", "집계기간"), ("extract_date", "추출시점"), ("source_version", "원자료 버전")]:
            a, b = r.get(f"old_{k}"), r.get(f"new_{k}")
            if pd.notna(a) and pd.notna(b) and str(a) != str(b): c.append(f"{label} 변경({a}→{b})")
        return "; ".join(c) if c else "근거 필드 동일 — 사유 입력 필요"
    m["단서"] = m.apply(clue, axis=1)
    order = {"차이": 0, "값 누락": 1, "과거 제출값 없음": 2, "신규 집계값 없음": 3, "일치": 4}
    m = m.sort_values(by=["판정", "center"], key=lambda s: s.map(order) if s.name == "판정" else s)
    return m.drop(columns=["_merge"]).reset_index(drop=True)

def checklist(m: pd.DataFrame, reasons: dict | None = None) -> pd.DataFrame:
    """제출 전 정합성 점검표(사람이 보는 형태). reasons: {(indicator,center,base_date): 사유}"""
    reasons = reasons or {}
    rows = []
    for _, r in m.iterrows():
        key = (r["indicator"], r["center"], r["base_date"])
        src = lambda f, row: f"{f} {int(row)}행" if pd.notna(f) and pd.notna(row) else ""
        rows.append({"지표": r["indicator"], "센터": r["center"], "기준일": r["base_date"],
                     "과거 제출값": r["old_value"], "신규 집계값": r["new_value"], "차이": r["diff"],
                     "판정": r["판정"], "단서": r["단서"], "차이 사유(담당자)": reasons.get(key, ""),
                     "과거 출처": src(r.get("old_source_file"), r.get("old_source_row")),
                     "신규 출처": src(r.get("new_source_file"), r.get("new_source_row"))})
    return pd.DataFrame(rows)
