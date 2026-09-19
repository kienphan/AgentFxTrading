import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"


def run_fn(snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source install.sh (main is guarded) and run a bash snippet against it."""
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", "-c", f"source '{SCRIPT}'; {snippet}"],
        capture_output=True, text=True, env=full_env,
    )


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_running_directly_as_non_root_hits_preflight():
    if os.geteuid() == 0:
        pytest.skip("must run as non-root")
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Run as root" in result.stderr


def test_piped_into_bash_also_runs_main():
    if os.geteuid() == 0:
        pytest.skip("must run as non-root")
    result = subprocess.run(["bash", "-c", f"cat '{SCRIPT}' | bash"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Run as root" in result.stderr


def test_sourcing_does_not_run_main():
    result = run_fn("echo sourced-ok")
    assert result.returncode == 0
    assert result.stdout.strip() == "sourced-ok"


@pytest.mark.parametrize("raw,encoded", [
    ("kaz@112358.", "kaz%40112358."),
    ("abc-_.~09", "abc-_.~09"),
    ("p w:d/#?", "p%20w%3Ad%2F%23%3F"),
])
def test_urlencode(raw, encoded):
    result = run_fn(f"urlencode '{raw}'")
    assert result.stdout == encoded


@pytest.mark.parametrize("encoded,raw", [
    ("kaz%40112358.", "kaz@112358."),
    ("p%20w%3Ad%2F%23%3F", "p w:d/#?"),
    ("a+b%2Bc", "a+b+c"),
])
def test_urldecode(encoded, raw):
    result = run_fn(f"urldecode '{encoded}'")
    assert result.stdout == raw


def test_validate_ssh_key_accepts_real_key(tmp_path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "k")], check=True)
    pub = (tmp_path / "k.pub").read_text().strip()
    assert run_fn(f"validate_ssh_key '{pub}'").returncode == 0


def test_validate_ssh_key_rejects_garbage():
    assert run_fn("validate_ssh_key 'not a key at all'").returncode != 0


@pytest.mark.parametrize("version", ["22.04", "24.04", "26.04"])
def test_supported_ubuntu_version_accepts_lts_releases(version):
    assert run_fn(f"supported_ubuntu_version '{version}'").returncode == 0


@pytest.mark.parametrize("version", ["20.04", "25.10", "26.10", ""])
def test_supported_ubuntu_version_rejects_other_releases(version):
    assert run_fn(f"supported_ubuntu_version '{version}'").returncode != 0


def test_db_password_from_env_decodes_password(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('X=1\nDATABASE_URL="postgresql://agentfx:kaz%40112358.@127.0.0.1:5432/agentfx"\n')
    result = run_fn(f"db_password_from_env '{env_file}'")
    assert result.stdout == "kaz@112358."


def test_db_password_from_env_missing_file_or_var(tmp_path):
    assert run_fn(f"db_password_from_env '{tmp_path / 'nope'}'").stdout == ""
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=qwen\n")
    assert run_fn(f"db_password_from_env '{env_file}'").stdout == ""


# Real `sshd -T` prints >4 KiB with passwordauthentication near the top, so the kernel
# delivers it in two write()s. This fake reproduces that shape with the producer
# descheduled between them — the case where `grep -q` closes the pipe early.
FAKE_SSHD_T_TWO_WRITES = (
    "sshd() { printf 'permitrootlogin prohibit-password\\npasswordauthentication no\\n'; "
    "sleep 0.1; printf 'ciphers %8192s\\n' x; }"
)


def test_sshd_password_auth_disabled_when_sshd_keeps_writing_after_the_match():
    result = run_fn(f"{FAKE_SSHD_T_TWO_WRITES}; sshd_password_auth_disabled")
    assert result.returncode == 0, result.stderr


def test_sshd_password_auth_disabled_rejects_yes():
    result = run_fn("sshd() { printf 'passwordauthentication yes\\n'; }; sshd_password_auth_disabled")
    assert result.returncode != 0


def test_sshd_password_auth_disabled_fails_when_sshd_T_fails():
    result = run_fn("sshd() { return 255; }; sshd_password_auth_disabled")
    assert result.returncode != 0


def test_generate_password_is_48_hex_chars():
    out = run_fn("generate_password").stdout.strip()
    assert len(out) == 48
    int(out, 16)


def test_err_trap_fires_for_failures_inside_functions():
    result = run_fn("CURRENT_STEP='demo step'; f() { false; }; f; echo unreachable")
    assert result.returncode != 0
    assert "unreachable" not in result.stdout
    assert 'Install failed during step "demo step"' in result.stderr


DB_URL = "postgresql://agentfx:kaz%40112358.@127.0.0.1:5432/agentfx"


def _env_for_write(tmp_path: Path) -> dict:
    """Point the script at a throwaway repo dir and at the current user so chown works."""
    repo = tmp_path / "AgentFxTrading"
    repo.mkdir(exist_ok=True)
    return {
        "FORGE_USER": subprocess.check_output(["id", "-un"], text=True).strip(),
        "FORGE_GROUP": subprocess.check_output(["id", "-gn"], text=True).strip(),
        "FORGE_HOME": str(tmp_path),
        "REPO_DIR": str(repo),
        "CTRADER_HOME": str(tmp_path / "ctrader"),
    }


def test_render_env_replaces_placeholders_and_keeps_rest(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(
        "LLM_PROVIDER=qwen\n"
        "# DATABASE_URL=postgresql://agentfx:old@127.0.0.1:5432/agentfx\n"
        "CTRADER_HOME=/root\n"
    )
    out = run_fn(f"render_env '{example}' '{DB_URL}' /home/forge/ctrader").stdout
    lines = out.splitlines()
    assert "LLM_PROVIDER=qwen" in lines
    assert f"DATABASE_URL={DB_URL}" in lines
    assert "CTRADER_HOME=/home/forge/ctrader" in lines
    assert not any(l.startswith("# DATABASE_URL") for l in lines)
    assert lines.count("CTRADER_HOME=/home/forge/ctrader") == 1
    assert "CTRADER_HOME=/root" not in lines


def test_write_env_creates_file_from_example(tmp_path):
    env = _env_for_write(tmp_path)
    (Path(env["REPO_DIR"]) / ".env.example").write_text("LLM_PROVIDER=qwen\n# DATABASE_URL=x\nCTRADER_HOME=/root\n")
    result = run_fn("DB_PASSWORD='kaz@112358.'; write_env", env)
    assert result.returncode == 0, result.stderr
    env_file = Path(env["REPO_DIR"]) / ".env"
    content = env_file.read_text().splitlines()
    assert f"DATABASE_URL={DB_URL}" in content
    assert f"CTRADER_HOME={env['CTRADER_HOME']}" in content
    assert "LLM_PROVIDER=qwen" in content
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"


def test_write_env_leaves_existing_file_alone_but_fills_missing_keys(tmp_path):
    env = _env_for_write(tmp_path)
    env_file = Path(env["REPO_DIR"]) / ".env"
    env_file.write_text("LLM_PROVIDER=openai\nOPENAI_API_KEY=sk-real\n")
    result = run_fn("DB_PASSWORD='kaz@112358.'; write_env", env)
    assert result.returncode == 0, result.stderr
    content = env_file.read_text().splitlines()
    assert content[0] == "LLM_PROVIDER=openai"
    assert "OPENAI_API_KEY=sk-real" in content
    assert f"DATABASE_URL={DB_URL}" in content
    assert f"CTRADER_HOME={env['CTRADER_HOME']}" in content


def test_write_env_is_idempotent_when_complete(tmp_path):
    env = _env_for_write(tmp_path)
    env_file = Path(env["REPO_DIR"]) / ".env"
    original = f"LLM_PROVIDER=qwen\nDATABASE_URL={DB_URL}\nCTRADER_HOME={env['CTRADER_HOME']}\n"
    env_file.write_text(original)
    run_fn("DB_PASSWORD='different'; write_env", env)
    assert env_file.read_text() == original


def test_render_systemd_unit():
    out = run_fn("render_systemd_unit", {"FORGE_USER": "forge", "FORGE_HOME": "/home/forge"}).stdout
    assert "User=forge" in out
    assert "Group=forge" in out
    assert "WorkingDirectory=/home/forge/AgentFxTrading" in out
    assert "ExecStart=/home/forge/AgentFxTrading/.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8000" in out
    assert "After=network-online.target postgresql.service docker.service" in out
    assert "Restart=always" in out
    assert "WantedBy=multi-user.target" in out


def test_render_sshd_dropin():
    out = run_fn("render_sshd_dropin").stdout.splitlines()
    assert "PasswordAuthentication no" in out
    assert "KbdInteractiveAuthentication no" in out
    assert "PubkeyAuthentication yes" in out
    assert "PermitRootLogin prohibit-password" in out


def test_render_backup_cron():
    out = run_fn("render_backup_cron", {"FORGE_USER": "forge", "FORGE_HOME": "/home/forge"}).stdout
    assert out == "0 3 * * * forge /home/forge/AgentFxTrading/scripts/backup_postgres.sh\n"
