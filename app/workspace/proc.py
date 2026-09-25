"""Run a subprocess with a hard timeout that kills its whole process group.

Used by the ``run_tests`` agent tool and by the workspace's git commands.

The child starts in a new session (its own process group), so on timeout
``killpg`` also takes down anything it forked; tests that spawn servers or
sleep cannot outlive the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


async def run_bounded(
    cmd: Sequence[str] | str,
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str],
    timeout: float,
    merge_stderr: bool = False,
) -> ProcResult:
    """Run ``cmd`` (argv list, or a shell string) and return its output.

    ``merge_stderr`` interleaves stderr into stdout, as a terminal would show it.
    """
    stderr = asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE
    common = dict(
        cwd=cwd,
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        start_new_session=True,
    )
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(cmd, **common)  # type: ignore[arg-type]
    else:
        proc = await asyncio.create_subprocess_exec(*cmd, **common)  # type: ignore[arg-type]
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        return ProcResult(proc.returncode, "", "", timed_out=True)
    return ProcResult(
        proc.returncode,
        (out or b"").decode(errors="replace"),
        (err or b"").decode(errors="replace"),
    )
