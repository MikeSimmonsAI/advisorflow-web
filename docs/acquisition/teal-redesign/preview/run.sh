#!/bin/bash
# usage: WHO=owner PAGES='[[name,path],...]' ./run.sh
cd /home/claude/wt-api && . /home/claude/qa/acq_preview/env.sh
(python3 -m uvicorn app.main:app --port 8765 > /home/claude/qa/acq_preview/server.log 2>&1 &)
for i in $(seq 1 40); do curl -s -o /dev/null http://localhost:8765/health && break; sleep 2; done
cd /home/claude/qa/acq_preview && NODE_PATH=$(npm root -g) timeout 500 node shot.js 2>&1 | tail -2
grep -v "^200\|^201\|^204" api.log | sort | uniq -c | head -8
pkill -f "uvicorn app.main:app --port 8765"
