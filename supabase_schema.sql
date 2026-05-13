-- Optional: The application creates these tables automatically at startup
-- when DATABASE_URL points to Supabase/PostgreSQL with sufficient privileges.
-- You can also run this in Supabase SQL Editor if you prefer manual setup.

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
