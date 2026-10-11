"""Command-line interface."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer

from aiswarm.config import DEFAULT_CONFIG, ClaudeCliBackend, Config, load_config
from aiswarm.db import connect
from aiswarm.checkin import checkin
from aiswarm.goals import GOALS_FILE, GoalError, add_goal, load_goals, next_goal, runs_since_benchmark, seed_goals, used_goals
from aiswarm.introduce import introduce
from aiswarm.models.base import ModelError, limit_clients
from aiswarm.models.claude_cli import ClaudeCliClient, leak_check
from aiswarm.models.factory import make_client, make_embedder
from aiswarm.models.openai_compat import OpenAICompatClient
from aiswarm.postrun import PostRun, PostRunResult
from aiswarm.profiles import PRIVATE_FILES, ProfileError, ProfileStore
from aiswarm.provenance import capture, probe_server, provenance_changes, provenance_rows
from aiswarm.reviews import dimension_reputations, ratings_for, render_review, reputation, responses_for, reviews_of
from aiswarm.runner import Runner
from aiswarm.timeline import resolve_agent, timeline
from aiswarm.view import make_who

app = typer.Typer(no_args_is_help=True, help="A self-organising community of LLM agents.")
agents_app = typer.Typer(no_args_is_help=True, help="Create and inspect agents.")
app.add_typer(agents_app, name="agents")
goals_app = typer.Typer(no_args_is_help=True, help="Manage the goal queue (categories are never shown to agents).")
app.add_typer(goals_app, name="goals")

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to aiswarm.toml.")]
DEFAULT_PATH = Path("aiswarm.toml")


def _fail(message: str) -> None:
    typer.echo(f"error: {message}")
    raise typer.Exit(1)


def _load(config: Path) -> Config:
    if not config.exists():
        _fail(f"{config} not found; run `aiswarm init` first")
    return load_config(config)


@app.command()
def init(config: ConfigOpt = DEFAULT_PATH) -> None:
    """Create a config file (if missing), the community repo, the database, and the runs folder."""
    if not config.exists():
        config.write_text(DEFAULT_CONFIG)
        typer.echo(f"wrote {config} — set models.local.model before using local agents")
    cfg = load_config(config)
    store = ProfileStore(cfg.community_dir)
    if seed_goals(store):
        typer.echo(f"wrote {cfg.community_dir / GOALS_FILE} — the goal queue for `aiswarm run --next`")
    connect(cfg.db_path).close()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"community ready at {cfg.community_dir}")


def _introduce(cfg: Config, store: ProfileStore, agent_id: str) -> None:
    backend = store.record(agent_id).model
    client = make_client(cfg.models[backend], cfg.run.model_retries)
    typer.echo(f"asking {agent_id} ({backend}) to introduce itself…", err=True)
    try:
        profile = asyncio.run(introduce(store, agent_id, client))
    except (ProfileError, ModelError) as exc:
        _fail(f"{exc}. Retry with `aiswarm agents introduce {agent_id}`")
    typer.echo(f"{profile.name} ({agent_id}) — {profile.headline}\n\n{profile.introduction}")


@agents_app.command("new")
def agents_new(
    agent_id: str,
    model: Annotated[str, typer.Option(help="Backend name from \\[models] in the config.")],
    temperament: Annotated[str | None, typer.Option(help="Seed temperament text.")] = None,
    temperament_file: Annotated[Path | None, typer.Option(help="File containing the seed temperament.")] = None,
    control: Annotated[bool, typer.Option("--control", help="A control agent: reset to its seed before every run.")] = False,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Create an agent from a temperament; the agent then writes its own public profile."""
    cfg = _load(config)
    if model not in cfg.models:
        _fail(f"unknown model backend {model!r}; known: {', '.join(cfg.models)}")
    text = temperament_file.read_text() if temperament_file else temperament
    if not text:
        _fail("give --temperament or --temperament-file")
    store = ProfileStore(cfg.community_dir)
    try:
        store.create(agent_id, model, text, control=control)
    except ProfileError as exc:
        _fail(str(exc))
    _introduce(cfg, store, agent_id)


