"""Tests for the git/tar self-target floor (``sandbox-escape-git-tar-self``).

``git`` and ``tar`` start an ssh transport (or run an arbitrary command) on their own,
without naming any ssh-family verb the ssh-self floor reads, so they are a second route
back into this host outside the sandbox.  ``TestSandboxEscapeGitTarSelf`` pins the rule on
both tiers and how ``_is_git_tar_to_self`` reads a transport through env/wrapper
unwrapping, git global options, quoting, ``user@`` prefixes, URI schemes, globs and
nested shell payloads, and that it leaves normal local git/tar use alone.  The own-host
set a destination is compared against is pinned here exactly as in
``test_denied_commands_security_ssh_self.py``.
"""

from __future__ import annotations

import pytest
from denied_commands_security_helpers import _own_address_probe_stays_local  # noqa: F401
from denied_commands_security_helpers import _denied_by, _rule_pattern

from kiro_crew import security
from kiro_crew.security import BUILTIN_DENIED_RULES
from kiro_crew.security import argv_floor as _argv_floor
from kiro_crew.security import compute_effective_denied, is_denied, is_safe_user_regex

pytestmark = pytest.mark.usefixtures("fresh_darwin_libc_handles")


class TestSandboxEscapeGitTarSelf:
    """A git/tar command dialing THIS host is refused; local use is not."""

    _RULE = "sandbox-escape-git-tar-self"

    @staticmethod
    def _effective():
        return list(compute_effective_denied(BUILTIN_DENIED_RULES, (), False, (), ()))

    @pytest.fixture(autouse=True)
    def _pin_own_host_cache(self, request, _floor_monkeypatch):
        # Same pin as the ssh-self suite: a fixed own-host cache with the
        # resolved-once latch set, so no command here spawns the real own-host
        # resolver thread or blocks on DNS, and the DNS-alias verdict layer is
        # stubbed to "not self" so remote-host allow cases stay allowed.
        own_mp = pytest.MonkeyPatch()
        request.addfinalizer(own_mp.undo)
        own = security.socket.gethostname().strip().lower()
        pinned = frozenset(name for name in {own, own.split(".", 1)[0]} if name)
        _floor_monkeypatch.setattr(_argv_floor, "_OWN_HOST_NAMES_CACHE", pinned)
        _floor_monkeypatch.setattr(_argv_floor, "_OWN_HOST_RESOLVE_DONE", True)
        _floor_monkeypatch.setattr(_argv_floor, "_NETLINK_ADDRS_PUBLISHED", True)
        own_mp.setattr(_argv_floor, "_OWN_HOST_RESOLVE_NEXT_TRY", float("inf"))
        _floor_monkeypatch.setattr(_argv_floor, "_resolved_host_verdict", lambda host, **_kw: False)

    def test_rule_is_registered_on_both_tiers(self):
        assert self._RULE in {r.id for r in BUILTIN_DENIED_RULES}
        assert self._RULE in security._SELF_PROTECTION_FLOOR_RULE_IDS
        assert self._RULE in security._SELF_PROTECTION_FLOOR_NOTES
        assert _rule_pattern(self._RULE) in self._effective()
        # A pattern that fails the safety lint is silently disabled, which is
        # how the first draft of the ssh-self rule failed.
        assert is_safe_user_regex(_rule_pattern(self._RULE))

    # Each form from the mapping in the PR: a git or tar transport that reaches
    # this host, or that runs a command dialing this host in place of the transport.
    _SELF_DENIED = [
        # git scp-like host:path, literal self host (``0`` is 0.0.0.0, this host)
        "git clone localhost:x",
        "git clone 0:x",
        "git clone localhost:/srv/repo.git",
        "git clone 127.0.0.1:x",
        "git ls-remote [::1]:x",
        # user@ prefix on an scp-like self host
        "git clone user@localhost:x",
        "git fetch me@127.0.0.1:x",
        "git clone user@[::1]:x",
        # git ssh-family URL schemes
        "git fetch ssh://127.0.0.1/x",
        "git fetch ssh://me@localhost:22/x",
        "git clone git+ssh://localhost/x",
        "git clone ssh+git://localhost/x",
        "git clone rsync://localhost/x",
        # git -C prefix does not shed the floor
        "git -C /tmp clone localhost:x",
        # git submodule / remote carry the url after their sub-subcommand
        "git submodule add localhost:x",
        "git remote add o localhost:x",
        "git remote set-url origin localhost:x",
        # every post-verb token is checked, even where option grammar would
        # otherwise consume or attach the remote
        "git fetch -u localhost:x",
        "git fetch -u=localhost:x",
        "git fetch -ulocalhost:x",
        # a remote after a detached option value is still read
        "git clone -b main localhost:x",
        "git fetch --depth 1 localhost:x",
        # git ext:: whose command dials this host
        "git clone ext::ssh localhost id",
        # core.sshCommand / GIT_SSH* overrides whose command dials this host
        "git -c core.sshCommand='ssh localhost' clone far:x",
        "git clone -c core.sshCommand='ssh localhost' far:x",
        "GIT_SSH_COMMAND='ssh localhost' git fetch origin",
        "env GIT_SSH_COMMAND='ssh 127.0.0.1' git fetch origin",
        # the override VALUE dials self via ::1 or a glob host, while the
        # remote itself is not self -- so the deny comes from the recursed value
        "git -c core.sshCommand='ssh ::1' clone far:x",
        # a shell runs the override value, so its glob host still expands
        "GIT_SSH_COMMAND='ssh local*' git fetch origin",
        # a ProxyJump / -J hop that is a literal self host dials this host
        "GIT_SSH_COMMAND='ssh -o ProxyJump=localhost' git fetch origin",
        "GIT_SSH_COMMAND='ssh -J127.0.0.1' git fetch origin",
        "git -c core.sshCommand='ssh -4vJlocalhost' fetch origin",
        "git clone ext::ssh 127.0.0.1 id",
        # --upload-pack / --receive-pack program that dials this host
        'git clone --upload-pack="ssh localhost id" /local/repo',
        "git clone --upload-pack='ssh localhost id' /local/repo",
        "git fetch -u 'ssh localhost id' origin",
        # wrappers keep the real verb behind them
        "sudo git clone localhost:x",
        "timeout 5 git clone localhost:x",
        # nested shell payload
        "sh -c 'git clone localhost:x'",
        # tar host:file, literal/old-style, and --rsh-command
        "tar -cf localhost:out.tar dir",
        "tar -xf localhost:/tmp/a.tar",
        "tar --file=localhost:/tmp/a.tar -x",
        "tar -f localhost:a.tar -c dir",
        "tar cf localhost:a.tar dir",
        "tar xf localhost:a.tar",
        # every old-style operand remains an archive candidate; ``--`` ends
        # option parsing, so a later member cannot enable force-local
        "tar cbf 20 localhost:a.tar dir --rsh-command=ssh",
        "tar cCf /tmp localhost:a.tar dir --rsh-command=ssh",
        "tar --rsh-command=ssh -cf localhost:a.tar -- --force-local",
        "tar -cf me@localhost:a.tar dir",
        "tar --rsh-command='ssh localhost' -cf far:a.tar dir",
        "tar --rsh-command 'ssh localhost' -cf far:a.tar dir",
        "nohup tar -cf localhost:a.tar dir",
        # a wrapper's value option is stepped over (argv_rules' wrapper reader)
        "sudo -u root git clone localhost:x",
        "env -u PATH git clone localhost:x",
        # glued --config-env reads the named variable, globally or below clone
        "X='ssh localhost' git --config-env=core.sshCommand=X clone far:x",
        "V='ssh localhost' git clone --config-env=core.sshCommand=V far:x",
        # git archive --remote is the remote it dials
        "git archive --remote=localhost:x HEAD",
        "git archive --remote localhost:x HEAD",
        "git archive --remote=far:x --exec='ssh localhost id' HEAD",
    ]

    @pytest.mark.parametrize("cmd", _SELF_DENIED)
    def test_self_targets_are_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git clone -u'ssh localhost echo x=y #' . out",
            "git clone -ccore.sshCommand='ssh localhost id #' far:x out",
            "tar --rsh-command=ssh -cf localhost:a.tar --exclude --force-local dir",
            "tar -cf localhost:a.tar --force-local",
        ],
        ids=[
            "short-upload-pack-command",
            "short-config-command",
            "tar-detached-option-collision",
            "tar-force-local-after-archive",
        ],
    )
    def test_token_agnostic_candidates_are_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git fetch origin localhost:localhost",
            "git clone https://github.com/kirodotdev/KiroCrew.git localhost:dir",
            "tar -cf out.tar localhost:file",
            "git fetch origin 0:0",
        ],
        ids=[
            "git-self-shaped-refspec",
            "git-self-shaped-clone-destination",
            "tar-self-shaped-member",
            "git-zero-host-refspec",
        ],
    )
    def test_maintainer_accepted_operand_overrefusals_are_denied(self, cmd):
        # Maintainer-accepted F3 price of checking every transport operand.
        assert _denied_by(cmd) == self._RULE, cmd

    def test_push_zero_host_refspec_is_refused(self):
        # The same F3 price on a push: this floor reads the ``0`` host as self,
        # and the git-publish floor (which claims a push first) refuses it too.
        cmd = "git push origin 0:main"
        assert security._is_git_tar_to_self(cmd), cmd
        assert is_denied(cmd, denied_regexes=self._effective()) is not None, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git 2>/dev/null clone localhost:x",
            "git >/tmp/o fetch localhost:x",
            "git -C . 2>/dev/null clone localhost:x",
            "git > /tmp/o fetch localhost:x",
        ],
        ids=["fd-redirect", "stdout-redirect", "redirect-after-global-option", "detached-redirect"],
    )
    def test_redirect_before_verb_is_denied(self, cmd):
        # A redirection is not git's verb: bash removes it before exec.
        assert _denied_by(cmd) == self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "2>/dev/null git clone localhost:x",
            "env 2>/dev/null git clone localhost:x",
            "sh -c '2>/dev/null git clone ssh://127.0.0.1/r'",
            "2>/dev/null tar -cf localhost:o d",
            ">out git clone localhost:x",
            ">>out git clone localhost:x",
            "2> /dev/null git clone localhost:x",
        ],
        ids=[
            "fd-attached",
            "wrapper-fd-attached",
            "nested-fd-attached",
            "tar-fd-attached",
            "stdout-attached",
            "stdout-append-attached",
            "fd-detached",
        ],
    )
    def test_redirect_before_program_is_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "2>/dev/null git clone https://github.com/x/y",
            "'>out' git clone localhost:x",
        ],
        ids=["remote-target", "quoted-redirect-program"],
    )
    def test_redirect_before_non_self_program_is_allowed(self, cmd):
        assert _denied_by(cmd) != self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git clone -b ';' localhost:x",
            "git clone -b '&&' localhost:x",
            "git clone -b '|' localhost:x",
            "git archive --prefix ';' --remote=localhost:x HEAD",
            "tar --exclude ';' -cf localhost:out.tar dir --rsh-command 'ssh localhost'",
        ],
        ids=["semicolon", "and-and", "pipe", "archive-prefix", "tar-exclude"],
    )
    def test_quoted_separator_does_not_end_the_scan(self, cmd):
        # A quoted separator is data, not a command boundary.
        assert _denied_by(cmd) == self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git -c url.localhost:.insteadOf=x: clone x:repo",
            "git -c url.ssh://localhost/.insteadOf=https://example.com/ ls-remote"
            " https://example.com/repo",
            "git -c url.https://example.com/.insteadOf=localhost: fetch https://example.com/repo",
            "X=x: git --config-env=url.localhost:.insteadOf=X clone x:repo",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.localhost:.insteadOf GIT_CONFIG_VALUE_0=x:"
            " git clone x:repo",
        ],
        ids=[
            "c-base-self",
            "c-base-self-url",
            "c-value-self",
            "config-env-base-self",
            "env-numbered",
        ],
    )
    def test_url_rewrite_to_self_is_denied(self, cmd):
        # git rewrites the remote through url.<base>.insteadOf before dialing.
        assert _denied_by(cmd) == self._RULE, cmd

    def test_url_rewrite_not_to_self_is_allowed(self):
        cmd = "git -c url.https://github.com/.insteadOf=gh: clone gh:o/r"
        assert _denied_by(cmd) != self._RULE, cmd

    # The text is read through the ssh floor's own source pre-walk, so a static
    # ``$(printf …)`` substitution is its output before git/tar is read.
    @pytest.mark.parametrize(
        "cmd",
        [
            'git ls-remote "$(printf localhost):repo"',
            "git ls-remote local$(printf host):repo",
            "$(printf git) ls-remote localhost:repo",
            'tar --rsh-command=ssh -cf "$(printf localhost):a.tar" dir',
            'git -c core.sshCommand="ssh $(printf localhost)" ls-remote far:x',
        ],
        ids=["quoted-host", "glued-host", "program-word", "tar-archive", "config-value"],
    )
    def test_static_substitution_to_self_is_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    @pytest.mark.parametrize(
        ("substituted", "literal"),
        [
            ('git ls-remote "$(printf localhost):repo"', "git ls-remote localhost:repo"),
            ("$(printf git) ls-remote localhost:repo", "git ls-remote localhost:repo"),
            # A bare word is a local path to git with or without the substitution.
            ('git ls-remote "$(printf localhost)repo"', "git ls-remote localhostrepo"),
            ("$(printf git) ls-remote localhostrepo", "git ls-remote localhostrepo"),
            ("git ls-remote $(printf github.com):x/y", "git ls-remote github.com:x/y"),
        ],
    )
    def test_static_substitution_reads_like_its_output(self, substituted, literal):
        assert security._is_git_tar_to_self(substituted) == security._is_git_tar_to_self(literal)

    # The environment a git command runs with is the ssh floor's
    # ``_FrameEnvironment``: an exported value persists past ``;`` and a
    # frame's leading assignments reach its nested ``sh -c`` payload.
    @pytest.mark.parametrize(
        "cmd",
        [
            "export GIT_SSH_COMMAND='ssh localhost id #'; git ls-remote far:x",
            "GIT_SSH_COMMAND='ssh localhost id #' sh -c 'git ls-remote far:x'",
            "GIT_SSH_COMMAND='ssh localhost id #'; export GIT_SSH_COMMAND; git ls-remote far:x",
            "export GIT_SSH='ssh localhost id #' && git fetch origin",
            "export GIT_CONFIG_KEY_0=core.sshCommand; export GIT_CONFIG_VALUE_0='ssh localhost id #';"
            " git ls-remote far:x",
            "export GIT_CONFIG_KEY_0=core.sshCommand; export GIT_CONFIG_VALUE_0='ssh localhost id #';"
            " sh -c 'git ls-remote far:x'",
            "export X='ssh localhost'; git --config-env=core.sshCommand=X ls-remote far:x",
        ],
        ids=[
            "export-ssh-command",
            "prefix-into-nested-sh-c",
            "assign-then-export",
            "export-git-ssh",
            "export-numbered-config",
            "export-numbered-config-nested",
            "export-config-env",
        ],
    )
    def test_carried_environment_to_self_is_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    def test_nested_payload_inherits_parent_launch_environment(self):
        # Each payload keeps the environment of the command that launches it;
        # sibling commands cannot replace that snapshot in either direction.
        self_target = "GIT_SSH_COMMAND='ssh localhost id #' sh -c 'git ls-remote far:x'"
        assert _denied_by(self_target) == self._RULE
        assert (
            _denied_by(
                self_target + "; GIT_SSH_COMMAND='ssh far.example.com' sh -c 'git ls-remote far:x'"
            )
            == self._RULE
        )
        assert (
            _denied_by(
                "sh -c 'git ls-remote far:x'; " "export GIT_SSH_COMMAND='ssh localhost id #'"
            )
            != self._RULE
        )

    @pytest.mark.parametrize(
        "cmd",
        [
            "git -c remote.selftarget.url=localhost:x ls-remote selftarget",
            "TARGET=localhost:x git --config-env=remote.selftarget.url=TARGET "
            "ls-remote selftarget",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.selftarget.url "
            "GIT_CONFIG_VALUE_0=localhost:x git ls-remote selftarget",
            "git -c arbitrary.key='ssh localhost' ls-remote far:x",
        ],
        ids=["config", "config-env", "numbered-env", "command-value"],
    )
    def test_every_invocation_local_config_value_to_self_is_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    def test_invocation_local_config_values_to_remote_hosts_are_allowed(self):
        for cmd in (
            "git -c remote.selftarget.url=github.com:x ls-remote selftarget",
            "git -c arbitrary.key='ssh far.example.com' ls-remote far:x",
        ):
            assert _denied_by(cmd) != self._RULE, cmd

    def test_multi_name_export_applies_git_ssh_command(self):
        cmd = "export A=1 GIT_SSH_COMMAND='ssh localhost id #'; git ls-remote far:x"
        assert _denied_by(cmd) == self._RULE

    @pytest.mark.parametrize(
        "cmd",
        [
            # a leading assignment ends with its simple command
            "GIT_SSH_COMMAND='ssh localhost id #' true; git ls-remote far:x",
            # an export after the git command does not reach it
            "git ls-remote far:x; export GIT_SSH_COMMAND='ssh localhost id #'",
            # a local verb runs no transport
            "export GIT_SSH_COMMAND='ssh localhost id #'; git status",
            # an exported command that does not dial this host
            "export GIT_SSH_COMMAND='ssh -i key'; git ls-remote far:x",
            "GIT_SSH_COMMAND='ssh -i key' sh -c 'git ls-remote far:x'",
        ],
        ids=["cleared-by-separator", "export-after", "local-verb", "export-far", "nested-far"],
    )
    def test_carried_environment_not_to_self_is_allowed(self, cmd):
        assert _denied_by(cmd) != self._RULE, cmd

    # git push to this host is a self dial too, but the git-publish floor claims
    # a push first; assert only that it is refused, not by which rule.
    @pytest.mark.parametrize("cmd", ["git push me@localhost:x", "git push localhost:x main"])
    def test_push_to_self_is_refused(self, cmd):
        assert is_denied(cmd, denied_regexes=self._effective()) is not None, cmd

    # The benign set the fix must not touch, including the 14 file-operand
    # commands the scope review pinned (``git add s* *.*`` and friends).
    _ALLOWED = [
        "git add s* *.*",
        "git add .",
        'git commit -m "fix localhost:8080 bug"',
        'git commit -m "see ssh://localhost/x please"',
        "git status",
        "git -c core.sshCommand='ssh localhost' status",
        "GIT_SSH_COMMAND='ssh localhost' git status",
        "git -c core.sshCommand='ssh localhost' archive HEAD -- --remote",
        "git log",
        "git log --grep=localhost:x",
        "git diff",
        "git clone /local/path",
        "git clone /tmp/mirror.git dest",
        "git clone file:///local/repo",
        "git clone https://github.com/kirodotdev/KiroCrew",
        "git clone https://github.com/kirodotdev/KiroCrew localhost-dir",
        "git clone https://localhost/x",
        "git clone https://localhost:8080/x",
        # http(s) with userinfo to this host is not an ssh transport
        "git clone http://dev@localhost:3000/org/app.git",
        "git fetch https://ci@127.0.0.1:8443/org/app.git main",
        "git.exe clone http://dev@localhost:3000/org/app.git",
        "git clone ssh://github.com/x/y.git",
        "git clone git@github.com:kirodotdev/KiroCrew.git",
        # dotted remote hosts: read as literal-self only, never DNS-pending.
        # clone, fetch AND push to github.com / gitlab.com all stay allowed.
        "git clone ssh://git@github.com/x/y.git",
        "git fetch github.com:x/y main",
        "git push git@github.com:kirodotdev/KiroCrew.git main",
        "GIT_SSH_COMMAND='ssh -i key' git push git@github.com:x",
        "git clone git@gitlab.com:group/proj.git",
        "git fetch git@gitlab.com:g/p.git main",
        "git push gitlab.com:g/p main",
        "tar -cf backup.example.com:/archive.tar dir",
        "git fetch origin",
        "git fetch upstream main",
        "git fetch https://github.com/kirodotdev/KiroCrew main",
        "git push origin my-feature-branch",
        "git fetch --upload-pack=git-upload-pack origin",
        # an option value is not a remote (bare words never dial)
        "git clone -b release-1.0 https://github.com/x/y",
        "git clone --branch v1.2.3 https://github.com/x/y",
        "git fetch -j 0 origin",
        "git clone -o myremote https://x/y",
        "git fetch origin main:main",
        "git push origin HEAD:main",
        "git clone localhost",
        # a command run in place of the transport that does not dial this host
        # runs in the agent's own sandbox, so it is not an escape
        'GIT_SSH_COMMAND="ssh" git clone far:x',
        'git -c core.sshCommand="ssh -o x" clone far:x',
        "GIT_SSH=/tmp/x git clone far:x",
        "env GIT_SSH_COMMAND=x git fetch origin",
        "tar --rsh-command=id -cf a.tar dir",
        "tar --rsh-command=/usr/bin/ssh -cf far:a.tar dir",
        "git clone --upload-pack=x file:///local/repo",
        'git clone -u "sh -c id" /local/repo',
        'git clone "ext::sh -c id"',
        "git -c protocol.ext.allow=always clone ext::id",
        # rsync-over-ssh to a remote host is the ssh floor's domain, not this
        # one; pin that the git/tar floor does not interfere with it.
        "rsync -e ssh file user@otherhost:/tmp/",
        "git -C /tmp status",
        "git config remote.origin.url localhost:x",
        "git remote -v",
        "git remote set-url --delete origin localhost:x",
        "GIT_SSH_COMMAND='ssh localhost' git remote show -n origin",
        "git remote get-url localhost:x",
        "git remote remove localhost:x",
        "git remote rename localhost:x elsewhere",
        "git remote add o https://localhost/x",
        "git submodule update --init",
        "git stash",
        "tar -czf x.tgz dir",
        "tar -czf out.tar dir",
        "tar -xf x.tar",
        "tar -czf handoff.tgz s* *.*",
        "tar -xzf x.tgz -C /tmp",
        "tar -cf /tmp/out.tar localhost",
        "tar -czf out.tgz ./localhost",
        "tar --exclude=localhost -czf out.tgz dir",
        "dir s* *.*",
        "git archive --remote=github.com:x/y HEAD",
        "git archive -o out.tar HEAD",
        "sudo -u root git status",
    ]

    @pytest.mark.parametrize("cmd", _ALLOWED)
    def test_benign_commands_stay_allowed(self, cmd):
        assert _denied_by(cmd) != self._RULE, cmd

    # The verb spelled through quoting or a glob is the program the shell runs,
    # exactly as the ssh floor reads ``ss''h``/``s*h``: the raw text carries no
    # literal ``git``/``tar`` (or not as the program word), so the quick gate
    # and the verb match must both see through the spelling.
    _SPELLED_VERB_SELF = [
        "gi''t fetch -q localhost:x",
        '"git" clone localhost:x',
        "g*t clone localhost:x",
        "/usr/bin/g?t clone localhost:x",
        "t''ar -cf localhost:x d",
    ]

    @pytest.mark.parametrize("cmd", _SPELLED_VERB_SELF)
    def test_quoted_or_globbed_verb_to_self_is_denied(self, cmd):
        assert security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) == self._RULE, cmd

    # The same spellings against a remote that is not this host stay allowed.
    _SPELLED_VERB_REMOTE = [
        "gi''t fetch -q git@github.com:x/y",
        '"git" clone https://github.com/x/y',
        "g*t clone git@github.com:x/y",
        "/usr/bin/g?t clone git@github.com:x/y",
        "t''ar -cf github.com:x d",
    ]

    @pytest.mark.parametrize("cmd", _SPELLED_VERB_REMOTE)
    def test_quoted_or_globbed_verb_to_remote_is_allowed(self, cmd):
        assert not security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) is None, cmd

    # The DNS-alias layer answering "self" (its fail-closed PENDING answer on a
    # first-seen dotted name) must not refuse a dotted git/tar remote: those
    # remotes are checked against literal self addresses only.
    _DOTTED_REMOTES = [
        "git clone git@github.com:kirodotdev/KiroCrew.git",
        "git clone ssh://git@github.com/x/y.git",
        "git fetch github.com:x/y main",
        "tar -cf backup.example.com:/archive.tar dir",
    ]

    @pytest.mark.parametrize("cmd", _DOTTED_REMOTES)
    def test_dotted_remote_is_not_dns_pending_refused(self, cmd, _floor_monkeypatch):
        _floor_monkeypatch.setattr(_argv_floor, "_resolved_host_verdict", lambda host, **_kw: True)
        assert not security._is_git_tar_to_self(cmd), cmd
        assert security._is_git_tar_to_self("git clone localhost:x")

    # A git/tar remote host is read as typed: git and tar never glob-expand a
    # host, and a word with a ``/`` before its colon is a refspec, not a
    # remote.  Maintainer decision: these are not hosts.
    _LITERAL_HOST_ALLOWED = [
        "git fetch origin '+refs/heads/*:refs/remotes/origin/*'",
        "git fetch origin +refs/heads/*:refs/remotes/origin/*",
        "git push origin '*:*'",
        "git ls-remote local*:repo",
        "git ls-remote l?calhost:repo",
        "git -c core.sshcommand=s?h ls-remote local*:x",
        "tar -cf local*:out.tar s*",
    ]

    @pytest.mark.parametrize("cmd", _LITERAL_HOST_ALLOWED)
    def test_glob_or_refspec_host_is_allowed(self, cmd):
        assert not security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) != self._RULE, cmd

    # A command value that names only REMOTE hosts (a ProxyJump bastion) is read
    # literally, with no DNS: the DNS layer answering "pending" for the
    # first-seen dotted bastion must not refuse it.
    _REMOTE_HOP_VALUES = [
        'GIT_SSH_COMMAND="ssh -o ProxyJump=bastion.example.com" git fetch origin',
        "GIT_SSH_COMMAND='ssh -J bastion.example.com' git fetch origin",
        "git -c core.sshCommand='ssh -o ProxyJump=bastion.example.com' fetch origin",
        "git clone --upload-pack='ssh bastion.example.com git-upload-pack' far:x",
        "tar --rsh-command='ssh -J bastion.example.com' -cf far.example.com:a.tar dir",
    ]

    @pytest.mark.parametrize("cmd", _REMOTE_HOP_VALUES)
    def test_remote_hop_value_is_not_dns_pending_refused(self, cmd, _floor_monkeypatch):
        _floor_monkeypatch.setattr(_argv_floor, "_resolved_host_verdict", lambda host, **_kw: True)
        assert not security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) != self._RULE, cmd
        # The literal self-dialing value is still refused under the same stub.
        assert security._is_git_tar_to_self("GIT_SSH_COMMAND='ssh localhost' git fetch origin")

    def test_force_local_cannot_exempt_a_self_shaped_archive(self):
        # Failing closed here is the maintainer-accepted price of removing the
        # option-grammar exemption: a self-shaped archive is always refused.
        assert security._is_git_tar_to_self("tar --force-local -cf localhost:a.tar dir")
        assert security._is_git_tar_to_self("tar -cf localhost:a.tar --force-local")

    def test_own_hostname_and_fqdn_still_refused(self, monkeypatch):
        # dns=False keeps the literal own-name cache authoritative: this
        # machine's own short name AND its FQDN are still refused as self-dials
        # for git and tar, while an unrelated dotted remote stays allowed.
        monkeypatch.setattr(
            _argv_floor,
            "_OWN_HOST_NAMES_CACHE",
            frozenset({"selfhost", "selfhost.example.com"}),
        )
        for cmd in (
            "git clone selfhost:x",
            "git clone git@selfhost.example.com:x",
            "git fetch selfhost.example.com:x main",
            "tar -cf selfhost.example.com:/a.tar dir",
        ):
            assert security._is_git_tar_to_self(cmd), cmd
        assert not security._is_git_tar_to_self("git clone notus.example.com:x")

    def test_pattern_is_a_subset_of_the_floor_predicate(self):
        """Every command the catalog pattern denies, the predicate denies too."""
        import re

        rx = re.compile(_rule_pattern(self._RULE), re.IGNORECASE)
        corpus = [
            "git clone localhost:x",
            "git fetch 127.0.0.1:x",
            "git pull localhost:/srv/x",
            "git ls-remote localhost:x",
            "git push me@localhost:x",
            "/usr/bin/git clone localhost:x",
            "git.exe clone localhost:x",
            "true; git clone localhost:x",
            "git clone [::1]:x",
            "git clone 127.0.0.1:",
            # non-escape forms the pattern must NOT match
            "git clone https://localhost/x",
            "git log --grep localhost:x",
            "git clone /local/path",
        ]
        for cmd in corpus:
            if rx.search(cmd.lower()):
                assert security._is_git_tar_to_self(
                    cmd.lower()
                ), f"pattern matched but predicate did not: {cmd}"

    # The raw-text pattern's userinfo group must not swallow a ``scheme://``
    # prefix: an http(s) URL with ``user@`` to this host is not an ssh dial.
    @pytest.mark.parametrize(
        "cmd",
        [
            "git clone http://dev@localhost:3000/org/app.git",
            "git fetch https://ci@127.0.0.1:8443/org/app.git main",
            "git.exe clone http://dev@localhost:3000/org/app.git",
        ],
    )
    def test_pattern_skips_http_userinfo_to_self(self, cmd):
        import re

        rx = re.compile(_rule_pattern(self._RULE), re.IGNORECASE)
        assert rx.search(cmd) is None, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git clone user@localhost:x",
            "git clone localhost:x",
            "git fetch me@127.0.0.1:x",
            "git clone user@[::1]:x",
        ],
    )
    def test_pattern_still_matches_scp_self_dials(self, cmd):
        import re

        rx = re.compile(_rule_pattern(self._RULE), re.IGNORECASE)
        assert rx.search(cmd) is not None, cmd

    _CONFIG_TRANSPORT_COMMANDS = [
        (
            "clone",
            "git {config} clone https://github.com/kirodotdev/KiroCrew.git",
            "git clone {config} https://github.com/kirodotdev/KiroCrew.git",
        ),
        ("fetch", "git {config} fetch origin", "git fetch {config} origin"),
        (
            "ls-remote",
            "git {config} ls-remote https://github.com/kirodotdev/KiroCrew.git",
            "git ls-remote {config} https://github.com/kirodotdev/KiroCrew.git",
        ),
        (
            "submodule-update",
            "git {config} submodule update",
            "git submodule update {config}",
        ),
        (
            "remote-add",
            "git {config} remote add origin https://github.com/kirodotdev/KiroCrew.git",
            "git remote add {config} origin https://github.com/kirodotdev/KiroCrew.git",
        ),
    ]
    _CONFIG_CASES = [
        ("http-proxy", "http.proxy", "localhost:3128", False),
        ("https-proxy", "https.proxy", "localhost:3128", False),
        ("remote-url", "remote.x.url", "localhost:x", True),
        (
            "url-rewrite",
            "url.ssh://localhost/.insteadOf",
            "https://github.com/",
            True,
        ),
        ("ssh-command", "core.sshCommand", "ssh localhost", True),
    ]

    @pytest.mark.parametrize(("verb", "pre", "post"), _CONFIG_TRANSPORT_COMMANDS)
    @pytest.mark.parametrize(("case", "key", "value", "denied"), _CONFIG_CASES)
    @pytest.mark.parametrize("position", ["pre", "post"])
    @pytest.mark.parametrize("source", ["config", "config-env"])
    def test_git_config_values_are_classified_by_key(
        self, verb, pre, post, case, key, value, denied, position, source
    ):
        del verb, case
        if source == "config-env":
            prefix = f"CONFIG_VALUE='{value}' "
            config = f"--config-env {key}=CONFIG_VALUE"
        else:
            prefix = ""
            config = f"-c {key}='{value}'"
        cmd = prefix + (pre if position == "pre" else post).format(config=config)
        assert security._is_git_tar_to_self(cmd) is denied, cmd
        if denied:
            assert _denied_by(cmd) == self._RULE, cmd
        else:
            assert _denied_by(cmd) != self._RULE, cmd

    def test_post_verb_proxy_config_is_not_a_remote_candidate(self):
        cmd = "git clone -c http.proxy=localhost:3128 https://github.com/org/repo"
        assert not security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) != self._RULE, cmd

    @pytest.mark.parametrize(
        "template",
        ["-c {}", "-c{}", "--config {}", "--config={}"],
        ids=["short-detached", "short-attached", "long-detached", "long-attached"],
    )
    @pytest.mark.parametrize(
        ("config", "denied"),
        [
            ("http.proxy=localhost:3128", False),
            ("remote.x.url=localhost:x", True),
        ],
        ids=["http-proxy", "remote-url"],
    )
    def test_post_verb_config_spellings_use_the_key_aware_classifier(
        self, template, config, denied
    ):
        option = template.format(config)
        cmd = f"git clone {option} https://github.com/kirodotdev/KiroCrew.git"
        assert security._is_git_tar_to_self(cmd) is denied, cmd
        if denied:
            assert _denied_by(cmd) == self._RULE, cmd
        else:
            assert _denied_by(cmd) != self._RULE, cmd

    @pytest.mark.parametrize(
        ("key", "value", "denied"),
        [
            ("remote.x.url", "localhost:x", True),
            ("http.proxy", "localhost:3128", False),
        ],
        ids=["transport-url", "http-proxy"],
    )
    @pytest.mark.parametrize(
        "env_form",
        [
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0={key} GIT_CONFIG_VALUE_0={value} ",
            "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0={key} " "GIT_CONFIG_VALUE_0={value}; ",
            "declare -x GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0={key} " "GIT_CONFIG_VALUE_0={value}; ",
            "typeset -x GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0={key} " "GIT_CONFIG_VALUE_0={value}; ",
        ],
        ids=["prefix", "export", "declare-export", "typeset-export"],
    )
    def test_numbered_environment_config_uses_the_key_aware_classifier(
        self, key, value, denied, env_form
    ):
        cmd = env_form.format(key=key, value=value) + "git fetch origin"
        assert security._is_git_tar_to_self(cmd) is denied, cmd
        if denied:
            assert _denied_by(cmd) == self._RULE, cmd
        else:
            assert _denied_by(cmd) != self._RULE, cmd

    @pytest.mark.parametrize(
        "cmd",
        [
            "git -c remote.selftarget.url=localhost:x ls-remote selftarget",
            "git -c remote.selftarget.pushurl=localhost:x ls-remote selftarget",
            "git -c alias.foo='ssh localhost' ls-remote far:x",
        ],
        ids=["remote-url", "remote-pushurl", "command-value-key-agnostic"],
    )
    def test_transport_url_key_or_command_value_to_self_is_denied(self, cmd):
        assert _denied_by(cmd) == self._RULE, cmd

    # git percent-decodes a URL authority before dialing, so the self check
    # reads the DECODED host.
    @pytest.mark.parametrize(
        "cmd",
        [
            "git ls-remote --upload-pack='sh -c id' ssh://local%68ost/x",
            "git fetch ssh://127.0.0.%31/x",
            "git clone git+ssh://local%68ost/x",
        ],
        ids=["encoded-host", "encoded-ip", "alias-encoded-host"],
    )
    def test_percent_encoded_self_host_is_denied(self, cmd):
        assert security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) == self._RULE, cmd

    def test_percent_encoded_remote_host_stays_allowed(self):
        # Decoding only ever reveals a literal self host; a decoded remote host
        # is still just a remote.
        cmd = "git clone ssh://git%68ub.example.com/x"
        assert not security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) != self._RULE, cmd

    # A separator REAL at the nested payload's own level splits it into its
    # sub-commands, so the git/tar verb after ``cd``/``true`` is read; a
    # single-command payload and a quoted separator at the TOP level are
    # unchanged.
    @pytest.mark.parametrize(
        "cmd",
        [
            "sh -c 'cd /; git clone ssh://127.0.0.1/r'",
            "bash -c 'true && tar -cf localhost:o d'",
            "sh -c 'cd /; git clone localhost:x'",
            "sh -c 'git clone localhost:x'",
        ],
        ids=["nested-semicolon-git", "nested-andand-tar", "nested-semicolon-scp", "single-command"],
    )
    def test_nested_multi_command_payload_to_self_is_denied(self, cmd):
        assert security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) == self._RULE, cmd

    def test_nested_payload_comment_is_data_at_its_own_level(self):
        # The ``#`` is a comment at the payload's OWN level, so ``localhost:y``
        # after it is prose, not a second remote; the clone target is far:x.
        # (A frame that left the separator masked would read the comment tail as
        # a self dial.)
        cmd = "sh -c 'git clone far:x # localhost:y'"
        assert not security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) != self._RULE, cmd

    def test_attr_source_global_option_value_is_stepped_over(self):
        # ``--attr-source`` takes a SEPARATE value, so the verb after its value
        # is the transport verb.
        cmd = "git --attr-source HEAD fetch ssh://localhost/x"
        assert security._is_git_tar_to_self(cmd), cmd
        assert _denied_by(cmd) == self._RULE, cmd

    def test_opt_out_disables_both_tiers(self):
        effective = compute_effective_denied(BUILTIN_DENIED_RULES, (self._RULE,), False, (), ())
        assert is_denied("git clone localhost:x", denied_regexes=list(effective)) is None
        assert is_denied("tar -cf localhost:a.tar dir", denied_regexes=list(effective)) is None
