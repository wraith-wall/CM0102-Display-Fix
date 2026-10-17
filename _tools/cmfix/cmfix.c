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
 * With text4k=1 (cmfix.ini or CMFIX_TEXT4K=1, see cfg_get), text4k.c also takes over the game's
 * text drawing and redraws the strings at the output resolution on top of cnc-ddraw's picture
 * (crisp text).
 *
 * It also subclasses the game window to turn cnc-ddraw's maximize toggle (largest 4:3 window <->
 * native 800x600) into a normal Windows maximize/restore; see "maximize / restore" below.
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
static char g_ini_path[MAX_PATH];
static volatile LONG g_want_full = -1;    /* mode the game asked for at the last init */
static volatile LONG g_pending;           /* a mode sync is due */
static volatile DWORD g_pending_tick;


void logf(const char *fmt, ...)
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

/* ---------------------------------------------------------------- maximize / restore */
/*
 * cnc-ddraw's own "maximize" (caption button, title double-click, Alt+PgDn) toggles between the
 * largest 4:3 window in the work area and the native 800x600. Instead we make it a real Windows
 * maximized window (caption kept, picture 4:3 centred) and restore the size the window had before.
 *
 * cnc-ddraw renders at the size stored in its window_rect, which only its own code updates: a
 * SetWindowPos/ShowWindow from outside resizes the window but not the picture (and the mouse
 * mapping), so the frame sticks to the corner and clicks miss. Mouse resizing works because of
 * the modal size loop: WM_ENTERSIZEMOVE, WM_SIZING (stops the render thread and stores the new
 * client rect) and WM_EXITSIZEMOVE (re-applies the display mode with that rect). We replay that
 * sequence on the window thread from a subclass that sits in front of cnc-ddraw's window proc.
 *
 * cnc-ddraw hooks the IAT of every module in the game folder (this DLL too): from here
 * SetWindowPos, ShowWindow(SW_MAXIMIZE), Get/SetWindowLongA, GetClientRect... are its fakes.
 * The real user32 functions are looked up in user32's export table directly.
 * dd_SetDisplayMode clears WS_MAXIMIZE whenever it runs, so the bit is put back afterwards
 * (also after Alt+Enter / an in-game mode switch returns to the window while "maximized").
 */
#define WM_CNC_TOGGLE_MAXIMIZE (WM_APP + 118)  /* cnc-ddraw: Alt+PgDn hotkey */
#define WM_CMFIX_REMAX         (WM_APP + 201)

typedef LONG(WINAPI *GetWindowLongAFn)(HWND, int);
typedef LONG(WINAPI *SetWindowLongAFn)(HWND, int, LONG);
typedef BOOL(WINAPI *SetWindowPosFn)(HWND, HWND, int, int, int, int, UINT);
typedef BOOL(WINAPI *ShowWindowFn)(HWND, int);
typedef BOOL(WINAPI *GetRectFn)(HWND, LPRECT);
typedef BOOL(WINAPI *ClientToScreenFn)(HWND, LPPOINT);

static GetWindowLongAFn  r_GetWindowLongA;
static SetWindowLongAFn  r_SetWindowLongA;
static SetWindowPosFn    r_SetWindowPos;
static ShowWindowFn      r_ShowWindow;
static GetRectFn         r_GetClientRect, r_GetWindowRect;
static ClientToScreenFn  r_ClientToScreen;

static WNDPROC g_prev_proc;        /* cnc-ddraw's window proc */
static int g_subclass_ok;
static int g_maxed;                /* the player maximized the window */
static int g_busy;                 /* inside our own resize sequence */
static int g_remax_posted;
static DWORD g_remax_tick;
static RECT g_normal;              /* client rect (screen coords) before maximizing */

