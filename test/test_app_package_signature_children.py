"""App Python children read no bytecode from the app folder under the signature policy.

The registry ``onInstall`` child gets ``PYTHONPYCACHEPREFIX`` and
``PYTHONDONTWRITEBYTECODE``; an app's cron script child gets ``-B -X pycache_prefix``
after the interpreter, which ``-I`` does not switch off. Without the policy both are
exactly what they were. The backend, stdio MCP, lifecycle and ``openCommand`` sites are
pinned beside their own tests.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from kiro_crew.apps import admission
from kiro_crew.apps.registry_pipeline.install import _install_script_env
from kiro_crew.apps.registry_pipeline.subprocess_env import minimal_env
from kiro_crew.cron_script import run_script_sandboxed
from kiro_crew.testing.wait import FALLBACK_TIMEOUT_SECS

APP = "child-env-app"


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "crew-home"
    (home / "crons").mkdir(parents=True)
    app = home / "apps" / APP
    app.mkdir(parents=True)
    (app / "job.py").write_text("def run(ctx):\n    return None\n", encoding="utf-8")
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    monkeypatch.setenv("KIROCREW_SESSION_KEY", "dashboard:child-env-test")
    return home


def _enforce(home: Path) -> None:
    (home / "app_admission.json").write_text(
        json.dumps({"mode": "enforce", "require_signature": True, "trust_keys": {}}),
        encoding="utf-8",
    )


def test_the_on_install_env_is_unchanged_without_the_policy(home):
    assert _install_script_env(APP) == minimal_env(NONINTERACTIVE="1")


def test_the_on_install_env_names_an_out_of_folder_prefix_under_the_policy(home):
    _enforce(home)
    env = _install_script_env(APP)
    expected = admission.signed_package_child_env(APP)
    assert expected and {k: env[k] for k in expected} == expected
    assert {k: v for k, v in env.items() if k not in expected} == minimal_env(NONINTERACTIVE="1")
    prefix = Path(expected["PYTHONPYCACHEPREFIX"])
    assert not prefix.is_relative_to(home / "apps" / APP)


def _cron_argv(monkeypatch, home: Path) -> list[str]:
    """The argv an app cron script's child is spawned with, stopped before it runs."""
    seen: list[list[str]] = []

    def _record(argv, **_kwargs):
        seen.append(list(argv))
        raise OSError("recorded the cron child's argv; stop before the spawn")

    monkeypatch.setattr("kiro_crew.cron_script.wrap_argv", _record)
    with contextlib.suppress(OSError):
        run_script_sandboxed(
            f"{home / 'apps' / APP / 'job.py'}:run", "job-child-env", timeout=FALLBACK_TIMEOUT_SECS
        )
    assert seen, "the script cron never reached its spawn"
    return seen[-1]


def test_a_cron_script_child_gets_the_prefix_flags_only_under_the_policy(home, monkeypatch):
    off = _cron_argv(monkeypatch, home)
    assert "-B" not in off
    assert not any(arg.startswith("pycache_prefix=") for arg in off)

    _enforce(home)
    on = _cron_argv(monkeypatch, home)
    flags = admission.signed_package_python_flags(APP)
    assert on[1:4] == flags
    # The launcher is a fresh temp file each run, so compare the shape around it.
    assert on[0] == off[0] and len(on) == len(off) + len(flags)


def test_a_script_outside_every_app_gets_no_flags(home, monkeypatch):
    _enforce(home)
    script = home / "crons" / "own.py"
    script.write_text("def run(ctx):\n    return None\n", encoding="utf-8")
    seen: list[list[str]] = []

    def _record(argv, **_kwargs):
        seen.append(list(argv))
        raise OSError("stop")

    monkeypatch.setattr("kiro_crew.cron_script.wrap_argv", _record)
    with contextlib.suppress(OSError):
        run_script_sandboxed(f"{script}:run", "job-own", timeout=FALLBACK_TIMEOUT_SECS)
    assert seen and "-B" not in seen[-1]


def test_the_npm_installs_keep_their_argv_without_the_policy_and_use_the_lockfile_with_it(
    home, tmp_path
):
    from kiro_crew.apps.admission import signed_npm_install_argv

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    backend = ["npm", "install", "--production", "--no-audit", "--no-fund"]
    backend_ci = ["npm", "ci", "--omit=dev", "--no-audit", "--no-fund"]
    assert signed_npm_install_argv(backend, pkg, ci_argv=backend_ci) == backend
    assert signed_npm_install_argv(["npm", "install"], pkg, ci_argv=["npm", "ci"]) == [
        "npm",
        "install",
    ]
    _enforce(home)
    assert signed_npm_install_argv(backend, pkg, ci_argv=backend_ci) == [*backend, "--no-save"]
    assert signed_npm_install_argv(["npm", "install"], pkg, ci_argv=["npm", "ci"]) == [
        "npm",
        "install",
        "--no-save",
    ]
    (pkg / "package-lock.json").write_text("{}\n", encoding="utf-8")
    assert signed_npm_install_argv(backend, pkg, ci_argv=backend_ci) == backend_ci
    assert signed_npm_install_argv(["npm", "install"], pkg, ci_argv=["npm", "ci"]) == ["npm", "ci"]
