import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_DIR = Path(__file__).resolve().parent
ENTRYPOINT = PLUGIN_DIR / "ws-keys.py"
MANIFEST = PLUGIN_DIR / "herdr-plugin.toml"
ROW_KEYS = "qwertyuiop"

FAKE_HERDR_SOURCE = """#!/usr/bin/env python3
import fcntl
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

state_path = Path(os.environ["FAKE_STATE"])
log_path = Path(os.environ["FAKE_LOG"])
arguments = sys.argv[1:]

server_lock = open(state_path.parent / "server.lock", "a")
fcntl.flock(server_lock.fileno(), fcntl.LOCK_EX)


def load_state():
    return json.loads(state_path.read_text())


def save_state(state):
    temporary_path = Path(str(state_path) + ".tmp")
    temporary_path.write_text(json.dumps(state))
    os.replace(temporary_path, state_path)


def append_log(entry):
    with open(log_path, "a") as handle:
        handle.write(json.dumps(entry) + "\\n")


def normalize_token_value(value):
    without_control_characters = "".join(
        char for char in value.strip() if unicodedata.category(char) != "Cc"
    )
    return without_control_characters[:80].strip()


if arguments[:2] == ["workspace", "list"]:
    list_exit_code = os.environ.get("FAKE_LIST_EXIT")
    if list_exit_code is not None:
        print("fake herdr: list failure", file=sys.stderr)
        sys.exit(int(list_exit_code))
    if os.environ.get("FAKE_LIST_OUTPUT") == "invalid-json":
        print("<not json>")
        sys.exit(0)
    snapshot_ready_file = os.environ.get("FAKE_LIST_SNAPSHOT_READY")
    if snapshot_ready_file:
        snapshot = json.dumps({"result": {"workspaces": load_state()["workspaces"]}})
        Path(snapshot_ready_file).touch()
        server_lock.close()
        release_file = os.environ["FAKE_LIST_SNAPSHOT_RELEASE"]
        deadline = time.monotonic() + 30
        while not os.path.exists(release_file):
            if time.monotonic() > deadline:
                sys.exit("fake herdr: snapshot release deadline exceeded")
            time.sleep(0.005)
        print(snapshot)
        sys.exit(0)
    markers_dir = os.environ.get("FAKE_LIST_MARKERS")
    if markers_dir:
        markers = Path(markers_dir)
        markers.mkdir(parents=True, exist_ok=True)
        (markers / f"list-{len(os.listdir(markers))}").touch()
        pause_file = os.environ.get("FAKE_LIST_PAUSE_FILE")
        if pause_file and len(os.listdir(markers)) == 1:
            deadline = time.monotonic() + 30
            while not os.path.exists(pause_file):
                if time.monotonic() > deadline:
                    sys.exit("fake herdr: pause deadline exceeded")
                time.sleep(0.005)
    time.sleep(float(os.environ.get("FAKE_LIST_SLEEP", "0")))
    print(json.dumps({"result": {"workspaces": load_state()["workspaces"]}}))
    sys.exit(0)

if arguments[:2] == ["workspace", "report-metadata"]:
    workspace_id = arguments[2]
    append_log({"argv": arguments})
    if os.environ.get("FAKE_REPORT_FAIL_ALL"):
        print(f"fake herdr: report for {workspace_id} rejected", file=sys.stderr)
        sys.exit(1)
    state = load_state()
    workspaces = state["workspaces"]
    workspace_index = next(
        (
            index
            for index, workspace in enumerate(workspaces)
            if workspace["workspace_id"] == workspace_id
        ),
        None,
    )
    vanish_id = os.environ.get("FAKE_VANISH_WORKSPACE_ID")
    if workspace_index is None or vanish_id == workspace_id:
        if vanish_id == workspace_id and workspace_index is not None:
            del workspaces[workspace_index]
            save_state(state)
        print(f"fake herdr: workspace {workspace_id} not found", file=sys.stderr)
        sys.exit(1)
    tokens = workspaces[workspace_index].setdefault("tokens", {})
    position = 3
    while position < len(arguments):
        flag = arguments[position]
        if flag == "--token":
            name, _, value = arguments[position + 1].partition("=")
            normalized_value = normalize_token_value(value)
            if normalized_value:
                tokens[name] = normalized_value
            else:
                tokens.pop(name, None)
            position += 2
        elif flag == "--clear-token":
            tokens.pop(arguments[position + 1], None)
            position += 2
        else:
            position += 1
    save_state(state)
    sys.exit(0)

print(f"fake herdr: unsupported command: {arguments}", file=sys.stderr)
sys.exit(2)
"""


