#!/opt/homebrew/bin/python3
from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast, final

if TYPE_CHECKING:
    from collections.abc import Generator

ROOT = Path.home() / "Library/Application Support/dotfiles-resume"
AGENTS = Path.home() / "Library/LaunchAgents"
APP = Path.home() / "Applications/Resume Scheduler.app"
EXECUTABLE = APP / "Contents/MacOS/ResumeScheduler"
SCRIPT = Path(__file__).resolve()
PYTHON = "/opt/homebrew/bin/python3"
LABEL = "com.tylerlaprade.resume."
PENDING = {"scheduled", "opening"}
MINUTE = 60
ACK_TIMEOUT = 30


class Job(TypedDict):
    id: str
    tool: str
    session: str
    at: int
    directory: str
    wake: str
    command: list[str]
    path: str
    state: str
    detail: str


@final
class ProcessInfo(ctypes.Structure):
    _fields_ = (
        [
            (name, ctypes.c_uint32)
            for name in (
                "flags",
                "status",
                "xstatus",
                "pid",
                "ppid",
                "uid",
                "gid",
                "ruid",
                "rgid",
                "svuid",
                "svgid",
                "reserved",
            )
        ]
        + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
        + [
            (name, ctypes.c_uint32)
            for name in (
                "nfiles",
                "pgid",
                "jobc",
                "tty",
                "tpgid",
                "nice",
            )
        ]
        + [("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]
    )


def process_start(pid: int) -> str | None:
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    inspect = libproc.proc_pidinfo
    inspect.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    inspect.restype = ctypes.c_int
    info = ProcessInfo()
    size = ctypes.sizeof(info)
    # PROC_PIDTBSDINFO is the macOS kernel's process-start identity, also used by Session Guard.
    if inspect(pid, 3, 0, ctypes.byref(info), size) != size:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        raise OSError(f"cannot verify the identity of process {pid}")
    started = dt.datetime.fromtimestamp(cast("int", info.start_seconds), dt.UTC)
    return started.strftime("%a %b %-d %H:%M:%S %Y")


def session_running(job: Job) -> bool:
    if not job["session"]:
        return False
    registry = Path.home() / ".config/session-guard/active-sessions.json"
    with registry.open() as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        records = cast("list[dict[str, object]]", json.load(handle))
    for record in records:
        if (
            record.get("tool") != job["tool"]
            or record.get("session_id") != job["session"]
        ):
            continue
        pid = record.get("pid")
        identity = record.get("pid_started_at")
        if isinstance(pid, int) and pid > 0:
            actual = process_start(pid)
            if actual is not None:
                if not isinstance(identity, str):
                    raise ValueError(
                        "Session Guard has no process identity for this session"
                    )
                if actual == identity:
                    return True
    return False


def resolve_session(job: Job) -> None:
    requested = job["session"]
    if not requested:
        return
    if requested.startswith("ses_") and requested[4:].isalnum():
        return
    try:
        uuid.UUID(requested)
    except ValueError:
        pass
    else:
        return
    matches: set[str] = set()
    for name in ("active-sessions", "last-sessions"):
        path = Path.home() / f".config/session-guard/{name}.json"
        if not path.exists():
            continue
        with path.open() as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            records = cast("list[dict[str, object]]", json.load(handle))
        for record in records:
            identifier = record.get("session_id")
            if (
                record.get("tool") == job["tool"]
                and isinstance(identifier, str)
                and (
                    identifier.startswith(requested)
                    or record.get("session_name") == requested
                )
            ):
                matches.add(identifier)
    if len(matches) != 1:
        raise ValueError(
            f"cannot uniquely identify session {requested!r}; use its full ID, or --wait for the tool's own name lookup"
        )
    identifier = matches.pop()
    argument = next(
        index
        for index in reversed(range(1, len(job["command"]) - 1))
        if job["command"][index] == requested
    )
    job["command"][argument] = identifier
    job["session"] = identifier


@contextlib.contextmanager
def locked() -> Generator[None]:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT / "lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def job_path(job_id: str) -> Path:
    if str(uuid.UUID(job_id)) != job_id:
        raise ValueError("invalid schedule ID")
    return ROOT / f"{job_id}.json"


def read_job(job_id: str) -> Job:
    return cast("Job", json.loads(job_path(job_id).read_text()))


def save(job: Job) -> None:
    path = job_path(job["id"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, indent=2) + "\n")
    temporary.replace(path)


def jobs() -> list[Job]:
    return sorted(
        (read_job(path.stem) for path in ROOT.glob("*.json")), key=lambda job: job["at"]
    )


def plist_path(job: Job) -> Path:
    return AGENTS / f"{LABEL}{job['id']}.plist"


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args], capture_output=True, text=True, check=False
    )


def disarm(job: Job) -> None:
    plist_path(job).unlink(missing_ok=True)
    launchctl("bootout", f"gui/{os.getuid()}/{LABEL}{job['id']}")


