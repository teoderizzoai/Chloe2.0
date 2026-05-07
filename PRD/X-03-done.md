# X-03 · `tools/code_runner.py` — sandboxed Python

## Overview

`CodeRunnerTool` with one verb: `execute(language="python", code)`. Returns `{stdout, stderr, exit_code, duration_ms}`. Sandboxed with firejail (Linux) or a single-shot Docker container as fallback. Hard limits: 30s wall time, 256 MB RAM, 64 MB output. Auth: `kinetic` (code execution has side effects; requires leash check).

## Context

Code execution enables interest-driven curiosity: Chloe can run a simulation, compute an answer, or explore a mathematical idea. The `kinetic` auth class means it goes through the gate and leash, preventing execution during quiet hours or when Teo is in focus mode. The sandbox is non-negotiable — any code execution without sandbox is a security hole.

**When:** Phase C (interest-driven curiosity actions).

## Implementation

### `tools/code_runner.py`

```python
# chloe/tools/code_runner.py
from __future__ import annotations
import asyncio
import subprocess
import tempfile
import os
import time
from pathlib import Path
from chloe.tools.base import BaseTool, ToolVerb, ToolResult
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("code_runner")

MAX_WALL_SECONDS = 30
MAX_RAM_MB = 256
MAX_OUTPUT_BYTES = 64 * 1024 * 1024  # 64 MB
SUPPORTED_LANGUAGES = {"python"}


class CodeRunnerTool(BaseTool):
    name = "code_runner"

    def __init__(self):
        self.verbs = {
            "execute": ToolVerb(
                name="execute",
                schema={
                    "type": "object",
                    "properties": {
                        "language": {
                            "type": "string",
                            "enum": ["python"],
                            "description": "Programming language (currently only 'python')",
                        },
                        "code": {
                            "type": "string",
                            "description": "Code to execute",
                        },
                    },
                    "required": ["language", "code"],
                },
                auth_class="kinetic",
                reversibility=0.3,
                description_for_model="Execute code in a sandboxed environment. Returns stdout, stderr, exit_code, duration_ms.",
                description_for_human="Execute sandboxed code",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb != "execute":
            return ToolResult(success=False, error=f"Unknown verb: {verb}")

        language = args.get("language", "python")
        code = args.get("code", "")

        if language not in SUPPORTED_LANGUAGES:
            return ToolResult(success=False, error=f"Unsupported language: {language!r}")
        if not code.strip():
            return ToolResult(success=False, error="code cannot be empty")

        settings = get_settings()
        use_docker = getattr(settings, "code_runner_use_docker", False)

        try:
            if use_docker:
                return await self._run_docker(code)
            else:
                return await self._run_firejail(code)
        except Exception as e:
            log.error("code_runner_unexpected_error", error=str(e))
            return ToolResult(success=False, error=f"Execution failed: {e}")

    async def _run_firejail(self, code: str) -> ToolResult:
        """Run code via firejail sandboxing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        try:
            cmd = [
                "firejail",
                "--quiet",
                "--private",
                "--net=none",
                "--nosound",
                "--no3d",
                "--noroot",
                f"--rlimit-as={MAX_RAM_MB * 1024 * 1024}",
                "--",
                "python3",
                tmp_path,
            ]

            start = time.monotonic()
            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=MAX_WALL_SECONDS,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=MAX_WALL_SECONDS
                )
            except asyncio.TimeoutError:
                return ToolResult(
                    success=False,
                    data={"stdout": "", "stderr": "Execution timed out", "exit_code": -1,
                          "duration_ms": int(MAX_WALL_SECONDS * 1000)},
                    error="Execution timed out",
                )

            duration_ms = int((time.monotonic() - start) * 1000)

            stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            return ToolResult(
                success=True,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                },
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def _run_docker(self, code: str) -> ToolResult:
        """Run code in a disposable Docker container."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        try:
            cmd = [
                "docker", "run", "--rm",
                "--network=none",
                f"--memory={MAX_RAM_MB}m",
                f"--memory-swap={MAX_RAM_MB}m",
                "--cpus=1",
                "-v", f"{tmp_path}:/code/run.py:ro",
                "python:3.12-slim",
                "python", "/code/run.py",
            ]

            start = time.monotonic()
            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=MAX_WALL_SECONDS,
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=MAX_WALL_SECONDS
                )
            except asyncio.TimeoutError:
                return ToolResult(
                    success=False,
                    data={"stdout": "", "stderr": "Execution timed out", "exit_code": -1,
                          "duration_ms": int(MAX_WALL_SECONDS * 1000)},
                    error="Execution timed out",
                )

            duration_ms = int((time.monotonic() - start) * 1000)
            stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

            return ToolResult(
                success=True,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": proc.returncode or 0,
                    "duration_ms": duration_ms,
                },
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def dry_run(self, verb: str, args: dict) -> str:
        code_preview = (args.get("code", "")[:60] + "...") if len(args.get("code", "")) > 60 else args.get("code", "")
        return f"Would execute Python: {code_preview!r} (sandboxed, no network, 30s limit)"
```

