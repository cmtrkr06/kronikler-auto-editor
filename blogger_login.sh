#!/usr/bin/env bash
# token.json for the Blogger API: the login renewed from the phone ("Blogger girisi yenile", encrypted on
# the state branch with BLOGGER_TOKEN_JSON as key) if it decrypts, else the BLOGGER_TOKEN_JSON secret itself.
set -euo pipefail
printf '%s' "$BLOGGER_TOKEN_JSON" > token.json
if [ -f data/blogger-token.enc ] && openssl enc -d -aes-256-cbc -pbkdf2 -in data/blogger-token.enc \
     -out token.new -pass env:BLOGGER_TOKEN_JSON 2>/dev/null \
   && python3 -c 'import json; assert json.load(open("token.new"))["refresh_token"]' 2>/dev/null; then
  mv token.new token.json; echo "Blogger girişi: state dalındaki yenilenmiş giriş"
else
  rm -f token.new; echo "Blogger girişi: BLOGGER_TOKEN_JSON secret'ı"
fi