def load_entrypoint_module():
    module_spec = importlib.util.spec_from_file_location("ws_keys", ENTRYPOINT)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def wait_until(predicate, timeout_seconds=30.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def workspace(workspace_id, label, tokens=None):
    return {"workspace_id": workspace_id, "label": label, "tokens": dict(tokens or {})}


def lettered_token(row_index, label):
    if row_index < len(ROW_KEYS):
        return f"[{ROW_KEYS[row_index]}] {label}"
    return label


class ManifestTest(unittest.TestCase):
    def test_manifest_declares_all_supported_events_and_entrypoint(self):
        manifest = tomllib.loads(MANIFEST.read_text())
        expected_events = [
            "workspace.created",
            "workspace.updated",
            "workspace.closed",
            "workspace.renamed",
            "workspace.moved",
            "workspace.reordered",
            "workspace.focused",
            "worktree.created",
            "worktree.opened",
            "worktree.removed",
            "tab.created",
            "tab.closed",
            "tab.renamed",
            "tab.moved",
            "tab.focused",
            "pane.created",
            "pane.closed",
            "pane.focused",
            "pane.moved",
            "pane.exited",
            "pane.agent_detected",
            "pane.agent_status_changed",
        ]
        event_names = [event["on"] for event in manifest["events"]]
        self.assertEqual(event_names, expected_events)
        self.assertEqual(len(event_names), len(set(event_names)))
        self.assertNotIn("workspace.metadata_updated", event_names)
        self.assertEqual(manifest["min_herdr_version"], "0.9.1")
        self.assertEqual(manifest["startup"][0]["command"], ["python3", "ws-keys.py"])
        self.assertTrue(ENTRYPOINT.is_file())
        self.assertTrue(
            all(
                event["command"] == ["python3", "ws-keys.py"]
                for event in manifest["events"]
            )
        )


class WorkspaceKeysTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_path = self.root / "server-state.json"
        self.log_path = self.root / "report-log.jsonl"
        self.home = self.root / "home"
        self.xdg_config_home = self.root / "xdg-config"
        self.xdg_state_home = self.root / "xdg-state"
        self.home.mkdir()
        self.xdg_config_home.mkdir()
        self.xdg_state_home.mkdir()
        self.default_socket_dir = self.xdg_config_home / "herdr"
        self.default_socket_dir.mkdir()
        self.plugin_state_dir = (
            self.xdg_state_home / "herdr" / "plugins" / "dotfiles.ws-keys"
        )
        self.plugin_state_dir.mkdir(parents=True)
        self.fake_herdr = self.root / "herdr"
        self.fake_herdr.write_text(FAKE_HERDR_SOURCE)
        self.fake_herdr.chmod(0o755)
        self.list_markers_dir = self.root / "list-markers"
        self.list_pause_file = self.root / "list-pause"

    def write_state(self, workspaces):
        self.state_path.write_text(json.dumps({"workspaces": workspaces}))

    def read_state(self):
        return json.loads(self.state_path.read_text())["workspaces"]

    def state_tokens(self, workspace_id):
        return next(
            workspace["tokens"]
            for workspace in self.read_state()
            if workspace["workspace_id"] == workspace_id
        )

    def report_log(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def report_log_workspaces(self):
        return [entry["argv"][2] for entry in self.report_log()]

    def environment(self, **extra):
        environment = os.environ.copy()
        environment.update(
            {
                "HERDR_BIN_PATH": str(self.fake_herdr),
                "HERDR_PLUGIN_STATE_DIR": str(self.plugin_state_dir),
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.xdg_config_home),
                "XDG_STATE_HOME": str(self.xdg_state_home),
                "FAKE_STATE": str(self.state_path),
                "FAKE_LOG": str(self.log_path),
            }
        )
        environment.pop("HERDR_SESSION", None)
        environment.pop("HERDR_SOCKET_PATH", None)
        environment.update({name: str(value) for name, value in extra.items()})
        return environment

    def run_entrypoint(self, **extra):
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            capture_output=True,
            text=True,
            timeout=120,
            env=self.environment(**extra),
        )

    def spawn_entrypoint(self, **extra):
        return subprocess.Popen(
            [sys.executable, str(ENTRYPOINT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment(**extra),
        )

    def spawn_entrypoint_with_environment(self, environment):
        return subprocess.Popen(
            [sys.executable, str(ENTRYPOINT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

    def lock_file(self):
        module = load_entrypoint_module()
        with mock.patch.dict(os.environ, self.environment(), clear=True):
            return module.lock_path()

    def assertTokens(self, expected):
        actual = {
            workspace["workspace_id"]: workspace["tokens"].get("key")
            for workspace in self.read_state()
        }
        self.assertEqual(actual, expected)


class LockScopingTest(WorkspaceKeysTestCase):
    def lock_environment(self, **extra):
        environment = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg_config_home),
            "XDG_STATE_HOME": str(self.xdg_state_home),
            "HERDR_PLUGIN_STATE_DIR": "",
        }
        environment.update(extra)
        return environment

    def test_default_manual_and_explicit_socket_share_lock(self):
        module = load_entrypoint_module()
        default_socket = self.xdg_config_home / "herdr" / "herdr.sock"
        with mock.patch.dict(os.environ, self.lock_environment(), clear=True):
            manual_lock = module.lock_path()
        with mock.patch.dict(
            os.environ,
            self.lock_environment(HERDR_SOCKET_PATH=str(default_socket)),
            clear=True,
        ):
            explicit_lock = module.lock_path()

        self.assertEqual(manual_lock, explicit_lock)

    def test_named_session_implicit_and_explicit_socket_share_lock(self):
        module = load_entrypoint_module()
        named_socket = (
            self.xdg_config_home / "herdr" / "sessions" / "work" / "herdr.sock"
        )
        with mock.patch.dict(
            os.environ, self.lock_environment(HERDR_SESSION="work"), clear=True
        ):
            implicit_lock = module.lock_path()
        with mock.patch.dict(
            os.environ,
            self.lock_environment(HERDR_SOCKET_PATH=str(named_socket)),
            clear=True,
        ):
            explicit_lock = module.lock_path()

        self.assertEqual(implicit_lock, explicit_lock)

    def test_explicit_socket_has_precedence_over_session(self):
        module = load_entrypoint_module()
        explicit_socket = self.root / "server" / "herdr.sock"
        with mock.patch.dict(
            os.environ,
            self.lock_environment(
                HERDR_SESSION="work",
                HERDR_SOCKET_PATH=str(explicit_socket),
            ),
            clear=True,
        ):
            lock_path = module.lock_path()

        expected_socket = explicit_socket.resolve(strict=False)
        self.assertEqual(
            lock_path,
            expected_socket.with_name(f"{expected_socket.name}.ws-keys.lock"),
        )
        with mock.patch.dict(
            os.environ,
            self.lock_environment(
                HERDR_SESSION="work",
                HERDR_SOCKET_PATH=str(explicit_socket),
            ),
            clear=True,
        ):
            effective_socket = module.effective_socket_path()
        self.assertEqual(effective_socket, explicit_socket)

    def test_same_socket_ignores_state_roots(self):
        module = load_entrypoint_module()
        socket_path = self.root / "server" / "herdr.sock"
        first_state_home = self.root / "state-a"
        second_state_home = self.root / "state-b"
        first_plugin_state = first_state_home / "herdr" / "plugins" / "dotfiles.ws-keys"
        second_plugin_state = (
            second_state_home / "herdr" / "plugins" / "dotfiles.ws-keys"
        )
        with mock.patch.dict(
            os.environ,
            self.lock_environment(
                XDG_STATE_HOME=str(first_state_home),
                HERDR_PLUGIN_STATE_DIR=str(first_plugin_state),
                HERDR_SOCKET_PATH=str(socket_path),
            ),
            clear=True,
        ):
            first_lock = module.lock_path()
        with mock.patch.dict(
            os.environ,
            self.lock_environment(
                XDG_STATE_HOME=str(second_state_home),
                HERDR_PLUGIN_STATE_DIR=str(second_plugin_state),
                HERDR_SOCKET_PATH=str(socket_path),
            ),
            clear=True,
        ):
            second_lock = module.lock_path()

        expected_socket = socket_path.resolve(strict=False)
        expected_lock = expected_socket.with_name(
            f"{expected_socket.name}.ws-keys.lock"
        )
        self.assertEqual(first_lock, expected_lock)
        self.assertEqual(second_lock, expected_lock)

    def test_different_socket_paths_use_independent_locks(self):
        module = load_entrypoint_module()
        first_socket = self.root / "server-a" / "herdr.sock"
        second_socket = self.root / "server-b" / "herdr.sock"
        with mock.patch.dict(
            os.environ,
            self.lock_environment(HERDR_SOCKET_PATH=str(first_socket)),
            clear=True,
        ):
            first_lock = module.lock_path()
        with mock.patch.dict(
            os.environ,
            self.lock_environment(HERDR_SOCKET_PATH=str(second_socket)),
            clear=True,
        ):
            second_lock = module.lock_path()

        self.assertNotEqual(first_lock, second_lock)
        self.assertEqual(first_lock.parent, first_socket.parent.resolve(strict=False))
        self.assertEqual(second_lock.parent, second_socket.parent.resolve(strict=False))
        self.assertEqual(first_lock.name, "herdr.sock.ws-keys.lock")
        self.assertEqual(second_lock.name, "herdr.sock.ws-keys.lock")

    def test_resolved_socket_alias_uses_the_same_lock(self):
        module = load_entrypoint_module()
        socket_target = self.root / "server" / "herdr.sock"
        socket_alias = self.root / "herdr-alias.sock"
        socket_alias.symlink_to(socket_target)
        environment = self.lock_environment(HERDR_SOCKET_PATH=str(socket_target))
        with mock.patch.dict(os.environ, environment, clear=True):
            target_lock = module.lock_path()
        environment["HERDR_SOCKET_PATH"] = str(socket_alias)
        with mock.patch.dict(os.environ, environment, clear=True):
            alias_lock = module.lock_path()

        self.assertEqual(target_lock, alias_lock)

    def test_default_lock_is_next_to_resolved_socket(self):
        module = load_entrypoint_module()
        with mock.patch.dict(os.environ, self.lock_environment(), clear=True):
            lock_path = module.lock_path()

        socket_path = (self.xdg_config_home / "herdr" / "herdr.sock").resolve(
            strict=False
        )
        self.assertEqual(
            lock_path, socket_path.with_name(f"{socket_path.name}.ws-keys.lock")
        )


class StartupAndIdempotenceTest(WorkspaceKeysTestCase):
    def test_startup_labels_every_workspace(self):
        self.write_state(
            [
                workspace("ws-1", "alpha"),
                workspace("ws-2", "beta"),
                workspace("ws-3", "gamma"),
            ]
        )

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTokens(
            {"ws-1": "[q] alpha", "ws-2": "[w] beta", "ws-3": "[e] gamma"}
        )
        self.assertEqual(sorted(self.report_log_workspaces()), ["ws-1", "ws-2", "ws-3"])

    def test_repeat_run_performs_no_reports(self):
        self.write_state([workspace("ws-1", "alpha"), workspace("ws-2", "beta")])
        self.assertEqual(self.run_entrypoint().returncode, 0)
        reports_before = len(self.report_log())

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.report_log()), reports_before)

    def test_only_stale_workspaces_are_rewritten(self):
        self.write_state(
            [
                workspace("ws-1", "alpha"),
                workspace("ws-2", "beta", tokens={"key": "[w] beta"}),
                workspace("ws-3", "gamma", tokens={"key": "stale"}),
            ]
        )

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report_log_workspaces(), ["ws-1", "ws-3"])
        self.assertTokens(
            {"ws-1": "[q] alpha", "ws-2": "[w] beta", "ws-3": "[e] gamma"}
        )

    def test_other_metadata_sources_survive(self):
        self.write_state(
            [
                workspace(
                    "ws-1",
                    "alpha",
                    tokens={"branch": "main", "status_icon": "●"},
                ),
            ]
        )

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        tokens = self.state_tokens("ws-1")
        self.assertEqual(tokens["branch"], "main")
        self.assertEqual(tokens["status_icon"], "●")
        self.assertEqual(tokens["key"], "[q] alpha")