static FARPROC export_of(HMODULE mod, const char *name)
{
    BYTE *b = (BYTE *)mod;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(b + ((IMAGE_DOS_HEADER *)b)->e_lfanew);
    IMAGE_DATA_DIRECTORY *dd = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    IMAGE_EXPORT_DIRECTORY *ex = (IMAGE_EXPORT_DIRECTORY *)(b + dd->VirtualAddress);
    DWORD *names = (DWORD *)(b + ex->AddressOfNames), *funcs = (DWORD *)(b + ex->AddressOfFunctions);
    WORD *ords = (WORD *)(b + ex->AddressOfNameOrdinals);
    DWORD i;
    for (i = 0; i < ex->NumberOfNames; i++) {
        if (!strcmp((char *)(b + names[i]), name)) {
            DWORD rva = funcs[ords[i]];
            if (rva >= dd->VirtualAddress && rva < dd->VirtualAddress + dd->Size)
                return NULL;               /* forwarder: not expected for these */
            return (FARPROC)(b + rva);
        }
    }
    return NULL;
}

static int resolve_user32(void)
{
    HMODULE u = GetModuleHandleA("user32.dll");
    if (!u)
        return 0;
    r_GetWindowLongA = (GetWindowLongAFn)export_of(u, "GetWindowLongA");
    r_SetWindowLongA = (SetWindowLongAFn)export_of(u, "SetWindowLongA");
    r_SetWindowPos   = (SetWindowPosFn)export_of(u, "SetWindowPos");
    r_ShowWindow     = (ShowWindowFn)export_of(u, "ShowWindow");
    r_GetClientRect  = (GetRectFn)export_of(u, "GetClientRect");
    r_GetWindowRect  = (GetRectFn)export_of(u, "GetWindowRect");
    r_ClientToScreen = (ClientToScreenFn)export_of(u, "ClientToScreen");
    return r_GetWindowLongA && r_SetWindowLongA && r_SetWindowPos && r_ShowWindow &&
           r_GetClientRect && r_GetWindowRect && r_ClientToScreen;
}

static void client_rect_screen(HWND h, RECT *rc)
{
    POINT p = {0, 0};
    r_GetClientRect(h, rc);
    r_ClientToScreen(h, &p);
    OffsetRect(rc, p.x, p.y);
}

static void window_from_client(HWND h, RECT *rc)
{
    AdjustWindowRectEx(rc, r_GetWindowLongA(h, GWL_STYLE) & ~WS_MAXIMIZE, GetMenu(h) != NULL,
                       r_GetWindowLongA(h, GWL_EXSTYLE));
}

/* Let cnc-ddraw adopt the window rect `wrc` the way it does after a mouse resize.
   `between` runs after the render thread is stopped and before the rect is taken. */
static void cnc_size_move(HWND h, void (*between)(HWND), RECT *wrc)
{
    RECT cur;
    g_busy = 1;
    r_GetWindowRect(h, &cur);
    CallWindowProcA(g_prev_proc, h, WM_ENTERSIZEMOVE, 0, 0);
    CallWindowProcA(g_prev_proc, h, WM_SIZING, 0, (LPARAM)&cur); /* stops the render thread */
    if (between) {
        between(h);
        r_GetWindowRect(h, wrc);
    }
    CallWindowProcA(g_prev_proc, h, WM_SIZING, 0, (LPARAM)wrc);  /* wParam 0: no aspect snapping */
    CallWindowProcA(g_prev_proc, h, WM_EXITSIZEMOVE, 0, 0);      /* dd_SetDisplayMode */
    g_busy = 0;
}

static void show_maximized(HWND h) { r_ShowWindow(h, SW_MAXIMIZE); }
static void show_restored(HWND h)  { r_ShowWindow(h, SW_RESTORE); }

