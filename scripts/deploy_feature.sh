#!/usr/bin/env bash
set -e

MSG="$1"
if [ -z "$MSG" ]; then
    echo "Usage: $0 \"<commit message>\" [--cbot <name>] [--all-cbots]"
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> 1. Running pytest..."
"$PROJECT_ROOT/.venv/bin/pytest" -q

echo "==> 2. Staging git changes..."
git add -A

if git diff-index --quiet HEAD --; then
    echo "==> No changes to commit."
else
    echo "==> 3. Committing: $MSG"
    git commit -m "$MSG"
    echo "==> 4. Pushing to origin main..."
    git push origin main
fi

echo "==> 5. Restarting agentfx.service..."
systemctl restart agentfx.service
systemctl is-active agentfx.service

# Check optional cBot restarts
shift
while [ "$#" -gt 0 ]; do
    case "$1" in
        --all-cbots)
            echo "==> Restarting all active cBot containers..."
            docker restart cbot-usdjpy cbot-gbpjpy cbot-gbpusd cbot-xauusd cbot-us30 cbot-ustec cbot-de40 cbot-gbpusd-judas cbot-xauusd-judas
            shift
            ;;
        --cbot)
            shift
            if [ -n "$1" ]; then
                echo "==> Restarting cBot container $1..."
                docker restart "$1"
                shift
            fi
            ;;
        *)
            shift
            ;;
    esac
done

echo "==> Post-feature deployment complete!"