class WorkspaceChangesTest(WorkspaceKeysTestCase):
    def test_workspace_closure_relabels_following_rows(self):
        self.write_state(
            [
                workspace("ws-1", "alpha", tokens={"key": "[q] alpha"}),
                workspace("ws-2", "beta", tokens={"key": "[w] beta"}),
                workspace("ws-3", "gamma", tokens={"key": "[e] gamma"}),
            ]
        )

        state = self.read_state()
        del state[1]
        self.write_state(state)

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTokens({"ws-1": "[q] alpha", "ws-3": "[w] gamma"})
        self.assertEqual(self.report_log_workspaces(), ["ws-3"])

    def test_workspace_reorder_moves_keys(self):
        self.write_state(
            [
                workspace("ws-1", "alpha", tokens={"key": "[q] alpha"}),
                workspace("ws-2", "beta", tokens={"key": "[w] beta"}),
            ]
        )

        self.write_state(list(reversed(self.read_state())))

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTokens({"ws-2": "[q] beta", "ws-1": "[w] alpha"})
        self.assertEqual(sorted(self.report_log_workspaces()), ["ws-1", "ws-2"])

    def test_rename_updates_only_renamed_workspace(self):
        self.write_state(
            [
                workspace("ws-1", "alpha", tokens={"key": "[q] alpha"}),
                workspace("ws-2", "beta", tokens={"key": "[w] beta"}),
            ]
        )

        state = self.read_state()
        state[1]["label"] = "beta renamed"
        self.write_state(state)

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTokens({"ws-1": "[q] alpha", "ws-2": "[w] beta renamed"})
        self.assertEqual(self.report_log_workspaces(), ["ws-2"])

    def test_rows_beyond_ten_use_plain_label(self):
        self.write_state(
            [
                workspace(f"ws-{index}", f"name-{index}", tokens={"key": "stale"})
                for index in range(1, 13)
            ]
        )

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTokens(
            {
                f"ws-{index}": lettered_token(index - 1, f"name-{index}")
                for index in range(1, 13)
            }
        )
        self.assertEqual(
            self.state_tokens("ws-11")["key"],
            "name-11",
        )
        self.assertEqual(self.state_tokens("ws-10")["key"], "[p] name-10")

    def test_transition_from_ten_to_eleven_appends_plain_row(self):
        self.write_state(
            [
                workspace(
                    f"ws-{index}",
                    f"name-{index}",
                    tokens={"key": lettered_token(index - 1, f"name-{index}")},
                )
                for index in range(1, 11)
            ]
        )

        state = self.read_state()
        state.append(workspace("ws-11", "name-11"))
        self.write_state(state)

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report_log_workspaces(), ["ws-11"])
        self.assertTokens(
            {
                **{
                    f"ws-{index}": lettered_token(index - 1, f"name-{index}")
                    for index in range(1, 10)
                },
                "ws-10": "[p] name-10",
                "ws-11": "name-11",
            }
        )


