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
        await _wait_group_gone(proc.pid)
        return ProcResult(proc.returncode, "", "", timed_out=True)
    return ProcResult(
        proc.returncode,
        (out or b"").decode(errors="replace"),
        (err or b"").decode(errors="replace"),
    )


async def _wait_group_gone(pgid: int, limit: float = 5.0) -> None:
    """Wait until no process is left in the killed group.

    ``proc.wait()`` only reaps the direct child. When a shell forks rather than
    exec'ing the command (dash, Linux's ``/bin/sh``, does), the command and its
    children are grandchildren: they are dying or zombies until init reaps them.
    """
    deadline = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        await asyncio.sleep(0.05)
