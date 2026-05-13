from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import os
import re
import sqlite3
from urllib.parse import urlparse

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # PostgreSQL is optional for local SQLite tests.
    psycopg = None
    dict_row = None
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "gtep_survey.db"
SEED_PATH = BASE_DIR / "seed_data.csv"
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or ""
SECRET_KEY = os.getenv("GTEP_SECRET_KEY", "change-this-secret-key-before-production")
ADMIN_ID = os.getenv("GTEP_ADMIN_ID", "admin")
ADMIN_PASSWORD = os.getenv("GTEP_ADMIN_PASSWORD", "5750!")

app = FastAPI(title="GTEP Survey System")

STOPWORDS = set("""
팀 활동 학생 박람회 직무팀 박람회팀 생각 부분 과정 준비 현장 많이 조금 매우 그리고 하지만 또한 있어서 통해 대한 경우 같다 같은 관련 우리 본인 해당 동안 정말 서로 함께 역할 업무 진행 중국 린이 평가 작성 구체 사례 중심 전체 아쉬운 점 잘한 보완 필요
""".split())

POSITIVE_WORDS = set("""
성실 책임감 주도 적극 자발 협력 배려 공유 소통 정확 빠르게 꼼꼼 도움 해결 대응 준비 분석 기여 원활 효율 열심히 완수 개선 제안 정리 지원 친절 안정 신속
""".split())
NEGATIVE_WORDS = set("""
부족 미흡 아쉬움 지연 늦게 소극 혼선 누락 어려움 불명확 문제 불편 부족했다 아쉬웠다 개선필요 책임부족 소통부족 참여부족
""".split())
COMPETENCY_KEYWORDS = {
    "책임감": ["책임", "마감", "완수", "꾸준", "성실", "빠짐", "맡은"],
    "주도성": ["주도", "먼저", "자발", "제안", "찾아서", "앞장"],
    "협업성": ["협력", "도움", "도와", "공유", "배려", "조율", "함께"],
    "소통능력": ["소통", "전달", "설명", "피드백", "회의", "연락", "통역"],
    "준비도": ["자료", "조사", "분석", "정리", "제품", "시장", "사전", "카탈로그"],
    "문제해결력": ["해결", "대응", "대안", "수습", "돌발", "문제", "조정"],
    "현장기여도": ["바이어", "응대", "상담", "부스", "통역", "기업", "지원", "현장"],
}

QUESTIONS = {
    "job_team": "1월부터 5월까지 본인의 직무팀 활동을 기준으로, 팀이 잘 수행한 점과 아쉬웠던 점을 구체적인 사례 중심으로 작성해주세요.",
    "job_member": "이 팀원이 직무팀 활동에서 실제로 기여한 점, 협업 태도, 보완할 점을 구체적인 행동이나 사례 중심으로 작성해주세요.",
    "fair_team": "본인의 박람회팀이 준비기간과 중국 린이 박람회 현장에서 수행한 활동 중 잘한 점과 아쉬웠던 점을 구체적인 사례 중심으로 작성해주세요.",
    "fair_member": "이 팀원이 박람회 준비기간과 현장에서 실제로 수행한 역할, 기여한 점, 보완할 점을 구체적인 장면이나 행동 중심으로 작성해주세요.",
    "other_fair_team": "이 박람회팀이 현장에서 기업 지원, 바이어 응대, 통역, 제품 설명, 팀 간 협력 등에서 보여준 활동을 구체적인 관찰 사례 중심으로 작성해주세요. 잘한 점과 아쉬운 점이 있다면 함께 작성해주세요.",
}
MIN_CHARS = {
    "job_team": 150,
    "job_member": 120,
    "fair_team": 150,
    "fair_member": 120,
    "other_fair_team": 120,
}

class LoginIn(BaseModel):
    username: str
    password: str

class StudentLogin(BaseModel):
    student_id: str
    name: str

class AdminLogin(BaseModel):
    username: str
    password: str

class ResponseIn(BaseModel):
    target_type: str
    target_id: str
    response_text: str
    writing_time_sec: int = 0
    paste_count: int = 0

class SubmitIn(BaseModel):
    responses: list[ResponseIn]


def normalize_database_url(url: str) -> str:
    """Supabase requires SSL. Add sslmode=require when the URL looks remote and has no sslmode."""
    if not url:
        return url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    is_local = host in {"localhost", "127.0.0.1", ""}
    if not is_local and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        return url + sep + "sslmode=require"
    return url


