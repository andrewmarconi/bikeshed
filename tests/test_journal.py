from bikeshed.journal import journal_section, write_journal


def test_journal_sections_render_to_one_file_and_replace_on_rewrite(community):
    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('r1', 'g', 'closed', '[]', 't')")
    write_journal(conn, store, "a", "r1", "before", "First thoughts.")
    write_journal(conn, store, "a", "r1", "after", "After reviews.")
    write_journal(conn, store, "a", "r1", "before", "Revised first thoughts.")
    path = store.root / "agents" / "a" / "journal" / "r1.md"
    assert path.read_text() == (
        "# Run r1\n\n## Before reviews\n\nRevised first thoughts.\n\n## After reviews\n\nAfter reviews.\n"
    )
    assert journal_section(conn, "a", "r1", "before") == "Revised first thoughts."
    assert journal_section(conn, "a", "r1", "after") == "After reviews."
    assert journal_section(conn, "b", "r1", "before") is None


def test_journals_returns_latest_files_oldest_first(community):
    store, conn = community
    for run_id in ("20260101-000000-aaaa", "20260102-000000-bbbb", "20260103-000000-cccc"):
        store.write_journal_file("a", run_id, f"# Run {run_id}\n")
    assert [run_id for run_id, _ in store.journals("a", 2)] == ["20260102-000000-bbbb", "20260103-000000-cccc"]
    assert store.journals("b", 2) == []
