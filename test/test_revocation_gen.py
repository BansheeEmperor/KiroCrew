"""The persisted revocation counter that ``kirocrew logout`` bumps.

The counter's fail-closed read and write rules are pinned in ``test_refresh_tokens.py``
(TR-U-28..37). This file pins that the event loop never waits on a bump's write.
"""

from __future__ import annotations

import threading
from pathlib import Path

import kiro_crew.dashboard.revocation_gen as rg
from kiro_crew.testing.wait import default_timeout


def test_validation_reads_the_counter_while_a_bump_write_is_stuck(
    tmp_path: Path, monkeypatch
) -> None:
    """A slow counter write never holds up token validation, and publishes only once it lands.

    Every token validation calls ``current_revocation_gen_or_none`` on the event loop;
    ``kirocrew logout`` bumps the counter on a worker thread. Where on-access antivirus
    scans the new temp file, that write takes seconds, and a lock held across it would
    park the loop until the loop watchdog kills the gateway. Here the bump is held
    before its rename on an Event, so the read either answers while the write is stuck
    or it does not. It must answer with the old value: the new one is not durable yet.
    """
    # The read gets half the lost-run bound and the stuck write all of it, so a
    # read that waits on the write fails while the write is still held.
    read_bound = default_timeout() / 2
    in_write = threading.Event()
    release = threading.Event()
    real_replace = rg.replace_with_retry

    def _stuck_replace(src: Path, dst: Path) -> None:
        in_write.set()
        release.wait(default_timeout())
        real_replace(src, dst)

    monkeypatch.setattr("kiro_crew.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(rg, "_gen", 4)
    monkeypatch.setattr(rg, "replace_with_retry", _stuck_replace)

    bumped: list[int] = []
    writer = threading.Thread(target=lambda: bumped.append(rg.bump_revocation_gen()), daemon=True)
    writer.start()
    try:
        assert in_write.wait(default_timeout()), "the bump never reached its write"
        seen: list[int | None] = []
        reader = threading.Thread(
            target=lambda: seen.append(rg.current_revocation_gen_or_none()), daemon=True
        )
        reader.start()
        reader.join(read_bound)
        assert not reader.is_alive(), "the counter read waited for the stuck bump write"
        assert seen == [4]
    finally:
        release.set()
        writer.join(default_timeout())

    assert not writer.is_alive()
    assert bumped == [5]
    assert rg.current_revocation_gen_or_none() == 5
    assert (tmp_path / rg._REVOCATION_FILE).read_text(encoding="utf-8") == "5"
