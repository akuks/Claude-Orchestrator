"""0009_tokens: task_usage, task_questions, tasks.auto_continued and
projects.fresh_session_tokens; upgrade from 0008 keeps old rows, downgrade removes it all."""

from alembic import command

from app.database import _alembic_cfg
from test_migrations import _inspect, scratch_url  # noqa: F401 - the fixture


def test_migration_0009(scratch_url):  # noqa: F811
    url = scratch_url
    command.upgrade(_alembic_cfg(url), "0008")

    def seed(sc, insp):
        sc.exec_driver_sql(
            "INSERT INTO tasks (id, title, prompt, status, priority, tags, model, max_turns, attempt,"
            " allow_remote, read_only, requires_approval, risk, created_at) VALUES ('t9', 'old', 'p',"
            " 'completed', 'normal', '[]', '', 25, 0, true, false, false, 'info', CURRENT_TIMESTAMP)")
        sc.exec_driver_sql(
            "INSERT INTO projects (id, name, slug, directory, default_model, env, memory,"
            " memory_enabled, archived, auto_review_prs, created_at)"
            " VALUES ('p9', 'old', 'old', '/x', 'sonnet', '{}', '', true, false, false, CURRENT_TIMESTAMP)")

    _inspect(url, seed)
    command.upgrade(_alembic_cfg(url), "head")

    def check_head(sc, insp):
        assert {"task_id", "root_id", "context_tokens", "peak_context", "est_cost_usd", "breakdown", "bands",
                "updated_at"} <= {c["name"] for c in insp.get_columns("task_usage")}
        assert {"id", "task_id", "root_id", "project_id", "question", "options", "recommendation", "blocks",
                "reversible", "source", "status", "answer", "answered_by", "answered_at", "followup_task_id",
                "created_at"} <= {c["name"] for c in insp.get_columns("task_questions")}
        assert ("root_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("task_usage")}
        assert ("root_id",) in {tuple(i["column_names"]) for i in insp.get_indexes("task_questions")}
        fks = insp.get_foreign_keys("task_usage")
        assert fks and fks[0]["referred_table"] == "tasks" and fks[0]["options"].get("ondelete") == "CASCADE"
        fks = insp.get_foreign_keys("task_questions")
        assert fks and fks[0]["referred_table"] == "tasks" and fks[0]["options"].get("ondelete") == "CASCADE"
        q = sc.exec_driver_sql
        assert q("SELECT auto_continued FROM tasks WHERE id='t9'").scalar() in (False, 0)
        assert q("SELECT fresh_session_tokens FROM projects WHERE id='p9'").scalar() is None
        q("INSERT INTO task_usage (task_id, root_id, updated_at) VALUES ('t9', 't9', CURRENT_TIMESTAMP)")
        q("INSERT INTO task_questions (id, task_id, root_id, question, source, created_at)"
          " VALUES ('q9', 't9', 't9', 'which?', 'agent', CURRENT_TIMESTAMP)")
        assert q("SELECT status FROM task_questions").scalar() == "open"
        return q("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_head) == "0009"
    command.downgrade(_alembic_cfg(url), "0008")

    def check_down(sc, insp):
        assert not ({"task_usage", "task_questions"} & set(insp.get_table_names()))
        assert "auto_continued" not in {c["name"] for c in insp.get_columns("tasks")}
        assert "fresh_session_tokens" not in {c["name"] for c in insp.get_columns("projects")}
        return sc.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    assert _inspect(url, check_down) == "0008"
