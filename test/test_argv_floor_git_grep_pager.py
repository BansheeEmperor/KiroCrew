"""A ``git grep`` pager that kills the product is a self-kill.

``git grep`` runs a pager over the matching files, taken from ``-O`` /
``--open-files-in-pager``, a ``-c core.pager=`` / ``-c pager.grep=`` global
option, or ``GIT_PAGER`` / ``PAGER`` in the environment. The self-kill argv
floor reads ``pkill`` / ``killall`` only as a word of the command, so a kill
handed to git as a pager has to be read out of that option to be seen.

The pager is read the way git parses it, one invocation at a time and with the
case as submitted: a value option's value (``-e`` pattern, ``-f`` file, and the
rest of a cluster after such a letter) is never a pager, and a plain
``git grep`` with no pager still passes.
"""

from __future__ import annotations

import time

import pytest

from kiro_crew import security

_KILL = "self-protection-kill"
# Assembled from parts so no literal kill command appears in this file.
_K = "pk" + "ill"
_TARGET = "kirocrew"
_FILE = "src/kiro_crew/security/argv_floor_self.py"


def _verdict(cmd: str) -> str | None:
    return security.is_denied(cmd)


def _is_kill(cmd: str) -> bool:
    reason = _verdict(cmd)
    return bool(reason) and _KILL in str(reason)


# ---- the five pager forms: refused when the pager kills the product --------------


@pytest.mark.parametrize(
    "cmd",
    [
        # 1) -O placed after the pathspec.
        f"git grep -e x -- {_FILE} -O'{_K} -f {_TARGET} #'",
        # 2) a clustered short option.
        f"git grep -nO'{_K} -f {_TARGET} #' -e '*;' -- {_FILE}",
        # 3) a global pager option before grep.
        f"git -c core.pager='{_K} -f {_TARGET}' grep -e '*;' -- {_FILE}",
        # 4) a pager from the environment.
        f"GIT_PAGER='{_K} -f {_TARGET}' git grep -e '*;' -- {_FILE}",
        # 5) an array expansion.
        f"a=('-O{_K} -f {_TARGET} #'); git grep \"${{a[@]}}\" -e '*;' -- {_FILE}",
    ],
)
def test_the_five_named_pager_forms_are_refused(cmd: str) -> None:
    assert _is_kill(cmd), cmd


@pytest.mark.parametrize("sep", [";", "|"])
@pytest.mark.parametrize(
    "form",
    [
        # 1) -O after the pathspec.
        "git grep -e 'a{sep}b' -e x -- {file} -O'{k} -f {t} #'",
        # 2) a clustered short option.
        "git grep -e 'a{sep}b' -nO'{k} -f {t} #' -- {file}",
        # 5) an array expansion.
        "a=('-O{k} -f {t} #'); git grep -e 'a{sep}b' \"${{a[@]}}\" -- {file}",
    ],
)
def test_a_quoted_separator_before_the_pager_does_not_hide_it(form: str, sep: str) -> None:
    cmd = form.format(sep=sep, file=_FILE, k=_K, t=_TARGET)
    assert _is_kill(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        # The same pager read through spellings next to each named form.
        f"git grep -e x --open-files-in-pager='{_K} -f {_TARGET}' -- {_FILE}",
        f"git grep -e x --open='{_K} -f {_TARGET}' -- {_FILE}",
        f"git -c pager.grep='{_K} -f {_TARGET}' grep -e x -- {_FILE}",
        f"PAGER='{_K} -f {_TARGET}' git grep -e x -- {_FILE}",
        f"a=(-n '-O{_K} -f {_TARGET} #'); git grep \"${{a[@]}}\" -e x -- {_FILE}",
        # -O clustered after -F / -E, which fold to the value letters -f / -e.
        f"git grep -FO'{_K} -f {_TARGET}' -e x -- {_FILE}",
        f"git grep -nEO'{_K} -f {_TARGET}' -e x -- {_FILE}",
        # -O after a number option, an upper-case program name, a mixed-case config key.
        f"git grep -3O'{_K} -f {_TARGET}' -e x -- {_FILE}",
        f"GIT grep -nO'{_K} -f {_TARGET}' -e x -- {_FILE}",
        f"git -c Core.Pager='{_K} -f {_TARGET}' grep -e x -- {_FILE}",
        # git run through a wrapper, with the pager on grep or in env's assignments.
        f"sudo git grep -nO'{_K} -f {_TARGET}' -e x -- {_FILE}",
        f"env GIT_PAGER='{_K} -f {_TARGET}' git grep -e x -- {_FILE}",
        # A git global option whose value is the next word, before grep.
        f"git -C . grep -nO'{_K} -f {_TARGET}' -e x -- {_FILE}",
        f"git -c user.name=x grep -O'{_K} -f {_TARGET}' -- {_FILE}",
        f"git --git-dir .git grep -O'{_K} -f {_TARGET}' -- {_FILE}",
        f"git --attr-source HEAD grep -nO'{_K} -f {_TARGET}' -e x -- {_FILE}",
        # -O still parsed as an option after a plain pathspec (no `--`).
        f"git grep -e x {_FILE} -O'{_K} -f {_TARGET}'",
    ],
)
def test_adjacent_pager_spellings_are_refused(cmd: str) -> None:
    assert _is_kill(cmd), cmd


