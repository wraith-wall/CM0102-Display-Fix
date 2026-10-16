## cm0102_GDI.exe

- Machine: 0x14c (x86 / PE32), magic 0x10b
- Subsystem: IMAGE_SUBSYSTEM_WINDOWS_GUI
- Linker: 6.0; OS version 4.0
- TimeDateStamp: 2002-11-21 17:19:18 UTC
- ImageBase 0x400000, DllCharacteristics 0x0
- Sections: .text(0x564925), .rdata(0x1f212), .data(0x45d59c), .rsrc(0x1238)
- Resource types: ['RT_CURSOR', 'RT_ICON', 'RT_GROUP_CURSOR', 'RT_GROUP_ICON']
- Manifest: NONE

### Imports
- **KERNEL32.dll** (84): CloseHandle, CompareStringA, CompareStringW, CreateDirectoryA, CreateFileA, CreateMutexA, CreateProcessA, DeleteFileA, ExitProcess, FileTimeToLocalFileTime, FileTimeToSystemTime, FindClose, FindFirstFileA, FindNextFileA, FlushFileBuffers, FreeEnvironmentStringsA, FreeEnvironmentStringsW, FreeLibrary, GetACP, GetCPInfo, GetCommandLineA, GetCurrentDirectoryA, GetCurrentProcess, GetDiskFreeSpaceA, GetDriveTypeA, GetEnvironmentStrings, GetEnvironmentStringsW, GetEnvironmentVariableA, GetExitCodeProcess, GetFileAttributesA, GetFileType, GetFullPathNameA, GetLastError, GetLocalTime, GetLogicalDriveStringsA, GetModuleFileNameA, GetModuleHandleA, GetOEMCP, GetProcAddress, GetStartupInfoA, GetStdHandle, GetStringTypeA, GetStringTypeW, GetSystemInfo, GetSystemTime, GetSystemTimeAsFileTime, GetTimeZoneInformation, GetVersion, GetVersionExA, GlobalAlloc, GlobalFree, GlobalMemoryStatus, HeapAlloc, HeapCreate, HeapDestroy, HeapFree, HeapReAlloc, HeapSize, IsBadCodePtr, IsBadReadPtr, IsBadWritePtr, LCMapStringA, LCMapStringW, LoadLibraryA, MoveFileA, MultiByteToWideChar, RaiseException, ReadFile, RtlUnwind, SetCurrentDirectoryA, SetEndOfFile, SetEnvironmentVariableA, SetFilePointer, SetHandleCount, SetStdHandle, SetUnhandledExceptionFilter, TerminateProcess, UnhandledExceptionFilter, VirtualAlloc, VirtualFree, WaitForSingleObject, WideCharToMultiByte, WinExec, WriteFile
- **USER32.dll** (30): BeginPaint, ChangeDisplaySettingsA, CloseWindow, CreateWindowExA, DefWindowProcA, DestroyCursor, DestroyWindow, DispatchMessageA, EndPaint, GetCursor, GetDC, GetDesktopWindow, GetKeyboardLayout, GetMessageA, GetSystemMetrics, GetWindowRect, LoadCursorA, LoadIconA, MessageBoxA, MoveWindow, PeekMessageA, RegisterClassA, ReleaseDC, SetCursor, SetWindowLongA, SetWindowPos, ShowCursor, ShowWindow, TranslateMessage, UpdateWindow
- **GDI32.dll** (13): CreateFontA, DeleteObject, EndDoc, EndPage, GetDeviceCaps, GetTextMetricsA, SelectObject, SetMapMode, SetStretchBltMode, StartDocA, StartPage, StretchDIBits, TextOutA
- **WINSPOOL.DRV** (1): EnumPrintersA
- **comdlg32.dll** (1): PrintDlgA
- **ADVAPI32.dll** (2): RegOpenKeyExA, RegQueryValueExA
- **DSOUND.dll** (1): ord1
- **WSOCK32.dll** (18): WSACleanup, WSAGetLastError, WSAStartup, accept, bind, closesocket, connect, gethostbyaddr, gethostname, htons, ioctlsocket, listen, recv, recvfrom, send, sendto, setsockopt, socket

### COM IIDs found in binary

### Interesting strings
- `0x584c04` SetStretchBltMode
- `0x584cac` GDI32.dll
- `0x584d20` DSOUND.dll
- `0x5889f0` pics.cfg
- `0x5a7108` Memory Allocation Error
- `0x5c8cf0` Club relocation plans completed
- `0x5cc360` -windowed
- `0x5cc378` game.cfg
- `0x5cc834` Unable to initialise the graphics in windowed mode - please ensure that DirectX has been installed correctly and that your desktop is in at least 800 x 600 pixel resolution and is in 16 bit colour
- `0x5f06e8` Location
- `0x5f06f4` Software\Sports Interactive Ltd\Championship Manager 01-02
- `0x614b84` events_eng.cfg
- `0x614b94` events_kor.cfg
- `0x614ba4` events_jap.cfg
- `0x614bb4` events_dan.cfg
- `0x614bc4` events_swe.cfg
- `0x614bd4` events_por.cfg
- `0x614be4` events_nor.cfg
- `0x614bf4` events_ned.cfg
- `0x614c04` events_spa.cfg
- `0x614c14` events_ita.cfg
- `0x614c24` events_ger.cfg
- `0x614c34` events_fra.cfg
- `0x6373b8` He has intimated that he would like a public show of support for his contribution to the club if any similar rumours surface in the future.
- `0x6375ab`  He has indicated that he would like a public show of support for his contribution to the club if any similar rumours surface in the future.
- `0x659a00` .sav
- `0x666974` {}<%s - Team Name>{} will play in the {}<%s - Competition Name>{} next season due to the relative location of {}<%s - City Name>{} in {}<%s - Nation Name>{}.
- `0x66a22c` player_setup.cfg
- `0x678328` pundit.cfg
- `0x67f288` auto_setup.cfg
- `0x67f2ec` quick_start.cfg
- `0x67fc9c` Windowed
- `0x67fca8` Full<%s - COMMENT - full screen mode>
- `0x67fcd0`  Screen Mode
- `0x6a09ac` euro.cfg
- `0x6d3d60` weather.cfg