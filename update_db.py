import sqlite3
import re
import subprocess

conn = sqlite3.connect("portfolio.db")
c = conn.cursor()
c.execute("SELECT name, run_command FROM cbot_configs")
bots = c.fetchall()

for bot_name, cmd in bots:
    # Fix BounceDistanceThreshold to 1.5 across all symbols
    new_cmd = re.sub(r'--BounceDistanceThreshold=[0-9.]+', '--BounceDistanceThreshold=1.5', cmd)
    
    if new_cmd != cmd:
        print(f"Updating {bot_name} in DB...")
        c.execute("UPDATE cbot_configs SET run_command = ? WHERE name = ?", (new_cmd, bot_name))
        
        # Now restart the docker container
        print(f"Restarting container {bot_name}...")
        subprocess.run(["docker", "stop", bot_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", bot_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Run the new command
        subprocess.run(new_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

conn.commit()
conn.close()
print("All bots updated and restarted!")
