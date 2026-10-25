#!/usr/bin/env python3

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

METADATA_SOURCE = "ws-keys"
METADATA_TOKEN_NAME = "key"
ROW_KEYS = "qwertyuiop"
LOCK_FILENAME = "ws-keys.lock"
MAX_TOKEN_VALUE_CHARS = 80
MAX_REFRESH_ATTEMPTS = 3
DEFAULT_HERDR_TIMEOUT_SECONDS = 10.0


class HerdrError(RuntimeError):
    pass


def herdr_timeout_seconds():
    raw_timeout = os.environ.get("WS_KEYS_TIMEOUT_SECONDS")
    if raw_timeout is None:
        return DEFAULT_HERDR_TIMEOUT_SECONDS
    try:
        return float(raw_timeout)
    except ValueError:
        raise HerdrError(f"WS_KEYS_TIMEOUT_SECONDS is not a number: {raw_timeout}")


def run_herdr(arguments):
    command = [os.environ.get("HERDR_BIN_PATH", "herdr"), *arguments]
    printable_command = " ".join(command)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=herdr_timeout_seconds(),
        )
    except subprocess.TimeoutExpired:
        raise HerdrError(f"{printable_command} timed out")
    except OSError as error:
        raise HerdrError(f"{printable_command} failed to start: {error}")
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise HerdrError(
            f"{printable_command} exited with {completed.returncode}: {stderr}"
        )
    return completed.stdout


def workspace_list():
    stdout = run_herdr(["workspace", "list"])
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise HerdrError(f"workspace list returned invalid JSON: {error}")
    result = payload.get("result") if isinstance(payload, dict) else None
    workspaces = result.get("workspaces") if isinstance(result, dict) else None
    if not isinstance(workspaces, list):
        raise HerdrError("workspace list returned unexpected payload")
    for workspace in workspaces:
        if not isinstance(workspace, dict) or not workspace.get("workspace_id"):
            raise HerdrError("workspace list returned a workspace without workspace_id")
        if not isinstance(workspace.get("label"), str):
            raise HerdrError("workspace list returned a workspace without a label")
    return workspaces


def normalize_token_value(value):
    without_control_characters = "".join(
        char for char in value.strip() if unicodedata.category(char) != "Cc"
    )
    return without_control_characters[:MAX_TOKEN_VALUE_CHARS].strip()


def desired_token_value(row_index, label):
    prefix = f"[{ROW_KEYS[row_index]}] " if row_index < len(ROW_KEYS) else ""
    return normalize_token_value(prefix + label)


def current_token_value(workspace):
    tokens = workspace.get("tokens") or {}
    if not isinstance(tokens, dict):
        raise HerdrError("workspace list returned non-object tokens")
    value = tokens.get(METADATA_TOKEN_NAME)
    return value if value else None


def pending_reports(workspaces):
    pending = []
    for row_index, workspace in enumerate(workspaces):
        workspace_id = workspace["workspace_id"]
        desired_value = desired_token_value(row_index, workspace["label"])
        if current_token_value(workspace) == (desired_value or None):
            continue
        pending.append((workspace_id, desired_value))
    return pending


def report_token(workspace_id, token_value):
    run_herdr(
        [
            "workspace",
            "report-metadata",
            workspace_id,
            "--source",
            METADATA_SOURCE,
            "--token",
            f"{METADATA_TOKEN_NAME}={token_value}",
        ]
    )


def refresh():
    workspaces = workspace_list()
    for attempt in range(MAX_REFRESH_ATTEMPTS):
        pending = pending_reports(workspaces)
        if not pending:
            return
        for workspace_id, token_value in pending:
            try:
                report_token(workspace_id, token_value)
            except HerdrError as error:
                current_workspaces = workspace_list()
                if any(
                    workspace["workspace_id"] == workspace_id
                    for workspace in current_workspaces
                ):
                    raise error
                if attempt + 1 == MAX_REFRESH_ATTEMPTS:
                    if not pending_reports(current_workspaces):
                        return
                    raise HerdrError(
                        f"workspace {workspace_id} disappeared while refreshing; "
                        f"refresh did not converge after {MAX_REFRESH_ATTEMPTS} attempts"
                    ) from error
                workspaces = current_workspaces
                break
        else:
            return


def valid_session_name(name):
    if not name or name == "default" or name in {".", ".."}:
        return False
    if len(name.encode("utf-8")) > 64:
        return False
    return all(
        character.isascii() and (character.isalnum() or character in "._-")
        for character in name
    )


def herdr_config_directory():
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home is not None:
        return Path(xdg_config_home) / "herdr"
    home = os.environ.get("HOME")
    if home is not None:
        return Path(home) / ".config" / "herdr"
    return Path(tempfile.gettempdir()) / "herdr"


def effective_socket_path():
    socket_path = os.environ.get("HERDR_SOCKET_PATH")
    if socket_path is not None:
        return Path(socket_path)
    session_name = os.environ.get("HERDR_SESSION")
    if session_name and valid_session_name(session_name):
        return herdr_config_directory() / "sessions" / session_name / "herdr.sock"
    return herdr_config_directory() / "herdr.sock"


def lock_path():
    socket_path = effective_socket_path().resolve(strict=False)
    return socket_path.with_name(f"{socket_path.name}.{LOCK_FILENAME}")


def acquire_refresh_lock():
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = path.open("a+")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    return lock_handle


def main():
    lock_handle = acquire_refresh_lock()
    try:
        refresh()
    finally:
        lock_handle.close()


if __name__ == "__main__":
    try:
        main()
    except HerdrError as error:
        print(f"ws-keys: {error}", file=sys.stderr)
        sys.exit(1)
