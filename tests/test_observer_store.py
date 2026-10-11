from aiswarm.observer.store import Judgment, ObserverStore


def judgment(status="ok", **kw):
    base = dict(analysis="themes", run_id="r1", agent_id="a", status=status, result={"themes": []} if status == "ok"
                else None, error=None if status == "ok" else "boom", model_reported="claude-x", prompt_sha256="p")
    base.update(kw)
    return Judgment(**base)


def test_cache_returns_only_ok_judgments(tmp_path):
    store = ObserverStore(tmp_path / "observer" / "observer.db")
    store.save("k1", judgment(), "sonnet")
    store.save("k2", judgment("failed", agent_id="b"), "sonnet")
    assert store.cached("k1") == judgment()
    assert store.cached("k2") is None
    assert [j.status for j in store.failed()] == ["failed"]


def test_save_replaces_a_failed_judgment(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.save("k", judgment("failed"), "sonnet")
    store.save("k", judgment(), "sonnet")
    assert store.failed() == [] and store.cached("k") is not None


def test_judgments_by_run_and_analysis(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.save("k1", judgment(), "sonnet")
    store.save("k2", judgment(analysis="self_image"), "sonnet")
    store.save("k3", judgment(run_id="r2"), "sonnet")
    assert len(store.judgments("r1")) == 2
    assert [j.analysis for j in store.judgments("r1", "self_image")] == ["self_image"]


def test_codebook_assign_and_lookup(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("pragmatism", "pragmatism", "Favours what works.")
    store.assign("practical focus", "pragmatism", "ignored: code exists")
    assert store.code_for("practical focus") == "pragmatism"
    assert store.code_for("unknown") is None
    assert store.codes() == [("pragmatism", "Favours what works.")]


def test_reopening_keeps_data(tmp_path):
    path = tmp_path / "o.db"
    ObserverStore(path).assign("x", "x", "d")
    assert ObserverStore(path).code_for("x") == "x"


def test_a_new_judgment_replaces_the_old_one_for_the_same_unit(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.save("old", judgment("failed"), "sonnet")
    store.save("new", judgment(result={"themes": ["fresh"]}), "sonnet")
    assert [j.result for j in store.judgments("r1")] == [{"themes": ["fresh"]}]
    assert store.failed() == []
    store.save("other-agent", judgment(agent_id="b"), "sonnet")
    store.save("codebook", judgment(analysis="codebook", agent_id=None), "sonnet")
    store.save("codebook2", judgment(analysis="codebook", agent_id=None), "sonnet")
    assert sorted((j.analysis, j.agent_id) for j in store.judgments("r1")) == [
        ("codebook", None), ("themes", "a"), ("themes", "b")]
