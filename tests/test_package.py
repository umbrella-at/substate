"""Smoke tests.

These exist so CI has something to run before the domain logic lands.
They get deleted once real tests cover the state machine.
"""

import substate


def test_version_is_exposed() -> None:
    assert isinstance(substate.__version__, str)
    assert substate.__version__
