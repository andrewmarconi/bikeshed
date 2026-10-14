"""Insert community records directly, for Observer tests."""

import json

T0 = "2026-10-01T00:00:00+00:00"


def add_run(conn, run_id, agents=("a", "b", "c"), *, created=T0, status="closed", ended="budget",
            goal="Pick a fruit.", goal_id=None, category=None, benchmark=False, process=1):
    conn.execute(
        "INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at, goal_id, goal_category, is_benchmark, "
        "process) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, goal, status, ended if status == "closed" else None, json.dumps(list(agents)), created, goal_id,
         category, int(benchmark), process),
    )
    return run_id


def add_entry(conn, run_id, author, type_, payload, refs=(), round=1, created=T0, visible_to=None, to="all"):
    cursor = conn.execute(
        "INSERT INTO entries (run_id, round, author, type, to_, refs, payload, created_at, visible_to) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, round, author, type_, to, json.dumps(list(refs)), json.dumps(payload), created,
         json.dumps(list(visible_to)) if visible_to else None),
    )
    return cursor.lastrowid


def add_review(conn, run_id, reviewer, reviewee, overall, *, more_of="more", less_of="less", note="note",
               reason="reason", created=T0):
    cursor = conn.execute(
        "INSERT INTO reviews (run_id, reviewer, reviewee, is_self, more_of, less_of, private_note, evidence, overall, "
        "overall_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)",
        (run_id, reviewer, reviewee, int(reviewer == reviewee), more_of, less_of, note, overall, reason, created),
    )
    return cursor.lastrowid


def add_journal(conn, run_id, agent_id, section, text, created=T0):
    conn.execute("INSERT INTO journals (run_id, agent_id, section, text, created_at) VALUES (?, ?, ?, ?, ?)",
                 (run_id, agent_id, section, text, created))


def add_briefs(conn, run_id, briefs):
    from bikeshed.briefs import save

    save(conn, run_id, briefs)


def add_conflict(conn, run_id, call_id, constraint_id):
    conn.execute("INSERT INTO scorer_conflicts (run_id, call_id, constraint_id) VALUES (?, ?, ?)",
                 (run_id, call_id, constraint_id))
