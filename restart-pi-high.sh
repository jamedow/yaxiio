#!/bin/bash
echo "🔄 重启 pi → deepseek high thinking"

PID=$(pgrep -f "pi-coding-agent" | head -1)
if [ -n "$PID" ]; then
  kill $PID 2>/dev/null
  sleep 2
fi

cd /app
exec pi --thinking high \
  --provider deepseek \
  --model deepseek/deepseek-chat \
  --api-key sk-7fc8fef282194bcd996c4bac97315067 \
  --continue
