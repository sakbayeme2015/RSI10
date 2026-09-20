/*
 * crypto_core.c — RSI Vault cryptographic core
 * ------------------------------------------------------------
 * The "fast path" of the platform. All bulk AEAD encryption and
 * key-derivation chain steps happen here in C, on top of OpenSSL's
 * libcrypto (a vetted primitive library — we do NOT roll our own
 * ciphers or hashes).
 *
 * Exposed to Python via ctypes:
 *   - hkdf_sha256()        : one-way key-derivation step (the ratchet chain)
 *   - aes256gcm_encrypt()  : authenticated encryption of file bytes
 *   - aes256gcm_decrypt()  : authenticated decryption + tag verification
 *   - random_bytes()       : CSPRNG bytes (keys, nonces, salts)
 *   - x25519_keypair()     : generate an X25519 keypair (DH ratchet)
 *   - x25519_shared()      : X25519 Diffie-Hellman shared secret
 *
 * Return convention: 0 = success, negative = error.
 *
 * Build:
 *   gcc -O3 -shared -fPIC -o libcryptocore.so crypto_core.c -lcrypto
 */

#include <stdint.h>
#include <string.h>
#include <openssl/evp.h>
#include <openssl/kdf.h>
#include <openssl/rand.h>
#include <openssl/core_names.h>
#include <openssl/params.h>

#define AES_KEY_LEN   32   /* 256-bit keys           */
#define GCM_NONCE_LEN 12   /* 96-bit GCM nonce       */
#define GCM_TAG_LEN   16   /* 128-bit auth tag       */
#define X25519_LEN    32   /* X25519 keys / secrets  */

/* ------------------------------------------------------------------ */
/* CSPRNG: fill buf with `len` cryptographically secure random bytes. */
/* ------------------------------------------------------------------ */
int random_bytes(unsigned char *buf, int len) {
    if (len <= 0) return -1;
    if (RAND_bytes(buf, len) != 1) return -2;
    return 0;
}

/* ------------------------------------------------------------------ */
/* HKDF-SHA256 (extract-and-expand).                                  */
/*                                                                    */
/* This is the one-way function at the heart of the symmetric         */
/* ratchet. Given a chain key as `ikm`, deriving the next chain key   */
/* and a message key is a forward-only operation: you cannot invert   */
/* SHA-256 to recover the previous chain key. That is precisely the   */
/* forward-secrecy property.                                          */
/* ------------------------------------------------------------------ */
int hkdf_sha256(const unsigned char *ikm,  int ikm_len,
                const unsigned char *salt, int salt_len,
                const unsigned char *info, int info_len,
                unsigned char *out,        int out_len) {
    int ret = -1;
    EVP_KDF *kdf = NULL;
    EVP_KDF_CTX *kctx = NULL;
    OSSL_PARAM params[5], *p = params;

    kdf = EVP_KDF_fetch(NULL, "HKDF", NULL);
    if (kdf == NULL) goto done;

    kctx = EVP_KDF_CTX_new(kdf);
    if (kctx == NULL) goto done;

    char digest[] = "SHA256";
    *p++ = OSSL_PARAM_construct_utf8_string(OSSL_KDF_PARAM_DIGEST, digest, 0);
    *p++ = OSSL_PARAM_construct_octet_string(OSSL_KDF_PARAM_KEY,
                                             (void *)ikm, (size_t)ikm_len);
    if (salt && salt_len > 0)
        *p++ = OSSL_PARAM_construct_octet_string(OSSL_KDF_PARAM_SALT,
                                                 (void *)salt, (size_t)salt_len);
    if (info && info_len > 0)
        *p++ = OSSL_PARAM_construct_octet_string(OSSL_KDF_PARAM_INFO,
                                                 (void *)info, (size_t)info_len);
    *p = OSSL_PARAM_construct_end();

    if (EVP_KDF_derive(kctx, out, (size_t)out_len, params) != 1) goto done;
    ret = 0;

done:
    if (kctx) EVP_KDF_CTX_free(kctx);
    if (kdf)  EVP_KDF_free(kdf);
    return ret;
}

/* ------------------------------------------------------------------ */
/* AES-256-GCM encrypt.                                               */
/*   key   : 32 bytes                                                 */
/*   nonce : 12 bytes                                                 */
/*   aad   : optional associated data (authenticated, not encrypted) */
/*   ct    : caller-allocated, >= pt_len bytes                        */
/*   tag   : caller-allocated, 16 bytes                              */
/* Returns ciphertext length (>=0) on success, negative on error.     */
/* ------------------------------------------------------------------ */
int aes256gcm_encrypt(const unsigned char *key,
                      const unsigned char *nonce,
                      const unsigned char *pt,  int pt_len,
                      const unsigned char *aad, int aad_len,
                      unsigned char *ct,
                      unsigned char *tag) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return -1;

    int len = 0, ct_len = 0, ret = -1;

    if (EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL) != 1) goto done;
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, GCM_NONCE_LEN, NULL) != 1) goto done;
    if (EVP_EncryptInit_ex(ctx, NULL, NULL, key, nonce) != 1) goto done;

    if (aad && aad_len > 0) {
        if (EVP_EncryptUpdate(ctx, NULL, &len, aad, aad_len) != 1) goto done;
    }

    if (pt_len > 0) {
        if (EVP_EncryptUpdate(ctx, ct, &len, pt, pt_len) != 1) goto done;
        ct_len = len;
    }

    if (EVP_EncryptFinal_ex(ctx, ct + ct_len, &len) != 1) goto done;
    ct_len += len;

    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, GCM_TAG_LEN, tag) != 1) goto done;
    ret = ct_len;

