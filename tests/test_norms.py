from aiswarm.norms import (
    add_norm_critique, add_norm_vote, add_proposal, critiques_of, current_norms, decide, dimensions, proposals,
    render_norms, render_proposal,
)

AGENTS = ["a", "b", "c", "d"]
WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)", "d": "Dee (d)"}.get


def run(conn, run_id="r1"):
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, 'g', 'closed', '[]', 't')", (run_id,))
    return run_id


def support(conn, proposal_id, *authors, stance="support"):
    for author in authors:
        add_norm_vote(conn, proposal_id, author, stance, "because")


def test_proposal_with_quorum_and_an_outside_critique_is_adopted(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Cite entry ids.")
    [p] = proposals(conn, "r1")
    add_norm_critique(conn, p.id, "b", "Too strict?")
    support(conn, p.id, "b", "c")                        # a counts as supporter: a, b, c = 3 of 4
    [decided] = decide(conn, "r1", AGENTS, quorum_slack=1)
    assert decided.status == "adopted"
    assert current_norms(conn) == [(1, "Cite entry ids.")]
    assert render_norms(conn) == "1. Cite entry ids.\n"
    assert critiques_of(conn, p.id) == [("b", "Too strict?")]


def test_unchallenged_or_short_of_quorum_is_rejected(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Unchallenged.")
    add_proposal(conn, run_id="r1", author="b", kind="norm", text="Short.")
    p1, p2 = proposals(conn, "r1")
    add_norm_critique(conn, p1.id, "a", "my own critique does not count")
    support(conn, p1.id, "b", "c", "d")
    add_norm_critique(conn, p2.id, "c", "fine")
    support(conn, p2.id, "c")
    support(conn, p2.id, "b", "d", stance="object")        # b, the author, objects to its own proposal
    assert [p.status for p in decide(conn, "r1", AGENTS, quorum_slack=1)] == ["rejected", "rejected"]
    assert current_norms(conn) == [] and render_norms(conn) == ""
    assert decide(conn, "r1", AGENTS, quorum_slack=1) == []      # nothing left open


def test_amendments_replace_text_and_dimensions_are_unique(community):
    store, conn = community
    run(conn, "r1")
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Old text.")
    add_proposal(conn, run_id="r1", author="b", kind="dimension", name="Credit-giving", question="Do they credit others?")
    for p in proposals(conn, "r1"):
        add_norm_critique(conn, p.id, "d", "ok")
        support(conn, p.id, "a", "b", "c")
    decide(conn, "r1", AGENTS, quorum_slack=1)
    run(conn, "r2")
    add_proposal(conn, run_id="r2", author="c", kind="amendment", target=1, text="New text.")
    add_proposal(conn, run_id="r2", author="d", kind="dimension", name="credit-giving", question="dup")
    for p in proposals(conn, "r2"):
        add_norm_critique(conn, p.id, "a", "ok")
        support(conn, p.id, "a", "b", "c", "d")
    assert [p.status for p in decide(conn, "r2", AGENTS, quorum_slack=1)] == ["adopted", "rejected"]
    assert current_norms(conn) == [(1, "New text.")]
    assert [(d.name, d.adopted_run) for d in dimensions(conn)] == [("Credit-giving", "r1")]
    assert dimensions(conn, exclude_run="r1") == []
    assert render_norms(conn) == '1. New text.\n\n## Rating dimensions\n\n- "Credit-giving": Do they credit others?\n'


def test_a_proposal_can_be_revised_only_while_open(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="First.")
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Second.")
    assert [p.text for p in proposals(conn, "r1")] == ["Second."]
    decide(conn, "r1", AGENTS, quorum_slack=1)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Third.")
    assert [(p.text, p.status) for p in proposals(conn, "r1")] == [("Second.", "rejected")]


def test_render_proposal(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="N.")
    add_proposal(conn, run_id="r1", author="b", kind="amendment", target=1, text="A.")
    add_proposal(conn, run_id="r1", author="c", kind="dimension", name="Care", question="Q?")
    norm, amendment, dimension = proposals(conn, "r1")
    texts = {1: "Current."}
    assert render_proposal(norm, WHO, texts) == f'Proposal {norm.id} by Alpha (a): a new norm: "N."'
    assert render_proposal(amendment, WHO, texts) == f'Proposal {amendment.id} by Bea (b): amend norm 1 (now: "Current.") to: "A."'
    assert render_proposal(dimension, WHO, texts) == f'Proposal {dimension.id} by Cyd (c): a new rating dimension "Care": Q?'


def test_write_community_file_commits(community):
    store, conn = community
    store.write_community_file("norms.md", "1. X.\n", "norms adopted")
    assert (store.root / "norms.md").read_text() == "1. X.\n"