DATABASE_URL = normalize_database_url(DATABASE_URL)
USE_POSTGRES = bool(DATABASE_URL)


class PgConnection:
    """Small compatibility wrapper so the app can run on SQLite locally or Supabase/Postgres in production."""

    def __init__(self) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg is not installed. Run: pip install -r requirements.txt")
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def execute(self, sql: str, params: tuple | list = ()):
        # Existing application queries use SQLite-style ? placeholders.
        # psycopg expects %s placeholders.
        return self.conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, script: str) -> None:
        with self.conn.cursor() as cur:
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.conn.close()


def db():
    if USE_POSTGRES:
        return PgConnection()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(payload: dict[str, Any], ttl_sec: int = 60 * 60 * 8) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_sec
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    sig = hmac.new(SECRET_KEY.encode(), raw, hashlib.sha256).digest()
    return f"{b64(raw)}.{b64(sig)}"


def verify_token(token: str) -> dict[str, Any]:
    try:
        raw_s, sig_s = token.split(".", 1)
        raw = unb64(raw_s)
        sig = unb64(sig_s)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    good = hmac.new(SECRET_KEY.encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, good):
        raise HTTPException(status_code=401, detail="Invalid token")
    payload = json.loads(raw.decode())
    if payload.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


def current_user(authorization: Optional[str]) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    return verify_token(authorization.replace("Bearer ", "", 1))


