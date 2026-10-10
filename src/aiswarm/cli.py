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
from aiswarm.introduce import introduce
from aiswarm.models.base import ModelError, limit_clients
from aiswarm.models.claude_cli import ClaudeCliClient, leak_check
from aiswarm.models.factory import make_client
from aiswarm.models.openai_compat import OpenAICompatClient
from aiswarm.postrun import PostRun, PostRunResult
from aiswarm.profiles import PRIVATE_FILES, ProfileError, ProfileStore
from aiswarm.reviews import dimension_reputations, ratings_for, render_review, reputation, responses_for, reviews_of
from aiswarm.runner import Runner
from aiswarm.view import make_who

app = typer.Typer(no_args_is_help=True, help="A self-organising community of LLM agents.")
agents_app = typer.Typer(no_args_is_help=True, help="Create and inspect agents.")
app.add_typer(agents_app, name="agents")

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
    ProfileStore(cfg.community_dir)
    connect(cfg.db_path).close()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"community ready at {cfg.community_dir}")


def _introduce(cfg: Config, store: ProfileStore, agent_id: str) -> None:
    client = make_client(cfg.models[store.record(agent_id).model], cfg.run.model_retries)
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
        store.create(agent_id, model, text)
    except ProfileError as exc:
        _fail(str(exc))
    _introduce(cfg, store, agent_id)


@agents_app.command("introduce")
def agents_introduce(agent_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Ask an existing agent to (re)write its first public profile."""
    cfg = _load(config)
    _introduce(cfg, ProfileStore(cfg.community_dir), agent_id)


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
        typer.echo(f"{agent_id}\t{name}\t{store.record(agent_id).model}\t{headline}")


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


def _clients(cfg: Config, store: ProfileStore, agent_ids: list[str]) -> dict[str, Any]:
    for agent_id in agent_ids:
        if agent_id not in store.ids():
            _fail(f"no agent {agent_id!r}")
        if store.public(agent_id) is None:
            _fail(f"{agent_id} has no public profile; run `aiswarm agents introduce {agent_id}`")
        if store.record(agent_id).model not in cfg.models:
            _fail(f"{agent_id} uses unknown model backend {store.record(agent_id).model!r}")
    return {a: make_client(cfg.models[store.record(a).model], cfg.run.model_retries) for a in agent_ids}


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
    if resume:
        row = conn.execute("SELECT agent_ids FROM runs WHERE id = ?", (resume,)).fetchone()
        if row is None:
            _fail(f"no run {resume!r}")
        agent_ids = json.loads(row["agent_ids"])
    else:
        if not goal:
            _fail("give a goal, or --resume RUN_ID")
        agent_ids = agent or store.ids()
        if not agent_ids:
            _fail("no agents yet; create some with `aiswarm agents new`")
    clients = _clients(cfg, store, agent_ids)

    async def go() -> None:
        limited = limit_clients(clients, cfg.run.max_concurrent_calls)
        runner = Runner(settings=cfg.run, conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                        sink=_print_event)
        run_id = resume or runner.start(goal or "", agent_ids)
        result = await runner.run(run_id)
        if result.status == "paused":
            typer.echo(f"run {run_id} paused; resume with `aiswarm run --resume {run_id}`")
            return
        typer.echo(f"run {run_id} closed: {result.reason}")
        typer.echo(f"report: {cfg.runs_dir / run_id / 'report'}")
        if not no_postrun:
            post = await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                                 sink=_print_event).run(run_id)
            _report_postrun(run_id, post)

    asyncio.run(go())


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

    async def go() -> PostRunResult:
        limited = limit_clients(clients, cfg.run.max_concurrent_calls)
        return await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                             sink=_print_event).run(run_id)

    _report_postrun(run_id, asyncio.run(go()))


@app.command()
def show(run_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Print a run's answer and minority report."""
    report = _load(config).runs_dir / run_id / "report"
    if not report.exists():
        _fail(f"no report for run {run_id!r}")
    typer.echo((report / "answer.md").read_text())
    typer.echo((report / "minority.md").read_text())


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
