#!/bin/bash
# Yaxiio background fix loop — run until mixed-language count drops below 100
while true; do
    # Run one batch of translations
    python3 /opt/commander/tools/fast_translate.py 2>&1 | tail -1
    
    # Re-audit
    result=$(python3 /opt/commander/tools/multilang_audit.py 2>&1 | grep "混杂")
    remaining=$(echo "$result" | grep -oP '混杂\K\d+')
    echo "[$(date +%H:%M:%S)] Remaining: $remaining"
    
    # Sync & deploy
    python3 /opt/commander/tools/content_sync.py full > /dev/null 2>&1
    python3 /opt/commander/tools/deploy_hook.py verify power > /dev/null 2>&1
    
    if [ "$remaining" -lt 100 ]; then
        echo "DONE! Only $remaining issues remain."
        break
    fi
    
    # Small pause between rounds
    sleep 5
done
