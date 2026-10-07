"""coach — ElevenLabs call coaching loop.   Run `./coach` with no arguments for a numbered menu.

  coach accounts                    list client accounts (one ElevenLabs key + agent set per client)
  coach use <account>               choose which client account the next commands work on
  coach agents [list --all]         agents in this account (● included, ○ paused); --all shows every agent in ElevenLabs
  coach agents add <agent_id>       add an agent (verified against this account's ElevenLabs key)
  coach agents remove|pause|resume <key>   take an agent out of runs, permanently or temporarily
  coach schedule install|status|run-now|uninstall   daily 07:00 refresh + always-on dashboard
  coach doctor                      check config + API access (never prints secrets)
  coach criteria show|push          show / push the checklist as ElevenLabs evaluation criteria (push is dry-run unless --apply)
  coach discover [--days N]         index calls for the selected agents (list endpoint, cheap)
  coach pilot --limit 25            discover recent calls, fetch details + checklist for up to N, analyze, report
  coach review --limit 300          resume: fetch details and grade the checklist (ElevenLabs backfill or Jev), capped
  coach analyze [--days N]          counts, funnel and comparisons — no model call
  coach report [--analysis ID]      coaching report (OpenRouter or Claude) from the latest (or given) analysis
  coach watch --interval 900        discover + details + analyze on a loop while the process runs
  coach experiment plan|start|status|stop   A/B test a new opener on half the calls (start/stop need --apply)
  coach compare-graders             agreement between ElevenLabs and Jev grades + list of disagreements
  coach spot-check --n 15           sheet of graded calls for you to judge by hand
"""
from __future__ import annotations

import argparse
import subprocess
import json
import os
import re
import sys
import time
from datetime import datetime

from . import db
from .analysis import analyze, load_calls, save_analysis, summary_text
from .checklist import current_criteria, diff_summary, is_applied, merged_criteria
from dotenv import load_dotenv

from .config import PROJECT_ROOT, SOURCES, ConfigError, current_account, list_accounts, load_settings, set_current_account
from .elevenlabs import CredentialsError, ElevenLabsClient, ElevenLabsError, RequestCapReached
from .pipeline import backfill, discover, fetch_details


def _write_client(settings) -> ElevenLabsClient:
    """Client for changing agents (branches, traffic, criteria). Uses api_write_key_env when configured."""
    key, name = settings.el_write_key
    el = settings.el
    return ElevenLabsClient(key, settings.el_base_url, timeout=el["timeout_secs"], max_retries=el["max_retries"],
                            min_interval=el["min_interval_secs"], max_requests=el["max_requests_per_run"], key_name=name)


def _client(settings):
    """Reads calls for this account: ElevenLabsClient, or FirefliesClient (same methods) for source = "fireflies"."""
    if settings.source == "fireflies":
        from .fireflies import FirefliesClient
        ff = settings.fireflies
        return FirefliesClient(settings.el_api_key, timeout=ff["timeout_secs"], max_retries=ff["max_retries"],
                               min_interval=ff["min_interval_secs"], max_requests=ff["max_requests_per_run"],
                               page_cap=ff["page_size"], rep_names=settings.rep_names, key_name=settings.el_key_name)
    el = settings.el
    if settings.source == "recordings":
        from .recordings import RecordingsClient
        rc = settings.recordings
        scribe = ElevenLabsClient(settings.el_api_key, settings.el_base_url, timeout=rc["timeout_secs"],
                                  max_retries=rc["max_retries"], min_interval=el["min_interval_secs"],
                                  max_requests=el["max_requests_per_run"], key_name=settings.el_key_name)
        return RecordingsClient(scribe, settings.root / rc["folder"], settings.db_path.parent / "transcripts",
                                model=rc["model"], language=rc["language"], rep_speaker=rc["rep_speaker"],
                                max_files=rc["max_files_per_run"])
    return ElevenLabsClient(settings.el_api_key, settings.el_base_url, timeout=el["timeout_secs"],
                            max_retries=el["max_retries"], min_interval=el["min_interval_secs"],
                            max_requests=el["max_requests_per_run"], key_name=settings.el_key_name)


def _window(args, settings) -> tuple[int, int]:
    until = int(time.time())
    if getattr(args, "since", None):
        since = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp())
    else:
        since = until - int((getattr(args, "days", None) or settings.discovery["default_days"]) * 86400)
    return since, until


def _check_openrouter(settings, model_ids: list[str]) -> bool:
    import httpx

    headers = {"Authorization": f"Bearer {settings.env['OPENROUTER_API_KEY']}"}
    try:
        k = httpx.get("https://openrouter.ai/api/v1/key", headers=headers, timeout=20)
        if k.status_code != 200:
            print(f"  OpenRouter key: ERROR HTTP {k.status_code}")
            return False
        info = k.json().get("data", {})
        limit = info.get("limit_remaining")
        print(f"  OpenRouter key: OK (credit remaining: {'unlimited' if limit is None else f'${limit:.2f}'})")
        models = httpx.get("https://openrouter.ai/api/v1/models", timeout=20).json()["data"]
        ok = True
        for mid in model_ids:
            m = next((m for m in models if m["id"] == mid), None)
            if not m:
                print(f"  OpenRouter model: ERROR '{mid}' not found"); ok = False
            elif "structured_outputs" not in (m.get("supported_parameters") or []):
                print(f"  OpenRouter model: ERROR '{mid}' lacks structured outputs"); ok = False
            else:
                print(f"  OpenRouter model: OK ({mid}, structured outputs supported)")
        return ok
    except httpx.HTTPError as e:
        print(f"  OpenRouter: ERROR {e}")
        return False


