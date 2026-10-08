"""통합 대시보드 저장 파일 가져오기: 허용 목록 상수만 읽고(계정·연락처·Firebase는 무시), 월별 값·이용현황·센터 명부로 변환, 이름 표준화, 부분 집계 월 제외."""
import json
import dashboard_import as di

HTML = """<!DOCTYPE html><html><head><title>대시보드</title><script src="https://cdn/x.js"></script></head><body>
<script>
  const HEAD_PHONE = { '홍길동': '010-1234-5678' };
  const FIREBASE_CONFIG = { apiKey: "AIza-secret" };
  /* 월간 출결: m[i] = [등원예정일, 등원일, 등원율, 목표시간, 달성시간, 시간달성율, 일평균] */
  const ATT_DATA = {"asof":"2026.09.07","source":"센터별월간출결_20260907.xls","months":["2025.12","2026.01","2026.09"],"partial":"2026.09",
    "centers":[{"edu":"강원","name":"영월 진로진학지원센터","m":[[0,0,0,0,0,0,0],[299,245,84,1220.5,915.8,75,3.7],[72,45,82,157.5,85.2,54.1,1.9]],"type":"지역센터"},
               {"edu":"서울","name":"강일고","m":[null,[100,90,90,200,180,90,2.0],[10,9,90,20,18,90,2.0]]}],
    "notes":{"rateDef":"등원율은 출결시스템 제공값"}};
  const DG_DATA = {"asof":"2026.09.14","source":"월별진단검사.xls","months":["2026.01"],"kinds":[{"k":"E-1","label":"진로적성(초)","lv":"초"},{"k":"M-1","label":"진로적성(중)","lv":"중"}],
    "centers":[{"edu":"강원","name":"영월진로진학지원센터","m":[[2,3,4]]}],"notes":{"def":"검사 건수 = 검사 실시 건수 합계"}};
  const MG_DATA = {"asof":"2026.09.11","source":"월별관리인원.xls","months":["2026.01"],"centers":[{"edu":"강원","name":"영월진로진학지원센터","m":[[1,2,3,6]]}],"notes":{"def":"관리 중인 학생 수"}};
  const CS_DATA = {"asof":"2026.09.14","source":"월별상담횟수.xls","months":["2026.01"],"centers":[{"edu":"강원","name":"영월진로진학지원센터","m":[[4,5,9]]}],"notes":{"def":"상담 건수"}};
  const USAGE_DATA = {"asof":"2026. 7. 17.","months":["'25.12"],"centers":[{"edu":"강원","name":"영월진로진학지원센터","size":"소형","seats":30,"quota":30,"codiTO":1,"adminTO":1,"codi":1,"mgr":1,"reg":48,"ing":21,"out":27,"hours":4117,"consult":168}]};
  const DATA25 = [ {no:1,name:'강일고등학교',edu:'서울',region:'강동구',facility:'교내',codi_type:'일반',codi:1,size:'소형',date:'2026.03.03',students:30,manager:'윤아무개'},
                   {no:26,name:'영월진로진학지원센터',edu:'강원',region:'영월군',facility:'교외',codi_type:'일반',codi:1,size:'소형',date:'2026.01.14',students:30,manager:'김아무개'} ];
  const DATA26 = [ {no:1,name:'고성자기주도학습센터',region:'경남 고성',type:'유형1',facility:'학교 밖',size:'중/35명',date:'2026.10.01'} ];
  const CENTER_REGISTRY = [
    // [센터 ID, 표준 센터명, 2025 번호, 2026 번호, 결과, 다른 표기]
    ["C001", "강일고등학교", 1, null, null, ["강일고"]],
    ["C026", "영월진로진학지원센터", 26, null, null, []],
    ["C061", "고성자기주도학습센터", null, 1, "선정", []],
  ];
  const WEEKEND_RAW = { asof: '2026.09.29', source: 'x.xlsx', markedOff: ["C061"], centers: { C001: ['토·일 10~20시', '토·일 10~20시', 2], C026: ['토·일 9~21시', '토·일 9~21시', 2], C061: ['토 10~19시', '토 10~19시', 1] } };
  const ASSEMBLY_DATA = {"staff":[{"name":"개인이름","cert":"자격"}]};
</script></body></html>"""

