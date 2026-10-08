import pandas as pd
import hwpx_build, docread, tabular

def test_split_body_by_item_numbers():
    pre, by = hwpx_build.split_body("안녕하십니까.\n1. 등원율은 표와 같습니다.\n  센터C는 보정.\n2) 학생 수는 [확인 필요]\n3. 범위 밖 번호", 2)
    assert pre == ["안녕하십니까."] and by[1] == ["등원율은 표와 같습니다.", "센터C는 보정."] and by[2] == ["학생 수는 [확인 필요]", "3. 범위 밖 번호"]

def test_table_pivot_and_single_date():
    df = pd.DataFrame([{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.3}, {"indicator": "등원율", "center": "센터A", "base_date": "2026-07-31", "value": 61.0},
                       {"indicator": "등원율", "center": "센터B", "base_date": "2026-06-30", "value": 59.4}])
    hdr, rows = hwpx_build._table_for(df)
    assert hdr == ["구분", "2026-06-30", "2026-07-31"] and rows == [["센터A", "60.3", "61.0"], ["센터B", "59.4", "-"]]
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
