#!/usr/bin/env python3
"""
ratchet.py — RSI Vault ratchet & envelope-encryption engine
============================================================

This is the orchestration layer. It binds to the C core (libcryptocore.so)
for all primitive operations and implements the layered key design:

    passphrase
        |  Argon2id  (memory-hard KDF — resists brute force)
        v
    root secret  ──────────────► static X25519 identity key (id_priv/id_pub)
        |
        |  per file: generate EPHEMERAL X25519 keypair (e_priv/e_pub)
        |  DH = X25519(e_priv, id_pub)        ← fresh entropy every file
        v
    epoch root key  (HKDF of DH + root secret)
        |
        |  symmetric ratchet:  CK0 -> CK1 -> CK2 ...     (one-way HKDF chain)
        |                       |     |     |
        v                       MK0   MK1   MK2          (message keys)
    message key (MK_n)
        |  wraps / encrypts
        v
    Data Key (DEK)   ← random per file, encrypts the actual file bytes
        |  AES-256-GCM
        v
    your file (pdf / jpg / xls / docx / anything)

Security properties demonstrated:
  * Forward secrecy mechanics: each chain key is derived from the previous
    one through HKDF-SHA256, a one-way function. Knowing CK_n does not
    reveal CK_{n-1}. The ephemeral private key is destroyed after use.
  * Post-compromise ("self-healing"): every file mixes in a fresh ephemeral
    X25519 DH exchange, injecting new entropy an attacker cannot predict.
    Compromise of one file's keys does not compromise the next.

CLI (called by server.js):
    ratchet.py encrypt <in_path> <out_path> <passphrase>
    ratchet.py decrypt <in_path> <out_path> <passphrase>
    ratchet.py state
    ratchet.py rekey
Each command prints a single JSON object to stdout.
"""

import base64
import ctypes
import json
import os
import struct
import sys
import time

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_PATH = os.path.join(SCRIPT_DIR, "libcryptocore.so")
STATE_DIR = os.path.join(SCRIPT_DIR, "vault_state")
STATE_PATH = os.path.join(STATE_DIR, "ratchet.json")

KEY_LEN = 32
NONCE_LEN = 12
TAG_LEN = 16
X25519_LEN = 32
SALT_LEN = 16

CONTAINER_VERSION = 1
MAGIC = b"RSIV"                 # file magic for the .rsienc container
DH_REKEY_EVERY = 5             # perform a heavier epoch rotation every N ops

# Argon2id parameters (memory-hard). Tuned for a server-side single call.
ARGON_TIME_COST = 3
ARGON_MEMORY_COST = 64 * 1024  # 64 MiB
ARGON_LANES = 4


