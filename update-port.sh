#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; . "$DIR/.env"; set +a

PORT=$(cat "$DIR/gluetun/forwarded_port" 2>/dev/null)
if [ -z "$PORT" ]; then
    echo "$(date -Iseconds) No forwarded port found"
    exit 1
fi

RESULT=$(docker exec qbittorrent sh -c "
COOKIE=\$(curl -sS -i 'http://localhost:$QBIT_WEBUI_PORT/api/v2/auth/login' --data 'username=$QBIT_USER&password=$QBIT_PASS' | grep -i '^set-cookie' | sed 's/Set-Cookie: //I' | cut -d';' -f1)
if [ -z \"\$COOKIE\" ]; then
    echo 'LOGIN_FAILED'
    exit 1
fi

CURRENT=\$(curl -sS -H \"Cookie: \$COOKIE\" 'http://localhost:$QBIT_WEBUI_PORT/api/v2/app/preferences' | python3 -c 'import json,sys; print(json.load(sys.stdin).get(\"listen_port\"))')
if [ \"\$CURRENT\" = \"$PORT\" ]; then
    echo \"ALREADY_CORRECT:$PORT\"
    exit 0
fi

curl -sS -H \"Cookie: \$COOKIE\" 'http://localhost:$QBIT_WEBUI_PORT/api/v2/app/setPreferences' -d 'json={\"listen_port\":$PORT,\"random_port\":false}' > /dev/null

NEW=\$(curl -sS -H \"Cookie: \$COOKIE\" 'http://localhost:$QBIT_WEBUI_PORT/api/v2/app/preferences' | python3 -c 'import json,sys; print(json.load(sys.stdin).get(\"listen_port\"))')
if [ \"\$NEW\" = \"$PORT\" ]; then
    echo \"UPDATED:\$CURRENT->\$NEW\"
else
    echo \"UPDATE_FAILED:wanted=$PORT got=\$NEW\"
    exit 1
fi
")

STATUS=$?
echo "$(date -Iseconds) $RESULT"
exit $STATUS
