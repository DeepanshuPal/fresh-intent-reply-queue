# fresh-intent-reply-queue

Find people on Reddit and Hacker News who are asking for what you sell, while
the thread is still fresh. Get a draft reply worth reading. You decide what
gets posted - every single time.

This is an open-source, self-hosted, BYOK answer to tools like Replymer
($99-399/mo). It costs $0/day on free models and runs against a SQLite file.

## The honest constraints (read this first)

**Reddit bans automated posting.** This tool never posts anything. It drafts,
a human reviews, the human posts from their own account. That approval step is
not a limitation to work around - it is the compliance layer, and it is also
the quality moat: a reply a person actually edited is the only kind that
survives mod review.

**No Reddit API anywhere.** Reddit's Responsible Builder Policy gates
commercial API use, so discovery here uses only the public RSS/Atom views any
feed reader can read (`/r/<sub>/new/.rss`), plus the official HN Algolia API.
Both are free and keyless. If you want Reddit's official API for your own
deployment, that approval is yours to get - this repo never asks for it.

**RSS gets throttled.** Verified live: Reddit rate-limits RSS fetches from
cloud/datacenter IPs (bursts get HTTP 429). The tool retries with backoff and
skips feeds it cannot read, but run it from a normal connection, keep the
subreddit list focused, and do not crank the poll interval below a few
minutes.

## Pipeline

```
INGEST      Reddit .rss feeds (your subreddit list) + HN Algolia search
PREFILTER   regex keywords, negative keywords, content-hash dedupe
MATCH       rank against your ICP blurb (SQLite FTS5 default, sqlite-vec optional)
SCORE       LLM intent score 0-100 with rationale (BYOK via OpenRouter)
DRAFT       stronger LLM writes a ~100-word reply in your voice
QUEUE       terminal approval queue: list / show / approve / edit / reject
LEARN       your edits are stored as diffs and few-shot the next drafts
WATCH       posted permalinks are polled 72h; a mod-removal is a label, not a loss
```

## Quickstart

```bash
git clone https://github.com/DeepanshuPal/fresh-intent-reply-queue
cd fresh-intent-reply-queue
pip install -e .
replyqueue init                 # creates replyqueue.yaml + the database
$EDITOR replyqueue.yaml         # your subreddits, keywords, ICP, voice
export OPENROUTER_API_KEY=...   # https://openrouter.ai/keys - free models work
replyqueue run                  # ingest -> match -> score -> draft
replyqueue queue                # review what it found
replyqueue show 1               # full context for one draft
replyqueue approve 1            # or: edit 1 / reject 1
```

Post the approved reply yourself, then hand the permalink back so the tool can
watch for removal:

```bash
replyqueue posted-add 1 https://www.reddit.com/r/startups/comments/...
replyqueue removal-check        # polls watching permalinks (72h window)
replyqueue stats                # funnel + LLM cost totals
replyqueue runs                 # append-only evidence of every batch
```

Run it on a loop with `replyqueue watch --interval 900`, under cron, or with
Docker Compose (`docker compose up` from `docker/`). **Do not schedule this
from GitHub Actions**: scheduled jobs get dropped under load and GitHub
disables them after 60 days of repo inactivity. A monitor that silently stops
is worse than none. Actions runs CI only.

## What the queue remembers

- **Edit diffs, not just verdicts.** `replyqueue edit 3` stores the unified
  diff of what you changed. Approved and edited drafts become few-shot style
  examples for the next drafting run, so draft ten sounds like you, not like a
  template.
- **Guardrails, enforced at approve time:** a weekly ceiling on approvals, a
  per-subreddit cooldown, and a minimum helpful:promotional ratio (default
  3:1 - you cannot approve a promotional reply until you have been genuinely
  useful three times). Every block and approval is logged append-only.
- **Append-only run records.** Every ingest/match/score/draft batch writes a
  run row: config snapshot, prompt and model versions, token usage, cost,
  side effects. Nothing overwrites them.
- **Removal detection.** Mod-removed replies are the highest-signal label this
  tool produces. Poll posted permalinks for 72h and a removal is recorded as
  `removed`; survival is recorded as `survived`. Reddit-side checks re-read
  the public thread RSS (no API), so detection is best-effort and can lag.

## BYOK: works with

- **OpenRouter** (verified). One key covers scoring and drafting. Defaults are
  two free models confirmed working 2026-09-12:
  `nex-agi/nex-n2.5-mini:free` for scoring and
  `nvidia/nemotron-3-super-120b-a12b:free` for drafting. Swap in stronger paid
  models in `replyqueue.yaml` whenever you want.
- **Exa** for semantic web discovery: planned, not implemented in v0.1.

Without a key the pipeline still runs: ingestion, dedupe, matching and a
transparent heuristic scorer work offline. Drafting is disabled with no key,
deliberately - a heuristic "draft" is the template slop this tool exists to
kill.

## Cost per day (measured, not vibes)

From the live verification run on 2026-09-12: a score call averages
~560 input + ~150 output tokens, a draft call ~520 input + ~780 output tokens.
At a typical self-hosted volume of 3 runs a day (about 60 score calls and 15
drafts), that is roughly 42k input + 20k output tokens:

- On the free default models: **$0.00/day**.
- On `meta-llama/llama-3.1-8b-instruct` ($0.05/M in, $0.08/M out): about
  **$0.004/day** (~$0.11/month).

Reddit RSS and HN Algolia are free and keyless. SQLite is a file. The only
moving part you ever pay for is the LLM, and only if you choose a paid one.

## Verified vs. stubbed (2026-09-12)

Verified against live services in a real end-to-end run: Reddit RSS ingest
(125 real items across 4 subreddits), HN Algolia ingest, keyword prefilter and
dedupe, FTS5 matching, LLM scoring on real posts (a genuine 92/100 buyer-intent
thread surfaced from r/startups), LLM drafting, the approval queue (approve,
edit with stored diff, reject), guardrail blocking (promo-ratio and
double-approve), posted-permalink recording and a live removal check.
`pytest`: 23 tests pass, including parsers run over real captured fixtures.

Stubbed or best-effort:

- **sqlite-vec backend** (`pip install .[vec]`): implemented behind the match
  adapter with a clean FTS fallback, but sqlite-vec is pre-v1 and this backend
  is not covered by the live verification. Treat as experimental.
- **Removal checks** are best-effort (RSS lag, throttling) as described above.
- **Docker / Compose**: provided, not exercised in this environment; the pip
  install path above is the verified one.
- **Offline heuristic scoring** works but is a triage aid, not a substitute
  for LLM scoring.

## Config

One file, `replyqueue.yaml` (see `config.example.yaml` for the commented
version): subreddits, keywords, negative keywords, your ICP blurb, your voice
notes, model names, per-run LLM budgets, guardrail limits, DB path.

## License

MIT. See LICENSE.
