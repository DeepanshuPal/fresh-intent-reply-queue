"""replyqueue CLI - ingest, match, score, draft, then the human decides."""
from __future__ import annotations

import argparse
import json
import sys
import time

from rich.console import Console
from rich.table import Table

from . import __version__
from .config import load_config
from .db import connect, utcnow
from .prefilter import compile_keywords, passes, content_hash
from .sources import build_sources

console = Console()
err_console = Console(stderr=True)


def _ingest(conn, cfg, rec=None) -> dict:
    stats = {"fetched": 0, "new": 0, "dupe": 0, "prefiltered_out": 0, "errors": 0}
    kw = compile_keywords(cfg.keywords)
    neg = compile_keywords(cfg.negative_keywords)
    for source in build_sources(cfg):
        try:
            items = source.fetch()
        except Exception as exc:
            err_console.print(f"[yellow]{source.name} failed: {exc}[/yellow]")
            stats["errors"] += 1
            if rec:
                rec.side_effects.append(f"{source.name} error: {exc}")
            continue
        stats["fetched"] += len(items)
        for it in items:
            if not it.title and not it.body:
                continue
            digest = content_hash(it.title, it.body)
            ok, hits = passes(it.title, it.body, kw, neg)
            cur = conn.execute(
                "INSERT OR IGNORE INTO items(source, source_id, channel, title, body, url,"
                " permalink, author, points, created_utc, ingested_at, content_hash, raw_json,"
                " prefilter_pass, keyword_hits) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    it.source, it.source_id, it.channel, it.title, it.body, it.url,
                    it.permalink, it.author, it.points, it.created_utc, utcnow(), digest,
                    json.dumps(it.raw)[:20000], int(ok), json.dumps(hits),
                ),
            )
            if cur.rowcount == 0:
                stats["dupe"] += 1
            else:
                stats["new"] += 1
                if not ok:
                    stats["prefiltered_out"] += 1
    conn.commit()
    return stats


def _match(conn, cfg) -> dict:
    from .match import get_backend

    backend = get_backend(cfg)
    rows = conn.execute("SELECT id, title, body FROM items WHERE prefilter_pass = 1").fetchall()
    items = [dict(r) for r in rows]
    backend.index(conn, items)
    ranked = backend.rank(conn, cfg.icp, cfg.keywords)
    kept = 0
    for item_id, score in ranked.items():
        if score >= cfg.match.threshold:
            conn.execute(
                "INSERT OR REPLACE INTO matches(item_id, backend, score, created_at)"
                " VALUES (?, ?, ?, ?)",
                (item_id, backend.name, score, utcnow()),
            )
            kept += 1
    conn.commit()
    return {"backend": backend.name, "indexed": len(items), "matched": kept}