def test_parse_reads_only_allowed_constants():
    d = di.parse(HTML.encode("utf-8"))
    assert set(d) == {"ATT_DATA", "DG_DATA", "MG_DATA", "CS_DATA", "USAGE_DATA", "DATA25", "DATA26", "CENTER_REGISTRY", "WEEKEND_RAW"}
    assert "HEAD_PHONE" not in d and "FIREBASE_CONFIG" not in d and "ASSEMBLY_DATA" not in d
    assert di.month_end("2025.12") == "2025-12-31" and di.month_end("'26.2") == "2026-02-28" and di.month_end("x") is None

def test_rows_values_definitions_partial_and_name_canonicalization():
    d = di.parse(HTML); rows, notes = di.indicator_rows(d)
    by = {}
    for r in rows: by.setdefault(r["indicator"], []).append(r)
    att = [r for r in by["등원율"]]
    assert {r["center"] for r in att} == {"영월진로진학지원센터", "강일고등학교"}                    # '영월 진로진학지원센터'·'강일고' → 명부 표준명
    assert {r["base_date"] for r in att} == {"2025-12-31", "2026-01-31"}                             # 부분 집계 2026.09 제외, null 달 건너뜀
    r1 = next(r for r in att if r["center"] == "영월진로진학지원센터" and r["base_date"] == "2026-01-31")
    assert r1["value"] == 84.0 and r1["definition"] == "등원율은 출결시스템 제공값" and r1["extract_date"] == "2026-09-07" and r1["source_version"] == "센터별월간출결_20260907.xls" and r1["calc_period"] == "2026.01"
    assert next(r for r in by["시간달성율"] if r["center"] == "강일고등학교" and r["base_date"] == "2026-01-31")["value"] == 90.0 and "일평균" not in by
    assert by["진단검사 건수"][0]["value"] == 5.0 and by["진단검사 학생 수"][0]["value"] == 4.0 and by["진단검사 건수(E-1 진로적성(초))"][0]["value"] == 2.0
    assert by["관리인원"][0]["value"] == 6.0 and by["관리인원(초등)"][0]["value"] == 1.0 and by["상담횟수"][0]["value"] == 9.0 and by["초기 상담 횟수"][0]["value"] == 4.0
    u = {r["indicator"]: r for r in rows if r["base_date"] == "2026-07-17"}
    assert u["등록 학생 수"]["value"] == 48.0 and u["이용 중 학생 수"]["value"] == 21.0 and u["하차 학생 수"]["value"] == 27.0 and u["정원"]["value"] == 30.0 and "누계" in u["이용시간"]["definition"]
    assert any("부분 집계 2026.09 제외" in n for n in notes)
    rows2, _ = di.indicator_rows(d, include_partial=True); assert any(r["base_date"] == "2026-09-30" for r in rows2)
    blob = json.dumps(rows, ensure_ascii=False); assert "010-" not in blob and "AIza" not in blob and "아무개" not in blob

def test_centers_master_merges_registry_year_lists_and_weekend():
    cs = di.centers(di.parse(HTML)); byid = {c["center_id"]: c for c in cs}
    assert len(cs) == 3 and byid["C001"]["aliases"] == ["강일고"] and byid["C001"]["year25"] and not byid["C001"]["year26"]
    assert byid["C026"] == {"center_id": "C026", "name": "영월진로진학지원센터", "aliases": [], "year25": True, "year26": False, "status": None, "edu": "강원", "region": "영월군", "facility": "교외", "type": "일반", "size": "소형", "open_date": "2026-01-14", "capacity": 30, "weekend": "토·일 9~21시", "weekend_days": 2}
    g = byid["C061"]; assert g["year26"] and g["status"] == "선정" and g["edu"] == "경남" and g["capacity"] == 35 and g["open_date"] == "2026-10-01" and g["weekend"] == "미운영(확정표 제외)"
    assert all("manager" not in c for c in cs)

def test_db_roundtrip(fresh_db):
    db = fresh_db; S = di.summarize(di.parse(HTML))
    bid, n = db.add_data_batch(S["rows"], "u", "통합 대시보드(t.html)", ""); assert n == len(S["rows"]) > 20
    assert db.replace_centers(S["centers"], "t.html") == 3 and db.center_count() == 3
    assert db.list_centers("영월")[0]["name"] == "영월진로진학지원센터" and db.list_centers("경남")[0]["center_id"] == "C061" and db.list_centers("강일고")[0]["center_id"] == "C001"   # 별칭으로도 찾힘
    assert sorted(db.data_dates_for("등원율")) == ["2025-12-31", "2026-01-31"]
    import pytest
    with pytest.raises(ValueError): di.parse("<html><body>no data</body></html>")
