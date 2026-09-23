#!/usr/bin/env python3
import subprocess
import json
import time
import sys

TARGETS = [
    'cbot-uk100-judas',
    'cbot-ustec-judas',
    'cbot-us500',
    'cbot-uk100',
    'cbot-de40',
    'cbot-us30'
]

def main():
    print("===> Recalibrating Live bot risk parameters to 0.2%...")
    for name in TARGETS:
        try:
            inspect_out = subprocess.check_output(['docker', 'inspect', name]).decode()
            inspect = json.loads(inspect_out)[0]
        except Exception as e:
            print(f"[-] Could not inspect {name}: {e}")
            continue

        cmd = inspect['Config']['Cmd']
        image = inspect['Config']['Image']
        
        # Modify risk args to 0.2
        new_cmd = []
        modified = False
        for arg in cmd:
            if arg == '--riskFactor=1.0' or arg.startswith('--riskFactor=1'):
                new_cmd.append('--riskFactor=0.2')
                modified = True
            elif arg == '--RiskPerTradePercent=1.0' or arg.startswith('--RiskPerTradePercent=1'):
                new_cmd.append('--RiskPerTradePercent=0.2')
                modified = True
            else:
                new_cmd.append(arg)
        if not modified:
            print(f"[i] {name} does not have 1.0 risk arg. Skipping.")
            continue

        print(f"\n[+] Updating {name} to risk 0.2%...")
        subprocess.run(['docker', 'stop', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'rm', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        run_cmd = [
            'docker', 'run', '-d',
            '--name', name,
            '--restart', 'unless-stopped',
            '--network', 'host',
            '-v', '/root:/root',
            '-v', '/root/AgentFxTrading:/workspace',
            image
        ] + new_cmd

        res = subprocess.run(run_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[-] Failed to run {name}: {res.stderr}")
        else:
            print(f"[+] {name} launched successfully ({res.stdout.strip()[:12]}).")

    print("\n===> Waiting 10s for containers to initialize...")
    time.sleep(10)

    print("\n===> Verifying container statuses and logs:")
    for name in TARGETS:
        ps_res = subprocess.run(['docker', 'ps', '--filter', f'name={name}', '--format', '{{.Status}}'], capture_output=True, text=True)
        status = ps_res.stdout.strip()
        print(f"{name:26}: Status = {status}")

if __name__ == '__main__':
    main()
