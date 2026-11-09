"""Which skills a KAS session for an agent offers, read from the spec KAS runs.

The application side (``remote_session.skills_copy``) needs the skill scope of a
KAS session before the engine starts, and must reach the ACP layer through this
SDK. The spec is picked in the order ``acp/harness/kas.session_extras`` picks it:
a derived agent's freshly re-derived snapshot, an app-bound file, then
``resolve_projected_spec`` (a user-level name wins over a checkout's); a
crewmate's worker runs its crewmate's merged spec. The scope is
``acp/kas_skill_scope.scope_globs`` over that spec.
"""

from __future__ import annotations

from pathlib import Path


def kas_session_skill_globs(
    session_key: str, agent: str, work_dir: str | Path | None
) -> list[str] | None:
    """The skill globs a KAS session of *agent* loads, or ``None``.

    ``None`` is ``scope_globs``' answer: a whole-catalog template, or a scope that
    is exactly the default trees KAS loads by itself. A failure raises.
    """
    from kiro_crew import agent as agent_mod
    from kiro_crew.acp import kas_skill_scope
    from kiro_crew.acp.harness.kas import mate_worker_projection, resolve_projected_spec
    from kiro_crew.apps.agent_launch import read_bound_app_spec
    from kiro_crew.config.paths import kiro_agents_dir
    from kiro_crew.mate_worker import MATE_WORKER_AGENT

    agents_dir = kiro_agents_dir()
    snapshot = agent_mod.require_fresh_derived_spec(agent, work_dir)
    bound = read_bound_app_spec(session_key, agent)
    if snapshot is not None and snapshot.spec is not None:
        spec, spec_dir = snapshot.spec, agents_dir
    elif bound is not None:
        spec, spec_dir = bound, agents_dir
    else:
        spec, spec_dir = resolve_projected_spec(agents_dir, agent, work_dir)
    if agent == MATE_WORKER_AGENT:
        # An unreadable kit raises, as it refuses the session in KAS.
        spec = mate_worker_projection(spec, spec_dir, session_key)
    return kas_skill_scope.scope_globs(agent, spec, agents_dir=spec_dir, work_dir=work_dir)