def _jev_test_grade(settings) -> bool:
    """Grades one tiny SYNTHETIC call to prove the route works end to end (schema + data policy)."""
    from .grader import grade_one
    from .llm import LLMError

    fake = {"transcript": [
        {"role": "agent", "message": "היי, דנה מחברת דוגמה. הפנייה שלך למימון עסקי עוד רלוונטית?", "time_in_call_secs": 0},
        {"role": "user", "message": "מי זה? את רובוט?", "time_in_call_secs": 6},
        {"role": "agent", "message": "אני עוזרת דיגיטלית מחברת דוגמה. אתה עדיין פעיל בעסק?", "time_in_call_secs": 9},
        {"role": "user", "message": "לא מעוניין, תודה", "time_in_call_secs": 13},
        {"role": "agent", "message": "הבנתי, תודה על המענה. יום טוב!", "time_in_call_secs": 15}]}
    try:
        grades, usage = grade_one(settings, fake)
    except (LLMError, ValueError) as e:
        print(f"  Jev test grade: ERROR {e}")
        return False
    cost = usage.get("cost_usd")
    who = grades.get(settings.checklist.ids[0], {})
    print(f"  Jev test grade: OK — served by {usage.get('model')} via {usage.get('provider')}, "
          f"{usage.get('input_tokens')} input tokens" + (f", ${cost:.6f}" if cost is not None else "")
          + f" | {settings.checklist.ids[0]} = {who.get('result')} (confidence {who.get('confidence')})")
    return True


# ----------------------------------------------------------------------------- commands
def cmd_doctor(args, settings) -> int:
    ok = True
    print(f"project root: {settings.root}")
    print(f"account: {settings.account} — {settings.account_label}  (data: {settings.db_path.parent}, reports: {settings.reports_dir})")
    needed = [settings.el_key_name, settings.report_key_name]
    if settings.grader_provider == "jev" and "OPENROUTER_API_KEY" not in needed:
        needed.append("OPENROUTER_API_KEY")
    for k in needed:
        present = bool(settings.el_api_key if k == settings.el_key_name else settings.env.get(k))
        print(f"{k}: {'set' if present else 'MISSING'}")
        ok &= present
    print(f"call source: {settings.source}")
    if settings.source == "elevenlabs":
        print(f"ELEVENLABS_BASE_URL: {settings.el_base_url}")
        wname = settings.env.get("_EL_WRITE_KEY_NAME")
        print(f"agent changes (experiments) use: " + (f"{wname} ({'set' if settings.env.get('ELEVENLABS_WRITE_KEY') else 'MISSING — falls back to read key'})"
                                                      if wname else f"{settings.el_key_name} (no separate write key configured)"))
    print(f"report writer: {settings.report_provider} / {settings.report_model}")
    print(f"grader: {settings.grader_provider}" + (f" / {settings.grader['model']}" if settings.grader_provider == "jev" else " (ElevenLabs evaluation criteria)"))
    print(f"checklist: {settings.checklist.version} ({len(settings.checklist.criteria)} criteria)  outcome: {settings.outcome['version']}")
    if settings.source == "fireflies" and settings.el_api_key:
        client = _client(settings)
        try:
            me = client.whoami()
            print(f"  Fireflies key: OK ({me.get('email') or me.get('name') or 'user'})")
            for a in settings.select_agents(args.agent):
                page, _ = next(client.iter_conversations(agent_id=a.agent_id, after_unix=int(time.time()) - 30 * 86400,
                                                         before_unix=None, page_size=1))
                print(f"  [{a.key}] {a.agent_id}: " + (f"OK (latest meeting: {page[0]['title']!r})" if page
                                                       else "no meetings in the last 30 days"))
        except ElevenLabsError as e:
            print(f"  Fireflies: ERROR {e}")
            ok = False
    elif settings.source == "recordings":
        from .recordings import audio_files
        folder = settings.root / settings.recordings["folder"]
        store = settings.db_path.parent / "transcripts"
        done = len(list(store.glob("*.json"))) if store.is_dir() else 0
        print(f"  recordings folder: {folder} ({'found' if folder.is_dir() else 'MISSING — create it and drop recordings in'})")
        for a in settings.select_agents(args.agent):
            print(f"  [{a.key}] {len(audio_files(folder, a.agent_id))} recording(s)")
        print(f"  transcribed so far: {done} (new files are transcribed by `coach discover` / the daily run)")
        ok &= folder.is_dir()
    elif settings.el_api_key:
        client = _client(settings)
        for a in settings.select_agents(args.agent):
            try:
                cfg = client.get_agent(a.agent_id)
            except ElevenLabsError as e:
                print(f"  [{a.key}] ERROR {e}")
                ok = False
                continue
            crit = [c.get("id") for c in current_criteria(cfg)]
            tools = [t.get("name") for t in ((cfg.get("conversation_config") or {}).get("agent") or {}).get("prompt", {}).get("tools", [])]
            booking = [t for t in tools if t and t.startswith(settings.outcome["booking_tool_prefix"])]
            print(f"  [{a.key}] {cfg.get('name')} | criteria: {crit} | checklist applied: {is_applied(cfg, settings.checklist)} | booking tools: {booking or 'NONE FOUND'}")
            if not booking:
                ok = False
        try:
            page = client.list_conversations_page(agent_id=settings.agents[0].agent_id, after_unix=int(time.time()) - 86400,
                                                  before_unix=None, page_size=1, cursor=None, criteria_ids=[])
            print(f"  list conversations: OK ({len(page['conversations'])} sample)")
        except ElevenLabsError as e:
            print(f"  list conversations: ERROR {e}")
            ok = False
    if settings.report_provider == "anthropic" and settings.env.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            m = anthropic.Anthropic().models.retrieve(settings.claude_model)
            print(f"  Claude model: OK ({m.id})")
        except Exception as e:  # noqa: BLE001 — doctor reports any failure
            print(f"  Claude model: ERROR {type(e).__name__}: {e}")
            ok = False
    or_models = [settings.report_model] if settings.report_provider == "openrouter" else []
    if settings.env.get("OPENROUTER_API_KEY"):
        if or_models:
            ok &= _check_openrouter(settings, or_models)
        if settings.grader_provider == "jev":
            ok &= _jev_test_grade(settings)
    print("doctor:", "OK" if ok else "problems found (see above)")
    return 0 if ok else 1