def init_db() -> None:
    sqlite_schema = """
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                job_team TEXT NOT NULL,
                fair_team TEXT NOT NULL,
                submitted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evaluator_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                question_text TEXT NOT NULL,
                response_text TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                writing_time_sec INTEGER DEFAULT 0,
                paste_count INTEGER DEFAULT 0,
                specificity_score REAL DEFAULT 0,
                evidence_score REAL DEFAULT 0,
                sentiment_score REAL DEFAULT 0,
                reliability_score REAL DEFAULT 0,
                competency_tags TEXT DEFAULT '[]',
                keywords TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(evaluator_id, target_type, target_id)
            );
            """
    postgres_schema = """
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                job_team TEXT NOT NULL,
                fair_team TEXT NOT NULL,
                submitted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS responses (
                id BIGSERIAL PRIMARY KEY,
                evaluator_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                question_text TEXT NOT NULL,
                response_text TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                writing_time_sec INTEGER DEFAULT 0,
                paste_count INTEGER DEFAULT 0,
                specificity_score DOUBLE PRECISION DEFAULT 0,
                evidence_score DOUBLE PRECISION DEFAULT 0,
                sentiment_score DOUBLE PRECISION DEFAULT 0,
                reliability_score DOUBLE PRECISION DEFAULT 0,
                competency_tags TEXT DEFAULT '[]',
                keywords TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(evaluator_id, target_type, target_id)
            );
            CREATE INDEX IF NOT EXISTS idx_responses_evaluator ON responses(evaluator_id);
            CREATE INDEX IF NOT EXISTS idx_responses_target ON responses(target_type, target_id);
            """
    with db() as con:
        con.executescript(postgres_schema if USE_POSTGRES else sqlite_schema)
        count = con.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
        if count == 0:
            with open(SEED_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    con.execute(
                        "INSERT INTO students(student_id,name,department,job_team,fair_team) VALUES(?,?,?,?,?)",
                        (row["student_id"], row["name"], row["department"], row["job_team"], row["fair_team"]),
                    )
        con.commit()


def get_students() -> list[dict[str, Any]]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM students ORDER BY job_team, fair_team, name")]


def get_student(student_id: str) -> dict[str, Any]:
    with db() as con:
        row = con.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Student not found")
    return dict(row)


def build_targets(student: dict[str, Any]) -> list[dict[str, Any]]:
    students = get_students()
    fair_teams = sorted({s["fair_team"] for s in students})
    targets: list[dict[str, Any]] = []
    targets.append({"target_type": "job_team", "target_id": student["job_team"], "target_label": f"직무팀: {student['job_team']}", "question": QUESTIONS["job_team"], "min_chars": MIN_CHARS["job_team"]})
    for s in students:
        if s["job_team"] == student["job_team"] and s["student_id"] != student["student_id"]:
            targets.append({"target_type": "job_member", "target_id": s["student_id"], "target_label": f"직무팀 팀원: {s['name']}", "question": QUESTIONS["job_member"], "min_chars": MIN_CHARS["job_member"]})
    targets.append({"target_type": "fair_team", "target_id": student["fair_team"], "target_label": f"본인 박람회팀: {student['fair_team']}", "question": QUESTIONS["fair_team"], "min_chars": MIN_CHARS["fair_team"]})
    for s in students:
        if s["fair_team"] == student["fair_team"] and s["student_id"] != student["student_id"]:
            targets.append({"target_type": "fair_member", "target_id": s["student_id"], "target_label": f"박람회팀 팀원: {s['name']}", "question": QUESTIONS["fair_member"], "min_chars": MIN_CHARS["fair_member"]})
    for team in fair_teams:
        if team != student["fair_team"]:
            targets.append({"target_type": "other_fair_team", "target_id": team, "target_label": f"다른 박람회팀: {team}", "question": QUESTIONS["other_fair_team"], "min_chars": MIN_CHARS["other_fair_team"]})
    return targets


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
    return [w for w in words if w not in STOPWORDS and not w.isdigit()]


def analyze_text(text: str, writing_time_sec: int, paste_count: int) -> dict[str, Any]:
    tokens = tokenize(text)
    token_counts = Counter(tokens)
    lower = text.lower()
    has_specific_marker = sum(1 for p in ["첫", "둘째", "오전", "오후", "부스", "바이어", "기업", "상담", "통역", "자료", "카탈로그", "설명", "현장", "제품"] if p in text)
    has_action = sum(1 for p in ["정리", "설명", "전달", "응대", "지원", "조율", "해결", "준비", "도움", "공유", "대응", "기록", "분석"] if p in text)
    has_result = sum(1 for p in ["결과", "덕분", "이어", "줄", "개선", "완료", "도움", "원활", "해결", "성공"] if p in text)
    specificity = min(100, 20 + has_specific_marker * 12 + has_action * 8 + has_result * 10 + min(len(text) / 5, 25))
    evidence = min(100, 15 + has_action * 12 + has_specific_marker * 8 + min(len(tokens) * 1.5, 30))
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    sentiment = max(0, min(100, 55 + pos * 7 - neg * 8))
    tags = []
    for tag, keys in COMPETENCY_KEYWORDS.items():
        if any(k in text for k in keys):
            tags.append(tag)
    if not tags:
        tags = ["일반평가"]
    too_fast_penalty = 15 if len(text) >= 120 and writing_time_sec < 20 else 0
    paste_penalty = min(20, paste_count * 5)
    short_penalty = 20 if len(text) < 120 else 0
    reliability = max(0, min(100, specificity * 0.5 + evidence * 0.4 + min(writing_time_sec / 2, 10) - too_fast_penalty - paste_penalty - short_penalty))
    return {
        "specificity_score": round(specificity, 1),
        "evidence_score": round(evidence, 1),
        "sentiment_score": round(sentiment, 1),
        "reliability_score": round(reliability, 1),
        "competency_tags": tags,
        "keywords": [w for w, _ in token_counts.most_common(8)],
    }


def score_response(row: sqlite3.Row) -> float:
    return (
        row["evidence_score"] * 0.30
        + row["specificity_score"] * 0.20
        + (min(len(json.loads(row["competency_tags"])), 4) / 4 * 100) * 0.25
        + row["sentiment_score"] * 0.10
        + row["reliability_score"] * 0.15
    )


def rankings() -> dict[str, Any]:
    students = get_students()
    student_map = {s["student_id"]: s for s in students}
    fair_teams = sorted({s["fair_team"] for s in students})
    job_teams = sorted({s["job_team"] for s in students})
    with db() as con:
        rows = con.execute("SELECT * FROM responses").fetchall()
    indiv_scores: dict[str, list[float]] = defaultdict(list)
    team_scores: dict[str, list[float]] = defaultdict(list)
    job_scores: dict[str, list[float]] = defaultdict(list)
    mentions: dict[str, int] = defaultdict(int)
    tags_by_target: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        s = score_response(r)
        target_type = r["target_type"]
        target_id = r["target_id"]
        if target_type in ("job_member", "fair_member") and target_id in student_map:
            indiv_scores[target_id].append(s)
            mentions[target_id] += 1
            tags_by_target[target_id].update(json.loads(r["competency_tags"]))
        elif target_type in ("fair_team", "other_fair_team"):
            team_scores[target_id].append(s)
        elif target_type == "job_team":
            job_scores[target_id].append(s)
    individual = []
    for sid, vals in indiv_scores.items():
        st = student_map.get(sid)
        if not st:
            continue
        avg = sum(vals) / len(vals) if vals else 0
        individual.append({
            "student_id": sid,
            "name": st["name"],
            "job_team": st["job_team"],
            "fair_team": st["fair_team"],
            "score": round(avg, 1),
            "mentions": mentions[sid],
            "tags": [x for x, _ in tags_by_target[sid].most_common(3)],
        })
    individual.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(individual, 1):
        item["rank"] = i
    fair = []
    for team in fair_teams:
        vals = team_scores.get(team, [])
        fair.append({"team": team, "score": round(sum(vals) / len(vals), 1) if vals else 0, "responses": len(vals)})
    fair.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(fair, 1):
        item["rank"] = i
    job = []
    for team in job_teams:
        vals = job_scores.get(team, [])
        job.append({"team": team, "score": round(sum(vals) / len(vals), 1) if vals else 0, "responses": len(vals)})
    job.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(job, 1):
        item["rank"] = i
    return {"individual": individual, "fair_teams": fair, "job_teams": job}

@app.on_event("startup")
def startup() -> None:
    init_db()


@app.post("/api/login")
def unified_login(data: LoginIn) -> dict[str, Any]:
    username = data.username.strip()
    password = data.password.strip()
    if username == ADMIN_ID and hmac.compare_digest(password, ADMIN_PASSWORD):
        return {"token": create_token({"role": "admin", "username": username}), "role": "admin"}
    with db() as con:
        row = con.execute("SELECT * FROM students WHERE student_id=? AND name=?", (username, password)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="아이디와 비밀번호를 확인해주세요.")
    return {"token": create_token({"role": "student", "student_id": username}), "role": "student"}

@app.post("/api/student/login")
def student_login(data: StudentLogin) -> dict[str, Any]:
    sid = data.student_id.strip()
    name = data.name.strip()
    with db() as con:
        row = con.execute("SELECT * FROM students WHERE student_id=? AND name=?", (sid, name)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="학번과 이름을 확인해주세요.")
    return {"token": create_token({"role": "student", "student_id": sid}), "student": dict(row)}

@app.post("/api/admin/login")
def admin_login(data: AdminLogin) -> dict[str, str]:
    if data.username != ADMIN_ID or not hmac.compare_digest(data.password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="관리자 계정을 확인해주세요.")
    return {"token": create_token({"role": "admin", "username": data.username})}

@app.get("/api/student/me")
def student_me(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    user = current_user(authorization)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Student only")
    st = get_student(user["student_id"])
    with db() as con:
        saved = con.execute("SELECT target_type,target_id,response_text,writing_time_sec,paste_count FROM responses WHERE evaluator_id=?", (st["student_id"],)).fetchall()
    return {"student": st, "targets": build_targets(st), "responses": [dict(r) for r in saved]}

@app.post("/api/student/save")
def save_response(data: ResponseIn, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    user = current_user(authorization)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Student only")
    st = get_student(user["student_id"])
    if st.get("submitted_at"):
        raise HTTPException(status_code=400, detail="이미 최종 제출되었습니다.")
    target_map = {(t["target_type"], t["target_id"]): t for t in build_targets(st)}
    if (data.target_type, data.target_id) not in target_map:
        raise HTTPException(status_code=400, detail="평가 대상이 올바르지 않습니다.")
    t = target_map[(data.target_type, data.target_id)]
    analysis = analyze_text(data.response_text, data.writing_time_sec, data.paste_count)
    with db() as con:
        con.execute(
            """
            INSERT INTO responses(evaluator_id,target_type,target_id,question_text,response_text,char_count,writing_time_sec,paste_count,specificity_score,evidence_score,sentiment_score,reliability_score,competency_tags,keywords,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(evaluator_id,target_type,target_id) DO UPDATE SET
                response_text=excluded.response_text,
                char_count=excluded.char_count,
                writing_time_sec=excluded.writing_time_sec,
                paste_count=excluded.paste_count,
                specificity_score=excluded.specificity_score,
                evidence_score=excluded.evidence_score,
                sentiment_score=excluded.sentiment_score,
                reliability_score=excluded.reliability_score,
                competency_tags=excluded.competency_tags,
                keywords=excluded.keywords,
                updated_at=excluded.updated_at
            """,
            (
                st["student_id"], data.target_type, data.target_id, t["question"], data.response_text, len(data.response_text), data.writing_time_sec, data.paste_count,
                analysis["specificity_score"], analysis["evidence_score"], analysis["sentiment_score"], analysis["reliability_score"], json.dumps(analysis["competency_tags"], ensure_ascii=False), json.dumps(analysis["keywords"], ensure_ascii=False), now_iso(), now_iso()
            ),
        )
        con.commit()
    return {"ok": True, "analysis": analysis}

@app.post("/api/student/submit")
def submit(data: SubmitIn, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    user = current_user(authorization)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Student only")
    st = get_student(user["student_id"])
    targets = build_targets(st)
    if st.get("submitted_at"):
        raise HTTPException(status_code=400, detail="이미 최종 제출되었습니다.")
    for item in data.responses:
        save_response(item, authorization)
    with db() as con:
        saved = con.execute("SELECT target_type,target_id,char_count FROM responses WHERE evaluator_id=?", (st["student_id"],)).fetchall()
        saved_map = {(r["target_type"], r["target_id"]): r["char_count"] for r in saved}
        missing = []
        for t in targets:
            if saved_map.get((t["target_type"], t["target_id"]), 0) < t["min_chars"]:
                missing.append(t["target_label"])
        if missing:
            raise HTTPException(status_code=400, detail="최소 글자 수 미달 또는 미작성 항목이 있습니다: " + ", ".join(missing[:5]))
        con.execute("UPDATE students SET submitted_at=? WHERE student_id=?", (now_iso(), st["student_id"]))
        con.commit()
    return {"ok": True}

@app.get("/api/admin/dashboard")
def admin_dashboard(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    user = current_user(authorization)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    students = get_students()
    with db() as con:
        rows = con.execute("SELECT * FROM responses").fetchall()
    submitted = sum(1 for s in students if s.get("submitted_at"))
    total_text = "\n".join(r["response_text"] for r in rows)
    keywords = Counter(tokenize(total_text)).most_common(30)
    avg_chars = round(sum(r["char_count"] for r in rows) / len(rows), 1) if rows else 0
    avg_time = round(sum(r["writing_time_sec"] for r in rows) / len(rows) / 60, 1) if rows else 0
    low_reliability = sum(1 for r in rows if r["reliability_score"] < 50)
    by_status = [{"student_id": s["student_id"], "name": s["name"], "job_team": s["job_team"], "fair_team": s["fair_team"], "submitted_at": s.get("submitted_at")} for s in students]
    return {
        "kpi": {"total_students": len(students), "submitted": submitted, "not_submitted": len(students) - submitted, "submit_rate": round(submitted / len(students) * 100, 1), "responses": len(rows), "avg_chars": avg_chars, "avg_minutes": avg_time, "low_reliability": low_reliability},
        "keywords": [{"word": w, "count": c} for w, c in keywords],
        "rankings": rankings(),
        "submission_status": by_status,
    }

@app.get("/api/admin/responses")
def admin_responses(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    user = current_user(authorization)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    students = get_students()
    student_map = {s["student_id"]: s for s in students}
    with db() as con:
        rows = con.execute(
            """
            SELECT r.*, s.name AS evaluator_name, s.job_team AS evaluator_job_team, s.fair_team AS evaluator_fair_team
            FROM responses r JOIN students s ON r.evaluator_id=s.student_id
            ORDER BY r.updated_at DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        if item["target_type"] in ("job_member", "fair_member") and item["target_id"] in student_map:
            item["target_label"] = student_map[item["target_id"]]["name"]
        else:
            item["target_label"] = item["target_id"]
        out.append(item)
    return {"responses": out}

@app.get("/api/admin/export.csv")
def export_csv(authorization: Optional[str] = Header(default=None)):
    user = current_user(authorization)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    with db() as con:
        rows = con.execute("SELECT * FROM responses ORDER BY evaluator_id,target_type,target_id").fetchall()
    headers = ["evaluator_id", "target_type", "target_id", "response_text", "char_count", "writing_time_sec", "paste_count", "specificity_score", "evidence_score", "sentiment_score", "reliability_score", "competency_tags", "keywords", "updated_at"]
    def stream():
        yield ",".join(headers) + "\n"
        for r in rows:
            vals = []
            for h in headers:
                v = str(r[h] or "").replace('"', '""')
                vals.append(f'"{v}"')
            yield ",".join(vals) + "\n"
    return StreamingResponse(stream(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=gtep_responses.csv"})

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

@app.get("/")
def root():
    return FileResponse(BASE_DIR / "static" / "index.html")
