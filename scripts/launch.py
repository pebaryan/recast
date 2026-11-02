#!/usr/bin/env python3
"""Cross-platform launcher for Recast (Windows, Linux, macOS; standard library only).

    python scripts/launch.py router [--validate-only]   run only the llama.cpp router
    python scripts/launch.py start                      run the router (if needed) and the web app
    python scripts/launch.py stop                       stop an app session started by `start`

The llama-server executable comes from --server-path, then $RECAST_LLAMA_SERVER, then PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESET = ROOT / "config" / "models.ini"
CACHE = ROOT / ".runtime" / "model-cache"
SESSION = ROOT / ".runtime" / "app-session.json"
IS_WINDOWS = os.name == "nt"


class LaunchError(Exception):
    pass


# ---------------------------------------------------------------- process helpers

def start_token(pid: int) -> str | None:
    """An opaque value that changes if the PID is reused; None if the process does not exist."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, exited, kernel, user))):
                return None
            if exited.dwHighDateTime or exited.dwLowDateTime:
                return None
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            return None
        return None if fields[0] == "Z" else fields[19]
    result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return result.stdout.strip() or None


def spawn(argv: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    kwargs: dict = {"cwd": ROOT, "env": env}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True  # own process group so the whole tree can be stopped
    return subprocess.Popen(argv, **kwargs)


def kill_tree(proc: subprocess.Popen, grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def install_sigterm_handler() -> None:
    # Turn SIGTERM into a normal exit so `finally` blocks stop child processes.
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


# ---------------------------------------------------------------- router

def resolve_server(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("RECAST_LLAMA_SERVER") or shutil.which("llama-server")
    if not candidate or not Path(candidate).is_file():
        raise LaunchError("llama-server executable not found. Set RECAST_LLAMA_SERVER, pass --server-path, or put llama-server on PATH.")
    return str(candidate)


def model_paths() -> list[Path]:
    if not PRESET.is_file():
        raise LaunchError(f"Router preset not found: {PRESET}")
    found = []
    for line in PRESET.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*model\s*=\s*(.+?)\s*$", line)
        if match:
            found.append(ROOT / match.group(1))  # absolute paths in the preset are kept as-is
    if not 1 <= len(found) <= 5:
        raise LaunchError("Expected between one and five explicit local model paths in config/models.ini.")
    for path in found:
        if not path.is_file():
            raise LaunchError(f"Model file not found: {path}")
    return found


def router_command(server: str, port: int) -> tuple[list[str], dict[str, str]]:
    argv = [server, "--host", "127.0.0.1", "--port", str(port), "--models-preset", str(PRESET), "--models-max", "1",
            "--models-autoload", "--offline", "--no-webui", "--no-ui-mcp-proxy", "--timeout", "600"]
    # Configure only the child's environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_") and k != "LLAMA_API_KEY"}
    env["LLAMA_CACHE"] = str(CACHE)
    return argv, env


def cmd_router(args: argparse.Namespace) -> int:
    server = resolve_server(args.server_path)
    models = model_paths()
    argv, env = router_command(server, args.router_port)
    if args.validate_only:
        print(json.dumps({"Server": server, "Preset": str(PRESET), "BaseUrl": f"http://127.0.0.1:{args.router_port}/v1",
                          "Models": [str(m) for m in models], "ConfiguredModelCount": len(models), "MaximumLoadedModels": 1,
                          "ContextPerModel": 16384, "IsolatedCache": str(CACHE), "Arguments": argv[1:]}, indent=2))
        return 0
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"Recast router: http://127.0.0.1:{args.router_port} (one loaded model maximum)", flush=True)
    install_sigterm_handler()
    proc = spawn(argv, env)
    try:
        return proc.wait()
    finally:
        kill_tree(proc)


# ---------------------------------------------------------------- app

def get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def router_ready(url: str) -> bool:
    try:
        return get_json(f"{url}/health", 1).get("status") == "ok"
    except Exception:
        return False


def read_session() -> dict | None:
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def live_session() -> dict | None:
    session = read_session()
    if session and start_token(int(session["pid"])) == session.get("start_token"):
        return session
    return None


def cmd_start(args: argparse.Namespace) -> int:
    url = f"http://127.0.0.1:{args.router_port}"
    previous = live_session()
    if previous:
        raise LaunchError(f"Recast is already managed at http://127.0.0.1:{previous['port']}. Use `launch.py stop` before starting another instance.")
    install_sigterm_handler()
    router = app = None
    session_written = False
    try:
        if not router_ready(url):
            server = resolve_server(args.server_path)
            model_paths()
            CACHE.mkdir(parents=True, exist_ok=True)
            argv, env = router_command(server, args.router_port)
            router = spawn(argv, env)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and not router_ready(url):
                if router.poll() is not None:
                    raise LaunchError("The local router stopped during startup. Check the server path and model preset.")
                time.sleep(0.25)
            if not router_ready(url):
                raise LaunchError("The local router did not become ready within 25 seconds.")
        expected = sorted(re.findall(r"(?m)^\[(recast-[^\]]+)\]\s*$", PRESET.read_text(encoding="utf-8")))
        actual = sorted(item["id"] for item in get_json(f"{url}/models", 5)["data"])
        if expected != actual:
            raise LaunchError("The service on the router port does not match Recast's configured model allowlist.")
        SESSION.parent.mkdir(parents=True, exist_ok=True)
        SESSION.write_text(json.dumps({"pid": os.getpid(), "start_token": start_token(os.getpid()), "port": args.port}), encoding="utf-8")
        session_written = True
        print(f"Open http://127.0.0.1:{args.port} to use Recast. Press Ctrl+C to stop.", flush=True)
        app = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "recast", "serve", "--port", str(args.port), "--base-url", url], cwd=ROOT)
        try:
            return app.wait()
        except KeyboardInterrupt:
            try:
                return app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                return 130
    finally:
        if app is not None and app.poll() is None:
            app.terminate()
            try:
                app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app.kill()
        if router is not None:
            kill_tree(router)
        if session_written and (read_session() or {}).get("pid") == os.getpid():
            SESSION.unlink(missing_ok=True)


def cmd_stop(_: argparse.Namespace) -> int:
    session = read_session()
    if session is None:
        print("No managed Recast app session was found.")
        return 0
    pid = int(session["pid"])
    token = start_token(pid)
    if token is not None:
        if token != session.get("start_token"):
            raise LaunchError("The recorded PID belongs to a different process. No process was stopped.")
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
        else:
            os.kill(pid, signal.SIGTERM)  # the launcher's cleanup stops the router and app
        deadline = time.monotonic() + 20
        while start_token(pid) is not None and time.monotonic() < deadline:
            time.sleep(0.2)
        if start_token(pid) is not None and not IS_WINDOWS:
            os.kill(pid, signal.SIGKILL)
    SESSION.unlink(missing_ok=True)
    print("Recast and any router it started have stopped. Completed job sections remain saved.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in [("router", cmd_router, "run only the llama.cpp router"),
                                     ("start", cmd_start, "run the router if needed, then the web app"),
                                     ("stop", cmd_stop, "stop a session started by `start`")]:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=handler)
        if name != "stop":
            p.add_argument("--server-path", help="path to llama-server (default: $RECAST_LLAMA_SERVER, then PATH)")
            p.add_argument("--router-port", type=int, default=8017)
        if name == "router":
            p.add_argument("--validate-only", action="store_true", help="check paths without loading weights")
        if name == "start":
            p.add_argument("--port", type=int, default=8020, help="web app port")
    args = parser.parse_args()
    try:
        return args.handler(args)
    except LaunchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