def cmd_criteria(args, settings) -> int:
    if args.action == "show":
        for c in settings.checklist.criteria:
            print(f"--- {c.id} ({c.name})\n{c.prompt}\n")
        return 0
    client = _client(settings)
    targets = [args.agent_id] if args.agent_id else [a.agent_id for a in settings.select_agents(args.agent)]
    for agent_id in targets:
        cfg = client.get_agent(agent_id)
        existing = current_criteria(cfg)
        merged = merged_criteria(existing, settings.checklist)
        print(f"[{cfg.get('name')}] {agent_id}")
        for line in diff_summary(existing, merged):
            print("   ", line)
        if not args.apply:
            continue
        _write_client(settings).update_agent_criteria(agent_id, merged, f"coach: add checklist {settings.checklist.version} evaluation criteria")
        after = client.get_agent(agent_id)
        if is_applied(after, settings.checklist) and {c.get("id") for c in existing} <= {c.get("id") for c in current_criteria(after)}:
            print("    applied and verified (existing criteria kept)")
        else:
            print("    WARNING: re-read does not show the expected criteria — check the agent's Analysis tab")
            return 1
    if not args.apply:
        print("\nDry run. Re-run with --apply to write these criteria to the agents. "
              "This creates a new agent version; it does not change what the agent says on calls.")
    return 0


def cmd_discover(args, settings, conn) -> int:
    client = _client(settings)
    since = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp()) if args.since else None
    st = discover(conn, client, settings, settings.select_agents(args.agent), days=args.days, since_unix=since)
    print(st.line("discover"), f"| requests {client.requests_made}")
    return 0


def _review(conn, client, settings, agents, limit, since=None) -> None:
    # booking calls first: rare, and they are the success side of every comparison
    st_b = fetch_details(conn, client, settings, agents, limit=limit, since_unix=since, booking=True)
    st = fetch_details(conn, client, settings, agents, limit=max(0, limit - st_b.seen), since_unix=since)
    print(st_b.line("details (booking calls)"))
    print(st.line("details (other calls)"))
    if settings.grader_provider == "jev":
        from .grader import grade_with_jev
        g_b = grade_with_jev(conn, client, settings, agents, limit=limit, since_unix=since, booked=True)
        st = grade_with_jev(conn, client, settings, agents, limit=max(0, limit - g_b.seen), since_unix=since)
        print(g_b.line("jev grading (booked)"), "|", g_b.notes[0])
        print(st.line("jev grading (other)"), "|", st.notes[0])
        return
    applied = {a.key: is_applied(client.get_agent(a.agent_id), settings.checklist) for a in agents}
    ready = [a for a in agents if applied[a.key]]
    if not ready:
        print("backfill: skipped — checklist not pushed to any selected agent yet (`coach criteria push --apply`)")
        return
    st = backfill(conn, client, settings, ready, limit=limit, since_unix=since)
    print(st.line("backfill"))
    for n in st.notes[:5]:
        print("   ", n)


def cmd_review(args, settings, conn) -> int:
    client = _client(settings)
    since, _ = _window(args, settings) if (args.days or args.since) else (None, None)
    _review(conn, client, settings, settings.select_agents(args.agent), args.limit, since)
    print(f"requests {client.requests_made}")
    return 0


def cmd_analyze(args, settings, conn) -> int:
    agents = settings.select_agents(args.agent)
    since, until = _window(args, settings)
    grader = getattr(args, "grader", None) or settings.grader_provider
    calls = load_calls(conn, settings, agents, since, until, grader=grader)
    if not calls:
        print("No calls in the window. Run `coach discover` first.")
        return 1
    payload = analyze(calls, settings, agents, since, until, grader=grader)
    aid = save_analysis(conn, payload)
    print(f"analysis #{aid}  ({payload['outcome_version']}, checklist {payload['checklist_version']}, grader {grader})")
    print(summary_text(payload))
    return 0


def cmd_report(args, settings, conn) -> int:
    from .report import build_report, record_failure

    aid = args.analysis or (conn.execute("SELECT max(id) AS id FROM analyses").fetchone()["id"])
    if not aid:
        print("No analysis yet. Run `coach analyze` first.")
        return 1
    use_llm = not args.numbers_only
    if getattr(args, "model", None):
        settings.env["REPORT_MODEL" if settings.report_provider == "openrouter" else "CLAUDE_MODEL"] = args.model
    if use_llm and not settings.env.get(settings.report_key_name):
        print(f"{settings.report_key_name} missing — writing a numbers-only report (use --numbers-only to silence this).")
        use_llm = False
    client = _client(settings) if use_llm else None
    try:
        path = build_report(conn, client, settings, aid, use_llm=use_llm)
    except Exception as e:  # noqa: BLE001 — grades and analysis are kept; only the report retries
        record_failure(conn, aid, settings.report_model, e)
        print(f"report failed: {type(e).__name__}: {e}\nGrades and analysis #{aid} are saved; re-run `coach report --analysis {aid}`.")
        return 1
    print(f"report written: {path}")
    from .export import write_export
    write_export(conn, settings)
    return 0


