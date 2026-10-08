"""SQLite 이력 저장소. 요청 → 항목 → 제출본 → 제출값(산출 근거 포함) → 차이 사유."""
import sqlite3, os, re, datetime as dt
from pathlib import Path

DB_PATH = Path(os.environ.get("HISTORY_DB", Path(__file__).parent / "storage" / "history.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (          -- 요구서
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester TEXT,            -- 요청 주체(의원실·감사 등)
    received_date TEXT,        -- 접수일
    due_date TEXT,             -- 제출기한
    title TEXT,                -- 요구 제목
    raw_text TEXT,             -- 요구서 원문
    source_file TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS items (             -- 요구 항목(요구서 1건에 여러 개)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER,
    seq INTEGER,
    item_text TEXT,            -- 항목 원문
    indicator TEXT,            -- 지표명(정규화)
    base_date TEXT,            -- 기준일
    unit TEXT,                 -- 대상 단위(센터별·전체 등)
    period TEXT,               -- 기간 표현(2024~2026, 최근 3년 등)
    FOREIGN KEY(request_id) REFERENCES requests(id)
);
CREATE TABLE IF NOT EXISTS submissions (       -- 제출본
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER,
    submitted_date TEXT,
    submitted_by TEXT,
    file_name TEXT,
    status TEXT,               -- draft / confirmed
    note TEXT,
    created_at TEXT,
    FOREIGN KEY(request_id) REFERENCES requests(id)
);
CREATE TABLE IF NOT EXISTS submission_values ( -- 제출값 + 산출 근거
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    indicator TEXT,
    center TEXT,
    base_date TEXT,
    value REAL,
    definition TEXT,           -- 지표 정의
    calc_period TEXT,          -- 집계기간
    extract_date TEXT,         -- 원자료 추출 시점
    source_version TEXT,       -- 원자료 버전
    source_file TEXT,          -- 출처 파일명
    source_sheet TEXT,         -- 출처 시트
    source_row INTEGER,        -- 출처 행 번호(엑셀 기준)
    FOREIGN KEY(submission_id) REFERENCES submissions(id)
);
CREATE TABLE IF NOT EXISTS drafts (         -- 회신 초안(문안·출력 파일)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    request_id INTEGER,
    title TEXT, body TEXT, reasons_text TEXT, provenance_text TEXT,
    hwpx_name TEXT,
    status TEXT,               -- draft / review_requested / approved / rejected
    created_at TEXT,
    FOREIGN KEY(submission_id) REFERENCES submissions(id)
);
CREATE TABLE IF NOT EXISTS reviews (        -- 팀장 검토 이력
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER,
    reviewer TEXT,
    decision TEXT,             -- approved / rejected / comment
    comment TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS data_batches (      -- 지표 데이터 적재 묶음(요구서와 무관하게 미리 넣어 두는 집계값)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loaded_at TEXT,
    loaded_by TEXT,
    source TEXT,               -- 파일명·시스템명
    note TEXT,
    n_rows INTEGER
);
CREATE TABLE IF NOT EXISTS indicator_data (    -- 지표 데이터(센터×지표×기준일 값 + 산출 근거). 같은 키는 최신 적재가 대체
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    indicator TEXT,
    center TEXT,
    base_date TEXT,
    value REAL,
    definition TEXT, calc_period TEXT, extract_date TEXT, source_version TEXT,
    source_file TEXT, source_sheet TEXT, source_row INTEGER,
    FOREIGN KEY(batch_id) REFERENCES data_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_indicator_data_key ON indicator_data(indicator, base_date);
CREATE TABLE IF NOT EXISTS diff_reasons (      -- 차이 사유(담당자 입력)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    indicator TEXT,
    center TEXT,
    base_date TEXT,
    old_value REAL,
    new_value REAL,
    reason TEXT,
    entered_by TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS centers (           -- 센터 명부(통합 대시보드 등에서 가져온 기초자료). 이름 표준화·센터 현황 답변에 쓴다. 개인 이름·연락처는 넣지 않는다
    center_id TEXT PRIMARY KEY,
    name TEXT,
    aliases TEXT,                               -- JSON 배열(다른 표기)
    year25 INTEGER,                             -- 2025 운영 센터면 1
    year26 INTEGER,                             -- 2026 선정 센터면 1
    status TEXT,                                -- 2026 공모 결과 등('취소' 등)
    edu TEXT, region TEXT, facility TEXT, type TEXT, size TEXT,
    open_date TEXT, capacity INTEGER,
    weekend TEXT, weekend_days INTEGER,
    source TEXT, loaded_at TEXT
);
CREATE TABLE IF NOT EXISTS ref_docs (          -- 참고 문서(지침·매뉴얼·FAQ): 사업 자체 질문에 답할 근거
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, file_name TEXT, pages INTEGER, chars INTEGER,
    uploaded_by TEXT, uploaded_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS ref_chunks (        -- 참고 문서 조각(쪽·순번)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER, page INTEGER, seq INTEGER, text TEXT
);
CREATE INDEX IF NOT EXISTS idx_ref_chunks_doc ON ref_chunks(doc_id);
CREATE TABLE IF NOT EXISTS dispatches (        -- 발송 기록: 확정한 회신을 언제 어디로 어떻게 보냈나(메일·공문). 확정 ≠ 발송
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER, draft_id INTEGER,
    sent_at TEXT, sent_to TEXT, method TEXT, sent_by TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS requesters (        -- 요청 주체 사전: 표준 이름·별칭·유형(의원실·감사·교육부·언론·기타). 표기 통일과 자동완성에 쓴다
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE, aliases TEXT, kind TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS indicator_dict (    -- 지표 사전·데이터 카탈로그(화면에서 편집). 비어 있으면 코드의 기본값(normalize.CANON·suggest.CATALOG)을 쓴다
    canon TEXT PRIMARY KEY, aliases TEXT, source TEXT, owner TEXT, note TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tone_presets (      -- 요청 주체 유형별 회신 톤(문서 제목 접미·확인 줄·문체 지침·메일 인사말). 비어 있으면 draft.DEFAULT_PRESETS
    kind TEXT PRIMARY KEY, doc_label TEXT, show_confirm INTEGER, style TEXT, mail_greeting TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (          -- 조직 설정(부서장·내선·발신 표기): 접속 세션이 아니라 기록에 남는다
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS phrases (           -- 자주 쓰는 문구 서랍(차이 사유·지표 정의·안내문). 확정 때 쓴 사유가 자동으로 쌓인다
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, text TEXT, created_by TEXT, created_at TEXT, last_used TEXT, use_count INTEGER DEFAULT 1,
    UNIQUE(kind, text)
);
"""

_initialized = set()

CORRUPT_MARKERS = ("file is not a database", "malformed", "not a database", "unsupported file format")

def connect(_retry=True):
    """연결. 잠금('database is locked')은 timeout 안에서 기다리고, 파일 손상이 확실할 때만 .broken으로 비켜 두고 새로 만든다."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.row_factory = sqlite3.Row
        if str(DB_PATH) not in _initialized:      # 스키마 생성·마이그레이션은 프로세스당 1회
            try: con.execute("PRAGMA journal_mode=WAL")   # 여러 접속자가 동시에 읽고 쓸 때 잠금 충돌을 줄인다
            except sqlite3.DatabaseError: pass
            con.executescript(SCHEMA)
            _migrate(con)
            _initialized.add(str(DB_PATH))
        return con
    except sqlite3.DatabaseError as e:
        if not _retry or not any(m in str(e).lower() for m in CORRUPT_MARKERS):
            raise                                   # 잠금·권한 등은 그대로 올려 사용자에게 보인다(데이터를 비켜 두지 않음)
        for suffix in ("", "-journal", "-wal", "-shm"):             # WAL·shm을 남기면 새 DB가 옛 WAL과 짝을 이뤄 다시 손상된다
            f = Path(str(DB_PATH) + suffix)
            if f.exists():
                try: f.rename(str(f) + ".broken")
                except OSError: pass
        return connect(_retry=False)

def _migrate(con):
    """구버전 DB에 새 컬럼 추가 + 중복 정리 + 고유 인덱스(같은 지표·센터·기준일 행이 둘 생기지 않게)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(submission_values)")}
    for c, t in [("source_file", "TEXT"), ("source_sheet", "TEXT"), ("source_row", "INTEGER")]:
        if c not in cols:
            con.execute(f"ALTER TABLE submission_values ADD COLUMN {c} {t}")
    icols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    if "period" not in icols:
        con.execute("ALTER TABLE items ADD COLUMN period TEXT")
    rcols = {r[1] for r in con.execute("PRAGMA table_info(requests)")}
    fresh_status = "status" not in rcols
    for c in ("status", "assignee", "memo", "updated_at"):
        if c not in rcols: con.execute(f"ALTER TABLE requests ADD COLUMN {c} TEXT")
    if fresh_status:                                              # 기존 기록의 상태를 제출본·발송 기록에서 한 번 채운다
        con.execute("UPDATE requests SET status='접수' WHERE status IS NULL")
        con.execute("UPDATE requests SET status='확정' WHERE id IN (SELECT request_id FROM submissions WHERE status='confirmed')")
        con.execute("UPDATE requests SET status='발송' WHERE id IN (SELECT s.request_id FROM dispatches d JOIN submissions s ON s.id=d.submission_id)")
    dcols = {r[1] for r in con.execute("PRAGMA table_info(indicator_data)")}
    if "active" not in dcols: con.execute("ALTER TABLE indicator_data ADD COLUMN active INTEGER DEFAULT 1")
    if "superseded_by" not in dcols: con.execute("ALTER TABLE indicator_data ADD COLUMN superseded_by INTEGER")
    con.execute("UPDATE indicator_data SET active=1 WHERE active IS NULL")
    # 같은 키의 활성 행이 둘 이상이면 최신(id 큰 것)만 활성으로
    con.execute("""UPDATE indicator_data SET active=0 WHERE active=1 AND id NOT IN (
                     SELECT MAX(id) FROM indicator_data WHERE active=1 GROUP BY indicator, center, base_date)""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_indicator_data_active ON indicator_data(indicator, center, base_date) WHERE active=1")
    con.execute("""DELETE FROM submission_values WHERE id NOT IN (
                     SELECT MAX(id) FROM submission_values GROUP BY submission_id, indicator, center, base_date)""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_submission_values ON submission_values(submission_id, indicator, center, base_date)")
    con.commit()

# ---------- 값 정규화(모든 쓰기 경로가 같은 함수를 쓴다) ----------
def norm_date(v, keep_unparsed: bool = False):
    """기준일·접수일·기한 → 'YYYY-MM-DD'. 엑셀 일련번호(20000~80000)·날짜 객체·'2026. 6. 30.'·"'26.6.30"·'2026년 6월 30일'·'2026.6'(→ 월말)을 받는다.
    해석 못 하면 None(keep_unparsed=True면 원문 그대로 — 머리 정보처럼 사람이 볼 값)."""
    import calendar
    if v is None: return None
    if isinstance(v, float) and v != v: return None
    if hasattr(v, "strftime"):
        try: return v.strftime("%Y-%m-%d")
        except ValueError: return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= float(v) <= 80000:                                                   # 엑셀 일련번호
            return (dt.date(1899, 12, 30) + dt.timedelta(days=int(v))).isoformat()
        return str(v) if keep_unparsed else None
    s = str(v).strip().strip("'")
    if not s: return None
    m = re.match(r"^(\d{4}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\.?$", s)
    if m:
        y = int(m.group(1)); y = y + 2000 if y < 100 else y
        try: return dt.date(y, int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError: return s if keep_unparsed else None
    m = re.match(r"^(\d{4}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*월?\.?$", s)                # 연·월만 → 월말
    if m:
        y = int(m.group(1)); y = y + 2000 if y < 100 else y; mo = int(m.group(2))
        if 1 <= mo <= 12: return f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try: return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError: return s if keep_unparsed else None
    return s if keep_unparsed else None

def center_key(name) -> str:
    """센터 이름 비교용 키: 공백·기호·대소문자·'EBS' 접두를 무시('EBS 계룡 자기주도학습센터' == 'EBS계룡자기주도학습센터')."""
    return re.sub(r"[\s·!()（）\-_.]", "", str(name or "")).lower().replace("ebs", "")

_center_cache: dict = {"ver": None, "map": {}}
def canonical_center(name):
    """센터 명부(이름·별칭)에 있는 표기면 표준 이름으로, 없으면 공백만 정리한 원래 이름."""
    if name is None: return None
    s = str(name).strip()
    if not s: return s
    try:
        con = connect(); ver = con.execute("SELECT COUNT(*), MAX(loaded_at) FROM centers").fetchone(); 
        if tuple(ver) != _center_cache["ver"]:
            mp = {}
            for r in con.execute("SELECT name, aliases FROM centers").fetchall():
                mp[center_key(r[0])] = r[0]
                try:
                    import json
                    for a in json.loads(r[1] or "[]"): mp[center_key(a)] = r[0]
                except ValueError: pass
            _center_cache.update(ver=tuple(ver), map=mp)
        con.close()
    except sqlite3.DatabaseError: pass
    return _center_cache["map"].get(center_key(s), re.sub(r"\s+", " ", s))

def _known_spellings() -> dict:
    """센터 비교 키 → 이미 저장된 표기. 명부가 없어도 먼저 들어온 표기('EBS 계룡 센터')에 뒤의 다른 표기('EBS계룡센터')를 맞춰
    같은 센터가 두 행으로 갈라지지 않게 한다(고유 인덱스는 글자 그대로 비교하므로 저장 전에 맞춰야 한다)."""
    known = {}
    try:
        con = connect()
        for (name,) in con.execute("SELECT DISTINCT center FROM submission_values UNION SELECT DISTINCT center FROM indicator_data").fetchall():
            if name: known.setdefault(center_key(name), name)
        con.close()
    except sqlite3.DatabaseError: pass
    return known

def _norm_rows(values) -> list[dict]:
    """값 행들을 저장 형태로: 센터 표준화(명부 → 저장된 표기 → 묶음 안 첫 표기)·기준일 정규화·값 float·중복 키는 마지막 것만."""
    known = _known_spellings(); out: dict = {}
    for v in values:
        r = _norm_value_row(v)
        if not r: continue
        k = center_key(r["center"])
        if k in _center_cache["map"] or k not in known: known.setdefault(k, r["center"])          # 명부 표기가 우선, 아니면 먼저 본 표기
        r["center"] = known[k] if k not in _center_cache["map"] else r["center"]
        out[(r["indicator"], r["center"], r["base_date"])] = r
    return list(out.values())

def _norm_value_row(v: dict) -> dict | None:
    """값 행 하나를 저장 형태로: 센터 표준화·기준일 정규화·값 float. 핵심 칸이 비면 None."""
    ind, center, bd = _s(v.get("indicator")), canonical_center(v.get("center")), norm_date(v.get("base_date"))
    val = v.get("value")
    if not ind or not center or not bd or val is None or val != val: return None
    try: val = float(val)
    except (TypeError, ValueError): return None
    return {**v, "indicator": ind, "center": center, "base_date": bd, "value": val}

def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------- 요구서 ----------
def add_request(requester, received_date, due_date, title, raw_text, source_file, items):
    con = connect()
    cur = con.execute(
        "INSERT INTO requests(requester,received_date,due_date,title,raw_text,source_file,created_at,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (canonical_requester(requester), received_date, due_date, title, raw_text, source_file, now(), "접수", now()))
    rid = cur.lastrowid
    def _s(v):                                   # NaN·None → None, 그 외 문자열
        return None if v is None or (isinstance(v, float) and v != v) else str(v).strip() or None
    seq = 0
    for it in items:
        text = _s(it.get("item_text"))
        if not text: continue                    # 빈 행(데이터 에디터에서 추가만 하고 비운 행)은 저장하지 않음
        seq += 1
        con.execute("INSERT INTO items(request_id,seq,item_text,indicator,base_date,unit,period) VALUES(?,?,?,?,?,?,?)",
                    (rid, seq, text, _s(it.get("indicator")), _s(it.get("base_date")), _s(it.get("unit")), _s(it.get("period"))))
    con.commit(); con.close()
    return rid

def _s(v):
    """NaN·None → None, 그 외 문자열(빈 문자열은 None)."""
    return None if v is None or (isinstance(v, float) and v != v) else (str(v).strip() or None)

def update_request(request_id, requester, received_date, due_date, title):
    """요구서 머리 정보 수정(원문·항목은 그대로)."""
    con = connect()
    con.execute("UPDATE requests SET requester=?, received_date=?, due_date=?, title=?, updated_at=? WHERE id=?",
                (canonical_requester(_s(requester)), _s(received_date), _s(due_date), _s(title), now(), request_id))
    con.commit(); con.close()

def replace_items(request_id, items):
    """요구 항목 전체 교체(빈 행 제외, 순번 다시 매김)."""
    con = connect()
    con.execute("DELETE FROM items WHERE request_id=?", (request_id,))
    seq = 0
    for it in items:
        text = _s(it.get("item_text"))
        if not text: continue
        seq += 1
        con.execute("INSERT INTO items(request_id,seq,item_text,indicator,base_date,unit,period) VALUES(?,?,?,?,?,?,?)",
                    (request_id, seq, text, _s(it.get("indicator")), _s(it.get("base_date")), _s(it.get("unit")), _s(it.get("period"))))
    con.commit(); con.close()
    return seq

def delete_request(request_id) -> dict:
    """요구서와 딸린 것 전부 삭제(항목·제출본·값·차이 사유·초안·검토). 지운 개수 반환."""
    con = connect()
    sids = [r[0] for r in con.execute("SELECT id FROM submissions WHERE request_id=?", (request_id,)).fetchall()]
    dids = [r[0] for r in con.execute("SELECT id FROM drafts WHERE request_id=?", (request_id,)).fetchall()]
    n = {"submissions": len(sids), "drafts": len(dids)}
    if dids: con.execute(f"DELETE FROM reviews WHERE draft_id IN ({','.join('?' * len(dids))})", dids)
    con.execute("DELETE FROM drafts WHERE request_id=?", (request_id,))
    if sids:
        q = ",".join("?" * len(sids))
        n["values"] = con.execute(f"DELETE FROM submission_values WHERE submission_id IN ({q})", sids).rowcount
        con.execute(f"DELETE FROM diff_reasons WHERE submission_id IN ({q})", sids)
    con.execute("DELETE FROM submissions WHERE request_id=?", (request_id,))
    n["items"] = con.execute("DELETE FROM items WHERE request_id=?", (request_id,)).rowcount
    con.execute("DELETE FROM requests WHERE id=?", (request_id,))
    con.commit(); con.close(); return n

def delete_submission(submission_id) -> dict:
    """제출본과 그 값·차이 사유, 그 제출본으로 만든 초안·검토 삭제."""
    con = connect()
    dids = [r[0] for r in con.execute("SELECT id FROM drafts WHERE submission_id=?", (submission_id,)).fetchall()]
    if dids: con.execute(f"DELETE FROM reviews WHERE draft_id IN ({','.join('?' * len(dids))})", dids)
    con.execute("DELETE FROM drafts WHERE submission_id=?", (submission_id,))
    nv = con.execute("DELETE FROM submission_values WHERE submission_id=?", (submission_id,)).rowcount
    con.execute("DELETE FROM diff_reasons WHERE submission_id=?", (submission_id,))
    con.execute("DELETE FROM submissions WHERE id=?", (submission_id,))
    con.commit(); con.close(); return {"values": nv, "drafts": len(dids)}

def _int_or_none(v):
    try: return int(float(v)) if _s(v) is not None else None
    except (TypeError, ValueError): return None

def replace_values(submission_id, values) -> int:
    """제출본의 값 전체 교체(기록 조회에서 표를 고쳐 저장할 때). 지표·센터·기준일·값이 비면 그 행은 버림."""
    con = connect()
    con.execute("DELETE FROM submission_values WHERE submission_id=?", (submission_id,))
    rows = []
    for v in _norm_rows(values):
        rows.append((submission_id, v["indicator"], v["center"], v["base_date"], v["value"], _s(v.get("definition")), _s(v.get("calc_period")),
                     _s(v.get("extract_date")), _s(v.get("source_version")), _s(v.get("source_file")), _s(v.get("source_sheet")),
                     _int_or_none(v.get("source_row"))))
    con.executemany("INSERT INTO submission_values(submission_id,indicator,center,base_date,value,definition,calc_period,extract_date,source_version,source_file,source_sheet,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close(); return len(rows)

def delete_draft(draft_id):
    con = connect()
    con.execute("DELETE FROM reviews WHERE draft_id=?", (draft_id,)); con.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    con.commit(); con.close()

def list_requests():
    con = connect()
    rows = con.execute("SELECT * FROM requests ORDER BY received_date DESC, id DESC").fetchall()
    con.close(); return [dict(r) for r in rows]

def get_items(request_id):
    con = connect()
    rows = con.execute("SELECT * FROM items WHERE request_id=? ORDER BY seq", (request_id,)).fetchall()
    con.close(); return [dict(r) for r in rows]

# ---------- 제출본 ----------
def add_submission(request_id, submitted_date, submitted_by, file_name, status, note, values):
    con = connect()
    cur = con.execute(
        "INSERT INTO submissions(request_id,submitted_date,submitted_by,file_name,status,note,created_at) VALUES(?,?,?,?,?,?,?)",
        (request_id, submitted_date, submitted_by, file_name, status, note, now()))
    sid = cur.lastrowid
    rows = _norm_rows(values)
    con.executemany(
        "INSERT INTO submission_values(submission_id,indicator,center,base_date,value,definition,calc_period,extract_date,source_version,source_file,source_sheet,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [(sid, v["indicator"], v["center"], v["base_date"], v["value"], _s(v.get("definition")), _s(v.get("calc_period")),
          _s(v.get("extract_date")), _s(v.get("source_version")), _s(v.get("source_file")), _s(v.get("source_sheet")), _int_or_none(v.get("source_row"))) for v in rows])
    if status == "confirmed": _advance(con, request_id, "확정")
    con.commit(); con.close()
    return sid

def list_submissions(request_id=None):
    con = connect()
    q = "SELECT s.*, r.title AS request_title, r.requester FROM submissions s JOIN requests r ON r.id=s.request_id"
    rows = con.execute(q + (" WHERE s.request_id=?" if request_id else "") + " ORDER BY s.submitted_date DESC",
                       (request_id,) if request_id else ()).fetchall()
    con.close(); return [dict(r) for r in rows]

def get_values(submission_id):
    con = connect()
    rows = con.execute("SELECT * FROM submission_values WHERE submission_id=?", (submission_id,)).fetchall()
    con.close(); return [dict(r) for r in rows]

def past_values_for(indicator, base_date):
    """같은 지표·기준일로 과거에 제출한 값 전부(제출본 정보 포함)."""
    con = connect()
    rows = con.execute("""
        SELECT v.*, s.submitted_date, s.file_name, s.status, r.requester, r.title AS request_title
        FROM submission_values v JOIN submissions s ON s.id=v.submission_id JOIN requests r ON r.id=s.request_id
        WHERE v.indicator=? AND v.base_date=? AND s.status='confirmed'
        ORDER BY s.submitted_date DESC, s.id DESC""", (indicator, base_date)).fetchall()
    con.close(); return [dict(r) for r in rows]

def add_reason(submission_id, indicator, center, base_date, old_value, new_value, reason, entered_by):
    con = connect()
    con.execute("INSERT INTO diff_reasons(submission_id,indicator,center,base_date,old_value,new_value,reason,entered_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (submission_id, indicator, center, base_date, old_value, new_value, reason, entered_by, now()))
    con.commit(); con.close()

def reasons_for(indicator, center, base_date):
    con = connect()
    rows = con.execute("SELECT * FROM diff_reasons WHERE indicator=? AND center=? AND base_date=? ORDER BY created_at DESC",
                       (indicator, center, base_date)).fetchall()
    con.close(); return [dict(r) for r in rows]

# ---------- 초안·검토(6층) ----------
def add_draft(submission_id, request_id, d: dict, hwpx_name=None, status="draft"):
    con = connect()
    cur = con.execute("INSERT INTO drafts(submission_id,request_id,title,body,reasons_text,provenance_text,hwpx_name,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (submission_id, request_id, d.get("제목"), d.get("본문"), d.get("차이사유"), d.get("산출근거"), hwpx_name, status, now()))
    did = cur.lastrowid; con.commit(); con.close(); return did

def list_drafts(status=None):
    con = connect()
    q = "SELECT d.*, r.requester, r.title AS request_title, r.due_date FROM drafts d JOIN requests r ON r.id=d.request_id"
    rows = con.execute(q + (" WHERE d.status=?" if status else "") + " ORDER BY d.id DESC", (status,) if status else ()).fetchall()
    con.close(); return [dict(r) for r in rows]

def set_draft_status(draft_id, status):
    con = connect(); con.execute("UPDATE drafts SET status=? WHERE id=?", (status, draft_id)); con.commit(); con.close()

def add_review(draft_id, reviewer, decision, comment):
    con = connect()
    con.execute("INSERT INTO reviews(draft_id,reviewer,decision,comment,created_at) VALUES(?,?,?,?,?)", (draft_id, reviewer, decision, comment, now()))
    if decision in ("approved", "rejected"):
        con.execute("UPDATE drafts SET status=? WHERE id=?", (decision, draft_id))
    con.commit(); con.close()

def reviews_for(draft_id):
    con = connect()
    rows = con.execute("SELECT * FROM reviews WHERE draft_id=? ORDER BY created_at", (draft_id,)).fetchall()
    con.close(); return [dict(r) for r in rows]

def get_request(request_id):
    con = connect(); r = con.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone(); con.close()
    return dict(r) if r else None

def latest_confirmed_values(request_id):
    con = connect()
    s = con.execute("SELECT id FROM submissions WHERE request_id=? AND status='confirmed' ORDER BY submitted_date DESC, id DESC LIMIT 1", (request_id,)).fetchone()
    con.close()
    return (s["id"], get_values(s["id"])) if s else (None, [])

def all_values_sample(limit=2000):
    """입력 서식용: 최근 제출값 일부(센터 목록 뽑기)."""
    con = connect()
    rows = con.execute("SELECT indicator, center, base_date FROM submission_values ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close(); return [dict(r) for r in rows]

# ---------- 지표 데이터(미리 넣어 두는 집계값) ----------
def add_data_batch(values, loaded_by, source, note="") -> tuple[int, int]:
    """집계값 묶음 적재. 같은 지표·센터·기준일이 이미 있으면 그 행을 '대체됨(active=0)'으로 두고 새 값을 활성으로 넣는다(지우지 않으므로 묶음을 되돌리면 이전 값이 살아난다).
    묶음 안의 중복 키는 마지막 것만. (batch_id, 저장 건수) 반환."""
    con = connect()
    rows = _norm_rows(values)
    cur = con.execute("INSERT INTO data_batches(loaded_at,loaded_by,source,note,n_rows) VALUES(?,?,?,?,?)", (now(), loaded_by, source, note, len(rows)))
    bid = cur.lastrowid
    con.executemany("UPDATE indicator_data SET active=0, superseded_by=? WHERE active=1 AND indicator=? AND center=? AND base_date=?", [(bid, v["indicator"], v["center"], v["base_date"]) for v in rows])
    con.executemany("INSERT INTO indicator_data(batch_id,indicator,center,base_date,value,definition,calc_period,extract_date,source_version,source_file,source_sheet,source_row,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    [(bid, v["indicator"], v["center"], v["base_date"], v["value"], _s(v.get("definition")), _s(v.get("calc_period")), _s(v.get("extract_date")),
                      _s(v.get("source_version")), _s(v.get("source_file")), _s(v.get("source_sheet")), _int_or_none(v.get("source_row"))) for v in rows])
    con.commit(); con.close(); return bid, len(rows)

def data_replaced_count(values) -> int:
    """이 값들을 넣으면 이미 있는 활성 행 몇 건이 바뀌는지(미리보기용)."""
    rows = _norm_rows(values)
    if not rows: return 0
    con = connect(); n = 0
    for v in rows:
        n += con.execute("SELECT COUNT(*) FROM indicator_data WHERE active=1 AND indicator=? AND center=? AND base_date=?", (v["indicator"], v["center"], v["base_date"])).fetchone()[0]
    con.close(); return n

def data_values_for(indicator, base_date):
    con = connect()
    rows = con.execute("SELECT d.*, b.loaded_at, b.source FROM indicator_data d JOIN data_batches b ON b.id=d.batch_id WHERE d.active=1 AND d.indicator=? AND d.base_date=? ORDER BY d.center", (indicator, base_date)).fetchall()
    con.close(); return [dict(r) for r in rows]

def data_coverage():
    """지표 × 기준일별 건수와 최근 적재일(화면의 '무엇이 들어 있나' 표)."""
    con = connect()
    rows = con.execute("""SELECT d.indicator, d.base_date, COUNT(*) AS n_centers, MAX(b.loaded_at) AS loaded_at, MAX(b.source) AS source
                          FROM indicator_data d JOIN data_batches b ON b.id=d.batch_id WHERE d.active=1 GROUP BY d.indicator, d.base_date ORDER BY d.indicator, d.base_date DESC""").fetchall()
    con.close(); return [dict(r) for r in rows]

def data_dates_for(indicator):
    con = connect()
    rows = con.execute("SELECT DISTINCT base_date FROM indicator_data WHERE active=1 AND indicator=? ORDER BY base_date DESC", (indicator,)).fetchall()
    con.close(); return [r[0] for r in rows]

def list_data_batches():
    con = connect()
    rows = con.execute("SELECT b.*, (SELECT COUNT(*) FROM indicator_data d WHERE d.batch_id=b.id AND d.active=1) AS n_live FROM data_batches b ORDER BY b.id DESC").fetchall()
    con.close(); return [dict(r) for r in rows]

def delete_data_batch(batch_id) -> int:
    """묶음 삭제: 이 묶음이 대체했던 이전 값을 되살린다. (이 묶음의 행이 이미 더 뒤 묶음에 대체됐으면 그 사슬을 이어 준다.)"""
    con = connect()
    mine = [dict(r) for r in con.execute("SELECT id, indicator, center, base_date, active, superseded_by FROM indicator_data WHERE batch_id=?", (batch_id,)).fetchall()]
    n = con.execute("DELETE FROM indicator_data WHERE batch_id=?", (batch_id,)).rowcount        # 먼저 지워야 되살리는 행이 고유 인덱스와 부딪히지 않는다
    for r in mine:
        if r["active"]: con.execute("UPDATE indicator_data SET active=1, superseded_by=NULL WHERE superseded_by=? AND indicator=? AND center=? AND base_date=?", (batch_id, r["indicator"], r["center"], r["base_date"]))
        else: con.execute("UPDATE indicator_data SET superseded_by=? WHERE superseded_by=? AND indicator=? AND center=? AND base_date=?", (r["superseded_by"], batch_id, r["indicator"], r["center"], r["base_date"]))
    con.execute("DELETE FROM data_batches WHERE id=?", (batch_id,))
    con.commit(); con.close(); return n

def past_dates_for(indicator) -> list[str]:
    """과거에 낸(확정) 값이 있는 기준일 목록."""
    con = connect()
    rows = con.execute("SELECT DISTINCT v.base_date FROM submission_values v JOIN submissions s ON s.id=v.submission_id WHERE v.indicator=? AND s.status='confirmed' AND v.base_date IS NOT NULL ORDER BY v.base_date", (indicator,)).fetchall()
    con.close(); return [r[0] for r in rows]

def data_count() -> int:
    con = connect(); n = con.execute("SELECT COUNT(*) FROM indicator_data WHERE active=1").fetchone()[0]; con.close(); return n

# ---------- 통계 ----------
def request_overview():
    """요구별 현황: 항목 수, 제출본 수, 확정 여부"""
    con = connect()
    rows = con.execute("""
        SELECT r.id, r.requester, r.received_date, r.due_date, r.title, r.status, r.assignee, r.memo, r.updated_at,
               (SELECT COUNT(*) FROM items i WHERE i.request_id=r.id) AS n_items,
               (SELECT COUNT(*) FROM submissions s WHERE s.request_id=r.id AND s.status='confirmed') AS n_confirmed,
               (SELECT MAX(submitted_date) FROM submissions s WHERE s.request_id=r.id AND s.status='confirmed') AS last_submitted
        FROM requests r ORDER BY r.received_date DESC""").fetchall()
    con.close(); return [dict(r) for r in rows]

def requester_stats():
    """요청 주체별 요구 건수·항목 수, 지표별 반복 횟수"""
    con = connect()
    by_req = con.execute("""
        SELECT r.requester, COUNT(DISTINCT r.id) AS n_requests, COUNT(i.id) AS n_items,
               MIN(r.received_date) AS first_date, MAX(r.received_date) AS last_date
        FROM requests r LEFT JOIN items i ON i.request_id=r.id GROUP BY r.requester ORDER BY n_requests DESC""").fetchall()
    by_ind = con.execute("""
        SELECT i.indicator, COUNT(*) AS n_times, COUNT(DISTINCT r.requester) AS n_requesters,
               GROUP_CONCAT(DISTINCT i.base_date) AS base_dates
        FROM items i JOIN requests r ON r.id=i.request_id WHERE i.indicator IS NOT NULL
        GROUP BY i.indicator ORDER BY n_times DESC""").fetchall()
    con.close(); return [dict(r) for r in by_req], [dict(r) for r in by_ind]

def all_items_with_requests():
    """유사 검색용: 항목 + 요구서 정보를 한 번에"""
    con = connect()
    rows = con.execute("""SELECT i.item_text, i.indicator, i.base_date, r.id AS request_id, r.requester, r.received_date, r.title
                          FROM items i JOIN requests r ON r.id=i.request_id""").fetchall()
    con.close(); return [dict(r) for r in rows]

def reset():
    """기록 전체 삭제: 파일을 지우지 않고 모든 표를 비운다(WAL·복제가 파일을 잡고 있어도 안전). 지우기 전에 백업 사본을 남긴다."""
    import shutil
    if DB_PATH.exists():
        bdir = DB_PATH.parent / "backup"; bdir.mkdir(parents=True, exist_ok=True)
        try: shutil.copy2(DB_PATH, bdir / f"history_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        except OSError: pass
    con = connect()
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    with con:
        for t in tables: con.execute(f"DELETE FROM {t}")
        con.execute("DELETE FROM sqlite_sequence")
    con.close()
    _center_cache.update(ver=None, map={})

# ---------- 보조 기능용(사유 후보·이력 질의) ----------
def all_reasons(indicator=None, limit=50, center=None, base_date=None):
    """입력된 차이 사유 기록(최근순). indicator·center·base_date로 좁힐 수 있음."""
    con = connect()
    q = "SELECT d.*, s.submitted_date, r.requester FROM diff_reasons d LEFT JOIN submissions s ON s.id=d.submission_id LEFT JOIN requests r ON r.id=s.request_id"
    conds, args = [], []
    if indicator: conds.append("d.indicator=?"); args.append(indicator)
    if center: conds.append("d.center LIKE ?"); args.append(f"%{center}%")
    if base_date: conds.append("d.base_date=?"); args.append(base_date)
    if conds: q += " WHERE " + " AND ".join(conds)
    rows = con.execute(q + " ORDER BY d.created_at DESC LIMIT ?", (*args, limit)).fetchall()
    con.close(); return [dict(r) for r in rows]

def search_requests(keyword: str, limit=20):
    """요청 주체·제목·원문·항목 원문·지표에 키워드가 든 요구서."""
    k = f"%{(keyword or '').strip()}%"
    con = connect()
    rows = con.execute("""
        SELECT DISTINCT r.id, r.requester, r.received_date, r.due_date, r.title FROM requests r
        LEFT JOIN items i ON i.request_id=r.id
        WHERE r.requester LIKE ? OR r.title LIKE ? OR r.raw_text LIKE ? OR i.item_text LIKE ? OR i.indicator LIKE ?
        ORDER BY r.received_date DESC LIMIT ?""", (k, k, k, k, k, limit)).fetchall()
    con.close(); return [dict(r) for r in rows]

def request_detail(request_id: int):
    """요구서 + 항목 + 제출본(값 건수) + 초안·검토."""
    r = get_request(request_id)
    if not r: return None
    r = {k: v for k, v in r.items() if k != "raw_text"} | {"raw_text": (r.get("raw_text") or "")[:1500]}
    r["items"] = get_items(request_id)
    subs = []
    for s in list_submissions(request_id):
        vals = get_values(s["id"])
        subs.append({k: s[k] for k in ("id", "submitted_date", "submitted_by", "file_name", "status", "note")} | {"n_values": len(vals), "indicators": sorted({v["indicator"] for v in vals}), "base_dates": sorted({v["base_date"] for v in vals})})
    r["submissions"] = subs
    r["drafts"] = [{k: d[k] for k in ("id", "status", "title", "hwpx_name", "created_at")} | {"reviews": reviews_for(d["id"])} for d in list_drafts() if d["request_id"] == request_id]
    return r


# ---------- 센터 명부 ----------
def replace_centers(rows: list[dict], source: str) -> int:
    """센터 명부 전체 교체(대시보드 저장 파일을 다시 가져올 때). aliases는 리스트로 받아 JSON으로 저장. 빈 목록이면 기존 명부를 지우지 않는다."""
    import json
    if not rows: return center_count()
    con = connect(); con.execute("DELETE FROM centers"); _center_cache.update(ver=None, map={})
    con.executemany("""INSERT INTO centers(center_id,name,aliases,year25,year26,status,edu,region,facility,type,size,open_date,capacity,weekend,weekend_days,source,loaded_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(_s(r.get("center_id")), _s(r.get("name")), json.dumps(r.get("aliases") or [], ensure_ascii=False), 1 if r.get("year25") else 0, 1 if r.get("year26") else 0, _s(r.get("status")),
                      _s(r.get("edu")), _s(r.get("region")), _s(r.get("facility")), _s(r.get("type")), _s(r.get("size")), _s(r.get("open_date")), _int_or_none(r.get("capacity")),
                      _s(r.get("weekend")), _int_or_none(r.get("weekend_days")), source, now()) for r in rows if _s(r.get("center_id")) and _s(r.get("name"))])
    con.commit(); n = con.execute("SELECT COUNT(*) FROM centers").fetchone()[0]; con.close(); return n

def list_centers(q: str | None = None, limit: int = 200) -> list[dict]:
    import json
    con = connect()
    if q:
        like = f"%{q}%"
        rows = con.execute("SELECT * FROM centers WHERE name LIKE ? OR aliases LIKE ? OR region LIKE ? OR edu LIKE ? OR type LIKE ? OR facility LIKE ? OR status LIKE ? ORDER BY center_id LIMIT ?", (like, like, like, like, like, like, like, limit)).fetchall()
    else: rows = con.execute("SELECT * FROM centers ORDER BY center_id LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        try: d["aliases"] = json.loads(d.get("aliases") or "[]")
        except ValueError: d["aliases"] = []
        out.append(d)
    return out

def center_count() -> int:
    con = connect(); n = con.execute("SELECT COUNT(*) FROM centers").fetchone()[0]; con.close(); return n

# ---------- 참고 문서 ----------
def add_ref_doc(title, file_name, pages, chunks: list[tuple[int, int, str]], uploaded_by, note="") -> int:
    """chunks: [(page, seq, text)]. 같은 제목의 문서가 있으면 교체."""
    con = connect()
    for (old,) in con.execute("SELECT id FROM ref_docs WHERE title=?", (title,)).fetchall():
        con.execute("DELETE FROM ref_chunks WHERE doc_id=?", (old,)); con.execute("DELETE FROM ref_docs WHERE id=?", (old,))
    cur = con.execute("INSERT INTO ref_docs(title,file_name,pages,chars,uploaded_by,uploaded_at,note) VALUES(?,?,?,?,?,?,?)",
                      (title, file_name, pages, sum(len(t) for _, _, t in chunks), uploaded_by, now(), note))
    did = cur.lastrowid
    con.executemany("INSERT INTO ref_chunks(doc_id,page,seq,text) VALUES(?,?,?,?)", [(did, p, q, t) for p, q, t in chunks])
    con.commit(); con.close(); return did

def list_ref_docs() -> list[dict]:
    con = connect()
    rows = con.execute("SELECT d.*, (SELECT COUNT(*) FROM ref_chunks c WHERE c.doc_id=d.id) AS n_chunks FROM ref_docs d ORDER BY d.id").fetchall()
    con.close(); return [dict(r) for r in rows]

def delete_ref_doc(doc_id) -> None:
    con = connect(); con.execute("DELETE FROM ref_chunks WHERE doc_id=?", (doc_id,)); con.execute("DELETE FROM ref_docs WHERE id=?", (doc_id,)); con.commit(); con.close()

def ref_chunks(doc_id=None) -> list[dict]:
    con = connect()
    q = "SELECT c.id, c.doc_id, c.page, c.seq, c.text, d.title FROM ref_chunks c JOIN ref_docs d ON d.id=c.doc_id"
    rows = con.execute(q + (" WHERE c.doc_id=?" if doc_id else "") + " ORDER BY c.doc_id, c.page, c.seq", (doc_id,) if doc_id else ()).fetchall()
    con.close(); return [dict(r) for r in rows]


# ---------- 발송 기록 ----------
def add_dispatch(submission_id, draft_id, sent_at, sent_to, method, sent_by, note="") -> int:
    con = connect()
    cur = con.execute("INSERT INTO dispatches(submission_id,draft_id,sent_at,sent_to,method,sent_by,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (submission_id, draft_id, norm_date(sent_at, keep_unparsed=True), _s(sent_to), _s(method), _s(sent_by), _s(note), now()))
    rid = con.execute("SELECT request_id FROM submissions WHERE id=?", (submission_id,)).fetchone()
    if rid: _advance(con, rid[0], "발송")
    con.commit(); did = cur.lastrowid; con.close(); return did

def dispatches_for(submission_id) -> list[dict]:
    con = connect(); rows = con.execute("SELECT * FROM dispatches WHERE submission_id=? ORDER BY id DESC", (submission_id,)).fetchall(); con.close(); return [dict(r) for r in rows]

def dispatched_request_ids() -> set:
    con = connect(); rows = con.execute("SELECT DISTINCT s.request_id FROM dispatches d JOIN submissions s ON s.id=d.submission_id").fetchall(); con.close(); return {r[0] for r in rows}

# ---------- 조직 설정 ----------
SETTING_DEFAULTS = {"dept_head": "", "dept_phone": "", "org_name": "지역교육협력부", "company": "한국교육방송공사"}

def get_settings() -> dict:
    """조직 설정(기록 DB) + 기본값. 부서장·내선이 비어 있으면 환경변수 DEPT_HEAD·DEPT_PHONE로 보충."""
    con = connect(); rows = con.execute("SELECT key, value FROM settings").fetchall(); con.close()
    out = dict(SETTING_DEFAULTS); out.update({r[0]: r[1] for r in rows if r[1] not in (None, "")})
    for k, env in (("dept_head", "DEPT_HEAD"), ("dept_phone", "DEPT_PHONE")):
        if not out.get(k): out[k] = os.environ.get(env, "")
    return out

def set_settings(values: dict, user: str = "") -> None:
    con = connect()
    with con:
        con.executemany("INSERT INTO settings(key,value,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                        [(k, str(v or "").strip(), now(), user) for k, v in values.items()])
    con.close()

# ---------- 자주 쓰는 문구 ----------
def add_phrase(kind: str, text, user: str = "") -> int | None:
    """문구 저장(같은 문구면 사용 횟수만 +1). 빈 문구는 무시."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t: return None
    con = connect()
    with con:
        con.execute("INSERT INTO phrases(kind,text,created_by,created_at,last_used,use_count) VALUES(?,?,?,?,?,1) ON CONFLICT(kind,text) DO UPDATE SET use_count=use_count+1, last_used=excluded.last_used",
                    (kind, t, user, now(), now()))
        pid = con.execute("SELECT id FROM phrases WHERE kind=? AND text=?", (kind, t)).fetchone()[0]
    con.close(); return pid

def list_phrases(kind: str | None = None, limit: int = 100) -> list[dict]:
    con = connect()
    rows = con.execute("SELECT * FROM phrases" + (" WHERE kind=?" if kind else "") + " ORDER BY use_count DESC, last_used DESC LIMIT ?", ((kind, limit) if kind else (limit,))).fetchall()
    con.close(); return [dict(r) for r in rows]

def delete_phrase(pid) -> None:
    con = connect(); con.execute("DELETE FROM phrases WHERE id=?", (pid,)); con.commit(); con.close()

def phrase_candidates(kind: str = "사유", limit: int = 12) -> list[str]:
    """서랍에 넣을 문구: 저장된 문구(많이 쓴 순) + 아직 저장되지 않은 최근 차이 사유."""
    out = [p["text"] for p in list_phrases(kind, limit)]
    if kind == "사유" and len(out) < limit:
        con = connect()
        rows = con.execute("SELECT reason FROM diff_reasons WHERE reason IS NOT NULL AND TRIM(reason) <> '' ORDER BY created_at DESC LIMIT 60").fetchall(); con.close()
        for (r,) in rows:
            t = re.sub(r"\s+", " ", r).strip()
            if t and t not in out: out.append(t)
            if len(out) >= limit: break
    return out

# ---------- 백업·복원 ----------
def snapshot_bytes() -> bytes:
    """일관된 사본: SQLite 백업 API로 임시 파일에 복사한 뒤 읽는다(WAL에만 있는 변경까지 포함)."""
    import tempfile
    from pathlib import Path as _P
    src = connect()
    with tempfile.TemporaryDirectory() as d:
        p = _P(d) / "snapshot.db"; dst = sqlite3.connect(p); src.backup(dst); dst.close(); data = p.read_bytes()
    src.close(); return data

def backup_dir():
    d = DB_PATH.parent / "backup"; d.mkdir(parents=True, exist_ok=True); return d

def backup_now(tag: str = "manual"):
    """서버 안 backup/ 폴더에 사본 파일을 만든다. 경로 반환."""
    p = backup_dir() / f"history_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.db"
    p.write_bytes(snapshot_bytes()); return p

def list_backups(limit: int = 10) -> list[dict]:
    files = sorted(backup_dir().glob("history_*.db"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    return [{"name": f.name, "size_kb": f.stat().st_size // 1024, "modified": dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")} for f in files]

def restore_from_bytes(data: bytes, user: str = "") -> dict:
    """올린 DB 파일로 기록을 통째로 되돌린다. 먼저 현재 기록을 backup/에 남기고, 백업 API로 페이지를 옮겨 쓴다
    (파일을 바꿔치기하지 않으므로 WAL·클라우드 복제가 깨지지 않는다). 구버전 파일이면 스키마를 보완한다."""
    import tempfile
    from pathlib import Path as _P
    if not data.startswith(b"SQLite format 3\x00"): raise ValueError("SQLite 데이터베이스 파일이 아닙니다.")
    with tempfile.TemporaryDirectory() as d:
        p = _P(d) / "upload.db"; p.write_bytes(data); src = sqlite3.connect(p)
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"requests", "submissions"} <= tables: src.close(); raise ValueError("이 앱의 기록 DB가 아닙니다(requests·submissions 표가 없음).")
        counts = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("requests", "submissions", "indicator_data", "ref_docs") if t in tables}
        before = backup_now("before_restore")
        dst = connect(); src.backup(dst); dst.close(); src.close()
    _initialized.discard(str(DB_PATH)); connect().close()          # 새 표·열이 없는 옛 파일이면 여기서 보완된다
    _center_cache.update(ver=None, map={})
    return {"counts": counts, "backup": before.name}

def db_info() -> dict:
    con = connect(); t = {}
    for name, q in (("요구서", "SELECT COUNT(*) FROM requests"), ("제출본", "SELECT COUNT(*) FROM submissions"), ("지표 데이터", "SELECT COUNT(*) FROM indicator_data WHERE active=1"),
                    ("참고 문서", "SELECT COUNT(*) FROM ref_docs"), ("발송 기록", "SELECT COUNT(*) FROM dispatches")):
        try: t[name] = con.execute(q).fetchone()[0]
        except sqlite3.DatabaseError: t[name] = None
    con.close()
    st_ = DB_PATH.stat() if DB_PATH.exists() else None
    return {"path": str(DB_PATH), "size_kb": (st_.st_size // 1024) if st_ else 0, "modified": dt.datetime.fromtimestamp(st_.st_mtime).strftime("%Y-%m-%d %H:%M") if st_ else "-",
            "tables": t, "replica": os.environ.get("LITESTREAM_REPLICA_URL", "")}

# ---------- 요구 건 상태 흐름 ----------
REQUEST_STATUSES = ["접수", "처리 중", "확정", "발송", "종결", "보류"]
_RANK = {"보류": -1, "접수": 0, "처리 중": 1, "확정": 2, "발송": 3, "종결": 4}

def _advance(con, request_id, to: str) -> None:
    """자동 전이: 뒤로 가지 않는다(종결된 건은 그대로, 보류 건은 일이 다시 움직이면 앞으로)."""
    cur = con.execute("SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
    if cur is None: return
    if _RANK.get(cur[0] or "접수", 0) < _RANK[to]:
        con.execute("UPDATE requests SET status=?, updated_at=? WHERE id=?", (to, now(), request_id))

def advance_request_status(request_id, to: str) -> None:
    con = connect(); _advance(con, request_id, to); con.commit(); con.close()

def set_request_status(request_id, status=None, assignee=None, memo=None) -> None:
    """담당자가 직접 바꾸는 상태·담당자·메모(None은 그대로)."""
    sets, args = [], []
    if status is not None:
        if status not in REQUEST_STATUSES: raise ValueError(f"상태는 {', '.join(REQUEST_STATUSES)} 중 하나")
        sets.append("status=?"); args.append(status)
    if assignee is not None: sets.append("assignee=?"); args.append(_s(assignee))
    if memo is not None: sets.append("memo=?"); args.append(_s(memo))
    if not sets: return
    sets.append("updated_at=?"); args += [now(), request_id]
    con = connect(); con.execute(f"UPDATE requests SET {', '.join(sets)} WHERE id=?", args); con.commit(); con.close()

# ---------- 요청 주체 사전 ----------
def requester_key(name) -> str:
    """비교 키: 괄호 안(위원회 등)·공백·기호·대소문자를 무시('○○○ 의원실(교육위원회)' == '○○○의원실')."""
    s = re.sub(r"\([^)]*\)|（[^）]*）", "", str(name or ""))
    return re.sub(r"[\s·,.\-_/]", "", s).lower()

_req_cache: dict = {"ver": None, "map": {}}
def _requester_map() -> dict:
    import json
    con = connect(); ver = con.execute("SELECT COUNT(*), MAX(id), MAX(created_at) FROM requesters").fetchone()
    if tuple(ver) != _req_cache["ver"]:
        mp = {}
        for r in con.execute("SELECT name, aliases FROM requesters").fetchall():
            mp[requester_key(r[0])] = r[0]
            try:
                for a in json.loads(r[1] or "[]"): mp[requester_key(a)] = r[0]
            except ValueError: pass
        _req_cache.update(ver=tuple(ver), map=mp)
    con.close(); return _req_cache["map"]

def canonical_requester(name):
    """사전에 있는 표기(이름·별칭)면 표준 이름으로, 없으면 공백만 정리한 원래 이름."""
    if name is None: return None
    s = re.sub(r"\s+", " ", str(name)).strip()
    if not s: return s
    try: return _requester_map().get(requester_key(s), s)
    except sqlite3.DatabaseError: return s

def list_requesters() -> list[dict]:
    import json
    con = connect(); rows = con.execute("SELECT * FROM requesters ORDER BY name").fetchall(); con.close()
    out = []
    for r in rows:
        d = dict(r)
        try: d["aliases"] = json.loads(d.get("aliases") or "[]")
        except ValueError: d["aliases"] = []
        out.append(d)
    return out

def upsert_requester(name, aliases=None, kind=None, note=None) -> int | None:
    import json
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n: return None
    al = list(dict.fromkeys(re.sub(r"\s+", " ", str(a)).strip() for a in (aliases or []) if str(a).strip() and requester_key(a) != requester_key(n)))
    con = connect()
    with con:
        con.execute("INSERT INTO requesters(name,aliases,kind,note,created_at) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET aliases=excluded.aliases, kind=COALESCE(excluded.kind, kind), note=COALESCE(excluded.note, note)",
                    (n, json.dumps(al, ensure_ascii=False), _s(kind), _s(note), now()))
        rid = con.execute("SELECT id FROM requesters WHERE name=?", (n,)).fetchone()[0]
    con.close(); _req_cache.update(ver=None, map={}); return rid

def delete_requester(rid) -> None:
    con = connect(); con.execute("DELETE FROM requesters WHERE id=?", (rid,)); con.commit(); con.close(); _req_cache.update(ver=None, map={})

def requester_names() -> list[str]:
    """자동완성 후보: 사전 이름 + 기록에 있는 요청 주체(사전 표준명 우선, 중복 제거)."""
    con = connect(); rows = con.execute("SELECT DISTINCT requester FROM requests WHERE requester IS NOT NULL AND TRIM(requester) <> '' ORDER BY requester").fetchall(); con.close()
    out = [r["name"] for r in list_requesters()]
    for (n,) in rows:
        c = canonical_requester(n)
        if c and c not in out: out.append(c)
    return out

def apply_requester_canon() -> int:
    """기존 기록의 요청 주체 표기를 사전 기준으로 통일. 바뀐 요구서 수 반환."""
    con = connect(); n = 0
    for rid, name in con.execute("SELECT id, requester FROM requests").fetchall():
        c = canonical_requester(name)
        if c and c != name: con.execute("UPDATE requests SET requester=?, updated_at=? WHERE id=?", (c, now(), rid)); n += 1
    con.commit(); con.close(); return n

# ---------- 지표 사전·카탈로그(화면 편집) ----------
def get_indicator_dict() -> list[dict]:
    import json
    con = connect(); rows = con.execute("SELECT * FROM indicator_dict ORDER BY rowid").fetchall(); con.close()
    out = []
    for r in rows:
        d = dict(r)
        try: d["aliases"] = json.loads(d.get("aliases") or "[]")
        except ValueError: d["aliases"] = []
        out.append(d)
    return out

def save_indicator_dict(rows: list[dict], user: str = "") -> int:
    """사전 전체를 교체(빈 지표명은 버림, 동의어는 중복 제거). 저장한 지표 수 반환. 빈 목록이면 전부 지워 코드 기본값으로 돌아간다."""
    import json
    con = connect()
    with con:
        con.execute("DELETE FROM indicator_dict")
        n = 0
        for r in rows:
            canon = re.sub(r"\s+", " ", str(r.get("canon") or "")).strip()
            if not canon: continue
            al = r.get("aliases") or []
            if isinstance(al, str): al = [a.strip() for a in re.split(r"[,，;/\n]", al)]
            al = list(dict.fromkeys(a for a in (str(x).strip() for x in al) if a and a != canon))
            con.execute("INSERT OR REPLACE INTO indicator_dict(canon,aliases,source,owner,note,updated_at) VALUES(?,?,?,?,?,?)",
                        (canon, json.dumps(al, ensure_ascii=False), _s(r.get("source")), _s(r.get("owner")), _s(r.get("note")), now())); n += 1
    con.close(); return n

# ---------- 화면 캐시용 변경 신호 · 보유 현황 ----------
def signature() -> tuple:
    """기록 DB가 바뀌었는지 알리는 값(파일·WAL의 수정 시각·크기). 화면 캐시 키로 써서, 쓰기가 있으면 다음 실행에서 다시 읽는다."""
    out = []
    for suf in ("", "-wal"):
        try: st_ = Path(str(DB_PATH) + suf).stat(); out.append((st_.st_mtime_ns, st_.st_size))
        except OSError: out.append(None)
    return tuple(out)

def data_matrix() -> list[dict]:
    """지표 × 기준일 센터 수(활성 행). 보유 현황 히트맵용."""
    con = connect()
    rows = con.execute("SELECT indicator, base_date, COUNT(*) AS n FROM indicator_data WHERE active=1 GROUP BY indicator, base_date ORDER BY indicator, base_date").fetchall()
    con.close(); return [dict(r) for r in rows]

def missing_centers(indicator: str, base_date: str) -> list[str]:
    """명부(취소된 곳 제외)에는 있는데 그 지표·기준일 값이 없는 센터 이름."""
    con = connect()
    have = {center_key(r[0]) for r in con.execute("SELECT center FROM indicator_data WHERE active=1 AND indicator=? AND base_date=?", (indicator, base_date)).fetchall()}
    names = [r[0] for r in con.execute("SELECT name FROM centers WHERE COALESCE(status,'') NOT LIKE '%취소%' ORDER BY center_id").fetchall()]
    con.close(); return [n for n in names if center_key(n) not in have]

# ---------- 회신 톤 설정 ----------
def get_tone_presets() -> dict:
    """{유형: {doc_label, show_confirm, style, mail_greeting}} — 설정 화면에서 고친 값만(기본값은 draft.DEFAULT_PRESETS)."""
    con = connect(); rows = con.execute("SELECT * FROM tone_presets").fetchall(); con.close()
    return {r["kind"]: {"doc_label": r["doc_label"], "show_confirm": None if r["show_confirm"] is None else bool(r["show_confirm"]), "style": r["style"], "mail_greeting": r["mail_greeting"]} for r in rows}

def save_tone_presets(rows: list[dict]) -> int:
    con = connect()
    with con:
        con.execute("DELETE FROM tone_presets")
        n = 0
        for r in rows:
            kind = str(r.get("kind") or "").strip()
            if not kind: continue
            con.execute("INSERT OR REPLACE INTO tone_presets(kind,doc_label,show_confirm,style,mail_greeting,updated_at) VALUES(?,?,?,?,?,?)",
                        (kind, _s(r.get("doc_label")), None if r.get("show_confirm") is None else int(bool(r.get("show_confirm"))), _s(r.get("style")), _s(r.get("mail_greeting")), now())); n += 1
    con.close(); return n

def requester_kind(name) -> str | None:
    """요청 주체 사전에 적힌 유형(이름·별칭으로 찾음). 없으면 None."""
    if not name: return None
    canon = canonical_requester(name)
    con = connect(); r = con.execute("SELECT kind FROM requesters WHERE name=?", (canon,)).fetchone(); con.close()
    return r[0] if r and r[0] else None

def list_dispatches() -> list[dict]:
    con = connect(); rows = con.execute("SELECT d.*, s.request_id FROM dispatches d JOIN submissions s ON s.id=d.submission_id ORDER BY d.sent_at, d.id").fetchall(); con.close()
    return [dict(r) for r in rows]
