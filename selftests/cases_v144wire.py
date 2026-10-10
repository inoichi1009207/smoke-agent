from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES = []

def case(function):
    CASES.append(function)
    return function

@case
def both_check_sites_look_at_infra_before_skipped():
    import main

    source = inspect.getsource(main)
    marker = 'if getattr(e2e, "infra", ""):'
    assert source.count(marker) >= 2, source.count(marker)
    for block in source.split(marker)[1:3]:
        head = block[:400]
        assert 'E2E NOT RUN (install)' in head and "infra=e2e.infra" in head, head

    first = source.index(marker)
    assert first < source.index("(Playwright unavailable here, skipped)")

@case
def free_port_never_hands_out_a_port_chromium_refuses():
    import os

    from agent.fsutil import CHROMIUM_UNSAFE_PORTS, free_port

    saved = os.environ.get("AGENT_PORT_BASE")
    os.environ["AGENT_PORT_BASE"] = "5600"
    try:
        assert free_port(3400) not in CHROMIUM_UNSAFE_PORTS
    finally:
        if saved is None:
            os.environ.pop("AGENT_PORT_BASE", None)
        else:
            os.environ["AGENT_PORT_BASE"] = saved
