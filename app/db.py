"""SQLite 이력 저장소. 요청 → 항목 → 제출본 → 제출값(산출 근거 포함) → 차이 사유."""
import sqlite3, os, datetime as dt
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
        for suffix in ("", "-journal"):
            f = Path(str(DB_PATH) + suffix)
            if f.exists():
                try: f.rename(str(f) + ".broken")
                except OSError: pass
        return connect(_retry=False)

def _migrate(con):
    """구버전 DB에 새 컬럼 추가"""
    cols = {r[1] for r in con.execute("PRAGMA table_info(submission_values)")}
    for c, t in [("source_file", "TEXT"), ("source_sheet", "TEXT"), ("source_row", "INTEGER")]:
        if c not in cols:
            con.execute(f"ALTER TABLE submission_values ADD COLUMN {c} {t}")
    icols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    if "period" not in icols:
        con.execute("ALTER TABLE items ADD COLUMN period TEXT")
    con.commit()

def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------- 요구서 ----------
def add_request(requester, received_date, due_date, title, raw_text, source_file, items):
    con = connect()
    cur = con.execute(
        "INSERT INTO requests(requester,received_date,due_date,title,raw_text,source_file,created_at) VALUES(?,?,?,?,?,?,?)",
        (requester, received_date, due_date, title, raw_text, source_file, now()))
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
    con.execute("UPDATE requests SET requester=?, received_date=?, due_date=?, title=? WHERE id=?",
                (_s(requester), _s(received_date), _s(due_date), _s(title), request_id))
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
    """제출본의 값 전체 교체(이력 조회에서 표를 고쳐 저장할 때). 지표·센터·기준일·값이 비면 그 행은 버림."""
    con = connect()
    con.execute("DELETE FROM submission_values WHERE submission_id=?", (submission_id,))
    rows = []
    for v in values:
        if not (_s(v.get("indicator")) and _s(v.get("center")) and _s(v.get("base_date"))) or v.get("value") is None or v.get("value") != v.get("value"): continue
        rows.append((submission_id, _s(v["indicator"]), _s(v["center"]), _s(v["base_date"]), float(v["value"]), _s(v.get("definition")), _s(v.get("calc_period")),
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
    con.executemany(
        "INSERT INTO submission_values(submission_id,indicator,center,base_date,value,definition,calc_period,extract_date,source_version,source_file,source_sheet,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [(sid, v["indicator"], v["center"], v["base_date"], v["value"], v.get("definition"), v.get("calc_period"),
          v.get("extract_date"), v.get("source_version"), v.get("source_file"), v.get("source_sheet"), v.get("source_row")) for v in values])
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
        ORDER BY s.submitted_date DESC""", (indicator, base_date)).fetchall()
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
    """집계값 묶음 적재. 같은 지표·센터·기준일이 이미 있으면 새 값으로 대체(이전 행 삭제). (batch_id, 저장 건수) 반환."""
    con = connect()
    rows = [v for v in values if _s(v.get("indicator")) and _s(v.get("center")) and _s(v.get("base_date")) and v.get("value") is not None and v.get("value") == v.get("value")]
    cur = con.execute("INSERT INTO data_batches(loaded_at,loaded_by,source,note,n_rows) VALUES(?,?,?,?,?)", (now(), loaded_by, source, note, len(rows)))
    bid = cur.lastrowid
    con.executemany("DELETE FROM indicator_data WHERE indicator=? AND center=? AND base_date=?", [(_s(v["indicator"]), _s(v["center"]), _s(v["base_date"])) for v in rows])
    con.executemany("INSERT INTO indicator_data(batch_id,indicator,center,base_date,value,definition,calc_period,extract_date,source_version,source_file,source_sheet,source_row) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(bid, _s(v["indicator"]), _s(v["center"]), _s(v["base_date"]), float(v["value"]), _s(v.get("definition")), _s(v.get("calc_period")), _s(v.get("extract_date")),
                      _s(v.get("source_version")), _s(v.get("source_file")), _s(v.get("source_sheet")), _int_or_none(v.get("source_row"))) for v in rows])
    con.commit(); con.close(); return bid, len(rows)

def data_values_for(indicator, base_date):
    con = connect()
    rows = con.execute("SELECT d.*, b.loaded_at, b.source FROM indicator_data d JOIN data_batches b ON b.id=d.batch_id WHERE d.indicator=? AND d.base_date=? ORDER BY d.center", (indicator, base_date)).fetchall()
    con.close(); return [dict(r) for r in rows]

def data_coverage():
    """지표 × 기준일별 건수와 최근 적재일(화면의 '무엇이 들어 있나' 표)."""
    con = connect()
    rows = con.execute("""SELECT d.indicator, d.base_date, COUNT(*) AS n_centers, MAX(b.loaded_at) AS loaded_at, MAX(b.source) AS source
                          FROM indicator_data d JOIN data_batches b ON b.id=d.batch_id GROUP BY d.indicator, d.base_date ORDER BY d.indicator, d.base_date DESC""").fetchall()
    con.close(); return [dict(r) for r in rows]

def data_dates_for(indicator):
    con = connect()
    rows = con.execute("SELECT DISTINCT base_date FROM indicator_data WHERE indicator=? ORDER BY base_date DESC", (indicator,)).fetchall()
    con.close(); return [r[0] for r in rows]

def list_data_batches():
    con = connect()
    rows = con.execute("SELECT b.*, (SELECT COUNT(*) FROM indicator_data d WHERE d.batch_id=b.id) AS n_live FROM data_batches b ORDER BY b.id DESC").fetchall()
    con.close(); return [dict(r) for r in rows]

def delete_data_batch(batch_id) -> int:
    con = connect()
    n = con.execute("DELETE FROM indicator_data WHERE batch_id=?", (batch_id,)).rowcount
    con.execute("DELETE FROM data_batches WHERE id=?", (batch_id,))
    con.commit(); con.close(); return n

def data_count() -> int:
    con = connect(); n = con.execute("SELECT COUNT(*) FROM indicator_data").fetchone()[0]; con.close(); return n

# ---------- 통계 ----------
def request_overview():
    """요구별 현황: 항목 수, 제출본 수, 확정 여부"""
    con = connect()
    rows = con.execute("""
        SELECT r.id, r.requester, r.received_date, r.due_date, r.title,
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
    _initialized.discard(str(DB_PATH))
    if DB_PATH.exists():
        DB_PATH.unlink()

# ---------- 보조 기능용(사유 후보·이력 질의) ----------
def all_reasons(indicator=None, limit=50):
    """입력된 차이 사유 기록(최근순). indicator로 좁힐 수 있음."""
    con = connect()
    q = "SELECT d.*, s.submitted_date, r.requester FROM diff_reasons d LEFT JOIN submissions s ON s.id=d.submission_id LEFT JOIN requests r ON r.id=s.request_id"
    args = ()
    if indicator: q += " WHERE d.indicator=?"; args = (indicator,)
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
