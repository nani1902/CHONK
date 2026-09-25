"""Disposable, deterministic signing material for signature-bearing fixtures.

No private key is stored in the repository. The RSA key is derived from a
fixed seed on every run, so the certificate and signature bytes are
reproducible while remaining obviously test-only. Never use this key, its
certificate, or this derivation outside the CHONK test suite.
"""

from __future__ import annotations

import datetime
import math
import random
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

SIGNER_SEED = "chonk-disposable-test-signer-v1"
SIGNER_COMMON_NAME = "CHONK disposable test signer - not for real use"
PUBLIC_EXPONENT = 65_537

_SMALL_PRIMES = tuple(
    n for n in range(3, 2_000, 2) if all(n % d for d in range(3, int(n**0.5) + 1, 2))
)


def _is_probable_prime(candidate: int, rng: random.Random, rounds: int = 40) -> bool:
    if any(candidate % p == 0 for p in _SMALL_PRIMES):
        return candidate in _SMALL_PRIMES
    d, s = candidate - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        witness = rng.randrange(2, candidate - 2)
        x = pow(witness, d, candidate)
        if x in (1, candidate - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, candidate)
            if x == candidate - 1:
                break
        else:
            return False
    return True


def _prime(rng: random.Random, bits: int) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (0b11 << (bits - 2)) | 1
        if math.gcd(PUBLIC_EXPONENT, candidate - 1) == 1 and _is_probable_prime(
            candidate, rng
        ):
            return candidate


@lru_cache(maxsize=1)
def signer() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    rng = random.Random(SIGNER_SEED)
    p = _prime(rng, 1024)
    q = _prime(rng, 1024)
    while q == p:
        q = _prime(rng, 1024)
    d = pow(PUBLIC_EXPONENT, -1, math.lcm(p - 1, q - 1))
    key = rsa.RSAPrivateNumbers(
        p=p,
        q=q,
        d=d,
        dmp1=rsa.rsa_crt_dmp1(d, p),
        dmq1=rsa.rsa_crt_dmq1(d, q),
        iqmp=rsa.rsa_crt_iqmp(p, q),
        public_numbers=rsa.RSAPublicNumbers(PUBLIC_EXPONENT, p * q),
    ).private_key()

    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, SIGNER_COMMON_NAME),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CHONK synthetic test corpus"),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(0xC40C0001)
        .not_valid_before(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2036, 1, 1, tzinfo=datetime.timezone.utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def detached_pkcs7(data: bytes) -> bytes:
    """Return a DER CMS SignedData over ``data`` without signed attributes.

    ``Binary`` signs the exact bytes (no S/MIME newline canonicalization).
    Omitting signed attributes removes the signing-time attribute, and RSA
    PKCS#1 v1.5 signatures are deterministic, so the output is reproducible.
    """
    key, certificate = signer()
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(data)
        .add_signer(certificate, key, hashes.SHA256())
        .sign(
            serialization.Encoding.DER,
            [
                pkcs7.PKCS7Options.DetachedSignature,
                pkcs7.PKCS7Options.NoAttributes,
                pkcs7.PKCS7Options.Binary,
            ],
        )
    )


def certificate_pem() -> bytes:
    return signer()[1].public_bytes(serialization.Encoding.PEM)
