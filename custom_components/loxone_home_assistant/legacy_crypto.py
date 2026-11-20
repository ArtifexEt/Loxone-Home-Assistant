"""Certificate verification and command encryption for the Gen1 Miniserver."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from secrets import token_bytes, token_hex
from urllib.parse import quote

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import ExtensionOID, NameOID

# SHA-256 over the DER root-ca.loxonecloud.com certificate embedded in the
# Authenticode-verified Loxone Config 17.3.11.12 and Loxone Monitor. The protocol
# includes this certificate first in the getcertificate response. The fingerprint
# pins that exact trust anchor independently of any certificate data from the device.
_LOXONE_ROOT_SHA256 = bytes.fromhex(
    "db486e6a9d868ae2fd7e1b199c038924b3d270ce669b5ef37f1113b5bc3ec1d7"
)
_CERTIFICATE_PATTERN = re.compile(
    rb"-----BEGIN CERTIFICATE-----([A-Za-z0-9+/=\s]+)-----END CERTIFICATE-----"
)
_SERIAL_PATTERN = re.compile(r"[0-9A-Fa-f]{12}")
_AES_BLOCK_SIZE = 16


class LegacyCryptoError(ValueError):
    """Raised when the legacy endpoint identity or encrypted payload is invalid."""


class LegacySession:
    """Hold one Gen1 WebSocket's key, IV and command salt."""

    def __init__(self, public_key: rsa.RSAPublicKey, serial: str) -> None:
        self.serial = serial
        self._public_key = public_key
        self._key = token_bytes(32)
        self._iv = token_bytes(_AES_BLOCK_SIZE)
        self._salt: str | None = None

    @classmethod
    def from_certificate_chain(
        cls, certificate_chain: str, *, expected_serial: str | None = None
    ) -> LegacySession:
        """Verify the vendor chain and the Miniserver serial before using its key."""
        if not isinstance(certificate_chain, str):
            raise LegacyCryptoError("Invalid Loxone certificate chain.")
        try:
            blocks = _CERTIFICATE_PATTERN.findall(certificate_chain.encode("ascii"))
            certificates = [
                x509.load_der_x509_certificate(base64.b64decode(b"".join(block.split()), validate=True))
                for block in blocks
            ]
        except (ValueError, UnsupportedAlgorithm) as err:
            raise LegacyCryptoError("Invalid Loxone certificate chain.") from err

        if (
            len(certificates) < 2
            or certificates[0].fingerprint(hashes.SHA256()) != _LOXONE_ROOT_SHA256
        ):
            raise LegacyCryptoError("The Miniserver certificate does not match the trusted Loxone root.")

        try:
            _verify_chain(certificates)
            leaf = certificates[-1]
            names = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            public_key = leaf.public_key()
        except (InvalidSignature, UnsupportedAlgorithm, x509.DuplicateExtension, ValueError) as err:
            raise LegacyCryptoError("Invalid Loxone certificate chain.") from err

        if len(names) != 1 or not _SERIAL_PATTERN.fullmatch(names[0].value):
            raise LegacyCryptoError("The Miniserver certificate is missing a valid device serial.")
        serial = names[0].value.upper()
        if expected_serial is not None:
            expected = expected_serial.replace(":", "").replace("-", "").upper()
            if serial != expected:
                raise LegacyCryptoError("The Miniserver certificate does not match the expected serial.")
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 1024:
            raise LegacyCryptoError("Unsupported RSA key in the Miniserver certificate.")
        return cls(public_key, serial)

    def key_exchange_command(self) -> str:
        """Build the RSA-encrypted AES session key exchange command."""
        material = f"{self._key.hex()}:{self._iv.hex()}".encode("ascii")
        encrypted = self._public_key.encrypt(material, padding.PKCS1v15())
        return f"jdev/sys/keyexchange/{base64.b64encode(encrypted).decode('ascii')}"

    def encrypt_command(self, command: str, *, encrypt_response: bool = False) -> str:
        """Encrypt a WebSocket command and ask for an encrypted response if needed."""
        salt = token_hex(8)
        if self._salt is None:
            payload = f"salt/{salt}/{command}"
        else:
            payload = f"nextSalt/{self._salt}/{salt}/{command}"
        plaintext = payload.encode("utf-8")
        zeros = b"\0" * (-len(plaintext) % _AES_BLOCK_SIZE)
        encryptor = self._cipher().encryptor()
        encrypted = encryptor.update(plaintext + zeros) + encryptor.finalize()
        encoded = quote(base64.b64encode(encrypted).decode("ascii"), safe="")
        self._salt = salt
        method = "fenc" if encrypt_response else "enc"
        return f"jdev/sys/{method}/{encoded}"

    def decrypt_response(self, payload: str) -> str:
        """Decode a base64, zero-padded AES-CBC reply from the Miniserver."""
        if not isinstance(payload, str):
            raise LegacyCryptoError("Invalid encrypted Miniserver response.")
        try:
            encrypted = base64.b64decode(payload.strip(), validate=True)
            if not encrypted or len(encrypted) % _AES_BLOCK_SIZE:
                raise ValueError("Incorrect AES response length")
            decryptor = self._cipher().decryptor()
            decrypted = decryptor.update(encrypted) + decryptor.finalize()
            return decrypted.rstrip(b"\0").decode("utf-8")
        except ValueError as err:
            raise LegacyCryptoError("Invalid encrypted Miniserver response.") from err

    def _cipher(self) -> Cipher:
        return Cipher(algorithms.AES256(self._key), modes.CBC(self._iv))


def _verify_chain(certificates: list[x509.Certificate]) -> None:
    """Check the validity and usage of the pinned, root-first device chain."""
    now = datetime.now(timezone.utc)
    for index, certificate in enumerate(certificates):
        if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
            raise ValueError("Certificate outside validity period")

        # This vendor chain needs only constraints and key usage. Reject other
        # critical extensions unless we know how to apply their restrictions.
        extensions = {extension.oid: extension for extension in certificate.extensions}
        if any(
            extension.critical and oid not in {ExtensionOID.BASIC_CONSTRAINTS, ExtensionOID.KEY_USAGE}
            for oid, extension in extensions.items()
        ):
            raise ValueError("Unsupported critical certificate extension")
        constraints_extension = extensions.get(ExtensionOID.BASIC_CONSTRAINTS)
        usage_extension = extensions.get(ExtensionOID.KEY_USAGE)
        constraints = constraints_extension.value if constraints_extension else None
        usage = usage_extension.value if usage_extension else None
        remaining_authorities = len(certificates) - index - 2
        if remaining_authorities >= 0:
            if constraints is None or not constraints.ca or (usage is not None and not usage.key_cert_sign):
                raise ValueError("Invalid certificate authority")
            if constraints.path_length is not None and remaining_authorities > constraints.path_length:
                raise ValueError("Certificate chain exceeds CA path length")
            certificates[index + 1].verify_directly_issued_by(certificate)
        elif (constraints is not None and constraints.ca) or (usage is not None and not usage.key_encipherment):
            raise ValueError("Invalid Miniserver leaf certificate")
