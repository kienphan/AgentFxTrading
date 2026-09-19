import gzip
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "backup_postgres.sh"
DB_URL = "postgresql://agentfx:kaz%40112358.@127.0.0.1:5432/agentfx"


def _make_fake_pg_dump(bin_dir: Path, args_file: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "pg_dump"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > '{args_file}'\n"
        "echo 'FAKE DUMP'\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)


def _run(project_root: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PROJECT_ROOT": str(project_root)}
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)


def test_backup_uses_database_url_from_env_file(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text(f"LLM_PROVIDER=qwen\nDATABASE_URL={DB_URL}\n")
    args_file = tmp_path / "pg_dump_args.txt"
    _make_fake_pg_dump(tmp_path / "bin", args_file)

    result = _run(project, tmp_path / "bin")

    assert result.returncode == 0, result.stderr
    dumps = sorted((project / "backups").glob("agentfx_*.sql.gz"))
    assert len(dumps) == 1
    with gzip.open(dumps[0], "rt") as fh:
        assert fh.read() == "FAKE DUMP\n"
    args = args_file.read_text().splitlines()
    assert f"--dbname={DB_URL}" in args
    assert "--clean" in args and "--if-exists" in args
    assert "SUCCESS" in (project / "backups" / "backup.log").read_text()


def test_backup_fails_without_database_url(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("LLM_PROVIDER=qwen\n")
    _make_fake_pg_dump(tmp_path / "bin", tmp_path / "args.txt")

    result = _run(project, tmp_path / "bin")

    assert result.returncode == 1
    assert list((project / "backups").glob("agentfx_*.sql.gz")) == []
    assert "DATABASE_URL" in (project / "backups" / "backup.log").read_text()