def cancel_wake(job: Job) -> None:
    if job["wake"] and job["at"] > time.time():
        result = subprocess.run(
            ["sudo", "-n", "pmset", "schedule", "cancel", "wake", job["wake"]],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            print(
                f"resume: canceled the session, but could not cancel its Mac wake: {result.stderr.strip()}",
                file=sys.stderr,
            )


def install_helper() -> None:
    source = SCRIPT.with_name("ResumeScheduler.swift")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    info_path = APP / "Contents/Info.plist"
    if info_path.exists() and EXECUTABLE.exists():
        info = cast("dict[str, object]", plistlib.loads(info_path.read_bytes()))
        if info.get("ResumeSchedulerSource") == digest and info.get(
            "ResumeSchedulerScript"
        ) == str(SCRIPT):
            return
    with tempfile.TemporaryDirectory(prefix="resume-scheduler-build-") as temporary:
        bundle = Path(temporary) / APP.name
        contents = bundle / "Contents"
        executable = contents / "MacOS/ResumeScheduler"
        executable.parent.mkdir(parents=True)
        info = {
            "CFBundleIdentifier": "com.tylerlaprade.resume-scheduler",
            "CFBundleName": "Resume Scheduler",
            "CFBundleDisplayName": "Resume Scheduler",
            "CFBundleExecutable": "ResumeScheduler",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
            "LSUIElement": True,
            "NSAppleEventsUsageDescription": "Resume Scheduler opens your scheduled sessions in Ghostty and restores your selected tab and app.",
            "ResumeSchedulerSource": digest,
            "ResumeSchedulerScript": str(SCRIPT),
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(
            [
                "/usr/bin/swiftc",
                "-j",
                "2",
                "-warnings-as-errors",
                "-O",
                "-parse-as-library",
                str(source),
                "-o",
                str(executable),
            ],
            check=True,
        )
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "Developer ID Application",
                str(bundle),
            ],
            check=True,
        )
        APP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, APP, dirs_exist_ok=True)


def register(job: Job) -> None:
    when = dt.datetime.fromtimestamp(job["at"]).astimezone()
    plist = {
        "Label": LABEL + job["id"],
        "ProgramArguments": [str(EXECUTABLE), "run", job["id"]],
        "RunAtLoad": True,
        "StartCalendarInterval": {
            "Month": when.month,
            "Day": when.day,
            "Hour": when.hour,
            "Minute": when.minute,
        },
        "EnvironmentVariables": {"PATH": job["path"]},
        "StandardOutPath": str(ROOT / f"{job['id']}.log"),
        "StandardErrorPath": str(ROOT / f"{job['id']}.log"),
    }
    AGENTS.mkdir(parents=True, exist_ok=True)
    plist_path(job).write_bytes(plistlib.dumps(plist))
    result = launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path(job)))
    if result.returncode:
        raise OSError(f"could not register the schedule: {result.stderr.strip()}")


def save_schedule(job: Job) -> None:
    if not Path(job["directory"]).is_dir():
        raise ValueError(f"folder does not exist: {job['directory']}")
    resolve_session(job)
    with locked():
        for existing in jobs():
            if (
                job["session"]
                and (existing["tool"], existing["session"])
                == (job["tool"], job["session"])
                and existing["state"] in PENDING
            ):
                raise ValueError(
                    f"this session already has schedule {existing['id']}; cancel it first"
                )
        install_helper()
        save(job)
        try:
            register(job)
        except OSError as error:
            job.update(state="failed", detail=str(error))
            save(job)
            plist_path(job).unlink(missing_ok=True)
            raise


def add(job: Job) -> None:
    try:
        save_schedule(job)
    except OSError, ValueError, subprocess.SubprocessError:
        cancel_wake(job)
        raise
    when = (
        dt.datetime.fromtimestamp(job["at"])
        .astimezone()
        .strftime("%b %d at %I:%M:%S %p %Z")
    )
    print(
        f"Scheduled {job['tool']} for {when}. You can close this tab.\nID: {job['id']}"
    )


def list_jobs(*, include_finished: bool) -> None:
    with locked():
        listed = [
            job
            for job in jobs()
            if include_finished or job["state"] in PENDING | {"failed"}
        ]
    for job in listed:
        when = (
            dt.datetime.fromtimestamp(job["at"])
            .astimezone()
            .strftime("%b %d %I:%M:%S %p %Z")
        )
        print(
            f"{job['id']}  {job['state']}  {when}  {job['tool']}  {job['session'] or 'new session'}\n  {job['directory']}"
        )
        if job["detail"]:
            print(f"  {job['detail']}")
    if not listed:
        print("No schedules.")


def cancel(job_id: str) -> None:
    with locked():
        job = read_job(job_id)
        if job["state"] not in PENDING | {"failed"}:
            raise ValueError(f"schedule is already {job['state']}")
        job.update(state="canceled", detail="")
        save(job)
    cancel_wake(job)
    disarm(job)
    print(f"Canceled {job_id}.")


def claim(job_id: str) -> bool:
    with locked():
        job = read_job(job_id)
        if job["state"] != "opening":
            print(
                f"resume: schedule is {job['state']}; nothing started", file=sys.stderr
            )
            return False
        running = session_running(job)
        job["state"] = "skipped" if running else "started"
        job["detail"] = "Session is already running." if running else ""
        save(job)
        return not running