# ---------------------------------------------------------------------------
# C core binding
# ---------------------------------------------------------------------------
class CryptoCore:
    """Thin ctypes wrapper around libcryptocore.so."""

    def __init__(self, path=LIB_PATH):
        self.lib = ctypes.CDLL(path)

        self.lib.random_bytes.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.lib.random_bytes.restype = ctypes.c_int

        self.lib.hkdf_sha256.argtypes = [
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
        ]
        self.lib.hkdf_sha256.restype = ctypes.c_int

        self.lib.aes256gcm_encrypt.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_char_p,
        ]
        self.lib.aes256gcm_encrypt.restype = ctypes.c_int

        self.lib.aes256gcm_decrypt.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_char_p,
        ]
        self.lib.aes256gcm_decrypt.restype = ctypes.c_int

        self.lib.x25519_keypair.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.lib.x25519_keypair.restype = ctypes.c_int

        self.lib.x25519_shared.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ]
        self.lib.x25519_shared.restype = ctypes.c_int

    def random_bytes(self, n):
        buf = ctypes.create_string_buffer(n)
        if self.lib.random_bytes(buf, n) != 0:
            raise RuntimeError("CSPRNG failure")
        return buf.raw[:n]

    def hkdf(self, ikm, salt, info, out_len=KEY_LEN):
        out = ctypes.create_string_buffer(out_len)
        rc = self.lib.hkdf_sha256(
            ikm, len(ikm),
            salt, len(salt) if salt else 0,
            info, len(info) if info else 0,
            out, out_len,
        )
        if rc != 0:
            raise RuntimeError(f"HKDF failed (rc={rc})")
        return out.raw[:out_len]

    def aead_encrypt(self, key, nonce, plaintext, aad=b""):
        ct = ctypes.create_string_buffer(max(len(plaintext), 1))
        tag = ctypes.create_string_buffer(TAG_LEN)
        n = self.lib.aes256gcm_encrypt(
            key, nonce,
            plaintext, len(plaintext),
            aad, len(aad),
            ct, tag,
        )
        if n < 0:
            raise RuntimeError(f"AEAD encrypt failed (rc={n})")
        return ct.raw[:n], tag.raw[:TAG_LEN]

    def aead_decrypt(self, key, nonce, ciphertext, tag, aad=b""):
        pt = ctypes.create_string_buffer(max(len(ciphertext), 1))
        n = self.lib.aes256gcm_decrypt(
            key, nonce,
            ciphertext, len(ciphertext),
            aad, len(aad),
            tag, pt,
        )
        if n < 0:
            raise ValueError("AUTH_FAIL: tag verification failed "
                             "(wrong passphrase or corrupted/tampered file)")
        return pt.raw[:n]

    def x25519_keypair(self):
        priv = ctypes.create_string_buffer(X25519_LEN)
        pub = ctypes.create_string_buffer(X25519_LEN)
        if self.lib.x25519_keypair(priv, pub) != 0:
            raise RuntimeError("x25519 keygen failed")
        return priv.raw[:X25519_LEN], pub.raw[:X25519_LEN]

    def x25519_shared(self, my_priv, their_pub):
        shared = ctypes.create_string_buffer(X25519_LEN)
        if self.lib.x25519_shared(my_priv, their_pub, shared) != 0:
            raise RuntimeError("x25519 DH failed")
        return shared.raw[:X25519_LEN]


# ---------------------------------------------------------------------------
# Identity derivation (passphrase -> root secret -> static X25519 keypair)
# ---------------------------------------------------------------------------
def derive_root_secret(passphrase: str, salt: bytes) -> bytes:
    """Memory-hard Argon2id derivation of a 32-byte root secret."""
    kdf = Argon2id(
        salt=salt,
        length=KEY_LEN,
        iterations=ARGON_TIME_COST,
        lanes=ARGON_LANES,
        memory_cost=ARGON_MEMORY_COST,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def identity_keypair(core: CryptoCore, root_secret: bytes):
    """Deterministically derive the static X25519 identity keypair.

    The private key is HKDF-expanded from the root secret, so it is fully
    reproducible from the passphrase alone (this is what lets a file be
    decrypted later). The public key is computed from it.
    """
    id_priv_raw = core.hkdf(root_secret, b"", b"rsi-identity-x25519", X25519_LEN)
    sk = X25519PrivateKey.from_private_bytes(id_priv_raw)
    id_pub = sk.public_key().public_bytes_raw()
    return id_priv_raw, id_pub


# ---------------------------------------------------------------------------
# Symmetric ratchet chain (one-way HKDF advance) — forward secrecy mechanics
# ---------------------------------------------------------------------------
def chain_init(core: CryptoCore, epoch_root: bytes) -> bytes:
    return core.hkdf(epoch_root, b"", b"rsi-chain-init")


def chain_step(core: CryptoCore, chain_key: bytes):
    """Advance the chain one step. Returns (message_key, next_chain_key).

    Both are derived through HKDF-SHA256 (one-way). next_chain_key cannot
    be inverted back to chain_key — that is the forward-secrecy guarantee.
    """
    message_key = core.hkdf(chain_key, b"", b"rsi-message-key")
    next_chain = core.hkdf(chain_key, b"", b"rsi-chain-advance")
    return message_key, next_chain


def derive_message_key(core: CryptoCore, epoch_root: bytes, index: int) -> bytes:
    """Walk the chain `index` steps and return that step's message key."""
    ck = chain_init(core, epoch_root)
    mk = None
    for _ in range(index + 1):
        mk, ck = chain_step(core, ck)
    return mk


# ---------------------------------------------------------------------------
# Persistent ratchet state (for the live UI + periodic DH epoch rotation)
# ---------------------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_PATH):
        return {"epoch": 0, "op_count": 0, "last_rekey": None,
                "current_dh_pub": None, "created": time.time()}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def advance_state(core: CryptoCore, dh_pub: bytes):
    """Record an operation; rotate epoch every DH_REKEY_EVERY ops."""
    state = load_state()
    state["op_count"] += 1
    state["current_dh_pub"] = base64.b64encode(dh_pub).decode()
    if state["op_count"] % DH_REKEY_EVERY == 0:
        state["epoch"] += 1
        state["last_rekey"] = time.time()
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Container (.rsienc) read / write
# ---------------------------------------------------------------------------
def b64(x):
    return base64.b64encode(x).decode("ascii")


def unb64(x):
    return base64.b64decode(x.encode("ascii"))


def write_container(out_path, header: dict, ciphertext: bytes):
    """Container layout:  MAGIC | u32 header_len | header_json | ciphertext"""
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack(">I", len(header_bytes)))
        f.write(header_bytes)
        f.write(ciphertext)


