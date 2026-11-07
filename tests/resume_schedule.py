from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import cast, final, override
from unittest.mock import patch

from scripts import resume_schedule as scheduler


@final
class ResumeScheduleTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "schedules"
        self.agents = self.directory / "LaunchAgents"
        self.registry = self.directory / ".config/session-guard/active-sessions.json"
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text("[]")
        self.context = contextlib.ExitStack()
        self.addCleanup(self.context.close)
        self.context.enter_context(patch.object(scheduler, "ROOT", self.root))
        self.context.enter_context(patch.object(scheduler, "AGENTS", self.agents))
        self.context.enter_context(patch.object(scheduler, "install_helper"))
        self.context.enter_context(
            patch.object(Path, "home", return_value=self.directory)
        )
        self.context.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.context.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.launchctl = self.context.enter_context(
            patch.object(
                scheduler,
                "launchctl",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            )
        )

    def job(self, *, session: str = "") -> scheduler.Job:
        return scheduler.Job(
            id=str(uuid.uuid4()),
            tool="claude",
            session=session,
            at=int(time.time()) - 1,
            directory=str(self.directory),
            wake="",
            command=["claude", "continue"],
            path=os.environ["PATH"],
            state="scheduled",
            detail="",
        )

    def store(self, job: scheduler.Job) -> None:
        with scheduler.locked():
            scheduler.save(job)

    def state(self, job: scheduler.Job) -> str:
        return scheduler.read_job(job["id"])["state"]

    def test_schedule_persists_command_and_calendar_with_login_recovery(self) -> None:
        job = self.job()
        job["at"] += 600
        job["command"] = ["claude", "a prompt\nwith 'quotes' and $(commands)"]
        scheduler.add(job)
        self.assertEqual(scheduler.read_job(job["id"]), job)
        plist = cast(
            "dict[str, object]", plistlib.loads(scheduler.plist_path(job).read_bytes())
        )
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(
            set(cast("dict[str, int]", plist["StartCalendarInterval"])),
            {"Month", "Day", "Hour", "Minute"},
        )
        self.assertEqual(
            cast("list[str]", plist["ProgramArguments"])[-2:], ["run", job["id"]]
        )
        self.launchctl.assert_called_once()
        with patch.object(scheduler, "open_session") as open_session:
            scheduler.run(job["id"])
        open_session.assert_not_called()
        self.assertEqual(self.state(job), "scheduled")

    def test_overdue_job_runs_once_even_if_dispatched_again(self) -> None:
        job = self.job()
        self.store(job)

        def accept(pending: scheduler.Job) -> bool:
            return scheduler.claim(pending["id"])

        with patch.object(
            scheduler, "open_session", side_effect=accept
        ) as open_session:
            scheduler.run(job["id"])
            scheduler.run(job["id"])
        open_session.assert_called_once()
        self.assertEqual(self.state(job), "started")

    def test_cancel_before_due_prevents_launch_and_removes_calendar(self) -> None:
        job = self.job()
        scheduler.add(job)
        scheduler.cancel(job["id"])
        with patch.object(scheduler, "open_session") as open_session:
            scheduler.run(job["id"])
        open_session.assert_not_called()
        self.assertEqual(self.state(job), "canceled")
        self.assertFalse(scheduler.plist_path(job).exists())

    def test_cancel_during_opening_rejects_a_late_tab(self) -> None:
        job = self.job()
        job["state"] = "opening"
        self.store(job)
        scheduler.cancel(job["id"])
        self.assertFalse(scheduler.claim(job["id"]))
        self.assertEqual(self.state(job), "canceled")

    def test_interrupted_opening_is_reported_without_another_tab(self) -> None:
        job = self.job()
        job["state"] = "opening"
        self.store(job)
        with patch.object(scheduler, "open_session") as open_session:
            scheduler.run(job["id"])
        open_session.assert_not_called()
        self.assertEqual(self.state(job), "failed")
        self.assertIn("interrupted", scheduler.read_job(job["id"])["detail"])
        self.assertFalse(scheduler.claim(job["id"]))

    def test_open_failure_is_visible_and_does_not_repeat(self) -> None:
        job = self.job()
        self.store(job)
        with patch.object(
            scheduler, "open_session", side_effect=OSError("terminal unavailable")
        ) as open_session:
            scheduler.run(job["id"])
            scheduler.run(job["id"])
        open_session.assert_called_once()
        self.assertEqual(self.state(job), "failed")
        self.assertEqual(
            scheduler.read_job(job["id"])["detail"], "terminal unavailable"
        )

    def test_duplicate_pending_schedule_is_rejected(self) -> None:
        first = self.job(session=str(uuid.uuid4()))
        scheduler.add(first)
        with self.assertRaisesRegex(ValueError, first["id"]):
            scheduler.add(self.job(session=first["session"]))
        self.assertEqual(len(scheduler.jobs()), 1)

    def test_named_session_is_frozen_to_its_id_before_scheduling(self) -> None:
        identifier = str(uuid.uuid4())
        self.registry.write_text(
            json.dumps(
                [
                    {
                        "session_id": identifier,
                        "tool": "claude",
                        "session_name": "Example session",
                    }
                ]
            )
        )
        job = self.job(session="Example session")
        job["command"] = ["claude", "--resume", "Example session", "Example session"]
        scheduler.add(job)
        saved = scheduler.read_job(job["id"])
        self.assertEqual(saved["session"], identifier)
        self.assertEqual(
            saved["command"], ["claude", "--resume", identifier, "Example session"]
        )

    def test_unknown_session_name_fails_before_registering_a_job(self) -> None:
        with self.assertRaisesRegex(ValueError, "full ID"):
            scheduler.add(self.job(session="unrecorded-name"))
        self.launchctl.assert_not_called()

    def test_rejected_schedule_cancels_its_requested_wake(self) -> None:
        job = self.job()
        job["directory"] = str(self.directory / "missing")
        with (
            patch.object(scheduler, "cancel_wake") as cancel_wake,
            self.assertRaisesRegex(ValueError, "does not exist"),
        ):
            scheduler.add(job)
        cancel_wake.assert_called_once_with(job)

    def test_bootstrap_failure_stays_visible_without_calendar(self) -> None:
        job = self.job()
        self.launchctl.return_value = subprocess.CompletedProcess(
            [], 1, "", "permission denied"
        )
        with self.assertRaisesRegex(OSError, "permission denied"):
            scheduler.add(job)
        self.assertEqual(self.state(job), "failed")
        self.assertFalse(scheduler.plist_path(job).exists())

    def test_live_session_is_skipped_using_exact_process_identity(self) -> None:
        job = self.job(session=str(uuid.uuid4()))
        self.registry.write_text(
            json.dumps(
                [
                    {
                        "session_id": job["session"],
                        "tool": "claude",
                        "pid": os.getpid(),
                        "pid_started_at": scheduler.process_start(os.getpid()),
                    }
                ]
            )
        )
        self.store(job)
        with patch.object(scheduler, "open_session") as open_session:
            scheduler.run(job["id"])
        open_session.assert_not_called()
        self.assertEqual(self.state(job), "skipped")

    def test_recycled_pid_does_not_count_as_a_running_session(self) -> None:
        job = self.job(session=str(uuid.uuid4()))
        self.registry.write_text(
            json.dumps(
                [
                    {
                        "session_id": job["session"],
                        "tool": "claude",
                        "pid": os.getpid(),
                        "pid_started_at": "Thu Jan 1 00:00:00 1970",
                    }
                ]
            )
        )
        self.assertFalse(scheduler.session_running(job))

    def test_claim_rechecks_a_session_resumed_while_waiting_for_unlock(self) -> None:
        job = self.job(session=str(uuid.uuid4()))
        job["state"] = "opening"
        self.store(job)
        with patch.object(scheduler, "session_running", return_value=True):
            self.assertFalse(scheduler.claim(job["id"]))
        self.assertEqual(self.state(job), "skipped")

    def test_command_reaches_the_shell_without_prompt_interpolation(self) -> None:
        fake_claim = self.directory / "claim.py"
        fake_claim.write_text("raise SystemExit(0)\n")
        capture = self.directory / "capture.py"
        output = self.directory / "arguments.json"
        capture.write_text(
            "import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n"
        )
        job = self.job()
        prompt = "quotes ' \" ; $(touch should-not-exist)\nsecond line"
        job["command"] = [scheduler.PYTHON, str(capture), str(output), prompt]
        with patch.object(scheduler, "SCRIPT", fake_claim):
            result = subprocess.run(
                ["/bin/zsh", "-f", "-c", scheduler.command_line(job)],
                cwd=self.directory,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text()), [prompt])
        self.assertFalse((self.directory / "should-not-exist").exists())

    def test_finished_job_cannot_be_canceled(self) -> None:
        job = self.job()
        job["state"] = "started"
        self.store(job)
        with self.assertRaisesRegex(ValueError, "already started"):
            scheduler.cancel(job["id"])

    def test_grok_rechecks_sign_in_after_models_refreshes_credentials(self) -> None:
        with (
            patch.object(scheduler, "grok_auth_revision", side_effect=[(1, 1), (2, 2)]),
            patch.object(
                scheduler,
                "grok_models",
                side_effect=[
                    subprocess.CompletedProcess(
                        [], 0, "You are not authenticated.\n", ""
                    ),
                    subprocess.CompletedProcess(
                        [], 0, "You are logged in with grok.com.\n", ""
                    ),
                ],
            ) as models,
        ):
            scheduler.check_grok_sign_in(str(self.directory))
        self.assertEqual(models.call_count, 2)

    def test_grok_signed_out_without_a_refresh_is_not_retried(self) -> None:
        with (
            patch.object(scheduler, "grok_auth_revision", return_value=None),
            patch.object(
                scheduler,
                "grok_models",
                return_value=subprocess.CompletedProcess(
                    [], 0, "You are not authenticated.\n", ""
                ),
            ) as models,
            self.assertRaisesRegex(ValueError, "did not confirm sign-in"),
        ):
            scheduler.check_grok_sign_in(str(self.directory))
        models.assert_called_once_with(str(self.directory))

    def test_grok_error_keeps_its_exit_code_and_diagnostic(self) -> None:
        with (
            patch.object(scheduler, "grok_auth_revision", return_value=None),
            patch.object(
                scheduler,
                "grok_models",
                return_value=subprocess.CompletedProcess(
                    [], 2, "", "network unavailable"
                ),
            ),
            self.assertRaisesRegex(ValueError, "exited 2:\nnetwork unavailable"),
        ):
            scheduler.check_grok_sign_in(str(self.directory))

    def test_grok_unknown_output_does_not_start_a_session(self) -> None:
        with (
            patch.object(scheduler, "grok_auth_revision", return_value=None),
            patch.object(
                scheduler,
                "grok_models",
                return_value=subprocess.CompletedProcess(
                    [], 0, "Available models: grok-4.7", ""
                ),
            ),
            self.assertRaisesRegex(ValueError, "did not confirm sign-in"),
        ):
            scheduler.check_grok_sign_in(str(self.directory))


if __name__ == "__main__":
    unittest.main()
