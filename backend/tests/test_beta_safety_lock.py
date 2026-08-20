"""APP_ENV=beta hard-locks execution to dry-run (the beta-channel safety lock).

The beta instance (beta.voteconcordia.com) runs the real code with real member
brokerage tokens against its own database — so a mis-set EXECUTION_MODE=live in
its .env, or an operator arming it, must still be incapable of placing a real
order. The lock lives on settings.is_live because that is the single choke
point every real-order path checks (executor.run and the dust sweeper both
compute will_place = settings.is_live AND armed), exactly how demo_mode is
made safe.

Because beta vaults real tokens on the public internet, it must also inherit
prod's secrets/transport hardening (settings.is_internet_facing): the
insecure-defaults boot refusal, the Secure session cookie, and losing the
localhost CORS carve-out. Those gates are pinned here too.

Run from backend/:  python -m pytest tests/test_beta_safety_lock.py -q
(no DB, no network — pure settings; conftest's autouse fixture restores the
frozen singleton after each test even if an assertion fails first.)
"""
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")

from app.config import insecure_prod_config, settings  # noqa: E402


def _set(**fields):
    # House pattern for the frozen settings singleton (see conftest.py).
    for k, v in fields.items():
        object.__setattr__(settings, k, v)


def test_beta_forces_is_live_false_even_when_env_says_live():
    _set(app_env="beta", execution_mode="live", demo_mode=False)
    assert settings.is_beta is True
    assert settings.is_live is False


def test_beta_lock_is_case_and_whitespace_insensitive():
    # .env files get hand-edited; " Beta " must not silently unlock live orders.
    _set(app_env="  Beta ", execution_mode="live", demo_mode=False)
    assert settings.is_beta is True
    assert settings.is_live is False


def test_prod_live_is_unaffected_by_the_beta_lock():
    # The lock must never leak into the real instance: prod + live + armed is
    # still the only combination that places orders.
    _set(app_env="prod", execution_mode="live", demo_mode=False)
    assert settings.is_beta is False
    assert settings.is_live is True


def test_beta_dry_run_is_the_expected_posture():
    # The documented beta .env (APP_ENV=beta EXECUTION_MODE=dry_run): both the
    # mode and the lock independently keep is_live off — belt and braces.
    _set(app_env="beta", execution_mode="dry_run", demo_mode=False)
    assert settings.is_live is False


def test_demo_lock_still_holds():
    # Regression guard: adding the beta clause must not weaken the demo one.
    _set(app_env="dev", execution_mode="live", demo_mode=True)
    assert settings.is_live is False


def test_beta_refuses_insecure_default_secrets_like_prod():
    # A default SESSION_SECRET on beta would let anyone mint an owner session
    # and sail past require_admin into real member tokens — so the boot gate
    # (main.startup raises on a non-empty list) must fail closed there too.
    _set(app_env="beta", session_secret="dev-insecure-session-secret",
         invite_code="concord", fernet_key="")
    assert set(insecure_prod_config()) == {
        "SESSION_SECRET", "INVITE_CODE", "CONCORD_FERNET_KEY"}


def test_beta_boots_with_real_secrets():
    _set(app_env="beta", session_secret="s" * 48,
         invite_code="beta-only-invite", fernet_key="k" * 44)
    assert insecure_prod_config() == []


def test_dev_still_skips_the_boot_gate():
    # Local dev keeps booting on the placeholder secrets.
    _set(app_env="dev", session_secret="dev-insecure-session-secret",
         invite_code="concord", fernet_key="")
    assert insecure_prod_config() == []


def test_session_cookie_secure_on_internet_facing_instances_only():
    # auth._issue stamps Secure from this property: beta+prod serve HTTPS and
    # guard real tokens; dev/demo sign in over http://localhost and must not.
    for env, expected in (("beta", True), ("prod", True), ("dev", False)):
        _set(app_env=env)
        assert settings.cookie_secure is expected, f"app_env={env}"
        assert settings.is_internet_facing is expected, f"app_env={env}"