static void set_max_bit(HWND h)
{
    LONG st = r_GetWindowLongA(h, GWL_STYLE);
    if (!(st & WS_MAXIMIZE)) {
        r_SetWindowLongA(h, GWL_STYLE, st | WS_MAXIMIZE);
        r_SetWindowPos(h, NULL, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
    }
}

static void do_maximize(HWND h)
{
    RECT wrc, crc;
    if (!g_maxed)
        client_rect_screen(h, &g_normal);
    cnc_size_move(h, show_maximized, &wrc);
    set_max_bit(h);
    g_maxed = 1;
    client_rect_screen(h, &crc);
    logf("maximized: client %ldx%ld at %ld,%ld (restore to %ldx%ld at %ld,%ld), zoomed=%d",
         crc.right - crc.left, crc.bottom - crc.top, crc.left, crc.top,
         g_normal.right - g_normal.left, g_normal.bottom - g_normal.top, g_normal.left, g_normal.top,
         IsZoomed(h) ? 1 : 0);
}

static void do_restore(HWND h)
{
    RECT wrc = g_normal, crc;
    if (IsRectEmpty(&g_normal)) {  /* no size seen before maximizing: 2400x1800 (ini) centred */
        MONITORINFO mi;
        mi.cbSize = sizeof(mi);
        GetMonitorInfoA(MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST), &mi);
        SetRect(&wrc, 0, 0, 2400, 1800);
        OffsetRect(&wrc, (mi.rcWork.left + mi.rcWork.right - 2400) / 2, (mi.rcWork.top + mi.rcWork.bottom - 1800) / 2);
    }
    g_maxed = 0;
    window_from_client(h, &wrc);
    cnc_size_move(h, NULL, &wrc);  /* dd_SetDisplayMode also drops WS_MAXIMIZE */
    if (IsZoomed(h))
        show_restored(h);
    client_rect_screen(h, &crc);
    logf("restored: client %ldx%ld at %ld,%ld", crc.right - crc.left, crc.bottom - crc.top, crc.left, crc.top);
}

static void toggle_maximize(HWND h)
{
    if (g_maxed)
        do_restore(h);
    else
        do_maximize(h);
}

static LRESULT CALLBACK wndproc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    LRESULT r;
    int windowed = cnc_is_window(h) && !IsIconic(h);
    if (windowed && !g_busy) {
        switch (msg) {
        case WM_SYSCOMMAND:
            switch (wp & 0xFFF0) {
            case SC_MAXIMIZE:
                if (!g_maxed)
                    do_maximize(h);
                return 0;
            case SC_RESTORE:
                if (g_maxed) {
                    do_restore(h);
                    return 0;
                }
                break;
            case SC_MOVE:
            case SC_SIZE:
                if (g_maxed)
                    return 0;              /* a maximized window stays put */
                break;
            }
            break;
        case WM_NCLBUTTONDBLCLK:
            if (wp == HTCAPTION)
                toggle_maximize(h);
            return 0;
        case WM_CNC_TOGGLE_MAXIMIZE:
            toggle_maximize(h);
            return 0;
        }
    }
    if (msg == WM_CMFIX_REMAX) {
        g_remax_posted = 0;
        if (g_maxed && windowed && !(r_GetWindowLongA(h, GWL_STYLE) & WS_MAXIMIZE)) {
            logf("window is back while maximized: maximizing again");
            do_maximize(h);
        }
        return 0;
    }
    r = CallWindowProcA(g_prev_proc, h, msg, wp, lp);
    /* after Alt+Enter / an in-game switch cnc-ddraw brings the window back without WS_MAXIMIZE */
    if (g_maxed && !g_busy && !g_remax_posted && (msg == WM_WINDOWPOSCHANGED || msg == WM_STYLECHANGED) &&
        cnc_is_window(h) && !(r_GetWindowLongA(h, GWL_STYLE) & (WS_MAXIMIZE | WS_MINIMIZE)) &&
        GetTickCount() - g_remax_tick > 1000) {
        g_remax_posted = 1;
        g_remax_tick = GetTickCount();
        PostMessageA(h, WM_CMFIX_REMAX, 0, 0);
    }
    return r;
}

/* Put wndproc in front of the window's current proc (cnc-ddraw's, or opengl32's which sits on top
   of it once the OpenGL renderer runs). cnc-ddraw puts the game's own proc back when the game
   releases DirectDraw (graphics re-init) and subclasses again on the next SetCooperativeLevel, which
   takes the current proc as "the game's" - so we never subclass while the game's own proc is on
   top (cnc-ddraw would then call us from its proc and we it: endless recursion); the check repeats. */
