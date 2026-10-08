import io
import pandas as pd
import hwpx_build, docread, tabular

def test_split_body_by_item_numbers():
    pre, by = hwpx_build.split_body("안녕하십니까.\n1. 등원율은 표와 같습니다.\n  센터C는 보정.\n2) 학생 수는 [확인 필요]\n3. 범위 밖 번호", 2)
    assert pre == ["안녕하십니까."] and by[1] == ["등원율은 표와 같습니다.", "센터C는 보정."] and by[2] == ["학생 수는 [확인 필요]", "범위 밖 번호"]   # 항목 수를 넘는 번호는 번호만 뗀다
    pre2, by2 = hwpx_build.split_body("1. 귀 기관의 요구에 따라 회신합니다.\n2. 수치는 붙임 표와 같습니다.\n3. 0%는 [확인 필요]합니다.\n붙임: 표 1부. 끝.", 1)
    assert pre2 == [] and by2[1] == ["귀 기관의 요구에 따라 회신합니다.", "수치는 붙임 표와 같습니다.", "0%는 [확인 필요]합니다."]           # 문단 번호 2.·3.은 떼고 '붙임…끝.'은 뺀다(양식이 붙임을 따로 넣음)

def test_table_pivot_and_single_date():
    df = pd.DataFrame([{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.3}, {"indicator": "등원율", "center": "센터A", "base_date": "2026-07-31", "value": 61.0},
                       {"indicator": "등원율", "center": "센터B", "base_date": "2026-06-30", "value": 59.4}])
    hdr, rows = hwpx_build._table_for(df)
    assert hdr == ["구분", "'26.6", "'26.7"] and rows == [["센터A", "60.3", "61.0"], ["센터B", "59.4", "-"]]      # 기준일이 여럿이면 머리글은 짧은 날짜(월말 → '연.월)
    assert hwpx_build._short_date("2026-09-15") == "'26.9.15" and hwpx_build._short_date("2025-12-31") == "'25.12"
    hdr1, rows1 = hwpx_build._table_for(df[df["base_date"] == "2026-06-30"]); assert hdr1 == ["구분", "값(2026-06-30 기준)"] and rows1[0] == ["센터A", "60.3"]
    ints = pd.DataFrame([{"indicator": "등록 학생 수", "center": "센터A", "base_date": "2026-06-30", "value": 188.0}, {"indicator": "등록 학생 수", "center": "센터B", "base_date": "2026-06-30", "value": 1095.0}])
    assert hwpx_build._table_for(ints)[1] == [["센터A", "188"], ["센터B", "1,095"]]        # 정수 표는 정수로, 소수 섞인 표(위)는 61.0처럼 소수 유지

def test_build_reply_structure_roundtrip():
    values = pd.DataFrame([{"indicator": "등원율", "center": c, "base_date": "2026-06-30", "value": v, "definition": "월 등원일수 ÷ 운영일수 × 100", "calc_period": "2026-06", "extract_date": "2026-09-10", "source_version": "v2"} for c, v in (("센터A", 60.3), ("센터C", 67.8))])
    req = {"requester": "○○○ 의원실", "title": "t"}
    items = [{"item_text": "2026. 6. 30. 기준 센터별 등원율", "indicator": "등원율", "base_date": "2026-06-30", "unit": "센터별"}, {"item_text": "운영 예산", "indicator": "운영 예산", "base_date": None}]
    draft = {"제목": "회신 제목", "본문": "1. 등원율은 아래 표와 같습니다.\n2. 운영 예산은 [확인 필요]", "산출근거": "정의: 월 등원일수 ÷ 운영일수 × 100"}
    cmp = pd.DataFrame([{"indicator": "등원율", "center": "센터C", "base_date": "2026-06-30", "old_value": 66.4, "new_value": 67.8, "판정": "차이"}])
    out = hwpx_build.build_reply(req, items, values, draft, {("등원율", "센터C", "2026-06-30"): "출결 사후 보정"}, cmp, dept_head="홍길동", phone="02-123-4567")
    assert out[:2] == b"PK" and len(out) > 3000
    text = docread.read("x.hwpx", out)
    for must in ("○○○ 의원실 답변자료", "1. 2026. 6. 30. 기준 센터별 등원율", "【확인 : 지역교육협력부장 홍길동 ☎ 02-123-4567】", "등원율은 아래 표와 같습니다.",
                 "※ 산출 근거 — 정의: 월 등원일수", "※ 지난 제출값과 차이: 센터C 66.4→67.8(출결 사후 보정)", "2. 운영 예산", "[확인 필요] 보유 자료 없음", "붙임. 산출 근거"):
        assert must in text, must
    grids, _ = tabular.grids_from_document("x.hwpx", out)
    assert len(grids) == 1 and grids[0].rows[0] == ["구분", "값(2026-06-30 기준)"] and grids[0].rows[1] == ["센터A", "60.3"] and len(grids[0].rows) == 3   # 합계 행을 만들지 않음
    # 생성한 파일을 다시 열어 표가 그대로 읽히는지(값 재현)
    assert docread.hwpx_tables(out)[0][2] == ["센터C", "67.8"]


