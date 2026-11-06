#!/bin/bash
# usage: run_all.sh FE API SHOTS STAFF STAFF_PW [UNENT UNENT_PW]
set -o pipefail
FE=$1; API=$2; SH=$3; mkdir -p $SH
OUT=$(timeout 500 python3 -I applicant_flow.py $FE $API $SH 2>&1 | grep -v "^  ")
echo "$OUT" | grep -E "^(PASS|FAIL)"
J=$(echo "$OUT" | tail -1)
AE=$(echo "$J" | python3 -c "import json,sys;print(json.load(sys.stdin)['applicant_email'])")
DE=$(echo "$J" | python3 -c "import json,sys;print(json.load(sys.stdin)['direct_email'])")
echo "APPLICANT $AE  DIRECT $DE"
timeout 600 python3 -I staff_flow.py $FE $API $SH $4 "$5" $AE 'Volt-Applicant-2026!' $DE 'Volt-Direct-2026!' $6 "$7" 2>&1 | grep -v "^  " | tail -60
