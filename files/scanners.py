import socket
import struct
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class ScannerUnavailable(Exception):
    pass


class ScannerVerdict:
    CLEAN = 'clean'
    INFECTED = 'infected'


def validate_scanner_configuration(*, app_env, backend):
    if backend not in {'clamav', 'fake', 'unavailable'}:
        raise ImproperlyConfigured(
            'ANTIVIRUS_SCANNER_BACKEND must be clamav, fake or unavailable.'
        )
    if app_env == 'production' and backend == 'fake':
        raise ImproperlyConfigured(
            'Fake antivirus scanner is not allowed in production.'
        )


class ClamAVScanner:
    CHUNK_SIZE = 64 * 1024
    MAX_RESPONSE_SIZE = 4096

    def __init__(self, *, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout

    def scan(self, stream):
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
            ) as connection:
                connection.settimeout(self.timeout)
                connection.sendall(b'zINSTREAM\0')
                while True:
                    chunk = stream.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    connection.sendall(struct.pack('!I', len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack('!I', 0))
                response = bytearray()
                while b'\0' not in response:
                    chunk = connection.recv(1024)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > self.MAX_RESPONSE_SIZE:
                        raise ScannerUnavailable(
                            'ClamAV response exceeded the size limit.'
                        )
        except (OSError, TimeoutError) as exc:
            raise ScannerUnavailable(
                f'ClamAV connection failed ({type(exc).__name__}).'
            ) from exc

        message = bytes(response).split(b'\0', 1)[0].strip()
        if message.endswith(b' OK'):
            return ScannerVerdict.CLEAN
        if message.endswith(b' FOUND'):
            return ScannerVerdict.INFECTED
        raise ScannerUnavailable('ClamAV returned an invalid scan response.')


class FakeScanner:
    def scan(self, stream):
        stream.read()
        return ScannerVerdict.CLEAN


class UnavailableScanner:
    def scan(self, stream):
        raise ScannerUnavailable('No antivirus scanner is configured.')


def get_scanner():
    backend = settings.ANTIVIRUS_SCANNER_BACKEND
    validate_scanner_configuration(
        app_env=settings.APP_ENV,
        backend=backend,
    )
    if backend == 'clamav':
        return ClamAVScanner(
            host=settings.ANTIVIRUS_CLAMAV_HOST,
            port=settings.ANTIVIRUS_CLAMAV_PORT,
            timeout=settings.ANTIVIRUS_SCAN_TIMEOUT,
        )
    if backend == 'fake':
        return FakeScanner()
    return UnavailableScanner()


def bytes_stream(content):
    return BytesIO(content)
