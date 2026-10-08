# Sales Call Coach

Finds out why your sales calls win or lose, and tells you the one thing to change next.

It reads your calls, grades each one against a short checklist of behaviours ("did the agent say who's calling?",
"did they ask before pitching?"), compares winning calls with losing ones, and writes a plain-language report
with real quotes and one suggested change. A local dashboard shows it all.

Works with:
- **ElevenLabs voice agents** (the main use): it can also A/B test a new opener on your agent.
- **Fireflies**: recorded Zoom / Google Meet / Teams sales calls.
- **Phone call recordings**: drop audio files in a folder; ElevenLabs Scribe turns them into text.

```
calls ──list──► SQLite (call metadata, funnel stage)                    ← cheap, no AI
      ──detail─► booking facts → win / loss                             ← code only
      ──grade──► checklist answers per call (Jev via OpenRouter, or ElevenLabs criteria)
                         │
             analyze: funnel + behaviour vs. outcome tables (code)
                         │
             report: an AI explains the numbers, quotes verified calls,
                     proposes ONE change + how to test it
```

Your data stays on your computer. The local database stores call metadata, checklist answers and hashed lead IDs,
never names, phone numbers or what was said (except recordings accounts, which keep their transcripts locally).

## Requirements

- macOS or Linux (on Windows, use WSL)
- [uv](https://docs.astral.sh/uv/) for Python, and Node.js 20+ for the dashboard
- An [OpenRouter](https://openrouter.ai) key (grading + report, usually a few cents a week)
- The key for your call source: ElevenLabs, Fireflies, or ElevenLabs with Speech to Text for recordings

## Quick start

```bash
git clone https://github.com/Agor56/sales-call-coach-aff.git sales-call-coach && cd sales-call-coach
uv sync
./coach setup                    # asks where your calls come from, about your business, and your keys
./coach doctor                   # checks keys and access (never prints them)
./coach pilot --limit 25         # first report from your last 2 days of calls
./coach dashboard on             # the dashboard at http://localhost:3007
./coach                          # numbered menu with everything else
```

`coach setup` asks:
1. **Where your calls come from**: ElevenLabs voice agents, Fireflies, or a folder of phone recordings.
2. **About your business**: what you sell, what the calls are for, what a good call ends with, what the agent must and
   must never do. Or point it at a file you already have (call script, agent prompt, sales process).
   This is saved in `config/business/<name>.md`.
3. **Your keys**, typed hidden and saved only in `.env` on your computer.
4. ElevenLabs only: which agents to coach, and which of their tools books the meeting.

Then an AI writes a checklist made for your business (`config/checklist_<name>_1.toml`): one question that decides
whether a call worked (unless your agent's booking tool tells it), plus 5–8 behaviours to check on every call.
Edit the business file any time and run `./coach checklist generate` for a new version. Works for any industry.

Using an AI coding assistant (Claude Code, Cursor…)? Open this folder and ask it to "set up Sales Call Coach for me":
`CLAUDE.md` tells it the steps.

Use `./coach …` (wrapper script). On macOS, `uv run coach` can fail with `No module named 'coach'`:
macOS puts a "hidden" flag on the venv's `.pth` files, and Python 3.12+ ignores hidden `.pth` files.

### Keys (`.env`)
| Variable | What for |
|---|---|
| `ELEVENLABS_API_KEY_<CLIENT>` | One per ElevenLabs account. The name is set by `api_key_env` in the account file. Needs conversation read access, plus agent write access only for `criteria push --apply` |
| `FIREFLIES_API_KEY_<CLIENT>` | Fireflies accounts only |
| `OPENROUTER_API_KEY` | Report writer (`REPORT_MODEL`, default `deepseek/deepseek-v4.1-flash`) and the Jev grader |
| `GRADER` | `jev` (default) or `elevenlabs` (ElevenLabs accounts only) |
| `ANTHROPIC_API_KEY` | Only if `REPORT_PROVIDER=anthropic` |

OpenRouter requests only go to providers that don't store or train on prompts (`data_collection: deny`)
and that honour the JSON schema.

## Dashboard

`./coach dashboard on` builds it once (a few minutes the first time) and runs it at `http://localhost:3007`.
`./coach` → **d** refreshes the data and opens it. It only runs on your computer.
Pages: **Overview** (calls, silent rate, conversations, bookings good / needs a check, daily chart, where calls end, agents,
next fix, latest change) · **What works** (the behaviours in won vs lost calls, answer time & interruptions, why bookings
need a check) · **Agents** · **Fix next** (the report with verified quotes and the one suggested change) ·
**Changes & results** (every change to the agents and whether it worked, see below) · **Opener tests**.

Periods: last 24 hours, 2, 7 and 30 days. Each number shows the change against the same-length period right before it
(exact dates and weekdays shown on top), tested against day-to-day noise: green/red only when the move is bigger than
chance (95%), otherwise grey "normal noise". A period is compared only when neither window has a hole in the stored calls.
A red warning appears when the data is more than a day old.
Data file: `dashboard/data/<client>.json`, written by `./coach export` (also after every `./coach report`).
It runs from a standalone build (`dashboard/.next/standalone`), and `node_modules` (~600 MB) is deleted after each build.
After changing dashboard code, `coach dashboard update` downloads it again, rebuilds, restarts and deletes it.

## Daily refresh (macOS)

`./coach schedule install` refreshes every account at 07:00 (discover → grade → analyze → report → scan for agent changes)
and keeps the dashboard always on, using macOS launchd. It keeps the Mac awake while it runs and sends a notification when
it's done. If the Mac sleeps at 07:00, the run happens when it wakes up. `./coach` → **r** runs the same refresh now.
On Sundays at 09:00 it also writes the weekly scorecard (below). On Linux, run `uv run python -m coach.daily` (daily) and
`uv run python -m coach.weekly` (weekly) from cron instead.

## Did the change work? (change log + weekly scorecard)

Every morning the coach reads each ElevenLabs agent's version history and the pronunciation dictionaries it uses, and logs
what changed in plain words: prompt sections added or removed, opener, model, temperature, voice, tools. Saves made within
30 minutes count as one change, and the same edit on several agents is one change. When the text of a suggestion from the
report appears in the agent, the change is marked as the coach's recommendation. Changes made outside ElevenLabs (a
workflow, the lead list, calling hours) you log yourself: `./coach` → **l**, or `./coach changes add "…" --at "YYYY-MM-DD HH:MM"`.

Each change is scored on the calls of the same agents: the days before it vs the same number of days after it (7, stretched
to 14 when the first week isn't clear), with the same noise test. Verdicts: *worked*, *made it worse*, *mixed*, *no clear
change*, *not clear yet*, *too early (day n of 7)*, *not enough calls*, or *can't judge* (holes in the data before it). When
other changes hit the same agents at the same time, that's flagged, since their effects can't be separated. The scorecard
(`reports/<client>/scorecard-*.md`) is written every Sunday at 09:00 with a notification; the dashboard page updates daily.
The report writer is told these results, so it builds on what worked and doesn't suggest a failed change again; each
suggestion also names the one number it should move. Fireflies / recordings accounts have no agent history: log changes by hand.

## Client accounts

Each client is one file: `config/accounts/<client>.toml`. It holds that client's key variable, agents,
success rule and checklist. Each client also gets its own database (`data/<client>/`) and report folder
(`reports/<client>/`), so client data is never mixed. `demo.toml` is a fake account used by the tests.

Pick the account with `./coach use <client>` (remembered), `--account <client>` (one command), or
`COACH_ACCOUNT=<client>`. Every command prints `[account: …]` first.

## Call sources

Each account reads its calls from one place, set by `source` in its account file:

| `source` | Calls | Key in `.env` | Win/loss | Template |
|---|---|---|---|---|
| `elevenlabs` (default) | AI voice agents | `ELEVENLABS_API_KEY_<CLIENT>` | the booking tool returned without error | `_template.toml` |
| `fireflies` | recorded meetings of human salespeople (Zoom, Google Meet, Teams) | `FIREFLIES_API_KEY_<CLIENT>` | the grader says the call ended with a dated next step | `_template_fireflies.toml` |
| `recordings` | audio files you drop in `recordings/<client>/` (phone calls) | `ELEVENLABS_API_KEY_<CLIENT>` (for Scribe) | same as Fireflies | `_template_recordings.toml` |

Recordings accounts:
- Supported files: mp3, wav, m4a, mp4, ogg, opus, flac, webm, aac. Each new file is transcribed once with ElevenLabs Scribe
  (about $0.22 per hour of audio, up to 50 new files per run), which also separates the speakers.
  A restricted ElevenLabs key needs the "Speech to Text" permission.
- The salesperson is whoever speaks first. Set `rep_speaker = 2` if the customer usually speaks first.
- A file is recognised by its content, so renaming or moving it costs nothing. Its call date is the day it was first transcribed.
- Transcripts are saved in `data/<client>/transcripts/` on this computer. Recordings and transcripts are never pushed.
- OpenRouter can transcribe too, but none of its models separate the speakers, so the coach can't tell who said what.

Fireflies accounts:
- In the account file, each "agent" is one salesperson: `agent_id` is the email that organizes their meetings, or `all`.
- The salesperson's side of the call is found from `rep_names`. If that's empty, the coach uses the meeting organizer's name.
- Grading always uses Jev, so `OPENROUTER_API_KEY` is needed.
- Opener tests (`experiment`, `opener`) and `criteria push` only work with ElevenLabs agents.
- The free plan allows 50 API requests a day. Each list request brings up to 25 meetings with their transcripts.
  Transcripts are kept in memory for that run only and never saved to disk.
- Calls longer than about 45 minutes are too long for the Jev grader and are skipped (recorded as `too_long`).

## Commands

| Command | What it does | Cost |
|---|---|---|
| `./coach accounts` / `use <client>` | List client accounts / switch account | — |
| `./coach agents [list --all]` | This account's agents (● included, ○ paused). `--all` also lists every agent in the ElevenLabs account with call counts | — |
| `./coach agents add <agent_id> [--key k]` | Adds an agent after checking the ID exists under this client's key | — |
| `./coach agents pause\|resume\|remove <key>` | Leave an agent out of runs temporarily (pause) or permanently (remove). Past data stays | — |
| `./coach doctor` | Checks keys (never prints them), agents, booking tools, criteria status, OpenRouter credit, models. With `GRADER=jev`, also grades one tiny synthetic call | ~0 |
| `./coach criteria show` / `push [--apply]` | Shows the checklist / adds it to the agents as ElevenLabs criteria (only needed for `GRADER=elevenlabs`). Dry run unless `--apply`. Keeps existing criteria and creates a new agent version; doesn't change what the agent says | — |
| `./coach discover [--days 7]` | Indexes calls from the list endpoint, 100 per request. Overlaps the last 48h and resumes interrupted scans | ElevenLabs reads |
| `./coach pilot --limit 25` | discover (last 2 days) → details + grading for up to 25 calls → analyze → report | ≤25 grades + 1 report |
| `./coach review --limit 300` | Resumes details + grading, capped. Newest first, so the window is covered systematically | per graded call (Jev) |
| `./coach analyze [--days 7] [--grader jev]` | Funnel, outcomes, behaviour comparisons. Code only | free |
| `./coach report [--model ID] [--numbers-only]` | Markdown report in `reports/<client>/` | 1 model call (well under 1¢) |
| `./coach compare-graders` | Agreement between ElevenLabs and Jev on the same calls, plus a table of disagreements for you to judge | free |
| `./coach spot-check --n 15 [--grader jev]` | Sheet of graded calls (redacted transcript + answers) to tick ✔/✘ by hand | ElevenLabs reads |
| `./coach watch --interval 900` | discover + review + analyze in a loop while the process runs | as above |
| `./coach changes [list\|scan\|scorecard]` | Change log with verdicts / scan agents for changes now / write the scorecard now | free |
| `./coach changes add "…" [--agents a,b] [--at "YYYY-MM-DD HH:MM"]` | Log a change made outside ElevenLabs | free |
| `./coach schedule status\|run-now\|run-weekly` | Did the jobs run (and the end of their logs) / start the daily refresh / start the weekly scorecard | as above |

## Choosing a grader (bake-off)

1. `GRADER=jev ./coach review --limit 30 --days 3` grades 30 calls with Jev. Nothing changes on the agents.
2. Optional: push the criteria for ElevenLabs grading (`criteria push --apply`), then `GRADER=elevenlabs ./coach review --limit 30 --days 3`.
3. `./coach compare-graders` shows where the two disagree, and you mark who's right. Or run `./coach spot-check --n 15`
   for a single grader.
4. Keep the grader that agrees with you more. Grades are stored per grader and are never mixed in one comparison.

## Definitions

**Funnel stage** (from list metadata):
- `no_connect`: dial failed.
- `voicemail`
- `no_reply`: the lead never spoke after the opener.
- `early_drop`: the lead spoke, but the call has fewer than 5 messages.
- `engaged`
- `pending`: still processing; picked up on a later run.

**Outcome** (per account, `[outcome]`):
- **success**: the booking tool returned without error (`rule = "booked"`), optionally **and** the booking passes a
  qualification rule on the tool's parameters (turnover / asset minimums, see `demo.toml`).
- **failure**: the lead spoke but nothing was booked, or the booking is disqualified ("needs a check": numbers that can't be
  right, usually mis-heard).
- Several actions can count as success: `booking_tool_prefix = ["book_", "send_lead_to_crm"]`, e.g. a booked call for
  voice agents and a lead sent to the CRM for chat agents. The setup wizard lets you pick several.
- **unknown**: a fast-track callback booked with no profile data, profile data that can't be parsed, or the transcript is
  unavailable. Unknown calls are counted but kept out of comparisons.

Bump `outcome.version` when you change the rule. Changing it recomputes the analysis from stored facts.
Nothing is regraded or refetched.

**Checklists** (`config/checklist_*.toml`): `coach setup` / `coach checklist generate` write one for your business.
`a1` (AI voice agents) and `s1` (human sales calls) are general fallbacks used until you have an OpenRouter key.
Each criterion is one observable behaviour with success / failure / unknown definitions. A changed checklist should
get a new version (the generator does this), so old and new grades are never mixed.

## Reading the report

- Every count and rate is computed in code. The model only explains them and may not introduce new numbers.
- Behaviour tables show exact denominators, 95% Wilson intervals, and what was excluded. ⚠ marks small groups.
- These are associations, not causes. Test the proposed edit on a branch with a traffic split before you trust it.
  ElevenLabs Experiments / Architect can run that test.
- Quotes are checked against the fetched transcript. Invented quotes are removed and counted.
- Results are call-level. Repeat leads are counted (hashed phone) but not de-duplicated.

## Unattended operation

`watch` runs only while its terminal stays open. For unattended runs use `coach schedule install` (macOS) or cron.

## Development

```bash
uv run pytest -q                                          # synthetic fixtures only; no real call content in the repo
PYTHONPATH=src uv run python scripts/synthetic_demo.py    # end-to-end demo → reports/SYNTHETIC-report-*.md
```

Layout: `config.py` (accounts, settings) · `elevenlabs.py` (API adapter, retries, request cap) · `fireflies.py`
(Fireflies adapter: same methods, meetings reshaped into calls) · `pipeline.py`
(discover/details/backfill) · `grader.py` (Jev grading) · `llm.py` (OpenRouter adapter) · `outcome.py` (funnel,
booking parse, qualification) · `analysis.py` (all numbers) · `compare.py` (grader bake-off) · `evidence.py`
(redaction, quote verification) · `report.py` (report) · `recordings.py` (audio folder + Scribe) · `db.py` (SQLite, migrations, process lock).

## License

MIT — see `LICENSE`.
