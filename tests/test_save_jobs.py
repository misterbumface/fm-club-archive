import threading

import pytest

from app.services import save_jobs


def _wait_until(check, seconds=5.0):
    done = threading.Event()
    for _ in range(int(seconds / 0.02)):
        if check():
            return True
        done.wait(0.02)
    return False


def test_one_job_at_a_time_and_its_state(app, tmp_path):
    app.instance_path = str(tmp_path)
    assert save_jobs.job(app) == {"status": None, "busy": False, "state": None}

    release = threading.Event()

    def work(set_status):
        set_status("Importing: reading the players")
        release.wait(5)
        set_status("Done: imported the save.")

    save_jobs._start(app, work)
    assert _wait_until(lambda: save_jobs.status(app) == "Importing: reading the players")
    assert save_jobs.job(app)["state"] == "working"
    with pytest.raises(RuntimeError):
        save_jobs._start(app, work)

    release.set()
    assert _wait_until(lambda: not save_jobs.busy(app))
    assert save_jobs.job(app) == {"status": "Done: imported the save.", "busy": False, "state": "done"}


def test_a_failed_job_and_a_job_cut_short(app, tmp_path):
    app.instance_path = str(tmp_path)

    def work(set_status):
        raise RuntimeError("database is locked")

    save_jobs._start(app, work)
    assert _wait_until(lambda: not save_jobs.busy(app) and save_jobs.status(app) != "Starting…")
    assert save_jobs.job(app)["state"] == "failed"
    assert save_jobs.job(app)["status"] == "Failed: database is locked"

    save_jobs._setter(app)("Importing: reading the players")  # the process stopped here
    job = save_jobs.job(app)
    assert job["state"] == "failed" and "restarted" in job["status"]
