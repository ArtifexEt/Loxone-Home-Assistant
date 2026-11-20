"""Verify Gen1 session wire format and vendor certificate trust rules."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import NameOID

from tests._loader import load_integration_module

legacy_crypto = load_integration_module("custom_components.loxone_home_assistant.legacy_crypto")


class LegacyCryptoTests(unittest.TestCase):
    """Use a controlled chain with the 1024-bit RSA key found on Gen1 devices."""

    serial = "504F94000001"

    @classmethod
    def setUpClass(cls) -> None:
        cls.root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.device_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        cls.root = cls._certificate("Loxone test root", cls.root_key, None, cls.root_key, ca=True)
        cls.issuer = cls._certificate(
            "Loxone test issuer", cls.issuer_key, cls.root, cls.root_key, ca=True
        )
        cls.device = cls._certificate(cls.serial, cls.device_key, cls.issuer, cls.issuer_key)
        cls.chain = cls._pem_chain(cls.root, cls.issuer, cls.device)

    @staticmethod
    def _certificate(name, key, authority, signer, *, ca=False, expired=False):
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.now(timezone.utc)
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(authority.subject if authority is not None else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if expired else 1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=not ca,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=ca,
                    crl_sign=ca,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(signer, hashes.SHA256())
        )

    @staticmethod
    def _pem_chain(*certificates) -> str:
        return "\r\n".join(
            certificate.public_bytes(serialization.Encoding.PEM).decode("ascii").strip()
            for certificate in certificates
        )

    def setUp(self) -> None:
        self.trust_root = patch.object(
            legacy_crypto, "_LOXONE_ROOT_SHA256", self.root.fingerprint(hashes.SHA256())
        )

    def test_supplied_root_is_never_trusted_on_its_own(self) -> None:
        with self.assertRaisesRegex(legacy_crypto.LegacyCryptoError, "trusted Loxone root"):
            legacy_crypto.LegacySession.from_certificate_chain(self.chain)

    def test_trusted_chain_requires_the_expected_serial(self) -> None:
        with self.trust_root:
            session = legacy_crypto.LegacySession.from_certificate_chain(
                self.chain, expected_serial="50:4f:94:00:00:01"
            )
            self.assertEqual(session.serial, self.serial)
            with self.assertRaisesRegex(legacy_crypto.LegacyCryptoError, "expected serial"):
                legacy_crypto.LegacySession.from_certificate_chain(
                    self.chain, expected_serial="504F94000002"
                )

    def test_rejects_expired_or_incorrectly_signed_certificate(self) -> None:
        expired = self._certificate(self.serial, self.device_key, self.issuer, self.issuer_key, expired=True)
        wrong_signature = self._certificate(self.serial, self.device_key, self.issuer, self.device_key)
        with self.trust_root:
            for device in (expired, wrong_signature):
                with self.subTest(certificate=device):
                    with self.assertRaises(legacy_crypto.LegacyCryptoError):
                        legacy_crypto.LegacySession.from_certificate_chain(
                            self._pem_chain(self.root, self.issuer, device)
                        )

    def test_session_key_and_encrypted_commands_use_loxone_wire_format(self) -> None:
        with self.trust_root:
            session = legacy_crypto.LegacySession.from_certificate_chain(self.chain)
        exchange = session.key_exchange_command()
        self.assertTrue(exchange.startswith("jdev/sys/keyexchange/"))
        material = self.device_key.decrypt(
            base64.b64decode(exchange.split("/", 3)[-1], validate=True), padding.PKCS1v15()
        ).decode("ascii")
        key, iv = (bytes.fromhex(field) for field in material.split(":"))
        self.assertEqual((len(key), len(iv)), (32, 16))
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))

        with patch.object(legacy_crypto, "token_hex", side_effect=("0123", "4567")):
            commands = (
                (session.encrypt_command("jdev/sys/getkey"), "enc", "salt/0123/jdev/sys/getkey"),
                (
                    session.encrypt_command("jdev/sps/io/light/on", encrypt_response=True),
                    "fenc",
                    "nextSalt/0123/4567/jdev/sps/io/light/on",
                ),
            )
        for command, kind, payload in commands:
            with self.subTest(kind=kind):
                prefix, encoded = command.rsplit("/", 1)
                self.assertEqual(prefix, f"jdev/sys/{kind}")
                self.assertNotRegex(encoded, r"[+/=]")
                decryptor = cipher.decryptor()
                encrypted = base64.b64decode(unquote(encoded), validate=True)
                plaintext = decryptor.update(encrypted) + decryptor.finalize()
                self.assertEqual(plaintext, payload.encode().ljust(len(plaintext), b"\0"))

        response = b'{"LL":{"Code":"200"}}'
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(response.ljust(32, b"\0")) + encryptor.finalize()
        self.assertEqual(session.decrypt_response(base64.b64encode(encrypted).decode()), response.decode())
        with self.assertRaisesRegex(legacy_crypto.LegacyCryptoError, "encrypted Miniserver response"):
            session.decrypt_response("AAAA")


if __name__ == "__main__":
    unittest.main()
