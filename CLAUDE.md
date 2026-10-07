# Sales Call Coach — notes for AI coding assistants

When the user asks you to set this project up, walk them through it one step at a time, in plain language.
Most users are business owners, not developers.

## Setup steps

1. Check `uv` and Node.js 20+ are installed (`uv --version`, `node --version`). If not, give them the install command
   for their OS and wait.
2. `uv sync`
3. Ask which call source they use:
   - ElevenLabs voice agents → copy `config/accounts/_template.toml`
   - Fireflies (recorded Zoom / Google Meet calls) → copy `config/accounts/_template_fireflies.toml`
   - Phone call recordings (audio files) → copy `config/accounts/_template_recordings.toml` and create `recordings/<name>/`
   Name the copy after their business, e.g. `config/accounts/acme.toml`, and set `api_key_env` to match
   (e.g. `ELEVENLABS_API_KEY_ACME`).
4. Ask them to describe their agent or sales call in one or two sentences, and put it in `description`.
5. `cp .env.example .env`, then tell them which keys to paste into `.env` themselves:
   `OPENROUTER_API_KEY` and their source key. **Never ask them to paste keys into the chat, and never print `.env`.**
6. `./coach use <name>`
7. ElevenLabs: `./coach agents list --all`, ask which agents to coach, then `./coach agents add <agent_id>` for each.
   Check the account's `booking_tool_prefix` matches the agent's booking tool (shown by `./coach doctor`).
8. `./coach doctor` and fix whatever it reports.
9. `./coach pilot --limit 25` for the first report, then `./coach dashboard on`.
10. Offer `./coach schedule install` (macOS) for a daily refresh.

## Rules

- Tests: `uv run pytest -q`. They use synthetic data only (`config/accounts/demo.toml`, `tests/synthetic.py`).
- `coach experiment` and `coach opener` change a live ElevenLabs agent. Always run without `--apply` first,
  show the user the plan, and only add `--apply` after they say yes.
- Never commit `.env`, `data/`, `recordings/`, `reports/` or `dashboard/data/` (they are in `.gitignore`).