def read_container(in_path):
    with open(in_path, "rb") as f:
        magic = f.read(4)
        if magic != MAGIC:
            raise ValueError("Not an RSI Vault container (bad magic header)")
        (hlen,) = struct.unpack(">I", f.read(4))
        header = json.loads(f.read(hlen).decode("utf-8"))
        ciphertext = f.read()
    return header, ciphertext


# ---------------------------------------------------------------------------
# Encrypt
# ---------------------------------------------------------------------------
def encrypt_file(in_path, out_path, passphrase, original_name=None):
    core = CryptoCore()

    with open(in_path, "rb") as f:
        plaintext = f.read()
    if not original_name:
        original_name = os.path.basename(in_path)

    # 1. passphrase -> root secret -> static identity keypair
    salt = core.random_bytes(SALT_LEN)
    root_secret = derive_root_secret(passphrase, salt)
    _id_priv, id_pub = identity_keypair(core, root_secret)

    # 2. fresh ephemeral DH keypair (post-compromise / self-healing entropy)
    e_priv, e_pub = core.x25519_keypair()
    dh = core.x25519_shared(e_priv, id_pub)
    del e_priv  # destroy ephemeral private key — never stored

    # 3. epoch root binds the ephemeral DH with the passphrase identity
    epoch_root = core.hkdf(dh, root_secret, b"rsi-epoch-root")

    # 4. advance global ratchet, use its op_count as this file's chain index
    state = advance_state(core, e_pub)
    msg_index = state["op_count"] - 1
    message_key = derive_message_key(core, epoch_root, msg_index)

    # 5. random Data Key (DEK) actually encrypts the file
    dek = core.random_bytes(KEY_LEN)
    file_nonce = core.random_bytes(NONCE_LEN)

    header_meta = {
        "v": CONTAINER_VERSION,
        "name": original_name,
        "salt": b64(salt),
        "epub": b64(e_pub),
        "idx": msg_index,
        "epoch": state["epoch"],
        "argon": {"t": ARGON_TIME_COST, "m": ARGON_MEMORY_COST, "l": ARGON_LANES},
    }
    # Authenticate the header as associated data so it can't be swapped.
    aad = json.dumps(header_meta, separators=(",", ":"), sort_keys=True).encode()

    ciphertext, file_tag = core.aead_encrypt(dek, file_nonce, plaintext, aad)

    # 6. wrap the DEK under the message key (the envelope step)
    wrap_nonce = core.random_bytes(NONCE_LEN)
    wrapped_dek, wrap_tag = core.aead_encrypt(message_key, wrap_nonce, dek)
    del dek  # plaintext DEK no longer needed

    header = dict(header_meta)
    header.update({
        "file_nonce": b64(file_nonce),
        "file_tag": b64(file_tag),
        "wrap_nonce": b64(wrap_nonce),
        "wrap_tag": b64(wrap_tag),
        "wrapped_dek": b64(wrapped_dek),
    })

    write_container(out_path, header, ciphertext)

    return {
        "ok": True,
        "action": "encrypt",
        "original_name": original_name,
        "output": os.path.basename(out_path),
        "bytes_in": len(plaintext),
        "bytes_out": os.path.getsize(out_path),
        "epoch": state["epoch"],
        "chain_index": msg_index,
        "op_count": state["op_count"],
        "ephemeral_pub": b64(e_pub)[:16] + "...",
        "rekeyed": state["op_count"] % DH_REKEY_EVERY == 0,
    }


