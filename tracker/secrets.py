"""Protect the database password with Windows DPAPI for the current user."""
import base64
import ctypes
from ctypes import wintypes as w


class BLOB(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def transform(value, decrypt=False):
    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, target = BLOB(len(raw), buffer), BLOB()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.restype = w.BOOL
    function.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(BLOB)]
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.HANDLE], w.HANDLE
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = ctypes.string_at(target.data, target.size)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel.LocalFree(target.data)


def protect(value):
    return transform(value)


def unprotect(value):
    return transform(value, True)
