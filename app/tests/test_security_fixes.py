"""보안 리뷰(2026-10-04) 반영: 엑셀 수식 주입 차단, 도구 결과·프롬프트 입력 마스킹."""
import io, pandas as pd, openpyxl, pii, history_qa

def test_excel_safe_neutralises_formula_leading_cells():
    df = pd.DataFrame({"항목": ['=HYPERLINK("http://x","click")', "+1+1", "-2", "정상", "@SUM(A1)"], "값": [1.5, -2.0, 3, 4, 5]})
    buf = io.BytesIO(); pii.excel_safe(df).to_excel(buf, index=False); buf.seek(0)
    ws = openpyxl.load_workbook(buf).active
    cells = [ws.cell(row=i, column=1) for i in range(2, 7)]
    assert all(c.data_type != "f" for c in cells)
    assert cells[0].value.startswith("'=") and cells[3].value == "정상" and cells[2].value == "-2"   # 음수 모양 글자는 그대로
    assert ws.cell(row=2, column=2).value == 1.5                                                    # 숫자 열은 손대지 않음

def test_redact_obj_recurses():
    out = pii.redact_obj({"a": ["문의 010-1234-5678", {"b": "x@y.kr"}], "n": 3})
    assert out == {"a": ["문의 [전화번호]", {"b": "[이메일]"}], "n": 3}

def test_tool_results_are_redacted(fresh_db):
    db = fresh_db
    rid = db.add_request("교육부 ○○과", "2026-09-19", "2026-09-19", "자료 요청", "담당 사무관 010-1234-5678, kim@moe.go.kr", "m.txt", [])
    d = history_qa.HANDLERS["request_detail"](request_id=rid)
    assert "010-1234-5678" not in d["raw_text"] and "[전화번호]" in d["raw_text"] and "[이메일]" in d["raw_text"]
