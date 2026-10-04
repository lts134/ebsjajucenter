"""요구서·회신 문서 본문 추출: txt / hwpx / pdf / docx → 문단 텍스트(표는 '셀 | 셀' 형태)."""
import io, re, zipfile
from hwpx_out import LEAF_P, LEAF_TBL, _para_text

def _hwpx_text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    out = []
    for name in sorted(n for n in z.namelist() if n.startswith("Contents/section")):
        xml = z.read(name).decode("utf-8", errors="ignore")
        # 표는 행 단위로, 일반 문단은 문단 단위로
        pos = 0
        for m in re.finditer(LEAF_TBL, xml, flags=re.S):
            out += _paras(xml[pos:m.start()])
            for tr in re.findall(r"<hp:tr\b.*?</hp:tr>", m.group(0), flags=re.S):
                cells = [" ".join(_paras(tc)) for tc in re.findall(r"<hp:tc\b.*?</hp:tc>", tr, flags=re.S)]
                out.append(" | ".join(c.strip() for c in cells))
            pos = m.end()
        out += _paras(xml[pos:])
    return "\n".join(x for x in out if x.strip())

def _paras(fragment: str) -> list[str]:
    return [t for p in re.findall(LEAF_P, fragment, flags=re.S) if (t := _para_text(p).strip())]

def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in r.pages)

def _docx_text(data: bytes) -> str:
    from docx import Document
    d = Document(io.BytesIO(data))
    out = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for r in t.rows:
            out.append(" | ".join(c.text.strip() for c in r.cells))
    return "\n".join(out)

def read(name: str, data: bytes) -> str:
    n = name.lower()
    if n.endswith(".hwpx"): return _hwpx_text(data)
    if n.endswith(".pdf"): return _pdf_text(data)
    if n.endswith(".docx"): return _docx_text(data)
    if n.endswith(".hwp"): raise ValueError("HWP(구형식)는 한글에서 HWPX로 저장 후 올려 주세요.")
    return data.decode("utf-8", errors="ignore")