def command_line(job: Job) -> str:
    claim_command = shlex.join([PYTHON, str(SCRIPT), "claim", job["id"]])
    return f"if {claim_command}; then export SESSION_GUARD_SHELL_PID=$$; {shlex.join(job['command'])}; fi"


def grok_auth_revision() -> tuple[int, int] | None:
    try:
        info = (Path.home() / ".grok/auth.json").stat()
    except FileNotFoundError:
        return None
    return info.st_ino, info.st_mtime_ns


def grok_models(directory: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["grok", "models"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def check_grok_sign_in(directory: str) -> None:
    revision = grok_auth_revision()
    result = grok_models(directory)
    output = result.stdout + result.stderr
    # Grok prints its initial auth state before models finishes a silent token refresh.
    if "You are not authenticated." in output and grok_auth_revision() != revision:
        result = grok_models(directory)
        output = result.stdout + result.stderr
    if result.returncode:
        raise ValueError(
            f"Grok sign-in check exited {result.returncode}:\n{output.strip()}"
        )
    confirmations = (
        "You are logged in with ",
        "You are using XAI_API_KEY.",
        "You are authenticated via deployment key.",
        "is using its own API key.",
    )
    if not any(confirmation in output for confirmation in confirmations):
        raise ValueError(f"Grok did not confirm sign-in:\n{output.strip()}")


def open_session(job: Job) -> None:
    if not Path(job["directory"]).is_dir():
        raise ValueError(f"folder no longer exists: {job['directory']}")
    if job["tool"] == "grok":
        check_grok_sign_in(job["directory"])
    result = subprocess.run(
        [
            str(SCRIPT.parent / "bin/ghostty-tab.sh"),
            job["directory"],
            command_line(job),
            str(EXECUTABLE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise OSError(f"could not open the tab: {result.stderr.strip()}")
    deadline = time.monotonic() + ACK_TIMEOUT
    while time.monotonic() < deadline:
        with locked():
            current = read_job(job["id"])
            if current["state"] != "opening":
                if current["state"] == "started":
                    current["detail"] = f"Opened tab {result.stdout.strip()}."
                    save(current)
                    print(current["detail"], flush=True)
                return
        time.sleep(0.1)
    raise TimeoutError(
        "The tab did not acknowledge the launch. Cancel this schedule before rescheduling."
    )


def begin_launch(job_id: str) -> Job:
    with locked():
        job = read_job(job_id)
        if job["state"] == "scheduled":
            if session_running(job):
                job.update(state="skipped", detail="Session is already running.")
            else:
                job.update(
                    state="opening",
                    detail="Waiting for the terminal to open the session.",
                )
            save(job)
        elif job["state"] == "opening":
            raise ValueError(
                "The previous launch was interrupted. Cancel this schedule before rescheduling."
            )
        return job


def run(job_id: str) -> None:
    job = read_job(job_id)
    remaining = job["at"] - time.time()
    if remaining >= MINUTE:
        return
    while remaining > 0:
        time.sleep(remaining)
        remaining = job["at"] - time.time()
    try:
        job = begin_launch(job_id)
        if job["state"] == "opening":
            open_session(job)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        with locked():
            job = read_job(job_id)
            if job["state"] in PENDING:
                job.update(state="failed", detail=str(error))
                save(job)
        print(f"resume: {error}", file=sys.stderr)
    finally:
        disarm(job)


class Args(argparse.Namespace):
    action: str
    tool: str
    session: str
    at: int
    directory: str
    wake: str
    command: list[str]
    id: str
    all: bool = False


def dispatch(args: Args) -> int:
    match args.action:
        case "install":
            with locked():
                install_helper()
        case "authorize":
            with locked():
                install_helper()
            subprocess.run(
                ["/usr/bin/open", "-n", str(APP), "--args", "authorize"], check=True
            )
        case "add":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if not command:
                raise ValueError("no session command supplied")
            add(
                Job(
                    id=str(uuid.uuid4()),
                    tool=args.tool,
                    session=args.session,
                    at=args.at,
                    directory=args.directory,
                    wake=args.wake,
                    command=command,
                    path=os.environ["PATH"],
                    state="scheduled",
                    detail="",
                )
            )
        case "list":
            list_jobs(include_finished=args.all)
        case "cancel":
            cancel(args.id)
        case "run":
            run(args.id)
        case "claim":
            return 0 if claim(args.id) else 1
        case _:
            raise ValueError(f"unknown action: {args.action}")
    return 0


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("install")
    actions.add_parser("authorize")
    create = actions.add_parser("add")
    for name in ("tool", "session", "directory", "wake"):
        create.add_argument(f"--{name}", required=True)
    create.add_argument("--at", type=int, required=True)
    create.add_argument("command", nargs=argparse.REMAINDER)
    actions.add_parser("list").add_argument("--all", action="store_true")
    for action in ("cancel", "run", "claim"):
        actions.add_parser(action).add_argument("id")
    args = parser.parse_args(namespace=Args())
    try:
        return dispatch(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"resume: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