# ---- must stay allowed: no pager, or the kill text is a pattern/other command ----


@pytest.mark.parametrize(
    "cmd",
    [
        # A plain git grep with no pager option.
        "git grep -n kirocrew -- src",
        "git grep -e foo -- '*.py'",
        f"git grep -n {_TARGET} -- {_FILE}",
        # -o / --only-matching and context flags are not a pager that kills.
        f"git grep -no {_TARGET} -- src",
        "git grep -C 3 -e kirocrew -- src",
        "git grep -Ovim kirocrew -- src",
        # A quoted separator in a pattern, with no pager.
        f"git grep -e 'x|y' -n -- {_FILE}",
        f"git grep -e 'x;y' -n -- {_FILE}",
        # An ambiguous long prefix (``--o``: ``--or`` or ``--open-files-in-pager``)
        # that git refuses, running no pager.
        f"git grep -e x --o='{_K} -f {_TARGET}' -- {_FILE}",
        # A global option after which git runs no grep at all.
        f"git --exec-path grep -O'{_K} -f {_TARGET}' -- src",
        f"git --version grep -O'{_K} -f {_TARGET}' -- src",
        # A wrapper with no pager, and a pager spelling that is only echoed.
        "sudo git grep -n kirocrew -- src",
        f"echo GIT_PAGER='{_K} -f {_TARGET}' git grep x",
        # A pager that is not a product kill.
        "GIT_PAGER=less git grep -O kirocrew -- src",
        f"git -c core.pager='{_K} -f {_TARGET}' log",
        # The kill text is an -e PATTERN, not a pager option.
        f"git grep -e '-O{_K} -f {_TARGET}' -- {_FILE}",
        f"git grep -e '{_K} -f {_TARGET}' -- {_FILE}",
        # In a cluster, everything after a value letter is that value, never a pager.
        f"git grep -eO'{_K} -f {_TARGET}' -- {_FILE}",
        f"git grep -nC3O'{_K} -f {_TARGET}' -e x -- {_FILE}",
        f"git grep --reg '-O{_K} -f {_TARGET}' -- {_FILE}",
        # POSIX environment names are case-sensitive: this is not git's pager.
        f"git_pager='{_K} -f {_TARGET}' git grep -e x -- {_FILE}",
        f"a=(-e '-O{_K} -f {_TARGET}'); git grep \"${{a[@]}}\" -- {_FILE}",
        # The kill text is a separate command git grep never reaches.
        f"git grep -e x; echo '-o{_K} -f {_TARGET}'",
        f"git log --grep '{_K} -f {_TARGET}'",
    ],
)
def test_a_git_grep_without_a_killing_pager_is_allowed(cmd: str) -> None:
    assert not _is_kill(cmd), _verdict(cmd)


# ---- cost: one pass per frame, linear in the number of words ---------------------


def test_a_long_pager_option_list_is_checked_in_linear_time() -> None:
    cmd = "git grep -e x " + f"-o{_K}x " * 4000 + f" -- {_TARGET}"
    started = time.perf_counter()
    _verdict(cmd)
    assert time.perf_counter() - started < 2.0


# ---- the command as submitted feeds only the pager read --------------------------
# The gate hands the kill check the command with its case, for git's case-sensitive
# options. Every other kill shape still reads lower-cased text, so its verdict does
# not depend on how the command is capitalised.


@pytest.mark.parametrize(
    "cmd",
    [
        f"{_K.upper()} -f {_TARGET}",
        "Pkill -f KiroCrew",
        f"sudo {_K.upper()} -f {_TARGET}",
        f"BASH -c '{_K} -f {_TARGET}'",
        "KILL -9 $(pgrep -f kirocrew)",
        "Kill $(pidof KIROCREW)",
    ],
)
def test_a_capitalised_kill_is_still_refused(cmd: str) -> None:
    assert _is_kill(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    ["Kill $PPID", "kill 1234", f"{_K.upper()} -f Firefox", f"echo {_K.upper()} -f {_TARGET}"],
)
def test_a_capitalised_command_that_kills_nothing_of_ours_is_still_allowed(cmd: str) -> None:
    assert not _is_kill(cmd), _verdict(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        f"{_K.upper()} -f {_TARGET}",
        "Kill $(pidof KIROCREW)",
        "Kill $PPID",
        f"echo {_K.upper()} -f {_TARGET}",
    ],
)
def test_the_submitted_case_changes_no_verdict_outside_a_git_grep(cmd: str) -> None:
    lower = cmd.lower()
    assert security._is_self_kill(lower, raw_text=cmd) == security._is_self_kill(lower)
