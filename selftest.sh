#!/usr/bin/env bash
# selftest.sh — validates the full stack without the browser.
# Exercises: C core, Python ratchet, round-trip, tamper detection,
# wrong-passphrase rejection, and binary-safety.
set -e
cd "$(dirname "$0")"

PASS="selftest-passphrase-2026"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }

echo "== RSI Vault self-test =="

# 0. Ensure the C core is built
[ -f libcryptocore.so ] || gcc -O3 -shared -fPIC -o libcryptocore.so crypto_core.c -lcrypto

# 1. Text round-trip
echo -n "1. text round-trip ............ "
echo "private key 0xABCDEF — secret notes" > "$TMP/a.txt"
python3 ratchet.py encrypt "$TMP/a.txt" "$TMP/a.enc" "$PASS" >/dev/null
python3 ratchet.py decrypt "$TMP/a.enc" "$TMP/a.out" "$PASS" >/dev/null
cmp -s "$TMP/a.txt" "$TMP/a.out" && green "PASS" || { red "FAIL"; exit 1; }

# 2. Binary round-trip (all 256 byte values incl. nulls)
echo -n "2. binary round-trip .......... "
python3 -c "open('$TMP/b.bin','wb').write(bytes(range(256))*500)"
python3 ratchet.py encrypt "$TMP/b.bin" "$TMP/b.enc" "$PASS" >/dev/null
python3 ratchet.py decrypt "$TMP/b.enc" "$TMP/b.out" "$PASS" >/dev/null
cmp -s "$TMP/b.bin" "$TMP/b.out" && green "PASS" || { red "FAIL"; exit 1; }

# 3. Wrong passphrase must fail (no output)
echo -n "3. wrong passphrase rejected .. "
if python3 ratchet.py decrypt "$TMP/a.enc" "$TMP/a.bad" "WRONG" 2>/dev/null | grep -q '"ok": false'; then
  green "PASS"
else
  red "FAIL"; exit 1
fi

# 4. Tamper detection: flip a byte in the ciphertext
echo -n "4. tamper detection ........... "
cp "$TMP/a.enc" "$TMP/a.tampered"
SIZE=$(stat -c%s "$TMP/a.tampered")
python3 -c "
data=bytearray(open('$TMP/a.tampered','rb').read())
data[-1]^=0xFF   # flip last byte of ciphertext
open('$TMP/a.tampered','wb').write(data)
"
if python3 ratchet.py decrypt "$TMP/a.tampered" "$TMP/a.t.out" "$PASS" 2>/dev/null | grep -q '"ok": false'; then
  green "PASS"
else
  red "FAIL"; exit 1
fi

# 5. Plaintext does not appear in container
echo -n "5. no plaintext leak .......... "
grep -q "secret notes" "$TMP/a.enc" && { red "FAIL"; exit 1; } || green "PASS"

# 6. Ratchet advances / heals
echo -n "6. ratchet epoch rotation ..... "
rm -f vault_state/ratchet.json
for i in $(seq 1 5); do python3 ratchet.py encrypt "$TMP/a.txt" "$TMP/e$i.enc" "$PASS" >/dev/null; done
EPOCH=$(python3 ratchet.py state | python3 -c "import sys,json;print(json.load(sys.stdin)['epoch'])")
[ "$EPOCH" -ge 1 ] && green "PASS (epoch=$EPOCH)" || { red "FAIL"; exit 1; }

echo ""
green "All self-tests passed."