class LabelSanitizationTest(WorkspaceKeysTestCase):
    def test_control_characters_and_unicode_do_not_break_parsing(self):
        long_label = "l" * 100
        self.write_state(
            [
                workspace("ws-1", "café ☕"),
                workspace("ws-2", "a\tb\nc d"),
                workspace("ws-3", long_label),
            ]
        )

        result = self.run_entrypoint()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTokens(
            {
                "ws-1": "[q] café ☕",
                "ws-2": "[w] abc d",
                "ws-3": "[e] " + "l" * 76,
            }
        )

        reports_before = len(self.report_log())
        repeat = self.run_entrypoint()

        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(len(self.report_log()), reports_before)


class ConcurrencyTest(WorkspaceKeysTestCase):
    def test_burst_of_parallel_invocations_produces_one_consistent_result(self):
        self.write_state([workspace("ws-1", "old-1"), workspace("ws-2", "old-2")])

        processes = [
            self.spawn_entrypoint(
                FAKE_LIST_MARKERS=self.list_markers_dir,
                FAKE_LIST_PAUSE_FILE=self.list_pause_file,
            )
            for _ in range(6)
        ]

        self.assertTrue(
            wait_until(
                lambda: self.list_markers_dir.exists()
                and len(list(self.list_markers_dir.iterdir())) >= 1
            )
        )
        self.write_state(
            [workspace(f"ws-{index}", f"name-{index}") for index in range(1, 12)]
        )
        self.list_pause_file.touch()

        for process in processes:
            process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0)

        self.assertTokens(
            {
                f"ws-{index}": lettered_token(index - 1, f"name-{index}")
                for index in range(1, 12)
            }
        )
        reported_ids = self.report_log_workspaces()
        self.assertEqual(
            sorted(
                reported_ids, key=lambda workspace_id: int(workspace_id.split("-")[1])
            ),
            [f"ws-{index}" for index in range(1, 12)],
        )

    def test_manual_and_hook_order_mutation_cannot_leave_stale_labels(self):
        self.write_state(
            [
                workspace("ws-1", "alpha", tokens={"key": "[q] alpha"}),
                workspace("ws-2", "beta", tokens={"key": "[w] beta"}),
            ]
        )
        manual_ready = self.root / "manual-ready"
        manual_release = self.root / "manual-release"
        manual_environment = self.environment(
            FAKE_LIST_SNAPSHOT_READY=manual_ready,
            FAKE_LIST_SNAPSHOT_RELEASE=manual_release,
        )
        manual_environment.pop("HERDR_PLUGIN_STATE_DIR")
        manual_state_home = self.root / "manual-xdg-state"
        manual_environment["XDG_STATE_HOME"] = str(manual_state_home)
        manual_process = self.spawn_entrypoint_with_environment(manual_environment)

        self.assertTrue(wait_until(manual_ready.exists))
        self.write_state(list(reversed(self.read_state())))

        default_socket = self.xdg_config_home / "herdr" / "herdr.sock"
        hook_process = self.spawn_entrypoint(HERDR_SOCKET_PATH=default_socket)
        hook_completed_before_manual_release = wait_until(
            lambda: len(self.report_log()) >= 2,
            timeout_seconds=2,
        )
        manual_release.touch()

        manual_process.communicate(timeout=120)
        hook_process.communicate(timeout=120)
        self.assertEqual(manual_process.returncode, 0)
        self.assertEqual(hook_process.returncode, 0)
        self.assertFalse(
            hook_completed_before_manual_release,
            "hook reached the API while manual snapshot held the shared lock",
        )
        self.assertTokens({"ws-2": "[q] beta", "ws-1": "[w] alpha"})

    def test_refresh_reads_workspace_list_only_after_lock(self):
        self.write_state([workspace("ws-1", "old-1")])

        lock_handle = self.lock_file().open("a+")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        self.addCleanup(lock_handle.close)
        process = self.spawn_entrypoint()

        held_long_enough = wait_until(
            lambda: self.state_tokens("ws-1").get("key") is not None,
            timeout_seconds=2,
        )
        self.assertFalse(
            held_long_enough,
            "entrypoint wrote metadata while another process held the lock",
        )

        self.write_state([workspace("ws-1", "new-1"), workspace("ws-2", "new-2")])
        lock_handle.close()

        process.communicate(timeout=120)
        self.assertEqual(process.returncode, 0)
        self.assertTokens({"ws-1": "[q] new-1", "ws-2": "[w] new-2"})
        self.assertEqual(sorted(self.report_log_workspaces()), ["ws-1", "ws-2"])
        for entry in self.report_log():
            self.assertIn("new-", entry["argv"][-1])

    def test_lock_is_released_when_entrypoint_is_interrupted(self):
        self.write_state([workspace("ws-1", "alpha")])

        process = self.spawn_entrypoint(
            FAKE_LIST_MARKERS=self.list_markers_dir,
            FAKE_LIST_SLEEP=5,
        )
        self.assertTrue(
            wait_until(
                lambda: self.list_markers_dir.exists()
                and len(list(self.list_markers_dir.iterdir())) >= 1
            )
        )
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=30)

        self.assertNotEqual(process.returncode, 0)
        lock_path = self.lock_file()
        lock_handle = lock_path.open("a+")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fail("lock file is still locked after interruption")
        finally:
            lock_handle.close()
        self.assertTrue(lock_path.exists())


