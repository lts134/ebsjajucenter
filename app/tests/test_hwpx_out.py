import io, re, zipfile, xml.dom.minidom as md, hwpx_out
from pathlib import Path
TPL = (Path(__file__).resolve().parents[1] / "templates" / "회신서식_샘플.hwpx").read_bytes()
ROWS = [{"no": i + 1, "indicator": "등원율", "center": f"센터{c}", "base_date": "2026-06-30", "value": 60 + i} for i, c in enumerate("ABCDEFGHIJKL")]

def _sec(b): return zipfile.ZipFile(io.BytesIO(b)).read("Contents/section0.xml").decode()

def _rezip(sec):
    z = zipfile.ZipFile(io.BytesIO(TPL)); buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zo:
        zo.writestr(zipfile.ZipInfo("mimetype"), z.read("mimetype"), compress_type=zipfile.ZIP_STORED)
        for n in z.namelist():
            if n != "mimetype": zo.writestr(n, sec.encode() if n == "Contents/section0.xml" else z.read(n))
    return buf.getvalue()

def test_render_fills_all_placeholders_and_expands_rows():
    out = hwpx_out.render(TPL, {"수신": "의원실", "발신": "EBS", "제목": "T", "요구항목": "1. a\n2. b", "본문": "x <y> & z", "차이사유": "-", "산출근거": "-", "담당자": "d"}, ROWS)
    sec = _sec(out); md.parseString(sec)
    assert re.findall(r"\{\{[^}]*\}\}", sec) == []
    assert all(f"센터{c}" in sec for c in "ABCDEFGHIJKL") and "&lt;y&gt; &amp; z" in sec
    assert 'rowCnt="13"' in sec                               # 헤더 1 + 데이터 12
    assert "<hp:t>1. a</hp:t>" in sec and "<hp:t>2. b</hp:t>" in sec   # 줄바꿈 → 문단 복제

def test_mimetype_first_and_stored():
    out = hwpx_out.render(TPL, {}, [])
    z = zipfile.ZipFile(io.BytesIO(out)); info = z.infolist()[0]
    assert info.filename == "mimetype" and info.compress_type == zipfile.ZIP_STORED

def test_split_run_placeholder_is_recognised_and_replaced():
    """한글에서 타이핑한 자리표시자가 '{{' '수신' '}}' 세 run으로 쪼개져 저장된 템플릿."""
    sec = _sec(TPL)
    m = re.search(r"(<hp:run\b[^>]*>)<hp:t>\{\{수신\}\}</hp:t>(</hp:run>)", sec)
    split = f"{m.group(1)}<hp:t>{{{{</hp:t>{m.group(2)}{m.group(1)}<hp:t>수신</hp:t>{m.group(2)}{m.group(1)}<hp:t>}}}}</hp:t>{m.group(2)}"
    tpl2 = _rezip(sec[:m.start()] + split + sec[m.end():])
    assert "수신" in hwpx_out.placeholders(tpl2)
    out = _sec(hwpx_out.render(tpl2, {"수신": "△△△ 의원실"}, []))
    assert "△△△ 의원실" in out and not re.search(r"<hp:t>수신</hp:t>|<hp:t>\}\}</hp:t>", out)

def test_placeholders_list():
    assert {"수신", "본문", "row.center", "row.value"} <= set(hwpx_out.placeholders(TPL))
