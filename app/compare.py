"""수치 대조: 새 집계값 vs 과거 제출값. AI를 쓰지 않고 pandas로만 계산."""
import io, os, re
from pathlib import Path
import pandas as pd

KEY = ["indicator", "center", "base_date"]
REQUIRED = ["indicator", "center", "base_date", "value"]

ENCODINGS = ("utf-8-sig", "cp949", "utf-8", "euc-kr")   # 한국어 엑셀에서 내보낸 CSV는 cp949인 경우가 많다

def _bytes(file) -> bytes:
    if hasattr(file, "getvalue"): return file.getvalue()
    return Path(str(file)).read_bytes()

def list_sheets(file) -> list[str]:
    """엑셀 시트 이름 목록(CSV면 빈 리스트)."""
    name = str(getattr(file, "name", file))
    if not name.lower().endswith((".xlsx", ".xls", ".xlsm")): return []
    return pd.ExcelFile(io.BytesIO(_bytes(file))).sheet_names

def _read_csv(data: bytes, header="infer") -> pd.DataFrame:
    last = None
    for enc in ENCODINGS:
        try: return pd.read_csv(io.BytesIO(data), encoding=enc, skip_blank_lines=False, header=header)   # 빈 줄도 행으로 읽어 엑셀 행 번호를 맞춘다
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
    n = new_df[KEY + EXTRA].rename(columns={c: f"new_{c}" for c in EXTRA})
    o = old_df[KEY + EXTRA].rename(columns={c: f"old_{c}" for c in EXTRA})
    m = n.merge(o, on=KEY, how="outer", indicator=True)
    m["diff"] = m["new_value"] - m["old_value"]
    def judge(r):
        if r["_merge"] == "left_only": return "과거 제출값 없음"
        if r["_merge"] == "right_only": return "신규 집계값 없음"
        if pd.isna(r["diff"]): return "값 누락"
        return "차이" if abs(r["diff"]) > tol else "일치"
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
