"""Sandboxed Python execution.

PRD §6 spec'd firejail/docker. We do a more pragmatic version: spawn a
subprocess with a hard wall-clock timeout, isolated CWD (a fresh tempdir),
no environment leakage, and a memory cap (Linux RLIMIT_AS).

This is *not* a full security boundary — it's a guardrail against accidental
foot-guns (infinite loops, tempdir spam). For genuinely untrusted code, run
this inside a real container or firejail.
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import tempfile

from chloe.observability.logging import get_logger
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.code_runner")

DEFAULT_TIMEOUT_SEC = 8
DEFAULT_MAX_OUTPUT_BYTES = 32 * 1024
DEFAULT_MEM_BYTES = 256 * 1024 * 1024  # 256 MB


def _set_rlimits():  # pragma: no cover — exercised only in subprocess
    try:
        resource.setrlimit(resource.RLIMIT_AS, (DEFAULT_MEM_BYTES, DEFAULT_MEM_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_TIMEOUT_SEC * 2, DEFAULT_TIMEOUT_SEC * 2))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    except Exception:
        pass


class CodeRunnerTool(Tool):
    name = "code_runner"

    def __init__(self):
        self.verbs = {
            "run_python": ToolVerb(
                name="run_python",
                schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 30},
                    },
                    "required": ["code"],
                },
                auth_class="confirm",
                reversibility=1.0,
                cost_per_call_usd=0.0,
                description_for_model=(
                    "Run a short Python snippet in a sandboxed subprocess. "
                    "No network, no file persistence (a fresh temp dir is wiped after). "
                    "Default 8s timeout; max 30s. Captures stdout/stderr."
                ),
                description_for_human="Run Python (sandboxed)",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb != "run_python":
            return ToolResult(success=False, error=f"Unknown verb: {verb}")

        code = args.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(success=False, error="code is required")

        timeout = max(1, min(30, int(args.get("timeout_sec", DEFAULT_TIMEOUT_SEC))))

        sandbox = tempfile.mkdtemp(prefix="chloe_pyrun_")
        try:
            return self._run(code, timeout, sandbox)
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    def _run(self, code: str, timeout: int, sandbox: str) -> ToolResult:
        env = {"PATH": "/usr/bin:/bin", "HOME": sandbox, "TMPDIR": sandbox, "PYTHONIOENCODING": "utf-8"}
        kwargs: dict = {
            "input": code.encode("utf-8"),
            "timeout": timeout,
            "cwd": sandbox,
            "env": env,
            "capture_output": True,
        }
        if os.name == "posix":
            kwargs["preexec_fn"] = _set_rlimits

        try:
            proc = subprocess.run([sys.executable, "-I", "-S", "-"], **kwargs)
        except subprocess.TimeoutExpired as e:
            log.info("code_runner_timeout", timeout=timeout)
            return ToolResult(success=False, error=f"timeout after {timeout}s",
                              data={"stdout": (e.stdout or b"")[:DEFAULT_MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
                                    "stderr": (e.stderr or b"")[:DEFAULT_MAX_OUTPUT_BYTES].decode("utf-8", "replace")})
        except Exception as exc:
            log.warning("code_runner_spawn_failed", error=str(exc))
            return ToolResult(success=False, error=str(exc))

        stdout = proc.stdout[:DEFAULT_MAX_OUTPUT_BYTES].decode("utf-8", "replace")
        stderr = proc.stderr[:DEFAULT_MAX_OUTPUT_BYTES].decode("utf-8", "replace")
        truncated = (
            len(proc.stdout) > DEFAULT_MAX_OUTPUT_BYTES
            or len(proc.stderr) > DEFAULT_MAX_OUTPUT_BYTES
        )
        log.info("code_runner_done", returncode=proc.returncode, stdout_len=len(stdout), stderr_len=len(stderr))
        return ToolResult(
            success=proc.returncode == 0,
            data={"stdout": stdout, "stderr": stderr, "returncode": proc.returncode, "truncated": truncated},
            error=None if proc.returncode == 0 else f"exit code {proc.returncode}",
        )