static void ensure_subclass(HWND h)
{
    static BYTE *last_seen;
    HMODULE exe = GetModuleHandleA(NULL), m = NULL;
    BYTE *cur = (BYTE *)GetWindowLongW(h, GWL_WNDPROC);  /* the real address for a W or A proc */
    LONG prev;
    if ((WNDPROC)r_GetWindowLongA(h, GWL_WNDPROC) == wndproc)
        return;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCSTR)cur, &m) || m == exe || m == g_self) {
        if (cur != last_seen)
            logf("game window proc %p is the game's own; not subclassing yet", (void *)cur);
        last_seen = cur;
        return;
    }
    last_seen = cur;
    prev = r_SetWindowLongA(h, GWL_WNDPROC, (LONG)wndproc);
    g_prev_proc = (WNDPROC)prev;
    {
        char mn[MAX_PATH] = "?";
        GetModuleFileNameA(m, mn, MAX_PATH);
        logf("subclassed game window %p over %p (%s)", (void *)h, (void *)cur, strrchr(mn, '\\') ? strrchr(mn, '\\') + 1 : mn);
    }
}

static DWORD WINAPI worker(void *arg)
{
    int tries = 0;
    (void)arg;
    for (;;) {
        HWND h;
        Sleep(150);
        h = *(HWND *)GAME_HWND_PTR;
        if (g_subclass_ok && h && IsWindow(h))
            ensure_subclass(h);
        if (!g_pending || GetTickCount() - g_pending_tick < 700)
            continue;
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

#ifdef CMFIX_TEXTLOG
/* ---------------------------------------------------------------- Phase 2b spike: text log */
/*
 * Temporary instrumentation (build with /DCMFIX_TEXTLOG): logs every call of the game's
 *   0x5E5160  draw_string(x, y, font, color565, const char *s, caret)  - all glyphs go through it
 *   0x5E6D80  draw_text_box(a1..a10)                                   - wraps/aligns, calls the above
 * and a few graphics primitives (present / grab / put / rect, see GFX_*) to cmfix_text.log. Env CMFIX_TEXT=hide also skips draw_string (no glyphs drawn at all).
 */
#define TXT_DRAW     0x005E5160
#define TXT_BOX      0x005E6D80
static const BYTE kTxtDraw[8]    = {0x6A, 0xFF, 0x64, 0xA1, 0x00, 0x00, 0x00, 0x00}; /* push -1; mov eax, fs:[0] */
static const BYTE kTxtBox[7]     = {0x6A, 0xFF, 0x68, 0x1E, 0xA9, 0x95, 0x00};       /* push -1; push 0x95A91E */
static const BYTE kGfxPrologue[5] = {0xA1, 0x0C, 0xBE, 0xAE, 0x00};                  /* mov eax, [0xAEBE0C] */
static char g_txt_path[MAX_PATH];
static volatile LONG g_txt_seq, g_txt_lines;
static DWORD g_txt_hide;

static void txtf(const char *fmt, ...)
{
    char buf[700];
    va_list ap;
    HANDLE f;
    DWORD n;
    int len;
    if (InterlockedIncrement(&g_txt_lines) > 200000)
        return;
    len = _snprintf(buf, 32, "%6ld %8lu ", InterlockedIncrement(&g_txt_seq), GetTickCount());
    va_start(ap, fmt);
    len += _vsnprintf(buf + len, sizeof(buf) - len - 3, fmt, ap);
    va_end(ap);
    if (len < 0 || len > (int)sizeof(buf) - 3)
        len = sizeof(buf) - 3;
    buf[len++] = '\r';
    buf[len++] = '\n';
    f = CreateFileA(g_txt_path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, 0, NULL);
    if (f != INVALID_HANDLE_VALUE) {
        WriteFile(f, buf, len, &n, NULL);
        CloseHandle(f);
    }
}

static void safe_str(char *dst, int cap, const char *s)
{
    int i = 0;
    if (!s || IsBadReadPtr(s, 1)) {
        lstrcpyA(dst, "(null)");
        return;
    }
    for (; s[i] && i < cap - 1; i++)
        dst[i] = (s[i] == '"' || (BYTE)s[i] < 0x20) ? '?' : s[i];
    dst[i] = 0;
}

static void __cdecl on_txt_draw(DWORD *a, DWORD ret)
{
    char s[400];
    safe_str(s, sizeof(s), (const char *)a[4]);
    txtf("DRAW x=%ld y=%ld font=%d col=%04X caret=%ld ret=%08lX surf=%p \"%s\"",
         (long)a[0], (long)a[1], (short)a[2], (unsigned)(a[3] & 0xFFFF), (long)a[5], ret,
         *(void **)0x00AEBDE4u, s);
}

static void __cdecl on_txt_box(DWORD *a, DWORD ret)
{
    char s[400];
    safe_str(s, sizeof(s), (const char *)a[7]);
    txtf("BOX  %ld %ld %ld %ld %ld font=%d a7=%08lX a9=%08lX a10=%08lX ret=%08lX \"%s\"",
         (long)a[0], (long)a[1], (long)a[2], (long)a[3], (long)a[4], (short)a[5], a[6], a[8], a[9], ret, s);
}

__declspec(naked) static void txt_draw_hook(void)
{
    __asm {
        pushad
        push dword ptr [esp + 0x20]          /* return address */
        lea eax, [esp + 0x28]                /* &arg1 */
        push eax
        call on_txt_draw
        add esp, 8
        popad
        cmp g_txt_hide, 0
        jne skip
        push -1
        mov eax, dword ptr fs:[0]
        push TXT_DRAW + 8
        ret
    skip:
        ret
    }
}

__declspec(naked) static void txt_box_hook(void)
{
    __asm {
        pushad
        push dword ptr [esp + 0x20]
        lea eax, [esp + 0x28]
        push eax
        call on_txt_box
        add esp, 8
        popad
        push -1
        push 0x0095A91E
        push TXT_BOX + 7
        ret
    }
}

/* graphics primitives that start with "mov eax, [0xAEBE0C]" (the "graphics off" flag) */
static void __cdecl on_gfx(int tag, DWORD *a, DWORD ret)
{
    static const char *names[] = {"PRES", "GRAB", "PUT ", "RECT"};
    if (tag == 2) {  /* put(x, y, saved*) - saved = {w, h, bytes, pixels} */
        DWORD *sv = (DWORD *)a[2];
        txtf("%s %ld %ld w=%ld h=%ld ret=%08lX", names[tag], (long)a[0], (long)a[1],
             sv ? (long)sv[0] : -1, sv ? (long)sv[1] : -1, ret);
        return;
    }
    txtf("%s %ld %ld %ld %ld %08lX %08lX ret=%08lX", names[tag], (long)a[0], (long)a[1], (long)a[2], (long)a[3],
         a[4], a[5], ret);
}

#define GFX_HOOK(name, addr, tag)                                                                  __declspec(naked) static void name(void)                                                       {                                                                                                  __asm pushad                                                                                   __asm push dword ptr [esp + 0x20]                                                              __asm lea eax, [esp + 0x28]                                                                    __asm push eax                                                                                 __asm push tag                                                                                 __asm call on_gfx                                                                              __asm add esp, 12                                                                              __asm popad                                                                                    __asm mov eax, dword ptr ds:[0x00AEBE0C]                                                       __asm push addr + 5                                                                            __asm ret                                                                                  }
#define GFX_PRESENT 0x005E2EB0  /* present_rect(x0, y0, x1, y1): back buffer -> primary */
#define GFX_GRAB    0x005E3DD0  /* grab(x0, y0, x1, y1, saved*): back buffer rect -> malloc copy */
#define GFX_PUT     0x005E3FD0  /* put(x, y, saved*): copy -> back buffer */
#define GFX_RECT    0x005E3730  /* rect/line primitive (x0, y0, x1, y1, mode, color) */
GFX_HOOK(gfx_present_hook, GFX_PRESENT, 0)
GFX_HOOK(gfx_grab_hook, GFX_GRAB, 1)
GFX_HOOK(gfx_put_hook, GFX_PUT, 2)
GFX_HOOK(gfx_rect_hook, GFX_RECT, 3)

static void patch_jmp(DWORD at, void *to, int len)
{
    BYTE *p = (BYTE *)at;
    DWORD old;
    int i;
    VirtualProtect(p, len, PAGE_EXECUTE_READWRITE, &old);
    p[0] = 0xE9;
    *(DWORD *)(p + 1) = (DWORD)to - (at + 5);
    for (i = 5; i < len; i++)
        p[i] = 0x90;
    VirtualProtect(p, len, old, &old);
    FlushInstructionCache(GetCurrentProcess(), p, len);
}

static void textlog_install(void)
{
    char v[16] = "";
    char *slash;
    lstrcpyA(g_txt_path, g_log_path);
    slash = strrchr(g_txt_path, '\\');
    lstrcpyA(slash ? slash + 1 : g_txt_path, "cmfix_text.log");
    GetEnvironmentVariableA("CMFIX_TEXT", v, sizeof(v));
    g_txt_hide = !lstrcmpiA(v, "hide");
    if (memcmp((void *)TXT_DRAW, kTxtDraw, 8) || memcmp((void *)TXT_BOX, kTxtBox, 7) ||
        memcmp((void *)GFX_PRESENT, kGfxPrologue, 5) || memcmp((void *)GFX_GRAB, kGfxPrologue, 5) ||
        memcmp((void *)GFX_PUT, kGfxPrologue, 5) || memcmp((void *)GFX_RECT, kGfxPrologue, 5)) {
        logf("textlog: code does not match 3.9.68; not hooking");
        return;
    }
    patch_jmp(TXT_DRAW, txt_draw_hook, 8);
    patch_jmp(TXT_BOX, txt_box_hook, 7);
    patch_jmp(GFX_PRESENT, gfx_present_hook, 5);
    patch_jmp(GFX_GRAB, gfx_grab_hook, 5);
    patch_jmp(GFX_PUT, gfx_put_hook, 5);
    patch_jmp(GFX_RECT, gfx_rect_hook, 5);
    logf("textlog: hooks installed, hide=%lu", g_txt_hide);
}
#endif

/* A setting: the environment variable CMFIX_<NAME> if set (harness, testing), else <name> in the
   [cmfix] section of cmfix.ini next to this DLL (what players edit). Returns 0 if neither has it. */
int cfg_get(const char *name, char *dst, int cap)
{
    char env[64];
    wsprintfA(env, "CMFIX_%s", name);
    if (GetEnvironmentVariableA(env, dst, cap))
        return 1;
    return GetPrivateProfileStringA("cmfix", name, "", dst, cap, g_ini_path) > 0;
}

int text4k_install(void);  /* text4k.c */

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
    g_subclass_ok = resolve_user32();
    if (!g_subclass_ok) {
        logf("user32 exports not found; maximize fix disabled");
    }
    CloseHandle(CreateThread(NULL, 0, worker, NULL, 0, NULL));
    logf("hooks installed at %08X and %08X", GFX_INIT, IS_FULL_GETTER);
#ifndef CMFIX_TEXTLOG
    {
        char v[8] = "";
        cfg_get("TEXT4K", v, sizeof(v));
        if (v[0] == '1')
            text4k_install();
    }
#endif
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
        lstrcpyA(g_ini_path, g_log_path);
        lstrcpyA(g_ini_path + lstrlenA(g_ini_path) - 3, "ini");
        logf("cmfix loaded");
        install();
#ifdef CMFIX_TEXTLOG
        textlog_install();
#endif
    }
    return TRUE;
}
