# Sales Call Coach — notes for AI coding assistants

When the user asks you to set this project up, walk them through it one step at a time, in plain language.
Most users are business owners, not developers.

## Setup steps

1. Check `uv` and Node.js 20+ are installed (`uv --version`, `node --version`). If not, give them the install command
   for their OS and wait.
2. `uv sync`
3. Ask the user to run `./coach setup` **in their own terminal** (it asks questions and takes keys hidden; you can't
   type into it). It asks where their calls come from, about their business (or a file with their script), their keys,
   and writes the account, the business profile and a checklist made for their business.
   If they'd rather answer you in chat, help them write `config/business/<name>.md`, then they run `./coach setup`
   and choose "Use a file I already have".
4. `./coach doctor` and fix whatever it reports.
5. `./coach pilot --limit 25` for the first report, then `./coach dashboard on`.
6. Offer `./coach schedule install` (macOS) for a daily refresh.

To improve the checklist later: edit `config/business/<name>.md`, then `./coach checklist generate`.
**Never ask the user to paste keys into the chat, and never print `.env`.**

## Rules

- Tests: `uv run pytest -q`. They use synthetic data only (`tests/demo_account.toml`, `tests/synthetic.py`).
- `coach experiment` and `coach opener` change a live ElevenLabs agent. Always run without `--apply` first,
  show the user the plan, and only add `--apply` after they say yes.
- Never commit `.env`, `data/`, `recordings/`, `reports/`, `dashboard/data/` or the user's own business files, accounts and checklists (all in `.gitignore`).
