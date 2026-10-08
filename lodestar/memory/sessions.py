"""Session persistence schema."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_id INTEGER,
    task_id TEXT,
    evidence TEXT NOT NULL DEFAULT '[]'
);
"""
