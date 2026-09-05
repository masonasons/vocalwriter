"""Native screen reader announcements, without moving keyboard focus."""
import ctypes
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _cocoa():
    appkit = ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    cf = ctypes.CDLL(
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ptr = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ptr, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ptr
    cf.CFNumberCreate.argtypes = [ptr, ctypes.c_int, ptr]
    cf.CFNumberCreate.restype = ptr
    cf.CFDictionaryCreate.argtypes = [ptr, ctypes.POINTER(ptr),
                                     ctypes.POINTER(ptr), ctypes.c_long, ptr, ptr]
    cf.CFDictionaryCreate.restype = ptr
    cf.CFRelease.argtypes = [ptr]
    cf.CFRelease.restype = None
    appkit.NSAccessibilityPostNotificationWithUserInfo.argtypes = [ptr, ptr, ptr]
    appkit.NSAccessibilityPostNotificationWithUserInfo.restype = None
    return appkit, cf


def announce(control, text):
    """Post a screen reader announcement on the UI thread; report availability.

    Cocoa's NSString/NSDictionary are toll-free bridged to their CF types.
    Using system frameworks keeps the packaged app independent of PyObjC.
    The notification is delivered by the screen reader, in its chosen voice.
    """
    if sys.platform == 'win32':
        from app.accessibility_windows import announce as announce_windows
        return announce_windows(control, text)
    if sys.platform != 'darwin':
        return False
    appkit, cf = _cocoa()
    ptr = ctypes.c_void_p
    message = cf.CFStringCreateWithCString(None, text.encode('utf-8'), 0x08000100)
    priority_value = ctypes.c_int(90)  # NSAccessibilityPriorityHigh
    priority = cf.CFNumberCreate(None, 9, ctypes.byref(priority_value))
    info = None
    try:
        keys = (ptr * 2)(
            ptr.in_dll(appkit, 'NSAccessibilityAnnouncementKey').value,
            ptr.in_dll(appkit, 'NSAccessibilityPriorityKey').value)
        values = (ptr * 2)(message, priority)
        info = cf.CFDictionaryCreate(
            None, keys, values, 2,
            ctypes.byref(ctypes.c_byte.in_dll(cf, 'kCFTypeDictionaryKeyCallBacks')),
            ctypes.byref(ctypes.c_byte.in_dll(cf, 'kCFTypeDictionaryValueCallBacks')))
        notification = ptr.in_dll(
            appkit, 'NSAccessibilityAnnouncementRequestedNotification').value
        appkit.NSAccessibilityPostNotificationWithUserInfo(
            control.GetHandle(), notification, info)
    finally:
        for obj in (info, priority, message):
            if obj:
                cf.CFRelease(obj)
    return True