def _candidates(conn, limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT m.item_id FROM matches m"
        " LEFT JOIN scores s ON s.item_id = m.item_id"
        " WHERE s.item_id IS NULL"
        " ORDER BY m.score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["item_id"] for r in rows]


def _draft_candidates(conn, cfg) -> list[int]:
    rows = conn.execute(
        "SELECT s.item_id FROM scores s"
        " LEFT JOIN drafts d ON d.item_id = s.item_id AND d.status != 'rejected'"
        " WHERE s.intent_score >= ? AND d.id IS NULL"
        " ORDER BY s.intent_score DESC LIMIT ?",
        (cfg.budgets.min_intent_score_to_draft, cfg.budgets.max_drafts_per_run),
    ).fetchall()
    return [r["item_id"] for r in rows]


def cmd_init(args):
    import shutil

    target = "replyqueue.yaml"
    if args.force or not __import__("os").path.exists(target):
        shutil.copy("config.example.yaml", target) if __import__("os").path.exists(
            "config.example.yaml"
        ) else None
    cfg = load_config(target if __import__("os").path.exists(target) else None)
    connect(cfg.db_path).close()
    console.print(f"[green]ready[/green] config={cfg.path} db={cfg.db_path}")
    if not cfg.api_key():
        console.print(
            "[yellow]no OPENROUTER_API_KEY set - scoring runs on the offline heuristic"
            " engine and drafting is disabled.[/yellow]"
        )


def cmd_run(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .llm import get_llm
    from .runs import run
    from .score import score_items, PROMPT_VERSION as SCORE_PV
    from .draft import draft_for_items, PROMPT_VERSION as DRAFT_PV

    llm = get_llm(cfg)
    with run(conn, cfg, "run") as rec:
        rec.stats["ingest"] = _ingest(conn, cfg, rec)
        console.print(f"ingest: {rec.stats['ingest']}")
        rec.stats["match"] = _match(conn, cfg)
        console.print(f"match: {rec.stats['match']}")
        ids = _candidates(conn, cfg.budgets.max_score_calls_per_run)
        rec.stats["score"] = score_items(conn, cfg, llm, ids)
        rec.prompt_versions.add(SCORE_PV)
        console.print(f"score: {rec.stats['score']}")
        rec.stats["draft"] = {"drafted": 0, "note": "offline engine: drafting disabled"}
        if llm.name != "offline":
            rec.stats["draft"] = draft_for_items(conn, cfg, llm, _draft_candidates(conn, cfg))
            rec.prompt_versions.add(DRAFT_PV)
        console.print(f"draft: {rec.stats['draft']}")
        rec.models.add(getattr(llm, "name", "unknown"))
        for stage in ("score", "draft"):
            st = rec.stats.get(stage, {})
            rec.cost_usd += st.get("cost_usd", 0.0)
            rec.tokens_in += st.get("tokens_in", 0)
            rec.tokens_out += st.get("tokens_out", 0)
        n_pending = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE status='pending'"
        ).fetchone()["n"]
        rec.stats["pending_drafts"] = n_pending
        console.print(f"\n[bold]{n_pending} draft(s) waiting in the queue.[/bold] `replyqueue queue` to review.")


def cmd_ingest(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .runs import run

    with run(conn, cfg, "ingest") as rec:
        rec.stats = _ingest(conn, cfg, rec)
        console.print(rec.stats)


def cmd_match(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .runs import run

    with run(conn, cfg, "match") as rec:
        rec.stats = _match(conn, cfg)
        console.print(rec.stats)


def cmd_score(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .llm import get_llm
    from .runs import run
    from .score import score_items, PROMPT_VERSION

    llm = get_llm(cfg)
    with run(conn, cfg, "score") as rec:
        ids = _candidates(conn, args.limit or cfg.budgets.max_score_calls_per_run)
        rec.stats = score_items(conn, cfg, llm, ids)
        rec.prompt_versions.add(PROMPT_VERSION)
        rec.cost_usd = rec.stats.get("cost_usd", 0.0)
        rec.tokens_in = rec.stats.get("tokens_in", 0)
        rec.tokens_out = rec.stats.get("tokens_out", 0)
        console.print(rec.stats)


def cmd_draft(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .llm import get_llm
    from .runs import run
    from .draft import draft_for_items, PROMPT_VERSION

    llm = get_llm(cfg)
    if llm.name == "offline":
        raise SystemExit("drafting needs OPENROUTER_API_KEY (BYOK). See README.")
    with run(conn, cfg, "draft") as rec:
        rec.stats = draft_for_items(conn, cfg, llm, _draft_candidates(conn, cfg))
        rec.prompt_versions.add(PROMPT_VERSION)
        rec.cost_usd = rec.stats.get("cost_usd", 0.0)
        rec.tokens_in = rec.stats.get("tokens_in", 0)
        rec.tokens_out = rec.stats.get("tokens_out", 0)
        console.print(rec.stats)


def cmd_queue(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .queue import pending

    rows = pending(conn)
    if not rows:
        console.print("[dim]queue is empty[/dim]")
        return
    table = Table(title=f"approval queue ({len(rows)} pending)")
    table.add_column("id", style="bold")
    table.add_column("score")
    table.add_column("channel")
    table.add_column("post")
    table.add_column("draft preview")
    for r in rows:
        preview = " ".join(r["text"].split())[:70]
        table.add_row(str(r["id"]), str(r["intent_score"] or "-"), r["channel"],
                      " ".join(r["title"].split())[:50], preview)
    console.print(table)
    console.print("`replyqueue show <id>` · `approve <id>` · `edit <id>` · `reject <id>`")


def cmd_show(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .queue import get_draft

    r = get_draft(conn, args.id)
    console.rule(f"draft #{r['id']} [{r['status']}] score={r['intent_score']}")
    console.print(f"[bold]{r['title']}[/bold]  (r/{r['channel']}, u/{r['author']})")
    console.print(f"[dim]{r['permalink']}[/dim]")
    console.print(f"rationale: {r['rationale']}")
    console.rule("original post")
    console.print((r["item_body"] or "")[:2000])
    console.rule("draft reply")
    console.print(r["text"])
    edits = conn.execute(
        "SELECT diff, created_at FROM draft_edits WHERE draft_id = ? ORDER BY id", (args.id,)
    ).fetchall()
    for e in edits:
        console.rule(f"your edit ({e['created_at']})")
        console.print(e["diff"])


def cmd_approve(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .queue import approve
    from .guardrails import GuardrailBlock

    try:
        approve(conn, cfg, args.id)
    except (GuardrailBlock, KeyError) as exc:
        raise SystemExit(f"blocked: {exc}")
    console.print(f"[green]draft #{args.id} approved.[/green] Post it yourself, then "
                  f"`replyqueue posted-add {args.id} <permalink>` so we can watch for removal.")


def cmd_reject(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .queue import reject
    from .guardrails import GuardrailBlock

    try:
        reject(conn, args.id, args.reason or "")
    except (GuardrailBlock, KeyError) as exc:
        raise SystemExit(f"blocked: {exc}")
    console.print(f"[red]draft #{args.id} rejected.[/red]")


def cmd_edit(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .queue import edit
    from .guardrails import GuardrailBlock

    try:
        edit(conn, cfg, args.id, args.text)
    except (GuardrailBlock, KeyError) as exc:
        raise SystemExit(f"blocked: {exc}")
    console.print(f"[green]draft #{args.id} edited + approved.[/green] The diff is stored "
                  "and becomes a style example for future drafts.")


def cmd_posted_add(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .removal import record_posted

    pid = record_posted(conn, args.id, args.permalink)
    console.print(f"watching {args.permalink} for 72h (posted #{pid}). "
                  "Run `replyqueue removal-check` periodically.")


def cmd_posted_list(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    rows = conn.execute("SELECT * FROM posted ORDER BY id DESC LIMIT 50").fetchall()
    table = Table(title="posted replies")
    for col in ("id", "draft", "status", "checks", "posted_at", "permalink"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), str(r["draft_id"]), r["status"],
                      str(r["checks"]), r["posted_at"], r["permalink"][:60])
    console.print(table)


def cmd_removal_check(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    from .removal import check_posted
    from .runs import run

    with run(conn, cfg, "removal-check") as rec:
        rec.stats = check_posted(conn, cfg, force=args.force)
        console.print(rec.stats)


def cmd_runs(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)
    rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 20").fetchall()
    table = Table(title="recent runs (append-only evidence)")
    for col in ("id", "kind", "status", "started_at", "cost_usd", "tokens"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["kind"], r["status"], r["started_at"],
                      f"{r['cost_usd']:.4f}", f"{r['tokens_in']}/{r['tokens_out']}")
    console.print(table)


def cmd_stats(args):
    cfg = load_config(args.config)
    conn = connect(cfg.db_path)

    def one(sql):
        return conn.execute(sql).fetchone()[0]

    q_pending = one("SELECT COUNT(*) FROM drafts WHERE status='pending'")
    q_approved = one("SELECT COUNT(*) FROM drafts WHERE status='approved'")
    q_edited = one("SELECT COUNT(*) FROM drafts WHERE status='edited'")
    q_rejected = one("SELECT COUNT(*) FROM drafts WHERE status='rejected'")
    p_watch = one("SELECT COUNT(*) FROM posted WHERE status='watching'")
    p_surv = one("SELECT COUNT(*) FROM posted WHERE status='survived'")
    p_rem = one("SELECT COUNT(*) FROM posted WHERE status='removed'")
    n_items = one("SELECT COUNT(*) FROM items")
    n_pass = one("SELECT COUNT(*) FROM items WHERE prefilter_pass=1")
    console.print(f"items: {n_items} (prefilter pass: {n_pass})")
    console.print(f"matched: {one('SELECT COUNT(*) FROM matches')}  "
                  f"scored: {one('SELECT COUNT(*) FROM scores')}  "
                  f"drafts: {one('SELECT COUNT(*) FROM drafts')}")
    console.print(f"queue: pending {q_pending}, approved {q_approved}, "
                  f"edited {q_edited}, rejected {q_rejected}")
    console.print(f"posted: watching {p_watch}, survived {p_surv}, "
                  f"[bold red]removed {p_rem}[/bold red]")
    cost = conn.execute("SELECT SUM(cost_usd), SUM(tokens_in), SUM(tokens_out) FROM runs").fetchone()
    console.print(f"total llm cost: ${cost[0] or 0:.4f} ({cost[1] or 0} in / {cost[2] or 0} out tokens)")


def cmd_watch(args):
    console.print(f"[bold]watch mode[/bold] - full pipeline every {args.interval}s. Ctrl-C to stop.")
    while True:
        cmd_run(args)
        time.sleep(args.interval)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="replyqueue", description=__doc__)
    p.add_argument("--config", default=None, help="path to replyqueue.yaml")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="create replyqueue.yaml + the database")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_init)

    for name, fn, help_ in [
        ("run", cmd_run, "ingest -> match -> score -> draft in one pass"),
        ("ingest", cmd_ingest, "pull Reddit RSS + HN Algolia into the db"),
        ("match", cmd_match, "rank prefiltered items against your ICP"),
        ("score", cmd_score, "LLM/heuristic intent score 0-100"),
        ("draft", cmd_draft, "draft replies for high-intent items (BYOK)"),
        ("queue", cmd_queue, "list pending drafts"),
        ("approve", cmd_approve, "approve a draft (guardrails enforced)"),
        ("reject", cmd_reject, "reject a draft"),
        ("edit", cmd_edit, "edit a draft in $EDITOR; stores the diff"),
        ("posted-add", cmd_posted_add, "record a permalink you posted; watch it 72h"),
        ("posted-list", cmd_posted_list, "list posted replies"),
        ("removal-check", cmd_removal_check, "poll posted permalinks for removal"),
        ("runs", cmd_runs, "append-only run records"),
        ("stats", cmd_stats, "funnel + cost totals"),
    ]:
        sp = sub.add_parser(name, help=help_)
        if name == "score":
            sp.add_argument("--limit", type=int, default=None)
        if name in ("approve", "reject", "edit", "posted-add", "show"):
            sp.add_argument("id", type=int)
        if name == "reject":
            sp.add_argument("--reason", default="")
        if name == "edit":
            sp.add_argument("--text", default=None, help="new text (skips $EDITOR)")
        if name == "posted-add":
            sp.add_argument("permalink")
        if name == "removal-check":
            sp.add_argument("--force", action="store_true")
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("show", help="show one draft with full context")
    sp.add_argument("id", type=int)
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("watch", help="loop `run` forever (cron alternative)")
    sp.add_argument("--interval", type=int, default=900)
    sp.set_defaults(fn=cmd_watch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
