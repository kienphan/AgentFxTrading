# AgentFxTrading Post-Feature Delivery Invariants

You MUST adhere to this delivery protocol on every task where code, templates, configs, or features are added, modified, or fixed:

1. **GIT COMMIT & PUSH**:
   - Before finishing any task, check `git status`.
   - If there are changes, stage them: `git add <files>`.
   - Commit with a clear conventional commit message: `git commit -m "<type>: <description>"`.
   - Push to GitHub: `git push origin main`.

2. **RESTART SERVICE (FastAPI / Dashboard / Watchdog)**:
   - If any python files (`app/*.py`), HTML/CSS (`templates/*`, `static/*`), or configurations were modified:
     Run `systemctl restart agentfx.service`.
   - Verify health: `curl -s http://127.0.0.1:8000/api/watchdog/status` and `systemctl status agentfx.service`.

3. **RESTART CBOT CONTAINERS (IF APPLICABLE)**:
   - If cBot algorithms (`cBot/*.cs`), bot run commands, parameters, or database configs were modified:
     Restart the affected container(s): `docker restart <container_name>` (e.g. `cbot-usdjpy`).
   - Check container logs to ensure clean login: `docker logs --tail 20 <container_name>`.

4. **FINAL DELIVERY PROOF**:
   - Never yield completion to the user until git is clean and services are verified running.