### Config additions

```python
# In chloe/config.py:
code_runner_use_docker: bool = False  # True to use Docker instead of firejail
```

### Register in ToolRegistry

```python
from chloe.tools.code_runner import CodeRunnerTool
self._tools["code_runner"] = CodeRunnerTool()
```

## Testing

### Unit tests — `tests/unit/test_code_runner.py`

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.tools.code_runner import CodeRunnerTool


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr("chloe.tools.code_runner.get_settings", lambda: MagicMock(
        code_runner_use_docker=False
    ))
    return CodeRunnerTool()


def test_execute_verb_is_kinetic(tool):
    assert tool.verbs["execute"].auth_class == "kinetic"


def test_dry_run_shows_preview(tool):
    result = tool.dry_run("execute", {"language": "python", "code": "print('hello')"})
    assert "sandboxed" in result.lower()
    assert "hello" in result


def test_unsupported_language_error(tool):
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute("execute", {"language": "javascript", "code": "console.log('hi')"})
    )
    assert not result.success
    assert "Unsupported" in result.error


def test_empty_code_error(tool):
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        tool.execute("execute", {"language": "python", "code": "  "})
    )
    assert not result.success


@pytest.mark.asyncio
async def test_successful_execution_via_mock(tool, monkeypatch):
    async def mock_run_firejail(code):
        return ToolResult(
            success=True,
            data={"stdout": "42\n", "stderr": "", "exit_code": 0, "duration_ms": 150},
        )

    monkeypatch.setattr(tool, "_run_firejail", mock_run_firejail)
    result = await tool.execute("execute", {"language": "python", "code": "print(6*7)"})
    assert result.success
    assert result.data["stdout"] == "42\n"
    assert result.data["exit_code"] == 0


@pytest.mark.asyncio
async def test_timeout_returns_error(tool, monkeypatch):
    import asyncio as _asyncio

    async def mock_run_firejail(code):
        return ToolResult(
            success=False,
            data={"stdout": "", "stderr": "Execution timed out", "exit_code": -1, "duration_ms": 30000},
            error="Execution timed out",
        )

    monkeypatch.setattr(tool, "_run_firejail", mock_run_firejail)
    result = await tool.execute("execute", {"language": "python", "code": "import time; time.sleep(100)"})
    assert not result.success
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_output_fields_present(tool, monkeypatch):
    async def mock_run_firejail(code):
        from chloe.tools.base import ToolResult
        return ToolResult(
            success=True,
            data={"stdout": "output\n", "stderr": "warning\n", "exit_code": 0, "duration_ms": 50},
        )

    monkeypatch.setattr(tool, "_run_firejail", mock_run_firejail)
    result = await tool.execute("execute", {"language": "python", "code": "print('output')"})
    assert "stdout" in result.data
    assert "stderr" in result.data
    assert "exit_code" in result.data
    assert "duration_ms" in result.data
```

### Integration test (requires firejail or Docker)

```python
# tests/integration/test_code_runner_live.py
import pytest

@pytest.mark.integration
@pytest.mark.asyncio
async def test_firejail_hello_world():
    """Requires firejail installed."""
    import shutil
    if not shutil.which("firejail"):
        pytest.skip("firejail not available")
    from chloe.tools.code_runner import CodeRunnerTool
    tool = CodeRunnerTool()
    result = await tool.execute("execute", {"language": "python", "code": "print('hello world')"})
    assert result.success
    assert "hello world" in result.data["stdout"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_network_blocked_in_sandbox():
    """Verify network is blocked inside sandbox."""
    import shutil
    if not shutil.which("firejail"):
        pytest.skip("firejail not available")
    from chloe.tools.code_runner import CodeRunnerTool
    tool = CodeRunnerTool()
    code = """
import urllib.request
try:
    urllib.request.urlopen('http://example.com', timeout=2)
    print('NETWORK_ALLOWED')
except:
    print('NETWORK_BLOCKED')
"""
    result = await tool.execute("execute", {"language": "python", "code": code})
    assert "NETWORK_BLOCKED" in result.data["stdout"]
```

## Dependencies

- `firejail` system package (Linux) or Docker daemon.
- `config.py` — `code_runner_use_docker` flag.

## Acceptance criteria

- `execute` has `auth_class="kinetic"`.
- Code that prints output → `stdout` in result.
- Code that runs for 31s → timeout error with `exit_code=-1`.
- Empty code → error without execution.
- Unsupported language → error without execution.
- Network blocked inside sandbox (integration test).
- Output truncated at 64 MB.
