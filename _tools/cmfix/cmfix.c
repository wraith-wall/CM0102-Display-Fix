/*
 * cmfix - proxy dsound.dll for Championship Manager 01/02 (cm0102.exe 3.9.68) + cnc-ddraw.
 *
 * The game imports only DirectSoundCreate (ordinal 1) from DSOUND.dll; this DLL forwards it to
 * the system dsound.dll and, on load, installs one in-memory hook (the exe file is not changed):
 *
 *   0x5E2620  graphics init (w, h, 2, ?, fullscreen, title)
 *     The in-game "Screen Mode: Windowed" makes the game call it with fullscreen=0, which asks
 *     DirectDraw for a desktop-sized 16-bit "display mode" and draws 800x600 into its corner.
 *     The hook remembers the requested mode, always passes fullscreen=1 (the game then asks for
 *     800x600, which cnc-ddraw scales), and afterwards switches cnc-ddraw to the requested mode:
 *     Full -> borderless fullscreen, Windowed -> resizable window. The switch is cnc-ddraw's own
 *     Alt+Enter toggle (ini: windowed=true, fullscreen=true, toggle_borderless=true), sent with
 *     SendInput only while the game window is in the foreground; cnc-ddraw ignores posted keys.
 *
 *   0x5E2EA0  "is full screen?" getter (mov eax,[0xAE319C]; ret), used by Game Settings to decide
 *     whether a Screen Mode change needs a graphics re-init, and by the focus/print code.
 *     Since the real flag [0xAE319C] is now always 1 (it also selects the present path at
 *     0x5E2F8D and must stay 1), the getter is redirected to the mode the player chose.
 *
 * Safety: the hooks are installed only if the bytes at 0x5E2620/0x5E2EA0 match 3.9.68 and the ddraw.dll
 * loaded next to the game is cnc-ddraw (exports GameHandlesClose). Otherwise this DLL only
 * forwards DirectSoundCreate. Log: cmfix.log next to the DLL.
 */
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>

#define GFX_INIT        0x005E2620u
#define GFX_INIT_RET    0x005E2625u
#define GFX_INIT_GLOBAL 0x009CD808u
#define GAME_HWND_PTR   0x00B67A2Cu

#define IS_FULL_GETTER  0x005E2EA0u

static const BYTE kExpect[5] = {0xA1, 0x08, 0xD8, 0x9C, 0x00};             /* mov eax, [0x9CD808] */
static const BYTE kExpectGetter[6] = {0xA1, 0x9C, 0x31, 0xAE, 0x00, 0xC3}; /* mov eax, [0xAE319C]; ret */
static volatile DWORD g_logical_full = 1;  /* what the getter reports: the player's Screen Mode */

static HMODULE g_self, g_real_dsound;
static char g_log_path[MAX_PATH];
static volatile LONG g_want_full = -1;    /* mode the game asked for at the last init */
static volatile LONG g_pending;           /* a mode sync is due */
static volatile DWORD g_pending_tick;