def test_table_layout_widths_heights_and_chunking():
    """실제 깨짐 사례(48센터 × 9개월, 긴 센터 이름)의 재현: 첫 열은 이름이 한 줄에 들어가는 폭, 수치 열은 머리글이 들어가는 폭, 24행 단위로 표를 나누고 머리글 반복, 행 높이는 줄 수에 맞춤."""
    import calendar, zipfile, re
    months = [f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}" for y, m in [(2025, 12)] + [(2026, m) for m in range(1, 9)]]
    names = [f"EBS {s} 자기주도학습센터" for s in ("계룡", "논산", "보은", "청양")] + [f"센터{i:02d}" for i in range(44)]
    values = pd.DataFrame([{"indicator": "등원율", "center": c, "base_date": d, "value": float((i * 7 + j * 3) % 101)} for i, c in enumerate(names) for j, d in enumerate(months)])
    hdr, rows = hwpx_build._table_for(values)
    pt = hwpx_build._fit_pt(hdr, rows); assert pt == 8                                    # 10열은 9pt로 안 들어가 8pt
    ws = hwpx_build._col_widths(hdr, rows, pt)
    assert len(ws) == 10 and sum(ws) == hwpx_build.TEXT_W and ws[0] >= hwpx_build._text_w("EBS 계룡 자기주도학습센터", pt) + hwpx_build.CELL_PAD and ws[1] >= hwpx_build._text_w("'25.12", pt) + hwpx_build.CELL_PAD
    assert all(hwpx_build._lines(r[0], ws[0], pt) == 1 for r in rows)                   # 이름이 한 줄에
    assert hwpx_build._fit_pt(["구분", "값(2026-06-30 기준)"], [["센터A", "60.3"]]) == 9
    items = [{"item_text": "2025년 12월 ~ 2026년 8월 센터별 월별 등원율", "indicator": "등원율", "base_date": None, "unit": "센터별"}]
    out = hwpx_build.build_reply({"requester": "테스트용"}, items, values, {"제목": "t", "본문": "1. 현황은 붙임 표와 같습니다."}, {}, None)
    sec = zipfile.ZipFile(io.BytesIO(out)).read("Contents/section0.xml").decode("utf-8")
    tbls = re.findall(r'<hp:tbl [^>]*>', sec)
    assert len(tbls) == 2 and all('repeatHeader="1"' in t and 'pageBreak="CELL"' in t for t in tbls)                 # 48행 → 24행 × 2
    assert [int(x) for x in re.findall(r'rowCnt="(\d+)"', sec)] == [25, 25]
    heights = [int(h) for h in re.findall(r'<hp:sz width="\d+" widthRelTo="ABSOLUTE" height="(\d+)"', sec)]
    assert heights == [25 * hwpx_build._line_h(8)] * 2                                        # 모든 행이 한 줄 → 25 × 줄 높이
    assert sec.count('<hp:cellMargin left="280" right="280"') == 2 * 25 * 10
    assert '(단위: %)' in docread.read("x.hwpx", out) and "(단위: 센터별)" not in docread.read("x.hwpx", out)
    assert len(docread.hwpx_tables(out)) == 2 and docread.hwpx_tables(out)[1][0][0] == "구분"
