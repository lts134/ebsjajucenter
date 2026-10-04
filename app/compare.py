"""수치 대조: 새 집계값 vs 과거 제출값. AI를 쓰지 않고 pandas로만 계산."""
import pandas as pd

KEY = ["indicator", "center", "base_date"]
REQUIRED = ["indicator", "center", "base_date", "value"]

def load_values(file) -> pd.DataFrame:
    """엑셀/CSV → 표준 컬럼. 한글 컬럼명도 허용. 출처(파일·시트·행)를 함께 기록."""
    name = str(getattr(file, "name", file))
    is_xl = name.lower().endswith((".xlsx", ".xls"))
    df = pd.read_excel(file) if is_xl else pd.read_csv(file)
    import os
    df["source_file"] = os.path.basename(name)
    df["source_sheet"] = "Sheet1" if is_xl else ""
    df["source_row"] = range(2, len(df) + 2)   # 엑셀 행 번호(헤더 다음부터)
    rename = {"지표": "indicator", "지표명": "indicator", "센터": "center", "센터명": "center", "기준일": "base_date",
              "값": "value", "수치": "value", "지표 정의": "definition", "정의": "definition", "집계기간": "calc_period",
              "추출시점": "extract_date", "추출일": "extract_date", "원자료 버전": "source_version", "버전": "source_version"}
    df = df.rename(columns={c: rename.get(str(c).strip(), str(c).strip()) for c in df.columns})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 없음: {missing} (현재 컬럼: {list(df.columns)})")
    df["base_date"] = pd.to_datetime(df["base_date"]).dt.strftime("%Y-%m-%d")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    for c in ["definition", "calc_period", "extract_date", "source_version"]:
        if c not in df.columns: df[c] = None
    return df

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
