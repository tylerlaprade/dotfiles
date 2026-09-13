import base64
import ctypes
import os
from pathlib import Path
import subprocess
import sys


KEYCHAIN_UNAVAILABLE = 10
LOGIN_REQUIRED = 11
REQUEST_FAILED = 12


class CredentialError(Exception):
    def __init__(self, message, exit_code):
        super().__init__(message)
        self.exit_code = exit_code


def read_keychain(service, account=None, *, interactive=False):
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
    security.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    if security.SecKeychainSetUserInteractionAllowed(interactive) != 0:
        raise CredentialError('keychain unavailable', KEYCHAIN_UNAVAILABLE)
    service_bytes = service.encode()
    account_bytes = account.encode() if account is not None else None
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None, len(service_bytes), service_bytes,
        len(account_bytes or b''), account_bytes,
        ctypes.byref(length), ctypes.byref(data), None,
    )
    if status == -25300:
        raise CredentialError('login required', LOGIN_REQUIRED)
    if status != 0:
        raise CredentialError('keychain unavailable', KEYCHAIN_UNAVAILABLE)
    try:
        return ctypes.string_at(data, length.value).decode()
    finally:
        security.SecKeychainItemFreeContent(None, data)


def keychain_password(service, account=None):
    command = [sys.executable, str(Path(__file__).resolve()), 'read', service]
    if account is not None:
        command.append(account)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
        raise CredentialError('keychain unavailable', KEYCHAIN_UNAVAILABLE)
    if result.returncode:
        code = LOGIN_REQUIRED if result.returncode == LOGIN_REQUIRED else KEYCHAIN_UNAVAILABLE
        raise CredentialError('login required' if code == LOGIN_REQUIRED else 'keychain unavailable', code)
    return result.stdout


def github_token():
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        return token
    account = subprocess.run(
        ['gh', 'config', 'get', 'user', '--host', 'github.com'],
        capture_output=True, text=True, timeout=3,
    ).stdout.strip()
    configured_token = subprocess.run(
        ['gh', 'config', 'get', 'oauth_token', '--host', 'github.com'],
        capture_output=True, text=True, timeout=3,
    ).stdout.strip()
    if configured_token:
        return configured_token
    try:
        token = keychain_password('gh:github.com', account)
    except CredentialError as error:
        if error.exit_code != LOGIN_REQUIRED or not account:
            raise
        token = keychain_password('gh:github.com', '')
    if token.startswith('go-keyring-base64:'):
        token = base64.b64decode(token.removeprefix('go-keyring-base64:')).decode()
    elif token.startswith('go-keyring-encoded:'):
        token = bytes.fromhex(token.removeprefix('go-keyring-encoded:')).decode()
    if not token:
        raise CredentialError('login required', LOGIN_REQUIRED)
    return token


def authorize():
    account = subprocess.run(
        ['gh', 'config', 'get', 'user', '--host', 'github.com'],
        capture_output=True, text=True, timeout=3,
    ).stdout.strip()
    failed = False
    for label, service, user in [
        ('GitHub', 'gh:github.com', account),
        ('Claude usage', 'Claude Code-credentials', None),
    ]:
        try:
            if label == 'GitHub':
                github_token()
            else:
                keychain_password(service, user)
            print(f'{label}: already available')
            continue
        except CredentialError:
            pass
        print(f'{label}: requesting Keychain access for this Python interpreter.', flush=True)
        try:
            read_keychain(service, user, interactive=True)
            print(f'{label}: ready')
        except CredentialError as error:
            print(f'{label}: {error}')
            failed = True
    return int(failed)


def main():
    mode, *arguments = sys.argv[1:]
    try:
        if mode == 'authorize':
            return authorize()
        if mode == 'read':
            sys.stdout.write(read_keychain(*arguments))
        elif mode == 'keychain':
            sys.stdout.write(keychain_password(*arguments))
        elif mode == 'github':
            environment = dict(os.environ, GH_TOKEN=github_token(), GH_PROMPT_DISABLED='1')
            result = subprocess.run(
                ['gh', *arguments], env=environment, capture_output=True, timeout=8,
            )
            if result.returncode:
                if b'HTTP 401' in result.stderr or b'Bad credentials' in result.stderr:
                    raise CredentialError('login required', LOGIN_REQUIRED)
                raise CredentialError('fetch failed', REQUEST_FAILED)
            sys.stdout.buffer.write(result.stdout)
        else:
            raise ValueError('unknown credential operation')
    except CredentialError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print('fetch failed', file=sys.stderr)
        return REQUEST_FAILED
    return 0


if __name__ == '__main__':
    sys.exit(main())