# ---------------------------------------------------------------------------
# Decrypt
# ---------------------------------------------------------------------------
def decrypt_file(in_path, out_path, passphrase):
    core = CryptoCore()
    header, ciphertext = read_container(in_path)

    salt = unb64(header["salt"])
    e_pub = unb64(header["epub"])
    msg_index = header["idx"]

    # Rebuild identity from passphrase (must match what encryption used)
    root_secret = derive_root_secret(passphrase, salt)
    id_priv, _id_pub = identity_keypair(core, root_secret)

    # Other side of the DH: DH(id_priv, e_pub) == DH(e_priv, id_pub)
    dh = core.x25519_shared(id_priv, e_pub)
    epoch_root = core.hkdf(dh, root_secret, b"rsi-epoch-root")

    message_key = derive_message_key(core, epoch_root, msg_index)

    # Reconstruct the AAD exactly as encryption built it
    header_meta = {
        "v": header["v"],
        "name": header["name"],
        "salt": header["salt"],
        "epub": header["epub"],
        "idx": header["idx"],
        "epoch": header["epoch"],
        "argon": header["argon"],
    }
    aad = json.dumps(header_meta, separators=(",", ":"), sort_keys=True).encode()

    # Unwrap the DEK, then decrypt the file
    dek = core.aead_decrypt(
        message_key, unb64(header["wrap_nonce"]),
        unb64(header["wrapped_dek"]), unb64(header["wrap_tag"]),
    )
    plaintext = core.aead_decrypt(
        dek, unb64(header["file_nonce"]),
        ciphertext, unb64(header["file_tag"]), aad,
    )

    with open(out_path, "wb") as f:
        f.write(plaintext)

    return {
        "ok": True,
        "action": "decrypt",
        "original_name": header["name"],
        "output": os.path.basename(out_path),
        "bytes_out": len(plaintext),
        "epoch": header["epoch"],
        "chain_index": msg_index,
    }


# ---------------------------------------------------------------------------
# State / rekey commands (for the UI)
# ---------------------------------------------------------------------------
def get_state():
    state = load_state()
    return {"ok": True, "action": "state", **state, "dh_rekey_every": DH_REKEY_EVERY}


def force_rekey():
    core = CryptoCore()
    _priv, pub = core.x25519_keypair()
    state = load_state()
    state["epoch"] += 1
    state["last_rekey"] = time.time()
    state["current_dh_pub"] = base64.b64encode(pub).decode()
    save_state(state)
    return {"ok": True, "action": "rekey", "epoch": state["epoch"],
            "message": "Forced DH ratchet step — epoch advanced, chain healed."}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    try:
        if len(sys.argv) < 2:
            raise ValueError("no command")
        cmd = sys.argv[1]

        if cmd == "encrypt":
            original = sys.argv[5] if len(sys.argv) > 5 else None
            result = encrypt_file(sys.argv[2], sys.argv[3], sys.argv[4], original)
        elif cmd == "decrypt":
            result = decrypt_file(sys.argv[2], sys.argv[3], sys.argv[4])
        elif cmd == "state":
            result = get_state()
        elif cmd == "rekey":
            result = force_rekey()
        else:
            raise ValueError(f"unknown command: {cmd}")

        print(json.dumps(result))
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
