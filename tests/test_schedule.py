from coach import schedule as sch


def test_daily_job_runs_at_seven_and_logs():
    p = sch.daily_plist()
    assert p["StartCalendarInterval"] == {"Hour": 7, "Minute": 0}
    assert p["ProgramArguments"][-2:] == ["-m", "coach.daily"]
    assert "/.local/bin" in p["EnvironmentVariables"]["PATH"]


def test_dashboard_job_is_always_on_production():
    p = sch.dashboard_plist()
    assert p["KeepAlive"] is True and p["RunAtLoad"] is True
    assert p["ProgramArguments"][1].endswith("/dashboard/.next/standalone/server.js")
    assert p["EnvironmentVariables"]["PORT"] == str(sch.PORT)
    assert p["EnvironmentVariables"]["COACH_DATA_DIR"].endswith("/dashboard/data")
    assert p["WorkingDirectory"].endswith("/dashboard")