static void logf(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    SYSTEMTIME t;
    HANDLE f;
    DWORD n;
    int len;
    GetLocalTime(&t);
    len = _snprintf(buf, sizeof(buf), "%02d:%02d:%02d.%03d ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_start(ap, fmt);
    len += _vsnprintf(buf + len, sizeof(buf) - len - 3, fmt, ap);
    va_end(ap);
    buf[len++] = '\r';
    buf[len++] = '\n';
    f = CreateFileA(g_log_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, 0, NULL);
    if (f != INVALID_HANDLE_VALUE) {
        WriteFile(f, buf, len, &n, NULL);
        CloseHandle(f);
    }
}

/* ---------------------------------------------------------------- DirectSound forward */
typedef HRESULT(WINAPI *DirectSoundCreateFn)(LPCGUID, void **, void *);

HRESULT WINAPI cmfix_DirectSoundCreate(LPCGUID guid, void **ds, void *outer)
{
    DirectSoundCreateFn fn;
    if (!g_real_dsound) {
        char path[MAX_PATH];
        GetSystemDirectoryA(path, MAX_PATH); /* SysWOW64 for this 32-bit process */
        lstrcatA(path, "\\dsound.dll");
        g_real_dsound = LoadLibraryA(path);
    }
    fn = g_real_dsound ? (DirectSoundCreateFn)GetProcAddress(g_real_dsound, (LPCSTR)1) : NULL;
    {
        HRESULT hr = fn ? fn(guid, ds, outer) : E_FAIL;
        logf("DirectSoundCreate -> system dsound.dll %s, hr=%08lX", fn ? "found" : "MISSING", (unsigned long)hr);
        return hr;
    }
}

/* ---------------------------------------------------------------- mode switching */
static int cnc_is_window(HWND h)
{
    return (GetWindowLongA(h, GWL_STYLE) & WS_CAPTION) == WS_CAPTION;
}

static void send_alt_enter_input(void)
{
    INPUT in[4];
    ZeroMemory(in, sizeof(in));
    in[0].type = in[1].type = in[2].type = in[3].type = INPUT_KEYBOARD;
    in[0].ki.wVk = VK_MENU;
    in[1].ki.wVk = VK_RETURN;
    in[2].ki.wVk = VK_RETURN; in[2].ki.dwFlags = KEYEVENTF_KEYUP;
    in[3].ki.wVk = VK_MENU;   in[3].ki.dwFlags = KEYEVENTF_KEYUP;
    SendInput(4, in, sizeof(INPUT));
}

/* Ask cnc-ddraw to toggle window <-> borderless (its Alt+Enter hotkey). Returns 1 if it changed. */
static int toggle(HWND h)
{
    int before = cnc_is_window(h), i;
    send_alt_enter_input();
    for (i = 0; i < 30 && cnc_is_window(h) == before; i++)
        Sleep(50);
    if (cnc_is_window(h) == before)
        return 0;
    logf("switched cnc-ddraw to %s", cnc_is_window(h) ? "window" : "fullscreen");
    return 1;
}

static DWORD WINAPI worker(void *arg)
{
    int tries = 0;
    (void)arg;
    for (;;) {
        HWND h;
        Sleep(150);
        if (!g_pending || GetTickCount() - g_pending_tick < 700)
            continue;
        h = *(HWND *)GAME_HWND_PTR;
        if (!h || !IsWindow(h) || !IsWindowVisible(h))
            continue;
        if (cnc_is_window(h) == !g_want_full) {
            logf("mode ok: %s", g_want_full ? "fullscreen" : "window");
            InterlockedExchange(&g_pending, 0);
            tries = 0;
            continue;
        }
        if (GetForegroundWindow() != h)
            continue;                        /* wait until the player is back in the game */
        if (toggle(h)) {
            g_pending_tick = GetTickCount(); /* re-check once it settled */
        } else if (++tries >= 5) {
            logf("cnc-ddraw did not switch to %s; giving up until next graphics init",
                 g_want_full ? "fullscreen" : "window");
            InterlockedExchange(&g_pending, 0);
            tries = 0;
        }
    }
}

/* ---------------------------------------------------------------- game hook */
static void __cdecl on_gfx_init(int fullscreen)
{
    g_want_full = fullscreen ? 1 : 0;
    g_logical_full = fullscreen ? 1 : 0;
    g_pending_tick = GetTickCount();
    InterlockedExchange(&g_pending, 1);
    logf("graphics init: game asked for %s -> forcing full-screen path, will switch cnc-ddraw to %s",
         fullscreen ? "Full" : "Windowed", fullscreen ? "fullscreen" : "window");
}

__declspec(naked) static void gfx_init_hook(void)
{
    __asm {
        pushad
        push dword ptr [esp + 0x20 + 0x14]   /* 5th argument: fullscreen flag */
        call on_gfx_init
        add esp, 4
        popad
        mov dword ptr [esp + 0x14], 1        /* always take the full-screen (800x600) path */
        mov eax, dword ptr ds:[GFX_INIT_GLOBAL]  /* replaced instruction */
        push GFX_INIT_RET
        ret
    }
}

static void install(void)
{
    BYTE *p = (BYTE *)GFX_INIT;
    HMODULE ddraw = GetModuleHandleA("ddraw.dll");
    DWORD old;
    BYTE *g = (BYTE *)IS_FULL_GETTER;
    if (IsBadReadPtr(p, 5) || memcmp(p, kExpect, 5) || IsBadReadPtr(g, 6) || memcmp(g, kExpectGetter, 6)) {
        logf("cm0102.exe code at %08X/%08X does not match 3.9.68; not hooking", GFX_INIT, IS_FULL_GETTER);
        return;
    }
    if (!ddraw || !GetProcAddress(ddraw, "GameHandlesClose")) {
        logf("ddraw.dll is not cnc-ddraw; not hooking");
        return;
    }
    VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &old);
    p[0] = 0xE9;
    *(DWORD *)(p + 1) = (DWORD)gfx_init_hook - (GFX_INIT + 5);
    VirtualProtect(p, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), p, 5);
    /* getter: mov eax, [&g_logical_full]; ret */
    VirtualProtect(g, 6, PAGE_EXECUTE_READWRITE, &old);
    *(DWORD *)(g + 1) = (DWORD)&g_logical_full;
    VirtualProtect(g, 6, old, &old);
    FlushInstructionCache(GetCurrentProcess(), g, 6);
    CloseHandle(CreateThread(NULL, 0, worker, NULL, 0, NULL));
    logf("hooks installed at %08X and %08X", GFX_INIT, IS_FULL_GETTER);
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        char *slash;
        g_self = inst;
        DisableThreadLibraryCalls(inst);
        GetModuleFileNameA(inst, g_log_path, MAX_PATH);
        slash = strrchr(g_log_path, '\\');
        lstrcpyA(slash ? slash + 1 : g_log_path, "cmfix.log");
        logf("cmfix loaded");
        install();
    }
    return TRUE;
}