class ErrorReportingTest(WorkspaceKeysTestCase):
    def test_workspace_list_failure_is_loud(self):
        self.write_state([workspace("ws-1", "alpha")])

        result = self.run_entrypoint(FAKE_LIST_EXIT=3)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("list failure", result.stderr)

    def test_invalid_json_from_workspace_list_is_loud(self):
        self.write_state([workspace("ws-1", "alpha")])

        result = self.run_entrypoint(FAKE_LIST_OUTPUT="invalid-json")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid JSON", result.stderr)

    def test_unresponsive_herdr_times_out_loudly(self):
        self.write_state([workspace("ws-1", "alpha")])

        result = self.run_entrypoint(FAKE_LIST_SLEEP=5, WS_KEYS_TIMEOUT_SECONDS=0.3)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out", result.stderr)

    def test_vanishing_workspace_retries_with_shifted_following_rows(self):
        self.write_state(
            [
                workspace("ws-1", "alpha", tokens={"key": "[q] alpha"}),
                workspace("ws-2", "beta", tokens={"key": "stale"}),
                workspace("ws-3", "gamma", tokens={"key": "stale"}),
            ]
        )

        result = self.run_entrypoint(FAKE_VANISH_WORKSPACE_ID="ws-2")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTokens({"ws-1": "[q] alpha", "ws-3": "[w] gamma"})
        self.assertEqual(self.report_log_workspaces(), ["ws-2", "ws-3"])

    def test_unexpected_report_failure_is_loud_and_not_retried(self):
        self.write_state([workspace("ws-1", "alpha"), workspace("ws-2", "beta")])

        result = self.run_entrypoint(FAKE_REPORT_FAIL_ALL=1)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("report for ws-1 rejected", result.stderr)
        self.assertEqual(len(self.report_log()), 1)


if __name__ == "__main__":
    unittest.main()