@agents_app.command("introduce")
def agents_introduce(agent_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Ask an existing agent to (re)write its first public profile."""
    cfg = _load(config)
    _introduce(cfg, ProfileStore(cfg.community_dir), agent_id)


@agents_app.command("set-model")
def agents_set_model(agent_id: str, model: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Point an agent at another model backend (not while it is in an unclosed run)."""
    cfg = _load(config)
    if model not in cfg.models:
        _fail(f"unknown model backend {model!r}; known: {', '.join(cfg.models)}")
    store = ProfileStore(cfg.community_dir)
    if agent_id not in store.ids():
        _fail(f"no agent {agent_id!r}")
    conn = connect(cfg.db_path)
    for row in conn.execute("SELECT id, status, agent_ids FROM runs WHERE status != 'closed'"):
        if agent_id in json.loads(row["agent_ids"]):  # provenance recorded its model when that run started
            _fail(f"{agent_id} is in run {row['id']} ({row['status']}); finish it with `aiswarm run --resume {row['id']}`")
    was = store.record(agent_id).model
    store.set_model(agent_id, model)
    typer.echo(f"{agent_id} now uses {model} (was {was})")


@agents_app.command("checkin")
def agents_checkin(
    agent: Annotated[list[str] | None, typer.Option("--agent", help="Agent ids to ask (default: all).")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Ask agents whether they want to change their name, and what pronouns others should use for them."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    agent_ids = agent or store.ids()
    clients = _clients(cfg, store, agent_ids)
    for agent_id in agent_ids:  # one at a time, so two agents cannot claim the same new name at once
        before = store.public(agent_id)
        try:
            profile = asyncio.run(checkin(store, agent_id, clients[agent_id]))
        except (ProfileError, ModelError) as exc:
            typer.echo(f"{agent_id}: no change ({exc})")
            continue
        pronouns = profile.pronouns or "pronouns not stated"
        renamed = f" — formerly {before.name}" if before and before.name != profile.name else ""
        typer.echo(f"{profile.name} ({agent_id}) · {pronouns}{renamed}")


@agents_app.command("list")
def agents_list(config: ConfigOpt = DEFAULT_PATH) -> None:
    """List agents with their public name, backend, and headline."""
    store = ProfileStore(_load(config).community_dir)
    for agent_id in store.ids():
        profile = store.public(agent_id)
        name, headline = (profile.name, profile.headline) if profile else ("(no profile)", "")
        marker = "\tcontrol" if store.record(agent_id).control else ""
        typer.echo(f"{agent_id}\t{name}\t{store.record(agent_id).model}\t{headline}{marker}")


@agents_app.command("show")
def agents_show(agent_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Show an agent's public profile, reputation, private files, reviews, and latest journal (observer view)."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    if agent_id not in store.ids():
        _fail(f"no agent {agent_id!r}")
    conn = connect(cfg.db_path)
    profile = store.public(agent_id)
    if profile:
        pronouns = f" · {profile.pronouns}" if profile.pronouns else ""
        typer.echo(f"# {profile.name} ({agent_id}){pronouns}\n{profile.headline}\n\n{profile.introduction}")
        if profile.former_names:
            typer.echo(f"\nformerly: {', '.join(profile.former_names)}")
    typer.echo(f"\nreputation: {reputation(conn, agent_id, cfg.run.reputation_half_life_runs).label()}")
    for name, rep in dimension_reputations(conn, agent_id, cfg.run.reputation_half_life_runs):
        typer.echo(f"{name}: {rep.label()}")
    private = store.private(agent_id)
    for filename in PRIVATE_FILES:
        typer.echo(f"\n## {filename}\n{private[filename].strip() or '(empty)'}")
    who = make_who({i: store.public(i) for i in store.ids()})
    reviews = reviews_of(conn, agent_id, include_self=True)
    responses = responses_for(conn, [r.id for r in reviews])
    ratings = ratings_for(conn, [r.id for r in reviews])
    typer.echo("\n## Reviews received")
    typer.echo("\n\n".join(render_review(r, who, private=True, response=responses.get(r.id), ratings=ratings[r.id])
                          for r in reviews) or "(none)")
    journals = store.journals(agent_id, 1)
    if journals:
        typer.echo(f"\n## Latest journal\n{journals[0][1].strip()}")


@agents_app.command("timeline")
def agents_timeline(
    agent: Annotated[str, typer.Argument(help="Agent id, current name, or former name.")],
    since: Annotated[str | None, typer.Option(help="Start at this run id.")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """One agent's chronological history: profile diffs, journals, reviews, credits, votes, reasoning (observer view)."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    agent_id = resolve_agent(store, agent)
    if agent_id is None:
        _fail(f"no agent with id or name {agent!r}")
    try:
        typer.echo(timeline(connect(cfg.db_path), store, agent_id, since), nl=False)
    except KeyError:
        _fail(f"no run {since!r}")


def _clients(cfg: Config, store: ProfileStore, agent_ids: list[str]) -> dict[str, Any]:
    for agent_id in agent_ids:
        if agent_id not in store.ids():
            _fail(f"no agent {agent_id!r}")
        if store.public(agent_id) is None:
            _fail(f"{agent_id} has no public profile; run `aiswarm agents introduce {agent_id}`")
        if store.record(agent_id).model not in cfg.models:
            _fail(f"{agent_id} uses unknown model backend {store.record(agent_id).model!r}")
    return {a: make_client(cfg.models[store.record(a).model], cfg.run.model_retries) for a in agent_ids}


_NARRATOR = "#narrator"  # never a valid agent id, so it can share the limited-clients dict


def _support(cfg: Config) -> tuple[Any, Any]:
    narrator = make_client(cfg.models[cfg.report.narrator], cfg.run.model_retries) if cfg.report.narrator else None
    embedder = make_embedder(cfg.models[cfg.library.embeddings]) if cfg.library.embeddings else None
    return narrator, embedder


def _report_postrun(run_id: str, result: PostRunResult) -> None:
    if result.status == "paused":
        typer.echo(f"post-run paused; resume with `aiswarm postrun {run_id}`")
    else:
        typer.echo(f"post-run done ({result.failed} failed steps)")


def _print_event(event: dict[str, Any]) -> None:
    kind = event["event"]
    if kind == "round_started":
        typer.echo(f"— round {event['round']} —")
    elif kind == "action_posted":
        suffix = f" ({event['failure']})" if event.get("failure") else ""
        typer.echo(f"  #{event['entry']} {event['agent']} {event['type']}{suffix}")
    elif kind == "run_paused":
        typer.echo(f"paused: {event['detail']}")
    elif kind == "postrun_started":
        typer.echo("— post-run —")
    elif kind == "postrun_step_finished":
        subject = f" → {event['subject']}" if event.get("subject") else ""
        status = "" if event["status"] == "done" else f" ({event['status']})"
        typer.echo(f"  {event['step']} {event['agent']}{subject}{status}")


@app.command()
def run(
    goal: Annotated[str | None, typer.Argument(help="The goal prompt.")] = None,
    next_: Annotated[bool, typer.Option("--next", help="Take the next goal from the queue (or the benchmark when due).")] = False,
    resume: Annotated[str | None, typer.Option(help="Resume a paused or interrupted run.")] = None,
    agent: Annotated[list[str] | None, typer.Option("--agent", help="Agent ids to include (default: all).")] = None,
    max_rounds: Annotated[int | None, typer.Option(help="Override the round budget.")] = None,
    no_postrun: Annotated[bool, typer.Option("--no-postrun", help="Skip the post-run cycle.")] = False,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Run the community on a goal (or resume a run)."""
    cfg = _load(config)
    if max_rounds is not None:
        cfg.run.max_rounds = max_rounds
    store = ProfileStore(cfg.community_dir)
    conn = connect(cfg.db_path)
    selected = None
    if resume:
        if goal or next_:
            _fail("give --resume or a goal/--next, not both")
        row = conn.execute("SELECT agent_ids FROM runs WHERE id = ?", (resume,)).fetchone()
        if row is None:
            _fail(f"no run {resume!r}")
        agent_ids = json.loads(row["agent_ids"])
    else:
        if next_ and goal:
            _fail("give a goal or --next, not both")
        if next_:
            try:
                selected = next_goal(conn, load_goals(cfg.community_dir / GOALS_FILE))
            except GoalError as exc:
                _fail(str(exc))
            goal = selected.text
            typer.echo(f"goal ({selected.id}, {selected.category}): {goal}")
        if not goal:
            _fail("give a goal, --next, or --resume RUN_ID")
        agent_ids = agent or store.ids()
        if not agent_ids:
            _fail("no agents yet; create some with `aiswarm agents new`")
    clients = _clients(cfg, store, agent_ids)
    backends = {a: store.record(a).model for a in agent_ids}
    narrator, embedder = _support(cfg)

    async def go() -> None:
        limited = limit_clients({**clients, **({_NARRATOR: narrator} if narrator else {})}, cfg.run.max_concurrent_calls)
        narrator_client = limited.pop(_NARRATOR, None)
        runner = Runner(settings=cfg.run, conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                        sink=_print_event, embedder=embedder, library_top_k=cfg.library.top_k)
        try:
            run_id = resume or runner.start(
                goal or "", agent_ids,
                goal_id=selected.id if selected else None,
                goal_category=selected.category if selected else None,
                is_benchmark=bool(selected and selected.is_benchmark),
            )
        except ValueError as exc:
            _fail(str(exc))
        await capture(conn, run_id, "resume" if resume else "start", cfg, backends, probe=probe_server)
        result = await runner.run(run_id)
        if result.status == "paused":
            typer.echo(f"run {run_id} paused; resume with `aiswarm run --resume {run_id}`")
            return
        typer.echo(f"run {run_id} closed: {result.reason}")
        typer.echo(f"report: {cfg.runs_dir / run_id / 'report'}")
        if not no_postrun:
            await capture(conn, run_id, "postrun", cfg, backends, probe=probe_server)
            post = await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                                 narrator=narrator_client, embedder=embedder, quorum_slack=cfg.run.quorum_slack,
                                 sink=_print_event).run(run_id)
            _report_postrun(run_id, post)

    asyncio.run(go())


@goals_app.command("list")
def goals_list(config: ConfigOpt = DEFAULT_PATH) -> None:
    """Show the benchmark, the queue (used and unused), and what `run --next` would take."""
    cfg = _load(config)
    conn = connect(cfg.db_path)
    try:
        gf = load_goals(cfg.community_dir / GOALS_FILE)
    except GoalError as exc:
        _fail(str(exc))
    b = gf.benchmark
    since = runs_since_benchmark(conn)
    if since is None:
        status = "never run; due now"
    else:
        last = conn.execute("SELECT id FROM runs WHERE is_benchmark = 1 ORDER BY rowid DESC LIMIT 1").fetchone()["id"]
        left = b.every - 1 - since
        status = f"last run {last}; " + ("due now" if left <= 0 else f"due after {left} more run{'s' if left != 1 else ''}")
    typer.echo(f"benchmark ({b.category}, every {b.every}): {b.text}\n  {status}\n")
    used = used_goals(conn)
    for g in gf.goals:
        mark = f"used by {used[g.id]}" if g.id in used else "unused"
        typer.echo(f"{g.id}\t{g.category}\t{mark}\t{g.text}")
    try:
        typer.echo(f"\nnext: {next_goal(conn, gf).id}")
    except GoalError as exc:
        typer.echo(f"\nnext: none ({exc})")


@goals_app.command("add")
def goals_add(
    text: str,
    category: Annotated[str, typer.Option(help="neutral, trade-off, or contested (Observer-only).")],
    goal_id: Annotated[str | None, typer.Option("--id", help="Goal id (default: derived from the text).")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Append a goal to the queue."""
    cfg = _load(config)
    try:
        goal = add_goal(ProfileStore(cfg.community_dir), text, category, goal_id)
    except GoalError as exc:
        _fail(str(exc))
    typer.echo(f"added {goal.id} ({goal.category}): {goal.text}")


@app.command()
def postrun(run_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Start, resume, or retry the post-run cycle (journals, reviews, responses, updates) for a closed run."""
    cfg = _load(config)
    store = ProfileStore(cfg.community_dir)
    conn = connect(cfg.db_path)
    row = conn.execute("SELECT status, agent_ids FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        _fail(f"no run {run_id!r}")
    if row["status"] != "closed":
        _fail(f"run {run_id} is not closed; finish it with `aiswarm run --resume {run_id}`")
    clients = _clients(cfg, store, json.loads(row["agent_ids"]))
    backends = {a: store.record(a).model for a in json.loads(row["agent_ids"])}
    narrator, embedder = _support(cfg)

    async def go() -> PostRunResult:
        await capture(conn, run_id, "postrun", cfg, backends, probe=probe_server)
        limited = limit_clients({**clients, **({_NARRATOR: narrator} if narrator else {})}, cfg.run.max_concurrent_calls)
        narrator_client = limited.pop(_NARRATOR, None)
        return await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                             narrator=narrator_client, embedder=embedder, quorum_slack=cfg.run.quorum_slack,
                             sink=_print_event).run(run_id)

    _report_postrun(run_id, asyncio.run(go()))


@app.command()
def show(run_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Print a run's answer and minority report, noting any harness change since the previous run."""
    cfg = _load(config)
    report = cfg.runs_dir / run_id / "report"
    if not report.exists():
        _fail(f"no report for run {run_id!r}")
    conn = connect(cfg.db_path)
    changes = provenance_changes(conn, run_id)
    if changes:
        typer.echo("Harness changed:\n" + "\n".join(f"- {c}" for c in changes) + "\n")
    start = next((p for p in provenance_rows(conn, run_id) if p.phase == "start"), None)
    if start and start.code_dirty:
        typer.echo("Note: this run started from uncommitted AISwarm code.\n")
    for name in ("answer.md", "minority.md", "account.md", "narrative.md"):
        path = report / name
        if path.exists():
            typer.echo(path.read_text())


@app.command()
def doctor(config: ConfigOpt = DEFAULT_PATH) -> None:
    """Check each model backend: oMLX reachable with the model loaded; claude -p isolated (leak check)."""
    cfg = _load(config)
    healthy = True
    for name, backend in cfg.models.items():
        try:
            if isinstance(backend, ClaudeCliBackend):
                client = ClaudeCliClient(backend, workdir=Path(tempfile.mkdtemp(prefix="aiswarm-claude-")))
                ok, tokens = asyncio.run(leak_check(client))
                detail = f"input tokens {tokens} (limit {backend.leak_threshold_tokens})"
                if not backend.bare:
                    detail += (
                        "\n     warning: subscription mode is not fully isolated — Claude Code still injects an SDK"
                        " preamble, environment details, and the account email. Set bare = true with"
                        " ANTHROPIC_API_KEY for clean agents."
                    )
            else:
                models = asyncio.run(OpenAICompatClient(backend).list_models())
                ok = backend.model in models
                detail = f"model {backend.model!r} {'available' if ok else 'not found'}; server lists {len(models)}"
        except ModelError as exc:
            ok, detail = False, str(exc)
        healthy &= ok
        typer.echo(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    if not healthy:
        raise typer.Exit(1)
