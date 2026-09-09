# AgentFxTrading - AI Agent Project Conventions & Post-Feature Delivery Hook

## Architecture Overview
- **Backend**: FastAPI server running under systemd as `agentfx.service` (`/root/AgentFxTrading/.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8000`).
- **Bots**: Spotware cTrader CLI containers running in Docker with `--network host`:
  - `cbot-usdjpy` (Tokyo session M15 TMS+ORB)
  - `cbot-gbpjpy`, `cbot-gbpusd`, `cbot-de40` (London session)
  - `cbot-xauusd`, `cbot-us30`, `cbot-ustec` (New York session)
  - `cbot-xauusd-judas`, `cbot-gbpusd-judas` (Judas sweep SMC bots)
- **Watchdog**: Background auto-healing loop in `app/cbot_watchdog.py` monitoring cBot login state and auto-restarting stuck containers.
- **Database**: PostgreSQL 17.11 (Production) via `DATABASE_URL` with automated daily backup cron job (`scripts/backup_postgres.sh`); fallback to SQLite WAL mode (`portfolio.db`) for testing.

## Mandatory Post-Feature Delivery Workflow
Whenever you add a feature, fix a bug, or adjust configurations:

1. **Test & Verify**:
   - Run unit tests: `/root/AgentFxTrading/.venv/bin/pytest`.
   - Ensure all tests pass.

2. **Commit & Push to GitHub**:
   - Stage modified files: `git add <files>`
   - Commit: `git commit -m "feat/fix/style: <concise message>"`
   - Push to main: `git push origin main`

3. **Restart Service**:
   - If `app/`, `templates/`, or `static/` files changed:
     `systemctl restart agentfx.service`
   - Verify: `systemctl status agentfx.service` and `curl -s http://127.0.0.1:8000/api/watchdog/status`

4. **Restart cBot Containers (If Relevant)**:
   - If `cBot/*.cs`, bot parameters, or database bot configs changed:
     Restart the affected container(s): `docker restart <container_name>` (e.g. `cbot-usdjpy`).
     Check container logs to ensure clean login: `docker logs --tail 20 <container_name>`.