def _balanced_sample(conn, client, settings, agents, limit, since) -> None:
    """Pilot sample: up to half calls where the booking tool ran, the rest engaged calls without it, newest first —
    so both sides of the success-vs-failure comparison exist."""
    half = max(1, limit // 2)
    st_b = fetch_details(conn, client, settings, agents, limit=half, since_unix=since, booking=True)
    st_n = fetch_details(conn, client, settings, agents, limit=limit - st_b.seen, since_unix=since, booking=False)
    print(f"details: {st_b.written} booking calls + {st_n.written} engaged calls without booking"
          + (f" ({st_b.unavailable + st_n.unavailable} unavailable)" if st_b.unavailable + st_n.unavailable else ""))
    if settings.grader_provider == "jev":
        from .grader import grade_with_jev
        g1 = grade_with_jev(conn, client, settings, agents, limit=half, since_unix=since, booked=True, log=lambda *a: None)
        g2 = grade_with_jev(conn, client, settings, agents, limit=limit - g1.seen, since_unix=since, booked=False, log=lambda *a: None)
        print(f"jev grading: {g1.written} booked + {g2.written} not booked"
              + (f", {g1.errors + g2.errors} errors" if g1.errors + g2.errors else "") + f" | {g1.notes[0]}, {g2.notes[0]}")
    else:
        ready = [a for a in agents if is_applied(client.get_agent(a.agent_id), settings.checklist)]
        if not ready:
            print("grading: skipped — checklist not pushed to the agents (`coach criteria push --apply`) and GRADER is not jev")
            return
        st = backfill(conn, client, settings, ready, limit=limit, since_unix=since)
        print(st.line("backfill"))


def cmd_pilot(args, settings, conn) -> int:
    agents = settings.select_agents(args.agent)
    client = _client(settings)
    days = args.days or 2
    discover(conn, client, settings, agents, days=days)
    since = int(time.time()) - int(days * 86400)
    _balanced_sample(conn, client, settings, agents, args.limit, since)
    args.days, args.since = days, None
    rc = cmd_analyze(args, settings, conn)
    print("\nPilot rates come from a small mixed sample (booking + non-booking calls); treat them as directional, "
          "not as the agent's overall rates.")
    if rc == 0 and not args.no_report:
        args.analysis, args.numbers_only, args.model = None, False, None
        rc = cmd_report(args, settings, conn)
    print(f"requests {client.requests_made}")
    return rc


def cmd_compare_graders(args, settings, conn) -> int:
    from .compare import agreement, render_agreement

    since, until = _window(args, settings)
    a = agreement(conn, settings, settings.select_agents(args.agent), since, until)
    if not a["calls_with_both"]:
        print("No calls graded by both ElevenLabs and Jev yet. Needs `criteria push --apply` (ElevenLabs side) "
              "and a review run with GRADER=jev.")
        return 1
    path = render_agreement(a, settings.reports_dir / f"grader-comparison-{datetime.now():%Y%m%d-%H%M%S}.md")
    for crit, p in a["per_criterion"].items():
        print(f"  {crit:<30} {p['agree']}/{p['compared']} agree")
    print(f"{len(a['disagreements'])} disagreements listed in {path}")
    return 0


def cmd_spot_check(args, settings, conn) -> int:
    from .compare import spot_check_sheet

    since, until = _window(args, settings)
    grader = args.grader or settings.grader_provider
    client = _client(settings)
    path = spot_check_sheet(conn, settings, settings.select_agents(args.agent), since, until, grader, args.n,
                            client.get_conversation, settings.reports_dir / f"spot-check-{grader}-{datetime.now():%Y%m%d-%H%M%S}.md")
    print(f"spot-check sheet: {path}")
    return 0


def cmd_experiment(args, settings, conn) -> int:
    from . import experiment as ex
    from .outcome import qualify

    o = settings.outcome
    qfn = lambda p: qualify(p, min_turnover=o["min_annual_turnover_nis"], min_asset=o["min_asset_value_nis"],
                            max_turnover=o.get("max_annual_turnover_nis"), max_loan_ratio=o.get("max_loan_to_turnover"))[0]

    if args.action == "list":
        for r in conn.execute("SELECT * FROM experiments ORDER BY id"):
            state = "running" if r["stopped_at"] is None else "stopped"
            print(f"#{r['id']} {r['agent_key']:<8} {r['name']:<34} {state:<8} since {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['started_at']))}"
                  f"  variant {r['variant_pct']:.0f}%")
        return 0

    if args.action in ("plan", "start"):
        if not args.agent or len(args.agent) != 1:
            print("Pick exactly one agent: --agent cold")
            return 2
        agent = settings.agent(args.agent[0])
        new = open(args.new_file, encoding="utf-8").read().strip() if args.new_file else (args.new or "")
        client = _client(settings)
        cfg = client.get_agent(agent.agent_id)
        p = ex.plan(cfg, new, args.pct)
        running = conn.execute("SELECT id FROM experiments WHERE agent_id=? AND stopped_at IS NULL", (agent.agent_id,)).fetchone()
        print(f"Agent: {agent.key} — {agent.label} ({agent.agent_id})")
        print(f"Control (current main branch {p['control_branch_id']}): {100 - p['variant_pct']:.0f}% of calls")
        print(f"Variant (new branch from version {p['parent_version_id']}): {p['variant_pct']:.0f}% of calls")
        print("\nOnly the opener changes.\n--- current opener ---\n" + p["old"] + "\n--- new opener ---\n" + p["new"])
        if running:
            print(f"\nNot started: experiment #{running['id']} is still running on this agent. Stop it first.")
            return 1
        if args.action == "plan" or not args.apply:
            print("\nDry run. Nothing was changed. Add --apply to `experiment start` to create the branch and split traffic.")
            return 0
        name = args.name or f"coach-opener-{time.strftime('%Y%m%d-%H%M')}"
        client = _write_client(settings)
        res = client.create_branch(agent.agent_id, parent_version_id=p["parent_version_id"], name=name,
                                   description="coach A/B test: first message only", conversation_config={"agent": {"first_message": p["new"]}})
        bid = res.get("created_branch_id") or res.get("branch_id")
        if not bid:
            print(f"Branch creation returned no branch id: {res}")
            return 1
        print(f"\nCreated branch {bid}.")
        client.set_traffic(agent.agent_id, {p["control_branch_id"]: 100 - p["variant_pct"], bid: p["variant_pct"]})
        eid = ex.record(conn, agent, name, p, bid)
        print(f"Traffic split set. Experiment #{eid} is running. Check it with `./coach experiment status`.")
        return 0

    row = (conn.execute("SELECT * FROM experiments WHERE id=?", (args.id,)).fetchone() if args.id else
           conn.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT 1").fetchone())
    if not row:
        print("No experiments yet.")
        return 1

    if args.action == "promote":
        change = json.loads(row["change"])
        print(f"Experiment #{row['id']} on {row['agent_key']}: make the NEW opener the default for 100% of calls.\n"
              "Steps: write the new opener to main (only that field) → verify → traffic 100% to main.")
        if not args.apply:
            print("Dry run. Add --apply to do it.")
            return 0
        client = _write_client(settings)
        diff = ex.set_opener_on_main(client, row["agent_id"], change["new"],
                                     f"coach: promote experiment #{row['id']} opener to 100%")
        client.set_traffic(row["agent_id"], {row["control_branch_id"]: 100.0})
        conn.execute("UPDATE experiments SET stopped_at=? WHERE id=?", (int(time.time()), row["id"]))
        conn.commit()
        print(f"Done: new opener on 100% of calls (changed: {diff}). Experiment closed; the test branch stays at 0%.")
        return 0

    if args.action == "stop":
        print(f"Experiment #{row['id']} on {row['agent_key']}: send 100% of calls back to the control branch {row['control_branch_id']}.")
        if not args.apply:
            print("Dry run. Add --apply to stop it. The variant branch is kept (merge it in ElevenLabs if it won).")
            return 0
        _write_client(settings).set_traffic(row["agent_id"], {row["control_branch_id"]: 100.0})
        conn.execute("UPDATE experiments SET stopped_at=? WHERE id=?", (int(time.time()), row["id"]))
        conn.commit()
        print("Stopped: 100% of calls on the original opener again.")
        return 0

    # status
    r = ex.readout(conn, row, qfn, o["booking_tool_prefix"])
    a, b = r["arms"]["control"], r["arms"]["variant"]
    rate = lambda n, d: "—" if not d else f"{n / d * 100:.1f}%"
    print(f"Experiment #{row['id']} — {row['agent_key']} — {row['name']} "
          f"({'running' if row['stopped_at'] is None else 'stopped'}, since {time.strftime('%Y-%m-%d %H:%M', time.localtime(row['started_at']))})")
    print(f"{'':<26}{'old opener':>14}{'new opener':>14}{'difference':>14}")
    print(f"{'calls':<26}{a['calls']:>14}{b['calls']:>14}")
    print(f"{'picked up (human)':<26}{a['connected']:>14}{b['connected']:>14}")
    for label, key, n_a, n_b in [("never spoke", "no_reply_rate", a["no_reply"], b["no_reply"]),
                                 ("real conversation", "engaged_rate", a["engaged"], b["engaged"]),
                                 ("booking tool used", "booking_rate", a["booking_tool_calls"], b["booking_tool_calls"])]:
        d = r[key]
        diff = "—" if d["diff_pp"] is None else f"{d['diff_pp']:+.1f} pp"
        sig = "" if d["p_value"] is None else ("  ← real difference" if d["p_value"] < 0.05 else "  (could be luck)")
        print(f"{label + ' (of picked up)':<26}{rate(n_a, a['connected']):>14}{rate(n_b, b['connected']):>14}{diff:>14}{sig}")
    print(f"{'qualified bookings*':<26}{a['qualified_bookings']:>14}{b['qualified_bookings']:>14}")
    need = r["needed_per_arm_for_5pp_no_reply"]
    have = min(a["connected"], b["connected"])
    print(f"\nTo detect a 5-point change in 'never spoke' you need about {need} picked-up calls per side; you have {have}."
          + (" Keep it running." if have < need else " Enough data for that metric."))
    print("* qualified bookings count only calls downloaded in full — run `./coach discover` and `./coach review` first.")
    if r["unassigned_calls"]:
        print(f"({r['unassigned_calls']} calls in this period were on other branches.)")
    return 0


def cmd_opener(args, settings, conn) -> int:
    """Puts a new opener on each named agent at 100%: updates only the opener on main, then verifies nothing else changed."""
    from . import experiment as ex

    if not args.agent:
        print("Name the agents: --agent cold --agent twilio")
        return 2
    new = open(args.new_file, encoding="utf-8").read().strip()
    client = _write_client(settings)
    for key in args.agent:
        agent = settings.agent(key)
        if conn.execute("SELECT 1 FROM experiments WHERE agent_id=? AND stopped_at IS NULL", (agent.agent_id,)).fetchone():
            print(f"[{key}] skipped: an experiment is running on this agent — use `experiment promote` or `stop` first.")
            continue
        cfg = client.get_agent(agent.agent_id)
        p = ex.plan(cfg, new, 50)          # same guards: not empty, not identical, keeps {{CONTACT_NAME}}
        print(f"[{key}] {agent.label}: opener → new text (100% of calls)")
        if not args.apply:
            print("    dry run — nothing changed")
            continue
        diff = ex.set_opener_on_main(client, agent.agent_id, new, args.name or "coach: new opener on 100%")
        now = int(time.time())
        conn.execute(
            """INSERT INTO experiments(agent_id, agent_key, name, change, control_branch_id, variant_branch_id, variant_pct,
                   started_at, stopped_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (agent.agent_id, agent.key, args.name or "opener set 100%",
             json.dumps({"field": "first_message", "old": p["old"], "new": new}, ensure_ascii=False),
             p["control_branch_id"], p["control_branch_id"], 100.0, now, now))
        conn.commit()
        print(f"    done and verified: new opener live on main (changed: {diff})")
    if not args.apply:
        print("\nDry run. Add --apply to change the live agents.")
    return 0


def cmd_export(args, settings, conn) -> int:
    from .export import write_export
    path = write_export(conn, settings)
    print(f"dashboard data written: {path}")
    return 0


def cmd_watch(args, settings, conn) -> int:
    agents = settings.select_agents(args.agent)
    print(f"watching {', '.join(a.key for a in agents)} every {args.interval}s — Ctrl+C to stop. "
          "This only runs while this process is alive; use launchd/cron or a server for unattended operation.")
    while True:
        try:
            client = _client(settings)
            discover(conn, client, settings, agents)
            _review(conn, client, settings, agents, args.limit)
            args.days, args.since = None, None
            cmd_analyze(args, settings, conn)
        except RequestCapReached as e:
            print(f"cycle stopped early: {e}")
        except ElevenLabsError as e:
            print(f"cycle failed: {e} — will retry next interval")
        time.sleep(args.interval)


def cmd_schedule(args) -> int:
    from . import schedule as sch

    if args.action == "install":
        sch.install(args.hour, args.minute)
        print(f"\nEvery day at {args.hour:02d}:{args.minute:02d} (this computer's local time) the calls are refreshed, graded,"
              f" and a new report is written. Dashboard: http://localhost:{sch.PORT} — always on, no terminal needed.\n"
              "It runs while the Mac is on. If the Mac is asleep at that time, it runs when it wakes up.")
        return 0
    if args.action == "uninstall":
        sch.uninstall()
        return 0
    if args.action == "run-now":
        sch.run_now()
        print("Daily refresh started in the background. Follow it with `./coach schedule status`.")
        return 0
    st = sch.status()
    names = {sch.DAILY: f"Daily refresh (07:00)", sch.DASH: "Dashboard (always on)"}
    for label in (sch.DAILY, sch.DASH):
        i = st[label]
        state = "not installed" if not i["installed"] else ("loaded" if i["loaded"] else "installed but not loaded")
        extra = ", ".join(f"{k} {i[k]}" for k in ("state", "runs", "last_exit") if k in i)
        print(f"{names[label]:<26} {state}{' — ' + extra if extra else ''}")
    print(f"{'Dashboard reachable':<26} {'yes — http://localhost:%d' % sch.PORT if st['dashboard_up'] else 'no'}")
    if st["last_log"]:
        print(f"\nLast daily log: {st['last_log']}")
        tail = open(st["last_log"], encoding="utf-8").read().splitlines()[-8:]
        print("\n".join("  " + l for l in tail))
    return 0


def cmd_dashboard(args) -> int:
    from . import schedule as sch

    if args.action == "on":
        sch.dashboard_start()
    elif args.action == "update":
        sch.dashboard_stop(log=lambda *a: None)
        sch.dashboard_start(rebuild=True)
    elif args.action == "off":
        sch.dashboard_stop()
    elif args.action == "open":
        if not sch._port_open():
            print("The dashboard is off — starting it…")
            sch.dashboard_start()
        subprocess.run(["open", f"http://localhost:{sch.PORT}"], check=False)
    else:
        print(f"Dashboard: {'ON — http://localhost:%d' % sch.PORT if sch._port_open() else 'OFF'}")
        print(f"Always-on mode: {'yes (starts with the Mac)' if sch._plist_path(sch.DASH).exists() else 'no'}")
    return 0


HELP_TEXT = """Sales Call Coach — the commands you need

  Dashboard (the web page in Chrome)
    coach dashboard on        turn it on (stays on, also after a restart)
    coach dashboard off       turn it off
    coach dashboard open      open it in Chrome (turns it on if needed)
    coach dashboard status    is it on or off?
    coach dashboard update    rebuild + restart it after a code change (not needed for the daily numbers)

  Daily 07:00 refresh (runs by itself)
    coach schedule status     did this morning's refresh run? (shows the log)
    coach schedule run-now    run the refresh right now

  Everything else
    coach                     the numbered menu (all of the above + more)
    coach help                this list
"""


def cmd_help(args) -> int:
    print(HELP_TEXT)
    return 0


def cmd_accounts(args) -> int:
    accounts = list_accounts()
    cur = current_account()
    if not accounts:
        print("No accounts. Copy config/accounts/_template.toml to config/accounts/<client>.toml")
        return 1
    for key, a in accounts.items():
        source = a.get("source") or "elevenlabs"
        env_name = a.get("api_key_env") or SOURCES.get(source, "ELEVENLABS_API_KEY")
        has_key = bool(os.environ.get(env_name))
        mark = "*" if key == cur else " "
        print(f"{mark} {key:<14} {a.get('label', key):<32} {source:<11} {len(a.get('agents', []))} agent(s)   "
              f"{env_name}: {'set' if has_key else 'MISSING'}")
    print("\n* = current account. Switch with `./coach use <account>`.")
    return 0


def cmd_use(args) -> int:
    set_current_account(args.account_name)
    s = load_settings(account=args.account_name)
    print(f"Now using account '{s.account}' — {s.account_label} ({len(s.agents)} agents). "
          f"Data: {s.db_path.parent}  Reports: {s.reports_dir}")
    return 0


def _slug(name: str, taken: set[str]) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    base = "-".join(words[-2:])[:24] or "agent"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}{n}", n + 1
    return key


def cmd_agents(args, settings) -> int:
    from .account_edit import account_file, add_agent, remove_agent, set_enabled

    path = account_file(settings.account)
    action = args.action

    if action == "list":
        print(f"Agents in account '{settings.account}' ({path.relative_to(settings.root)}):")
        for a in settings.agents:
            print(f"  {'●' if a.enabled else '○ paused'}  {a.key:<10} {a.agent_id}  {a.label}")
        if settings.source == "fireflies":
            print(f"\n● = included in runs   ○ = paused\nAdd a salesperson: ./coach agents add <their email> --label \"Name\"  "
                  "(or 'all' for every meeting the key can see)")
            return 0
        if settings.source == "recordings":
            print(f"\n● = included in runs   ○ = paused\nAdd a salesperson: ./coach agents add <subfolder name> --label \"Name\"  "
                  f"(their recordings in {settings.recordings['folder']}/<subfolder>; 'all' = the whole folder)")
            return 0
        if not args.all:
            print("\n● = included in runs   ○ = paused\nSee every agent in the ElevenLabs account: ./coach agents list --all")
            return 0
        client = _client(settings)
        configured = {a.agent_id: a for a in settings.agents}
        remote = sorted(client.list_agents(), key=lambda a: -(a.get("last_7_day_call_count") or 0))
        print(f"\nAll agents visible to {settings.el_key_name}:")
        for a in remote:
            c = configured.get(a.get("agent_id"))
            mark = ("● " + c.key) if c and c.enabled else ("○ " + c.key) if c else "+ not added"
            print(f"  {mark:<16} {a.get('agent_id')}  {(a.get('last_7_day_call_count') or 0):>6} calls/7d  {a.get('name')}")
        print("\nAdd one: ./coach agents add <agent_id>")
        return 0

    if not args.target:
        print(f"usage: ./coach agents {action} <{'agent_id' if action == 'add' else 'key or agent_id'}>")
        return 2

    if action == "add":
        agent_id = args.target
        label = args.label
        if settings.source != "elevenlabs":
            label = label or agent_id          # a salesperson's email (meeting organizer), or "all"
        elif settings.el_api_key:
            try:
                remote = _client(settings).get_agent(agent_id)       # proves the id exists in THIS account
            except ElevenLabsError as e:
                print(f"Not added: {agent_id} is not reachable with {settings.el_key_name} ({e}).")
                return 1
            label = label or remote.get("name") or agent_id
            tools = [t.get("name") for t in ((remote.get("conversation_config") or {}).get("agent") or {}).get("prompt", {}).get("tools", [])]
            if not any(t and t.startswith(settings.outcome["booking_tool_prefix"]) for t in tools):
                print(f"Warning: no tool starting with '{settings.outcome['booking_tool_prefix']}' on this agent — "
                      "bookings won't be detected for it.")
        elif not label:
            print(f"{settings.el_key_name} isn't set, so the agent can't be verified. Add --label \"Name\" to add it anyway.")
            return 1
        key = args.key or _slug(label, {a.key for a in settings.agents})
        add_agent(path, agent_id=agent_id, key=key, label=label)
        print(f"Added '{key}' → {agent_id} ({label}) to account '{settings.account}'.")
        return 0

    if action == "remove":
        f = remove_agent(path, args.target)
        print(f"Removed '{f.get('key')}' ({f.get('agent_id')}) from account '{settings.account}'. "
              "Its past calls stay in the database but are no longer included in runs.")
        return 0

    f = set_enabled(path, args.target, enabled=(action == "resume"))
    print(f"{'Resumed' if action == 'resume' else 'Paused'} '{f.get('key')}' ({f.get('agent_id')}).")
    return 0


# ----------------------------------------------------------------------------- entry point
ELEVENLABS_ONLY = {"criteria push": "adds the checklist to the agents", "experiment": "A/B tests an agent's opener",
                   "opener": "changes an agent's opener"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coach", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", help="client account from config/accounts/ (default: the one chosen with `coach use`)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts", help="list client accounts and whether their keys are set")
    sp = sub.add_parser("use", help="choose the client account for following commands")
    sp.add_argument("account_name")
    sp = sub.add_parser("dashboard", help="turn the dashboard on/off, open it, or check it")
    sp.add_argument("action", nargs="?", default="status", choices=["on", "off", "open", "status", "update"])
    sub.add_parser("help", help="the short list of commands you need")
    sp = sub.add_parser("schedule", help="daily 07:00 refresh + always-on dashboard (macOS launchd)")
    sp.add_argument("action", choices=["install", "uninstall", "status", "run-now"])
    sp.add_argument("--hour", type=int, default=7)
    sp.add_argument("--minute", type=int, default=0)
    sp = sub.add_parser("agents", help="list / add / remove / pause / resume the agents of the current account")
    sp.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove", "pause", "resume"])
    sp.add_argument("target", nargs="?", help="agent id (add) or agent key/id (remove, pause, resume)")
    sp.add_argument("--all", action="store_true", help="list: also show every agent in the ElevenLabs account")
    sp.add_argument("--key", help="add: short name to use with --agent (default: from the agent's name)")
    sp.add_argument("--label", help="add: display name (default: the agent's name in ElevenLabs)")

    def agent_opt(sp):
        sp.add_argument("--agent", action="append", help="agent key from config (repeatable); default: all")

    def window_opt(sp):
        sp.add_argument("--days", type=float)
        sp.add_argument("--since", help="YYYY-MM-DD")

    sp = sub.add_parser("doctor"); agent_opt(sp)
    sp = sub.add_parser("criteria"); agent_opt(sp)
    sp.add_argument("action", choices=["show", "push"])
    sp.add_argument("--apply", action="store_true", help="actually write to the agents (default: dry run)")
    sp.add_argument("--agent-id", help="push to an agent id not in config (e.g. a test agent)")
    sp = sub.add_parser("discover"); agent_opt(sp); window_opt(sp)
    sp = sub.add_parser("pilot"); agent_opt(sp)
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--days", type=float, help="how far back to sample (default 2)")
    sp.add_argument("--no-report", action="store_true")
    sp = sub.add_parser("review"); agent_opt(sp); window_opt(sp)
    sp.add_argument("--limit", type=int, default=300)
    sp = sub.add_parser("analyze"); agent_opt(sp); window_opt(sp)
    sp.add_argument("--grader", choices=["elevenlabs", "jev"], help="whose checklist grades to compare (default: config/GRADER)")
    sp = sub.add_parser("compare-graders"); agent_opt(sp); window_opt(sp)
    sp = sub.add_parser("spot-check"); agent_opt(sp); window_opt(sp)
    sp.add_argument("--n", type=int, default=15)
    sp.add_argument("--grader", choices=["elevenlabs", "jev"])
    sp = sub.add_parser("report")
    sp.add_argument("--analysis", type=int)
    sp.add_argument("--numbers-only", action="store_true")
    sp.add_argument("--model", help="override the report model for this run, e.g. openai/gpt-6-luna")
    sp = sub.add_parser("experiment", help="A/B test a new opener on a branch with a traffic split")
    agent_opt(sp)
    sp.add_argument("action", choices=["plan", "start", "status", "stop", "promote", "list"])
    sp.add_argument("--new", help="new first message text")
    sp.add_argument("--new-file", help="file containing the new first message")
    sp.add_argument("--pct", type=float, default=50.0, help="share of calls for the new opener (default 50)")
    sp.add_argument("--name", help="branch name (default coach-opener-<date>)")
    sp.add_argument("--id", type=int, help="experiment id for status/stop (default: latest)")
    sp.add_argument("--apply", action="store_true", help="start/stop: actually change the live agent's traffic")
    sp = sub.add_parser("opener", help="set a new opener at 100% on agents (only that field; verified after)")
    agent_opt(sp)
    sp.add_argument("action", choices=["set"])
    sp.add_argument("--new-file", required=True)
    sp.add_argument("--name")
    sp.add_argument("--apply", action="store_true")
    sub.add_parser("export", help="write the dashboard data file for the current client")
    sp = sub.add_parser("watch"); agent_opt(sp)
    sp.add_argument("--interval", type=int, default=900)
    sp.add_argument("--limit", type=int, default=200, help="max detail/backfill calls per cycle")
    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from .menu import run_menu
        return run_menu(lambda a: main(a))
    args = build_parser().parse_args(argv)
    try:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        if args.cmd == "accounts":
            return cmd_accounts(args)
        if args.cmd == "use":
            return cmd_use(args)
        if args.cmd == "schedule":
            return cmd_schedule(args)
        if args.cmd == "dashboard":
            return cmd_dashboard(args)
        if args.cmd == "help":
            return cmd_help(args)
        settings = load_settings(account=args.account)
        print(f"[account: {settings.account}]", file=sys.stderr)
        what = ELEVENLABS_ONLY.get("criteria push" if args.cmd == "criteria" and args.action == "push" else args.cmd)
        if what and settings.source != "elevenlabs":
            raise ConfigError(f"`coach {args.cmd}` ({what}) only works for ElevenLabs agents; "
                              f"account '{settings.account}' reads calls from {settings.source}.")
        if args.cmd == "agents":
            return cmd_agents(args, settings)
        if args.cmd == "doctor":
            return cmd_doctor(args, settings)
        if args.cmd == "criteria":
            return cmd_criteria(args, settings)
        handlers = {"discover": cmd_discover, "pilot": cmd_pilot, "review": cmd_review, "analyze": cmd_analyze,
                    "report": cmd_report, "watch": cmd_watch, "compare-graders": cmd_compare_graders,
                    "spot-check": cmd_spot_check, "experiment": cmd_experiment,
                    "opener": cmd_opener, "export": cmd_export}
        conn = db.connect(settings.db_path)
        with db.process_lock(settings.db_path):
            return handlers[args.cmd](args, settings, conn)
    except (ConfigError, CredentialsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except RequestCapReached as e:
        print(f"stopped: {e}. Progress is saved; re-run to continue.", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\ninterrupted — progress is saved; re-run to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
