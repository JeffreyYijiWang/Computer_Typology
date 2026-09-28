"""Read the interactive Windows desktop using documented Win32 APIs."""
import ctypes
import hashlib
import logging
import threading
from ctypes import wintypes as w
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def bind(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype, function.argtypes = restype, list(argtypes)
    return function


foreground = bind(user32, "GetForegroundWindow", w.HWND)
get_title = bind(user32, "GetWindowTextW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
get_pid = bind(user32, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
open_process = bind(kernel32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
query_path = bind(kernel32, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
close_handle = bind(kernel32, "CloseHandle", w.BOOL, w.HANDLE)
tick_count = bind(kernel32, "GetTickCount64", ctypes.c_ulonglong)
open_desktop = bind(user32, "OpenInputDesktop", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
switch_desktop = bind(user32, "SwitchDesktop", w.BOOL, w.HANDLE)
close_desktop = bind(user32, "CloseDesktop", w.BOOL, w.HANDLE)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]


last_input = bind(user32, "GetLastInputInfo", w.BOOL, ctypes.POINTER(LASTINPUTINFO))


class SHFILEINFO(ctypes.Structure):
    _fields_ = [("hIcon", w.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", w.DWORD), ("szDisplayName", w.WCHAR * 260), ("szTypeName", w.WCHAR * 80)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("header", BITMAPINFOHEADER), ("colors", w.DWORD * 3)]


get_file_info = bind(shell32, "SHGetFileInfoW", ctypes.c_size_t, w.LPCWSTR, w.DWORD, ctypes.POINTER(SHFILEINFO), w.UINT, w.UINT)
get_dc = bind(user32, "GetDC", w.HDC, w.HWND)
release_dc = bind(user32, "ReleaseDC", ctypes.c_int, w.HWND, w.HDC)
create_dc = bind(gdi32, "CreateCompatibleDC", w.HDC, w.HDC)
create_dib = bind(gdi32, "CreateDIBSection", w.HBITMAP, w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD)
select_object = bind(gdi32, "SelectObject", w.HANDLE, w.HDC, w.HANDLE)
delete_object = bind(gdi32, "DeleteObject", w.BOOL, w.HANDLE)
delete_dc = bind(gdi32, "DeleteDC", w.BOOL, w.HDC)
draw_icon = bind(user32, "DrawIconEx", w.BOOL, w.HDC, ctypes.c_int, ctypes.c_int, w.HICON, ctypes.c_int, ctypes.c_int, w.UINT, w.HBRUSH, w.UINT)
destroy_icon = bind(user32, "DestroyIcon", w.BOOL, w.HICON)

NAMES = {"chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox", "brave": "Brave", "code": "Visual Studio Code", "explorer": "File Explorer", "windowsterminal": "Windows Terminal", "powershell": "PowerShell", "pwsh": "PowerShell", "winword": "Microsoft Word", "excel": "Microsoft Excel", "powerpnt": "PowerPoint", "outlook": "Outlook", "slack": "Slack", "discord": "Discord", "codex": "Codex", "notepad": "Notepad"}


def extract_icon(executable, destination):
    from PIL import Image
    info = SHFILEINFO()
    if not get_file_info(executable, 0, ctypes.byref(info), ctypes.sizeof(info), 0x100):
        return
    screen = dc = bitmap = previous = None
    try:
        screen = get_dc(None)
        dc = create_dc(screen)
        bmi = BITMAPINFO()
        bmi.header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), 64, -64, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bitmap = create_dib(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            return
        previous = select_object(dc, bitmap)
        ctypes.memset(bits, 0, 64 * 64 * 4)
        if draw_icon(dc, 0, 0, info.hIcon, 64, 64, 0, None, 3):
            picture = Image.frombytes("RGBA", (64, 64), ctypes.string_at(bits, 64 * 64 * 4), "raw", "BGRA")
            if picture.getchannel("A").getextrema()[1] == 0:
                picture.putalpha(255)
            picture.save(destination)
    except Exception:
        logging.exception("Could not extract an application icon")
    finally:
        if previous:
            select_object(dc, previous)
        if bitmap:
            delete_object(bitmap)
        if dc:
            delete_dc(dc)
        if screen:
            release_dc(None, screen)
        destroy_icon(info.hIcon)


class WindowsCollector:
    def __init__(self, icon_dir):
        self.icon_dir = icon_dir
        icon_dir.mkdir(parents=True, exist_ok=True)
        self.seen = set()

    def snapshot(self):
        desktop = open_desktop(0, False, 0x0100)
        if not desktop:
            return {"locked": True}
        try:
            if not switch_desktop(desktop):
                return {"locked": True}
        finally:
            close_desktop(desktop)
        idle = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not last_input(ctypes.byref(idle)):
            return None
        idle_seconds = ((tick_count() & 0xFFFFFFFF) - idle.dwTime) & 0xFFFFFFFF
        hwnd = foreground()
        if not hwnd:
            return None
        title = ctypes.create_unicode_buffer(4096)
        get_title(hwnd, title, len(title))
        pid = w.DWORD()
        get_pid(hwnd, ctypes.byref(pid))
        process = open_process(0x1000, False, pid.value)
        if not process:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = w.DWORD(len(buffer))
            if not query_path(process, 0, buffer, ctypes.byref(size)):
                return None
            path = buffer.value
        finally:
            close_handle(process)
        executable = Path(path)
        icon_key = hashlib.sha256(path.lower().encode()).hexdigest()[:24]
        if icon_key not in self.seen:
            self.seen.add(icon_key)
            target = self.icon_dir / (icon_key + ".png")
            if not target.exists():
                threading.Thread(target=extract_icon, args=(path, target), daemon=True).start()
        return {"app_name": NAMES.get(executable.stem.lower(), executable.stem), "process_name": executable.name,
                "window_title": title.value, "icon_key": icon_key, "idle_seconds": idle_seconds / 1000, "locked": False}

