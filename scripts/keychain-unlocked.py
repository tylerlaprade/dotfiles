import ctypes
import sys

KEYCHAIN_UNLOCKED = 1


def keychain_unlocked() -> bool:
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
    security.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
    security.SecKeychainGetStatus.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
    ]
    security.SecKeychainGetStatus.restype = ctypes.c_int32

    user_interaction_allowed = ctypes.c_ubyte(0)
    if security.SecKeychainSetUserInteractionAllowed(user_interaction_allowed) != 0:
        return False
    status = ctypes.c_uint32()
    result = security.SecKeychainGetStatus(None, ctypes.byref(status))
    return result == 0 and bool(status.value & KEYCHAIN_UNLOCKED)


if __name__ == '__main__':
    sys.exit(0 if keychain_unlocked() else 1)
