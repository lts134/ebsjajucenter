"""HWPX 템플릿 치환 출력.
원칙: 한글에서 저장한 템플릿의 XML 구조를 건드리지 않고 <hp:t> 안 글자만 바꾼다.
- {{키}}            : 텍스트 치환. 값에 줄바꿈이 있으면 그 문단을 복제해 여러 문단으로.
- {{row.컬럼}}      : 이 자리표시자가 있는 표 행을 '행 템플릿'으로 보고 데이터 행 수만큼 복제.
- 채운 셀의 세로 정렬은 CENTER로 통일(빈 셀이 TOP으로 저장돼 있는 경우 대비).
"""
import io, re, zipfile

# 말단 문단(안에 다른 문단이 없는 것)만 매칭 → 표를 품은 바깥 문단을 건드리지 않음
LEAF_P = r"<hp:p\b(?:(?!<hp:p\b).)*?</hp:p>"
# 말단 표(안에 다른 표가 없는 것)
LEAF_TBL = r"<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>"

def _esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _para_text(fragment: str) -> str:
    """조각 안의 모든 <hp:t> 글자를 이어 붙인다(태그 제거, 엔티티 복원)."""
    t = "".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", fragment, flags=re.S))
    t = re.sub(r"<[^>]+>", "", t)
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")

_T_TAG = r"<hp:t\b[^>]*>.*?</hp:t>|<hp:t\b[^>]*/>"

def _para_with_text(p: str, text: str) -> str:
    """문단 p의 전체 글자를 text 하나로 바꾼다: 첫 run에 넣고, 나머지 run의 <hp:t>는 비운다(컨트롤·서식은 유지).
    한글에서 '{{수신}}'을 타이핑하면 '{{' '수신' '}}'처럼 run이 쪼개져 저장되는 경우가 있어 첫 run만 바꾸면 글자가 남는다.
    linesegarray(줄 배치 캐시)는 제거해 한글이 다시 계산하게 한다."""
    p2 = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", p, flags=re.S)
    runs = list(re.finditer(r"(<hp:run\b[^>]*?)(/>|>(.*?)</hp:run>)", p2, flags=re.S))
    if not runs: return p2
    out, pos = [], 0
    for i, m in enumerate(runs):
        inner = re.sub(_T_TAG, "", m.group(3) or "", flags=re.S)          # 기존 글자 제거, 컨트롤은 유지
        new_run = f"{m.group(1)}><hp:t>{_esc(text)}</hp:t>{inner}</hp:run>" if i == 0 else f"{m.group(1)}>{inner}</hp:run>"
        out.append(p2[pos:m.start()]); out.append(new_run); pos = m.end()
    out.append(p2[pos:])
    return "".join(out)

def _fill_placeholders(xml: str, values: dict) -> str:
    """문단 단위로 {{키}} 처리. 다중 행 값이면 문단 복제."""
    def repl_para(m):
        p = m.group(0)
        full = _para_text(p)                      # run이 쪼개져 있어도 문단 전체 글자로 자리표시자를 찾는다
        keys = re.findall(r"\{\{(?!row\.)([^}]+)\}\}", full)
        if not keys: return p
        for k in keys:
            full = full.replace("{{%s}}" % k, str(values.get(k, "")))
        lines = full.split("\n")
        return "".join(_para_with_text(p, ln) for ln in lines) if len(lines) > 1 else _para_with_text(p, full)
    return re.sub(LEAF_P, repl_para, xml, flags=re.S)

def _expand_rows(xml: str, rows: list[dict]) -> str:
    """{{row.*}}가 있는 <hp:tr>을 데이터 행 수만큼 복제."""
    def repl_tbl(m):
        t = m.group(0)
        trs = list(re.finditer(r"<hp:tr\b.*?</hp:tr>", t, flags=re.S))
        idx = next((i for i, tr in enumerate(trs) if "{{row." in tr.group(0)), None)
        if idx is None: return t
        tpl = trs[idx].group(0)
        tpl_addr = int(re.search(r'rowAddr="(\d+)"', tpl).group(1))
        row_h = int(re.search(r'<hp:cellSz width="\d+" height="(\d+)"', tpl).group(1))
        new_rows = []
        def cell_fix(cm, data):
            c = cm.group(0)
            full = _para_text(c)
            keys = re.findall(r"\{\{row\.([^}]+)\}\}", full)
            if not keys: return c
            for k in keys: full = full.replace("{{row.%s}}" % k, str(data.get(k, "")))
            return re.sub(LEAF_P, lambda pm: _para_with_text(pm.group(0), full), c, count=1, flags=re.S)
        for i, data in enumerate(rows):
            r = re.sub(r'rowAddr="\d+"', f'rowAddr="{tpl_addr + i}"', tpl)
            r = re.sub(r"<hp:tc\b.*?</hp:tc>", lambda cm, d=data: cell_fix(cm, d), r, flags=re.S)
            new_rows.append(r)
        # 템플릿 행 뒤 행들의 rowAddr 밀기
        after = []
        for tr in trs[idx + 1:]:
            a = int(re.search(r'rowAddr="(\d+)"', tr.group(0)).group(1))
            after.append(re.sub(r'rowAddr="\d+"', f'rowAddr="{a + len(rows) - 1}"', tr.group(0)))
        body = t[:trs[idx].start()] + "".join(new_rows) + "".join(after) + t[trs[-1].end():]
        rc = int(re.search(r'rowCnt="(\d+)"', body).group(1))
        body = re.sub(r'rowCnt="\d+"', f'rowCnt="{rc + len(rows) - 1}"', body, count=1)
        body = re.sub(r'(<hp:sz width="\d+" widthRelTo="ABSOLUTE" height=")(\d+)(")',
                      lambda sm: f"{sm.group(1)}{int(sm.group(2)) + row_h * (len(rows) - 1)}{sm.group(3)}", body, count=1)
        return body
    return re.sub(LEAF_TBL, repl_tbl, xml, flags=re.S)

def render(template_bytes: bytes, values: dict, rows: list[dict] | None = None) -> bytes:
    z = zipfile.ZipFile(io.BytesIO(template_bytes))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zo:
        zo.writestr(zipfile.ZipInfo("mimetype"), z.read("mimetype"), compress_type=zipfile.ZIP_STORED)
        for n in z.namelist():
            if n == "mimetype": continue
            data = z.read(n)
            if n.startswith("Contents/section"):
                xml = data.decode("utf-8")
                if rows is not None: xml = _expand_rows(xml, rows)
                xml = _fill_placeholders(xml, values)
                xml = re.sub(r'(<hp:subList\b[^>]*vertAlign=")TOP(")', r"\1CENTER\2", xml)
                data = xml.encode("utf-8")
            zo.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()

def placeholders(template_bytes: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(template_bytes))
    xml = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("Contents/section"))
    found = set()
    for pm in re.finditer(LEAF_P, xml, flags=re.S):                 # 문단 단위 글자에서 찾아야 쪼개진 run도 잡힌다
        found.update(re.findall(r"\{\{([^}]+)\}\}", _para_text(pm.group(0))))
    return sorted(found)
