import pandas as pd, pytest, compare
from pathlib import Path
S = Path(__file__).resolve().parents[1] / "sample_data"

class F:
    def __init__(self, name, data): self.name, self._d = name, data
    def getvalue(self): return self._d

def test_load_sample_excel():
    df = compare.load_values(S / "등원율_2026-06-30기준_7월제출본.xlsx")
    assert len(df) == 12 and set(compare.REQUIRED) <= set(df.columns)
    assert df["source_row"].tolist() == list(range(2, 14)) and df["source_sheet"][0] == "Sheet1"

def test_load_cp949_csv_with_korean_dates_percent_and_blank_rows():
    csv = "지표명,센터명,기준일,값\n등원율,센터A,2026. 6. 30.,67.8%\n,,,\n등원율,센터B,'26.6.30,\"1,234\"\n".encode("cp949")
    df = compare.load_values(F("x.csv", csv))
    assert df[["center", "base_date", "value", "source_row"]].to_dict("records") == [
        {"center": "센터A", "base_date": "2026-06-30", "value": 67.8, "source_row": 2},
        {"center": "센터B", "base_date": "2026-06-30", "value": 1234.0, "source_row": 4}]

def test_load_utf8_bom_csv():
    csv = "﻿지표,센터,기준일,수치\n등원율,센터A,2026-06-30,60.3\n".encode("utf-8")
    assert compare.load_values(F("y.csv", csv))["value"][0] == 60.3

def test_bad_date_and_missing_columns_raise_clear_errors():
    with pytest.raises(ValueError, match="기준일을 날짜로"):
        compare.load_values(F("z.csv", "지표명,센터명,기준일,값\n등원율,센터A,기준없음,1\n".encode()))
    with pytest.raises(ValueError, match="필수 컬럼 없음"):
        compare.load_values(F("w.csv", "지표명,센터명,값\n등원율,센터A,1\n".encode()))

def test_compare_judgements_and_clues():
    old = compare.load_values(S / "등원율_2026-06-30기준_7월제출본.xlsx")
    new = compare.load_values(S / "등원율_2026-06-30기준_9월재산출.xlsx")
    m = compare.compare(new, old, 0.0)
    assert (m["판정"] == "차이").sum() == 3 and (m["판정"] == "일치").sum() == 9
    diff = m[m["판정"] == "차이"]
    assert set(diff["center"]) == {"센터C", "센터G", "센터K"}
    assert all("추출시점 변경" in c and "원자료 버전 변경" in c for c in diff["단서"])
    assert m.iloc[0]["판정"] == "차이"   # 차이가 먼저 정렬
    # 허용 오차를 크게 주면 전부 일치
    assert (compare.compare(new, old, 10.0)["판정"] == "일치").all()

def test_compare_missing_sides():
    old = pd.DataFrame({"indicator": ["등원율"], "center": ["센터A"], "base_date": ["2026-06-30"], "value": [1.0]})
    new = pd.DataFrame({"indicator": ["등원율"], "center": ["센터B"], "base_date": ["2026-06-30"], "value": [2.0]})
    for d in (old, new):
        for c in ("definition", "calc_period", "extract_date", "source_version"): d[c] = None
    m = compare.compare(new, old)
    assert set(m["판정"]) == {"과거 제출값 없음", "신규 집계값 없음"}

def test_checklist_includes_reasons_and_sources():
    old = compare.load_values(S / "등원율_2026-06-30기준_7월제출본.xlsx")
    new = compare.load_values(S / "등원율_2026-06-30기준_9월재산출.xlsx")
    m = compare.compare(new, old)
    ck = compare.checklist(m, {("등원율", "센터C", "2026-06-30"): "사후 보정"})
    row = ck[ck["센터"] == "센터C"].iloc[0]
    assert row["차이 사유(담당자)"] == "사후 보정" and row["과거 출처"].endswith("행") and "7월제출본" in row["과거 출처"]