done:
    EVP_CIPHER_CTX_free(ctx);
    return ret;
}

/* ------------------------------------------------------------------ */
/* AES-256-GCM decrypt with tag verification.                        */
/* Returns plaintext length (>=0) on success.                        */
/* Returns -1 if the authentication tag does NOT verify (tampering    */
/* or wrong key) — the output buffer must be treated as invalid.      */
/* ------------------------------------------------------------------ */
int aes256gcm_decrypt(const unsigned char *key,
                      const unsigned char *nonce,
                      const unsigned char *ct,  int ct_len,
                      const unsigned char *aad, int aad_len,
                      const unsigned char *tag,
                      unsigned char *pt) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) return -1;

    int len = 0, pt_len = 0, ret = -1;

    if (EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, NULL, NULL) != 1) goto done;
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, GCM_NONCE_LEN, NULL) != 1) goto done;
    if (EVP_DecryptInit_ex(ctx, NULL, NULL, key, nonce) != 1) goto done;

    if (aad && aad_len > 0) {
        if (EVP_DecryptUpdate(ctx, NULL, &len, aad, aad_len) != 1) goto done;
    }

    if (ct_len > 0) {
        if (EVP_DecryptUpdate(ctx, pt, &len, ct, ct_len) != 1) goto done;
        pt_len = len;
    }

    /* Set expected tag, then finalize. Finalize fails if tag mismatch. */
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, GCM_TAG_LEN, (void *)tag) != 1) goto done;

    if (EVP_DecryptFinal_ex(ctx, pt + pt_len, &len) != 1) {
        ret = -1;          /* authentication failure */
        goto done;
    }
    pt_len += len;
    ret = pt_len;

done:
    EVP_CIPHER_CTX_free(ctx);
    return ret;
}

/* ------------------------------------------------------------------ */
/* X25519 keypair generation. Writes 32-byte raw private and public.  */
/* ------------------------------------------------------------------ */
int x25519_keypair(unsigned char *priv_out, unsigned char *pub_out) {
    int ret = -1;
    EVP_PKEY *pkey = NULL;
    EVP_PKEY_CTX *pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_X25519, NULL);
    if (!pctx) return -1;

    if (EVP_PKEY_keygen_init(pctx) != 1) goto done;
    if (EVP_PKEY_keygen(pctx, &pkey) != 1) goto done;

    size_t priv_len = X25519_LEN, pub_len = X25519_LEN;
    if (EVP_PKEY_get_raw_private_key(pkey, priv_out, &priv_len) != 1) goto done;
    if (EVP_PKEY_get_raw_public_key(pkey, pub_out, &pub_len) != 1) goto done;
    ret = 0;

done:
    if (pkey) EVP_PKEY_free(pkey);
    if (pctx) EVP_PKEY_CTX_free(pctx);
    return ret;
}

/* ------------------------------------------------------------------ */
/* X25519 Diffie-Hellman: shared = DH(my_private, their_public).      */
/* This is what injects fresh entropy into the DH ratchet, giving     */
/* the post-compromise ("self-healing") property.                     */
/* ------------------------------------------------------------------ */
int x25519_shared(const unsigned char *my_priv,
                  const unsigned char *their_pub,
                  unsigned char *shared_out) {
    int ret = -1;
    EVP_PKEY *priv = NULL, *peer = NULL;
    EVP_PKEY_CTX *dctx = NULL;

    priv = EVP_PKEY_new_raw_private_key(EVP_PKEY_X25519, NULL, my_priv, X25519_LEN);
    if (!priv) goto done;
    peer = EVP_PKEY_new_raw_public_key(EVP_PKEY_X25519, NULL, their_pub, X25519_LEN);
    if (!peer) goto done;

    dctx = EVP_PKEY_CTX_new(priv, NULL);
    if (!dctx) goto done;
    if (EVP_PKEY_derive_init(dctx) != 1) goto done;
    if (EVP_PKEY_derive_set_peer(dctx, peer) != 1) goto done;

    size_t slen = X25519_LEN;
    if (EVP_PKEY_derive(dctx, shared_out, &slen) != 1) goto done;
    ret = 0;

done:
    if (dctx) EVP_PKEY_CTX_free(dctx);
    if (peer) EVP_PKEY_free(peer);
    if (priv) EVP_PKEY_free(priv);
    return ret;
}
