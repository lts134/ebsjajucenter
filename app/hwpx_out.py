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

def _para_with_text(p: str, text: str) -> str:
    """문단 p의 첫 run 텍스트를 text로. linesegarray(레이아웃 캐시)는 제거해 한글이 재계산하게 함."""
    p2 = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", p, flags=re.S)
    m = re.search(r"(<hp:run\b[^>]*?)(/>|>(.*?)</hp:run>)", p2, flags=re.S)
    if not m: return p2
    inner = m.group(3) or ""
    inner = re.sub(r"<hp:t\b[^>]*>.*?</hp:t>|<hp:t\b[^>]*/>", "", inner, flags=re.S)  # 기존 t 제거
    return p2[:m.start()] + f"{m.group(1)}><hp:t>{_esc(text)}</hp:t>{inner}</hp:run>" + p2[m.end():]

def _fill_placeholders(xml: str, values: dict) -> str:
    """문단 단위로 {{키}} 처리. 다중 행 값이면 문단 복제."""
    def repl_para(m):
        p = m.group(0)
        keys = re.findall(r"\{\{(?!row\.)([^}]+)\}\}", p)
        if not keys: return p
        # 문단 전체 텍스트를 구해 치환
        full = "".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", p, flags=re.S))
        full = re.sub(r"<[^>]+>", "", full)
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
            keys = re.findall(r"\{\{row\.([^}]+)\}\}", c)
            if not keys: return c
            full = "".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", c, flags=re.S)); full = re.sub(r"<[^>]+>", "", full)
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
    return sorted(set(re.findall(r"\{\{([^}]+)\}\}", xml)))
