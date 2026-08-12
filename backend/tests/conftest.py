"""Process-wide test isolation for the frozen `settings` singleton.

`app.config.settings` is a single frozen dataclass instance, created once when
`app.config` is first imported and shared by every test module for the whole
`pytest` run. The suite's house pattern for exercising a config-dependent branch
is to mutate that singleton in place with `object.__setattr__` (going around the
freeze) and undo it in a `try/finally` — see `test_club_mirror._restore_settings`
and the many `object.__setattr__(settings, "demo_mode", ...)` sites.

The hazard: a test that fails an assertion *before* reaching its `finally`
leaks the mutation into every later module. The most damaging case is
`demo_mode=True` leaking out of the time-of-day-sensitive demo-roll test — with
`demo_mode` stuck on, `settings.email_enabled` flips true and `vault.ensure_client`
hands back a `DemoClient`, which is exactly why `test_verify_email` (2),
`test_vault_reauth` (3) and `test_signup_password_floor` (1) pass in isolation
but fail in the full suite.

This autouse fixture snapshots the singleton's fields before each test and
restores them afterwards (teardown runs even when the test fails), so no test's
settings mutation can escape its own body. It is a superset of the per-module
`_restore_settings` fixtures, which remain correct and harmless alongside it.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_settings():
    """Restore `app.config.settings` to its pre-test field values after each test."""
    from app.config import settings

    before = dict(vars(settings))
    try:
        yield
    finally:
        current = vars(settings)
        if current != before:
            current.clear()
            current.update(before)
