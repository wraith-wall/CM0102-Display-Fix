/*
 * text4k - crisp text for cm0102.exe 3.9.68 under cnc-ddraw (OpenGL renderer).
 *
 * The game draws every string itself, glyph by glyph, into its single 800x600 back buffer
 * [0xAEBDE4] through one function, draw_string (0x5E5160); see _docs\phase2b_text_spike.md.
 * Here that function no longer draws: it records the string (position, font, colour) in a
 * "scene" instead, and the cnc-ddraw render thread draws the scene on top of the scaled frame
 * at the output resolution with a Windows TrueType font, just before SwapBuffers.
 *
 * Which strings are still on screen: when a string is recorded we keep a checksum of the back
 * buffer pixels under its box (the background, since the glyphs are not drawn), compared per 16-pixel strip. At each
 * present_rect (0x5E2EB0, back buffer -> primary) the boxes inside the presented rect are
 * re-checked: strips whose background changed (something drew over them) are hidden, and shown
 * again if the background comes back. grab/put of saved rects (0x5E3DD0 / 0x5E3FD0) carry the
 * strings along (see my_grab); a put removes the strings entirely under it. A new string removes
 * older strings it overlaps.
 *
 * Left to the game (drawn low-res as before): strings with a caret (input fields), strings
 * with #hhhh# colour codes, font 0 strings without letters/digits (arrow icons), and every
 * string while the "Traditional" fonts (bitmap .fnt, Arial) are selected: only "Futuristic"
 * (T2K) is redrawn.
 *
 * Until the overlay has run once (cnc-ddraw renderer=opengl calling SwapBuffers), the game keeps
 * drawing its own glyphs, so another renderer just means low-res text, never missing text.
 * Likewise text4k stays off if its font is not installed, and leaves the text to the game while
 * the frame is not 800x600 (resolution patches such as CM0102Patcher's 1280x800).
 *
 * Threads: the hooks on the game's functions run on the game thread and own the scene;
 * the overlay runs on cnc-ddraw's render thread and reads a snapshot under g_snap_cs.
 */
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

void logf(const char *fmt, ...);
int cfg_get(const char *name, char *dst, int cap);  /* cmfix.c: CMFIX_<NAME> or cmfix.ini */

#define T_DRAW     0x005E5160  /* draw_string(x, y, short font, u16 color565, const char *s, int caret) */
#define T_PRESENT  0x005E2EB0  /* present_rect(x0, y0, x1, y1) */
#define T_PUT      0x005E3FD0  /* put(x, y, saved*) with saved = {w, h, bytes, pixels} */
#define T_GRAB     0x005E3DD0  /* saved* grab(x0, y0, x1, y1, saved* reuse) */
#define T_DIM      0x005E42E0  /* dim(x0, y0, x1, y1, ...): darkens a rect */
#define T_PANEL    0x005E5DF0  /* panel(x0, y0, x1, y1, mode, ...): dialog background, dimming */
#define T_WIDTH    0x005E5AD0  /* text_width(short font, const char *s) */
#define BACKBUF    (*(void **)0x00AEBDE4)
#define FONT_MODE  (*(int *)0x009CC2C4)      /* -1: bitmap .fnt fonts ("Traditional"), else T2K ("Futuristic") */
#define GAME_W 800
#define GAME_H 600

static const BYTE kDraw[8] = {0x6A, 0xFF, 0x64, 0xA1, 0x00, 0x00, 0x00, 0x00}; /* push -1; mov eax, fs:[0] */
static const BYTE kGfx[5]  = {0xA1, 0x0C, 0xBE, 0xAE, 0x00};                   /* mov eax, [0xAEBE0C] */

/* T2K: line height and point size per game font id (from the jump tables in 0x5E5160 / 0x5E5AD0) */
static const int kT2kLine[8] = {15, 15, 18, 21, 24, 27, 39, 45};
static const int kT2kPt[8]   = {9, 9, 10, 14, 16, 18, 20, 22};

typedef int(__cdecl *TextWidthFn)(int font, const char *s);

/* ---------------------------------------------------------------- scene (game thread) */
#define MAX_ITEMS 2048
#define MAX_STR   120
#define STRIP     16        /* the background checksum is kept per vertical strip of 16 game pixels */
#define MAX_STRIPS 64
typedef unsigned __int64 Mask;

typedef struct {
    short x, y, w, h;
    short font;
    WORD color;
    BYTE state;       /* 0 free, 1 shown, 2 hidden (background changed) */
    DWORD gen;        /* present generation when recorded */
    Mask mask;        /* strips still showing the background the string was drawn on */
    Mask covered;     /* strips under a translucent panel (blend) until what was there is put back */
    BYTE dim;         /* times dimmed (0x5E42E0) since drawn: drawn darker, like the game's glyphs */
    DWORD fx_epoch;   /* panel/dim that last covered or dimmed it */
    WORD *px;         /* copy of the background under the glyph band when recorded (owned) */
    char s[MAX_STR];
} Item;

typedef struct {
    short x, y, w, h;
    short font;
    WORD color;
    Mask mask;
    BYTE dim;
    char s[MAX_STR];
} SnapItem;

static Item g_items[MAX_ITEMS];
static DWORD g_gen = 1;
static int g_dirty;

static CRITICAL_SECTION g_snap_cs;
static SnapItem g_snap[MAX_ITEMS];
static int g_snap_n;
static volatile LONG g_snap_ver;
static volatile LONG g_draws, g_kept, g_presents;
static int g_debug;
static DWORD g_blend_epoch;
static volatile LONG g_probe_n, g_probe_armed;  /* debug probe, see probe_install */
static char g_probe_flag[MAX_PATH];
static volatile LONG g_overlay_alive;  /* the overlay has run with a usable GL context: safe to hide glyphs */

typedef struct { BYTE *bits; int pitch, w, h; void *surf; } Locked;

static int lock_back(Locked *l)
{
    BYTE desc[0x6C];
    void *s = BACKBUF;
    HRESULT(WINAPI * lockfn)(void *, RECT *, void *, DWORD, HANDLE);
    if (!s)
        return 0;
    ZeroMemory(desc, sizeof(desc));
    *(DWORD *)desc = sizeof(desc);
    lockfn = (void *)(*(void ***)s)[0x64 / 4];
    if (lockfn(s, NULL, desc, 0x11 /* DDLOCK_WAIT | DDLOCK_READONLY */, NULL) != 0)
        return 0;
    l->surf = s;
    l->h = *(DWORD *)(desc + 0x08);
    l->w = *(DWORD *)(desc + 0x0C);
    l->pitch = *(LONG *)(desc + 0x10);
    l->bits = *(BYTE **)(desc + 0x24);
    return l->bits != NULL;
}

static void unlock_back(Locked *l)
{
    HRESULT(WINAPI * unlockfn)(void *, void *) = (void *)(*(void ***)l->surf)[0x80 / 4];
    unlockfn(l->surf, NULL);
}

static int strips(const Item *it)
{
    return min((it->w + STRIP - 1) / STRIP, MAX_STRIPS);
}

static Mask all_strips(const Item *it)
{
    int n = strips(it);
    return n >= 64 ? ~(Mask)0 : (((Mask)1 << n) - 1);
}

/* Only the middle half of the line height is compared: the glyph bodies are there, while the
   top/bottom margins often touch neighbouring widgets (e.g. the Action button over a header).
   A strip counts as drawn over when more than 30% of its band changed: buttons, popups and
   menus cover it, while thin lines (the dashed outline of a dragged position label) do not. */
#define BAND_TOP(it) ((it)->h / 4)
#define BAND_H(it)   ((it)->h - 2 * BAND_TOP(it))

static WORD back_px(const Locked *l, int x, int y)
{
    return x >= 0 && y >= 0 && x < l->w && y < l->h ? ((const WORD *)(l->bits + y * l->pitch))[x] : 0;
}

static void take_px(const Locked *l, Item *it)
{
    int r, c, bh = BAND_H(it), by = it->y + BAND_TOP(it);
    free(it->px);
    it->px = malloc(sizeof(WORD) * it->w * bh);
    if (!it->px)
        return;
    for (r = 0; r < bh; r++)
        for (c = 0; c < it->w; c++)
            it->px[r * it->w + c] = back_px(l, it->x + c, by + r);
}

/* strips whose background is unchanged since the string was recorded: at most `pct` % of
   the band pixels differ (30 for "is it still visible", 0 for "is it the very same background") */
static Mask same_strips_pct(const Locked *l, const Item *it, int pct)
{
    Mask m = 0;
    int k, n = strips(it), r, c, bh = BAND_H(it), by = it->y + BAND_TOP(it);
    if (!it->px)
        return all_strips(it);
    for (k = 0; k < n; k++) {
        int c0 = k * STRIP, c1 = min(c0 + STRIP, it->w), changed = 0;
        for (r = 0; r < bh; r++)
            for (c = c0; c < c1; c++)
                changed += back_px(l, it->x + c, by + r) != it->px[r * it->w + c];
        if (changed * 100 <= (c1 - c0) * bh * pct)
            m |= (Mask)1 << k;
    }
    return m;
}

static Mask same_strips(const Locked *l, const Item *it)
{
    return same_strips_pct(l, it, 30);
}

/* the strips of `it` that the range [x, x + w) covers still show the background it was drawn on */
static int background_kept(const Locked *l, const Item *it, int x, int w)
{
    Mask m = same_strips_pct(l, it, 0);
    int k, n = strips(it);
    for (k = 0; k < n; k++) {
        int a = it->x + k * STRIP, b = a + STRIP;
        if (a < x + w && x < b && !(m >> k & 1))
            return 0;
    }
    return 1;
}

static int overlap(int ax, int ay, int aw, int ah, int bx, int by, int bw, int bh)
{
    return ax < bx + bw && bx < ax + aw && ay < by + bh && by < ay + ah;
}

static int line_height(int font)
{
    return kT2kLine[font];
}

/* Traditional fonts selected (in game, at any time) or a frame other than 800x600: the game
   draws its text itself; strings recorded before must not stay on top of its glyphs */
static int g_scene_empty = 1;
static int g_size_logged;

static void drop_all(void)
{
    int i;
    for (i = 0; i < MAX_ITEMS; i++)
        g_items[i].state = 0;
    g_scene_empty = 1;
    g_dirty = 1;
}

static void publish(void)
{
    int i, n = 0;
    EnterCriticalSection(&g_snap_cs);
    for (i = 0; i < MAX_ITEMS; i++) {
        Item *it = &g_items[i];
        SnapItem *o;
        if (it->state != 1 || !(it->mask & ~it->covered))
            continue;
        o = &g_snap[n++];
        o->x = it->x; o->y = it->y; o->w = it->w; o->h = it->h;
        o->font = it->font; o->color = it->color;
        o->mask = it->mask & ~it->covered;
        o->dim = it->dim;
        memcpy(o->s, it->s, MAX_STR);
    }
    g_snap_n = n;
    LeaveCriticalSection(&g_snap_cs);
    InterlockedIncrement(&g_snap_ver);
    g_dirty = 0;
}

/* returns 1 when the string was taken over (the game must not draw it) */
static int __cdecl on_draw(DWORD *a)
{
    int x = (int)a[0], y = (int)a[1], font = (short)a[2], caret = (int)a[5], w, h, i, alnum = 0;
    const char *s = (const char *)a[4];
    Item *slot = NULL, *oldest = NULL;
    Locked l;
    InterlockedIncrement(&g_draws);
    if (!g_overlay_alive || !s || !*s || caret >= 0 || font < 0 || font > 7 || strlen(s) >= MAX_STR || strchr(s, '#'))
        return 0;
    if (FONT_MODE == -1) {
        if (!g_scene_empty)
            drop_all();
        return 0;
    }
    for (i = 0; s[i]; i++)
        alnum |= IsCharAlphaNumericA(s[i]);
    if (font == 0 && !alnum)
        return 0;
    w = ((TextWidthFn)T_WIDTH)(font, s);
    h = line_height(font);
    if (w <= 0 || h <= 0)
        return 0;
    if (!lock_back(&l))
        return 0;
    if (l.w != GAME_W || l.h != GAME_H) {
        /* a resolution patch (CM0102Patcher/Loader 1280x800, 1024x576...): positions, font
           metrics and line heights are not the ones text4k knows */
        unlock_back(&l);
        if (!g_size_logged) {
            logf("text4k: game frame is %dx%d, not %dx%d; text left to the game", l.w, l.h, GAME_W, GAME_H);
            g_size_logged = 1;
        }
        if (!g_scene_empty)
            drop_all();
        return 0;
    }
    for (i = 0; i < MAX_ITEMS; i++) {
        Item *it = &g_items[i];
        if (!it->state) {
            if (!slot)
                slot = it;
            continue;
        }
        /* same place and font: a redraw; an older string whose letters the new one's letters
           overlap (line margins do not count: the pressed "Action" button touches a header name),
           on its own unchanged background: replaced (relabelled/re-centred). If something was
           painted over the older string first (a popup menu, a dialog), it is only covered and
           comes back later. */
        if ((it->x == x && it->y == y && it->font == font) ||
            (it->gen != g_gen && overlap(x, y + h / 4, w, h - 2 * (h / 4), it->x, it->y + BAND_TOP(it), it->w, BAND_H(it)) &&
             background_kept(&l, it, x, w))) {
            if (g_debug)
                logf("text4k: drop \"%s\" (%d,%d %dx%d) under new \"%s\" (%d,%d %dx%d)", it->s, it->x, it->y,
                     it->w, it->h, s, x, y, w, h);
            it->state = 0;
            if (!slot)
                slot = it;
            continue;
        }
        if (!oldest || it->gen < oldest->gen || (it->gen == oldest->gen && it->state == 2 && oldest->state == 1))
            oldest = it;
    }
    if (!slot)
        slot = oldest;
    slot->x = (short)x; slot->y = (short)y; slot->w = (short)w; slot->h = (short)h;
    slot->font = (short)font;
    slot->color = (WORD)a[3];
    slot->gen = g_gen;
    take_px(&l, slot);
    slot->mask = all_strips(slot);
    slot->covered = 0;
    slot->dim = 0;
    slot->state = 1;
    g_scene_empty = 0;
    lstrcpynA(slot->s, s, MAX_STR);
    unlock_back(&l);
    g_dirty = 1;
    InterlockedIncrement(&g_kept);
    if (g_debug)
        logf("text4k: draw \"%s\" %d,%d %dx%d font %d color %04X gen %lu", s, x, y, w, h, font, (unsigned)(WORD)a[3], g_gen);
    return 1;
}

static void __cdecl on_present(DWORD *a)
{
    int x0 = (int)a[0], y0 = (int)a[1], x1 = (int)a[2], y1 = (int)a[3], i;
    Locked l;
    InterlockedIncrement(&g_presents);
    if (g_probe_flag[0])
        g_probe_armed = GetFileAttributesA(g_probe_flag) != INVALID_FILE_ATTRIBUTES;
    if (lock_back(&l)) {
        for (i = 0; i < MAX_ITEMS; i++) {
            Item *it = &g_items[i];
            BYTE st;
            Mask m;
            if (!it->state || !overlap(x0, y0, x1 - x0 + 1, y1 - y0 + 1, it->x, it->y, it->w, it->h))
                continue;
            m = same_strips(&l, it);
            st = m ? 1 : 2;  /* partly drawn over: show the strips still on their background */
            if (st != it->state || m != it->mask) {
                if (g_debug)
                    logf("text4k: present %d,%d-%d,%d: %s \"%s\" strips %I64X of %I64X", x0, y0, x1, y1,
                         st == 1 ? "show" : "hide", it->s, m, all_strips(it));
                it->state = st;
                it->mask = m;
                g_dirty = 1;
            }
        }
        unlock_back(&l);
    }
    g_gen++;
    if (g_dirty)
        publish();
}

__declspec(naked) static void draw_hook(void)
{
    __asm {
        pushad
        lea eax, [esp + 0x24]                /* &arg1 */
        push eax
        call on_draw
        add esp, 4
        test eax, eax
        popad                                /* flags survive popad */
        jnz skip
        push -1
        mov eax, dword ptr fs:[0]
        push T_DRAW + 8
        ret
    skip:
        ret
    }
}

__declspec(naked) static void present_hook(void)
{
    __asm {
        pushad
        lea eax, [esp + 0x24]
        push eax
        call on_present
        add esp, 4
        popad
        mov eax, dword ptr ds:[0x00AEBE0C]
        push T_PRESENT + 5
        ret
    }
}

/*
 * grab/put: the game saves rects of the back buffer (grab) and copies them back later (put) -
 * widget backgrounds, the screen background, whatever is under a popup. With the glyphs not in
 * the back buffer, a grab remembers which strings were inside its rect, and a put of that buffer
 * puts them back; any put first removes the strings it covers (it overwrites their pixels).
 */
typedef void *(__cdecl *GrabFn)(int x0, int y0, int x1, int y1, void *saved);
typedef void(__cdecl *PutFn)(int x, int y, DWORD *saved);
static GrabFn g_orig_grab;
static PutFn g_orig_put;

#define MAX_SAVED 128
typedef struct {
    void *buf;
    int gx, gy, n;
    DWORD stamp;
    DWORD hash;           /* of the saved pixels: the game may reuse the memory for something else */
    DWORD epoch;          /* g_blend_epoch at grab time */
    Item *items;          /* x/y relative to (gx, gy) */
} Saved;
static Saved g_saved[MAX_SAVED];
static DWORD g_saved_stamp;

static DWORD saved_hash(const DWORD *sv)
{
    const DWORD *p = (const DWORD *)sv[3];
    DWORD h = 2166136261u, n = sv[2] / 4, i, step = n > 65536 ? 7 : 1;
    if (!p)
        return 0;
    for (i = 0; i < n; i += step)
        h = (h ^ p[i]) * 16777619u;
    return h ^ sv[0] ^ (sv[1] << 16);
}

static Saved *saved_slot(void *buf, int create)
{
    Saved *free_ = NULL, *lru = NULL;
    int i;
    for (i = 0; i < MAX_SAVED; i++) {
        Saved *v = &g_saved[i];
        if (v->buf == buf)
            return v;
        if (!v->buf) {
            if (!free_)
                free_ = v;
        } else if (!lru || v->stamp < lru->stamp) {
            lru = v;
        }
    }
    if (!create)
        return NULL;
    if (!free_) {
        free_ = lru;
        free(free_->items);
        ZeroMemory(free_, sizeof(*free_));
    }
    return free_;
}

/* strips of `it` horizontally inside [x0, x1] */
static Mask strips_in(const Item *it, int x0, int x1)
{
    Mask m = 0;
    int k, n = strips(it);
    for (k = 0; k < n; k++) {
        int a = it->x + k * STRIP, b = min(a + STRIP, it->x + it->w) - 1;
        if (a <= x1 && b >= x0)
            m |= (Mask)1 << k;
    }
    return m;
}

static void *__cdecl my_grab(int x0, int y0, int x1, int y1, void *saved)
{
    void *r = g_orig_grab(x0, y0, x1, y1, saved);
    Saved *v;
    Locked l;
    int i, n = 0;
    if (!r || !lock_back(&l))
        return r;
    v = saved_slot(r, 1);
    free(v->items);
    v->items = NULL;
    v->buf = r;
    v->gx = x0;
    v->gy = y0;
    v->stamp = ++g_saved_stamp;
    for (i = 0; i < MAX_ITEMS; i++) {
        Item *it = &g_items[i];
        Mask m;
        if (it->state != 1 || it->x < x0 || it->y < y0 || it->x + it->w > x1 + 1 || it->y + it->h > y1 + 1)
            continue;
        m = same_strips(&l, it) & it->mask;
        if (!m)
            continue;  /* drawn over since: not really there */
        if (!v->items) {
            int cnt = 0, k;
            for (k = i; k < MAX_ITEMS; k++)
                cnt += g_items[k].state == 1;
            v->items = malloc(sizeof(Item) * cnt);
        }
        if (!v->items)
            break;
        v->items[n] = *it;
        v->items[n].px = NULL;  /* taken again when put back */
        v->items[n].mask = m;   /* covered/dim kept: still under the panel if it was */
        v->items[n].x -= (short)x0;
        v->items[n].y -= (short)y0;
        n++;
    }
    v->n = n;
    v->hash = saved_hash((DWORD *)r);
    v->epoch = g_blend_epoch;
    unlock_back(&l);
    if (g_debug)
        logf("text4k: grab %d,%d-%d,%d -> %p, %d strings", x0, y0, x1, y1, r, n);
    return r;
}

static void __cdecl my_put(int x, int y, DWORD *saved)
{
    Saved *v;
    Locked l;
    int i, j, w, h, locked;
    g_orig_put(x, y, saved);
    if (!saved)
        return;
    w = (int)saved[0];
    h = (int)saved[1];
    v = saved_slot(saved, 0);
    if (v && v->hash != saved_hash(saved))
        v = NULL;  /* not what we saw grabbed */
    locked = lock_back(&l);
    for (i = 0; i < MAX_ITEMS; i++) {
        Item *it = &g_items[i];
        /* only strings entirely under the put: one it just touches (e.g. a name under a dragged
           label) stays, and the strip check at present shows/hides its parts */
        if (it->state && it->x >= x && it->y >= y && it->x + it->w <= x + w && it->y + it->h <= y + h) {
            if (g_debug && w * h < GAME_W * GAME_H)
                logf("text4k: put %d,%d %dx%d removes \"%s\"", x, y, w, h, it->s);
            it->state = 0;
            g_dirty = 1;
        } else if (it->state && (it->covered || it->dim) && v && v->epoch < it->fx_epoch &&
                   overlap(x, y, w, h, it->x, it->y, it->w, it->h)) {
            /* pixels from before the panel/dimming are back: it is gone there. A put of something
               saved later (a widget inside the dialog redrawing itself) does not uncover. */
            it->covered &= ~strips_in(it, x, x + w - 1);
            if (it->dim) {
                it->dim = 0;
                if (locked)
                    take_px(&l, it);  /* the pixel check compares with the undimmed background now */
            }
            g_dirty = 1;
        }
    }
    if (locked)
        unlock_back(&l);
    if (!v || !v->n) {
        if (g_debug && saved_slot(saved, 0))
            logf("text4k: put %d,%d %p: buffer changed since grab, not restoring", x, y, saved);
        return;
    }
    if (FONT_MODE == -1)  /* grabbed while Futuristic was on; Traditional is left to the game */
        return;
    if (g_debug)
        logf("text4k: put %d,%d %dx%d %p: restoring %d strings (\"%s\"...)", x, y, w, h, saved, v->n, v->items[0].s);
    if (!lock_back(&l))
        return;
    v->stamp = ++g_saved_stamp;
    for (i = 0, j = 0; i < v->n; i++) {
        Item *src = &v->items[i];
        for (; j < MAX_ITEMS && g_items[j].state; j++)
            ;
        if (j == MAX_ITEMS)
            break;
        free(g_items[j].px);
        g_items[j] = *src;
        g_items[j].x += (short)x;
        g_items[j].y += (short)y;
        take_px(&l, &g_items[j]);  /* mask: as grabbed */
        g_items[j].state = 1;
    }
    unlock_back(&l);
    g_dirty = 1;
}

/*
 * Panels and dimming under dialogs (found with the probe, opening Team Instructions):
 *   0x5E42E0 dim(x0, y0, x1, y1, ...)            darkens a rect: the whole screen under a dialog.
 *       The game's glyphs under it get darker but stay readable, so ours are drawn darker too
 *       (and their background reference is retaken, so the pixel check does not hide them).
 *   0x5E5DF0 panel(x0, y0, x1, y1, flags, colour) with flag 0x10: a (translucent) textured panel,
 *       the body of dialogs and popup menus. Over a background of nearly the same colour (the dark
 *       green Team Instructions over the dark green tactics pitch) it leaves the pixels as they
 *       were, so the pixel check cannot see it: strings under a large one are covered outright.
 *       Other flags (1, frames, gradients) are drawn over labels that stay visible: ignored.
 * Both are undone when pixels saved before them are put back (the dialog closing).
 */
typedef int(__cdecl *Fn6)(int x0, int y0, int x1, int y1, int a5, int a6);
static Fn6 g_orig_panel, g_orig_dim;

static int __cdecl my_panel(int x0, int y0, int x1, int y1, int flags, int colour)
{
    int r = g_orig_panel(x0, y0, x1, y1, flags, colour), i, n = 0;
    if (!(flags & 0x10) || (x1 - x0 + 1) * (y1 - y0 + 1) < 200 * 150)
        return r;
    g_blend_epoch++;
    for (i = 0; i < MAX_ITEMS; i++) {
        Item *it = &g_items[i];
        /* the glyph band, not the whole line box: a panel just below a header title leaves it */
        if (it->state && overlap(x0, y0, x1 - x0 + 1, y1 - y0 + 1, it->x, it->y + BAND_TOP(it), it->w, BAND_H(it))) {
            it->covered |= strips_in(it, x0, x1);
            it->fx_epoch = g_blend_epoch;
            g_dirty = 1;
            n++;
        }
    }
    if (g_debug)
        logf("text4k: panel %d,%d-%d,%d flags %X covers %d strings", x0, y0, x1, y1, flags, n);
    return r;
}

static int __cdecl my_dim(int x0, int y0, int x1, int y1, int a5, int a6)
{
    int r = g_orig_dim(x0, y0, x1, y1, a5, a6), i, n = 0;
    Locked l;
    if (!lock_back(&l))
        return r;
    g_blend_epoch++;
    for (i = 0; i < MAX_ITEMS; i++) {
        Item *it = &g_items[i];
        if (it->state && overlap(x0, y0, x1 - x0 + 1, y1 - y0 + 1, it->x, it->y, it->w, it->h)) {
            if (it->dim < 3)
                it->dim++;
            it->fx_epoch = g_blend_epoch;
            take_px(&l, it);
            g_dirty = 1;
            n++;
        }
    }
    unlock_back(&l);
    if (g_debug)
        logf("text4k: dim %d,%d-%d,%d dims %d strings", x0, y0, x1, y1, n);
    return r;
}

/* copy of a 5-byte position-independent prologue + jmp back: calls the original function */
static void *make_tramp(DWORD at)
{
    BYTE *t = VirtualAlloc(NULL, 16, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!t)
        return NULL;
    memcpy(t, (void *)at, 5);
    t[5] = 0xE9;
    *(DWORD *)(t + 6) = (at + 5) - ((DWORD)t + 10);
    return t;
}


/* ---------------------------------------------------------------- debug probe (CMFIX_TEXT4K_PROBE=1) */
/* logs calls of the other pixel-writing primitives with their first 6 arguments */
static const DWORD kProbe[] = {0x5E3730, 0x5E3B50, 0x5E3CE0, 0x5E40D0, 0x5E42E0, 0x5E4560, 0x5E4800,
                               0x5E49F0, 0x5E5DF0, 0x5E76F0, 0x5E7F60};  /* 0x5E5DF0 skipped if hooked */

static void __cdecl on_probe(int tag, DWORD *a, DWORD ret)
{
    if (!g_probe_armed || InterlockedIncrement(&g_probe_n) > 20000)
        return;
    logf("text4k: probe %06lX(%ld, %ld, %ld, %ld, %08lX, %08lX) ret %08lX", tag == 99 ? 0x5E7840 : kProbe[tag],
         (long)a[0], (long)a[1], (long)a[2], (long)a[3], a[4], a[5], ret);
}

#define PROBE(n)                                                                                       __declspec(naked) static void probe_##n(void)                                                      {                                                                                                      __asm pushad                                                                                       __asm push dword ptr [esp + 0x20]                                                                  __asm lea eax, [esp + 0x28]                                                                        __asm push eax                                                                                     __asm push n                                                                                       __asm call on_probe                                                                                __asm add esp, 12                                                                                  __asm popad                                                                                        __asm mov eax, dword ptr ds:[0x00AEBE0C]                                                           __asm jmp dword ptr [kProbeRet + n * 4]                                                        }
static DWORD kProbeRet[11];
PROBE(0) PROBE(1) PROBE(2) PROBE(3) PROBE(4) PROBE(5) PROBE(6) PROBE(7) PROBE(8) PROBE(9) PROBE(10)
static void *const kProbeFn[11] = {probe_0, probe_1, probe_2, probe_3, probe_4, probe_5, probe_6, probe_7,
                                   probe_8, probe_9, probe_10};
__declspec(naked) static void probe_99(void)
{
    __asm {
        pushad
        push dword ptr [esp + 0x20]
        lea eax, [esp + 0x28]
        push eax
        push 99
        call on_probe
        add esp, 12
        popad
        sub esp, 0x94
        push 0x005E7846
        ret
    }
}

static void patch_jmp5(DWORD at, void *to, int len);

static void probe_install(void)
{
    static const BYTE k7840[6] = {0x81, 0xEC, 0x94, 0x00, 0x00, 0x00};
    int i;
    for (i = 0; i < 11; i++) {
        if (memcmp((void *)kProbe[i], kGfx, 5))
            continue;
        kProbeRet[i] = kProbe[i] + 5;
        patch_jmp5(kProbe[i], kProbeFn[i], 5);
    }
    if (!memcmp((void *)0x5E7840, k7840, 6))
        patch_jmp5(0x5E7840, probe_99, 6);
    GetModuleFileNameA(NULL, g_probe_flag, MAX_PATH);
    lstrcpyA(strrchr(g_probe_flag, 0x5C) + 1, "probe.on");
    logf("text4k: probe ready (logs while %s exists)", g_probe_flag);
}

/* ---------------------------------------------------------------- overlay (render thread) */
#define GL_TEXTURE_2D 0x0DE1
#define GL_BLEND 0x0BE2
#define GL_SRC_ALPHA 0x0302
#define GL_ONE_MINUS_SRC_ALPHA 0x0303
#define GL_VIEWPORT 0x0BA2
#define GL_RGBA 0x1908
#define GL_BGRA 0x80E1
#define GL_UNSIGNED_BYTE 0x1401
#define GL_TEXTURE_MIN_FILTER 0x2801
#define GL_TEXTURE_MAG_FILTER 0x2800
#define GL_TEXTURE_WRAP_S 0x2802
#define GL_TEXTURE_WRAP_T 0x2803
#define GL_LINEAR 0x2601
#define GL_CLAMP_TO_EDGE 0x812F
#define GL_PROJECTION 0x1701
#define GL_MODELVIEW 0x1700
#define GL_TEXTURE 0x1702
#define GL_QUADS 0x0007
#define GL_ALL_ATTRIB_BITS 0x000FFFFF
#define GL_CLIENT_ALL_ATTRIB_BITS 0xFFFFFFFF
#define GL_CURRENT_PROGRAM 0x8B8D
#define GL_DRAW_FRAMEBUFFER 0x8CA9
#define GL_DRAW_FRAMEBUFFER_BINDING 0x8CA6
#define GL_VERTEX_ARRAY_BINDING 0x85B5
#define GL_ACTIVE_TEXTURE 0x84E0
#define GL_TEXTURE0 0x84C0
#define GL_ARRAY_BUFFER 0x8892
#define GL_ARRAY_BUFFER_BINDING 0x8894
#define GL_PIXEL_UNPACK_BUFFER 0x88EC
#define GL_PIXEL_UNPACK_BUFFER_BINDING 0x88EF
#define GL_SAMPLER_BINDING 0x8919
#define GL_TEXTURE_ENV 0x2300
#define GL_TEXTURE_ENV_MODE 0x2200
#define GL_MODULATE 0x2100
#define GL_VERSION 0x1F02
#define GL_RENDERER 0x1F01
#define GL_CONTEXT_PROFILE_MASK 0x9126
#define GL_DEPTH_TEST 0x0B71
#define GL_SCISSOR_TEST 0x0C11
#define GL_CULL_FACE 0x0B44
#define GL_ALPHA_TEST 0x0BC0
#define GL_STENCIL_TEST 0x0B90
#define GL_FRAMEBUFFER_SRGB 0x8DB9
#define GL_UNPACK_ALIGNMENT 0x0CF5
#define GL_NO_ERROR 0

typedef unsigned int GLuint;
#define GLAPI WINAPI
static void(GLAPI *glGetIntegerv_)(unsigned, int *);
static const char *(GLAPI *glGetString_)(unsigned);
static unsigned(GLAPI *glGetError_)(void);
static void(GLAPI *glEnable_)(unsigned);
static void(GLAPI *glDisable_)(unsigned);
static void(GLAPI *glBlendFunc_)(unsigned, unsigned);
static void(GLAPI *glGenTextures_)(int, GLuint *);
static void(GLAPI *glDeleteTextures_)(int, const GLuint *);
static void(GLAPI *glBindTexture_)(unsigned, GLuint);
static void(GLAPI *glTexImage2D_)(unsigned, int, int, int, int, int, unsigned, unsigned, const void *);
static void(GLAPI *glTexParameteri_)(unsigned, unsigned, int);
static void(GLAPI *glTexEnvi_)(unsigned, unsigned, int);
static void(GLAPI *glPixelStorei_)(unsigned, int);
static void(GLAPI *glPushAttrib_)(unsigned);
static void(GLAPI *glPopAttrib_)(void);
static void(GLAPI *glPushClientAttrib_)(unsigned);
static void(GLAPI *glPopClientAttrib_)(void);
static void(GLAPI *glMatrixMode_)(unsigned);
static void(GLAPI *glPushMatrix_)(void);
static void(GLAPI *glPopMatrix_)(void);
static void(GLAPI *glLoadIdentity_)(void);
static void(GLAPI *glOrtho_)(double, double, double, double, double, double);
static void(GLAPI *glBegin_)(unsigned);
static void(GLAPI *glEnd_)(void);
static void(GLAPI *glTexCoord2f_)(float, float);
static void(GLAPI *glVertex2f_)(float, float);
static void(GLAPI *glColor4ub_)(BYTE, BYTE, BYTE, BYTE);
static PROC(GLAPI *wglGetProcAddress_)(const char *);
static void(GLAPI *glUseProgram_)(GLuint);
static void(GLAPI *glBindFramebuffer_)(unsigned, GLuint);
static void(GLAPI *glBindVertexArray_)(GLuint);
static void(GLAPI *glActiveTexture_)(unsigned);
static void(GLAPI *glBindBuffer_)(unsigned, GLuint);
static void(GLAPI *glBindSampler_)(unsigned, GLuint);

static int g_gl_state;  /* 0 not tried, 1 ok, -1 failed */

static int gl_resolve(void)
{
    HMODULE gl = GetModuleHandleA("opengl32.dll");
#define R(name) if (!(*(FARPROC *)&name##_ = GetProcAddress(gl, #name))) { logf("text4k: %s missing", #name); return 0; }
#define X(name) *(PROC *)&name##_ = wglGetProcAddress_(#name)
    if (!gl)
        return 0;
    R(glGetIntegerv) R(glGetString) R(glGetError) R(glEnable) R(glDisable) R(glBlendFunc) R(glGenTextures)
    R(glDeleteTextures) R(glBindTexture) R(glTexImage2D) R(glTexParameteri) R(glTexEnvi) R(glPixelStorei)
    R(glPushAttrib) R(glPopAttrib) R(glPushClientAttrib) R(glPopClientAttrib) R(glMatrixMode) R(glPushMatrix)
    R(glPopMatrix) R(glLoadIdentity) R(glOrtho) R(glBegin) R(glEnd) R(glTexCoord2f) R(glVertex2f) R(glColor4ub)
    R(wglGetProcAddress)
    X(glUseProgram); X(glBindFramebuffer); X(glBindVertexArray); X(glActiveTexture); X(glBindBuffer); X(glBindSampler);
#undef R
#undef X
    return 1;
}

/* ---- text textures (GDI-rasterised, cached) */
#define MAX_TEX 768

typedef struct {
    int used;
    short font, em;
    char s[MAX_STR];
    GLuint tex;
    int tw, th;       /* texture size = GDI cell */
    int ink_w;        /* advance width of the string */
    DWORD last;       /* frame last used */
} Tex;

static Tex g_tex[MAX_TEX];
static DWORD g_frame;
static HDC g_dc;
static char g_face_small[LF_FACESIZE] = "Eras Demi ITC";
static char g_face_large[LF_FACESIZE] = "Eras Demi ITC";
static int g_weight_large = FW_NORMAL;
static float g_em_k = 1.33f;      /* T2K point size -> pixels per em in game pixels */
static float g_y_off = 0.0f;      /* vertical tweak, game pixels */

static const char *face_for(int font)
{
    return font <= 2 ? g_face_small : g_face_large;
}

static int em_for(int font, float scale)
{
    return (int)(kT2kPt[font] * g_em_k * scale + 0.5f);
}

static Tex *get_tex(const SnapItem *it, float scale)
{
    int em = em_for(it->font, scale), i, x, y, n;
    WCHAR ws[MAX_STR];
    Tex *t = NULL, *lru = NULL;
    HFONT f, of;
    SIZE sz;
    TEXTMETRICA tm;
    BITMAPINFO bi;
    HBITMAP bmp, ob;
    DWORD *px;
    RECT rc;
    for (i = 0; i < MAX_TEX; i++) {
        Tex *c = &g_tex[i];
        if (c->used && c->em == em && c->font == it->font && !strcmp(c->s, it->s)) {
            c->last = g_frame;
            return c;
        }
        if (!c->used) {
            if (!t)
                t = c;
        } else if (!lru || c->last < lru->last) {
            lru = c;
        }
    }
    if (!t) {
        t = lru;
        glDeleteTextures_(1, &t->tex);
    }
    if (!g_dc)
        g_dc = CreateCompatibleDC(NULL);
    /* the game's strings are Windows-1252 (Verón, Svärd, £), whatever the system ANSI code page
       is: with DEFAULT_CHARSET and the A calls a Cyrillic system showed "Verуn", "Ј" */
    n = MultiByteToWideChar(1252, 0, it->s, -1, ws, MAX_STR);
    n = n > 0 ? n - 1 : 0;
    f = CreateFontA(-em, 0, 0, 0, it->font >= 3 ? g_weight_large : FW_NORMAL, 0, 0, 0, ANSI_CHARSET,
                    OUT_TT_ONLY_PRECIS, CLIP_DEFAULT_PRECIS, ANTIALIASED_QUALITY, DEFAULT_PITCH, face_for(it->font));
    of = SelectObject(g_dc, f);
    GetTextMetricsA(g_dc, &tm);
    GetTextExtentPoint32W(g_dc, ws, n, &sz);
    t->tw = sz.cx + 2;
    t->th = tm.tmHeight;
    t->ink_w = sz.cx;
    ZeroMemory(&bi, sizeof(bi));
    bi.bmiHeader.biSize = sizeof(bi.bmiHeader);
    bi.bmiHeader.biWidth = t->tw;
    bi.bmiHeader.biHeight = -t->th;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bmp = CreateDIBSection(g_dc, &bi, DIB_RGB_COLORS, (void **)&px, NULL, 0);
    ob = SelectObject(g_dc, bmp);
    SetRect(&rc, 0, 0, t->tw, t->th);
    FillRect(g_dc, &rc, GetStockObject(BLACK_BRUSH));
    SetTextColor(g_dc, RGB(255, 255, 255));
    SetBkMode(g_dc, TRANSPARENT);
    TextOutW(g_dc, 1, 0, ws, n);
    GdiFlush();
    for (y = 0; y < t->th; y++)
        for (x = 0; x < t->tw; x++) {
            DWORD *p = px + y * t->tw + x;
            *p = ((*p >> 8) & 0xFF) << 24 | 0x00FFFFFF;  /* coverage (green) -> alpha, white */
        }
    glGenTextures_(1, &t->tex);
    glBindTexture_(GL_TEXTURE_2D, t->tex);
    glTexParameteri_(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri_(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri_(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri_(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D_(GL_TEXTURE_2D, 0, GL_RGBA, t->tw, t->th, 0, GL_BGRA, GL_UNSIGNED_BYTE, px);
    SelectObject(g_dc, ob);
    DeleteObject(bmp);
    SelectObject(g_dc, of);
    DeleteObject(f);
    t->used = 1;
    t->em = (short)em;
    t->font = it->font;
    lstrcpynA(t->s, it->s, MAX_STR);
    t->last = g_frame;
    return t;
}

static SnapItem g_draw[MAX_ITEMS];
static int g_draw_n;
static LONG g_draw_ver = -1;

static void overlay(void)
{
    int vp[4], prog = 0, fbo = 0, vao = 0, atex = 0, abuf = 0, ubuf = 0, samp = 0, i;
    float sx, sy;
    LONG ver;
    static int logged;
    g_frame++;
    if (g_gl_state == 0) {
        g_gl_state = gl_resolve() ? 1 : -1;
        if (g_gl_state == 1) {
            int prof = 0;
            glGetIntegerv_(GL_CONTEXT_PROFILE_MASK, &prof);
            glGetError_();
            logf("text4k: GL %s / %s, profile mask %d", glGetString_(GL_VERSION), glGetString_(GL_RENDERER), prof);
        }
    }
    if (g_gl_state != 1)
        return;
    g_overlay_alive = 1;
    ver = g_snap_ver;
    if (ver != g_draw_ver) {
        EnterCriticalSection(&g_snap_cs);
        g_draw_n = g_snap_n;
        memcpy(g_draw, g_snap, g_snap_n * sizeof(SnapItem));
        g_draw_ver = g_snap_ver;
        LeaveCriticalSection(&g_snap_cs);
    }
    glGetIntegerv_(GL_VIEWPORT, vp);
    if (logged < 3 || (g_frame % 600) == 0) {
        logf("text4k: frame %lu viewport %d,%d %dx%d, items %d (draws %ld kept %ld presents %ld)",
             g_frame, vp[0], vp[1], vp[2], vp[3], g_draw_n, g_draws, g_kept, g_presents);
        logged++;
    }
    if (!g_draw_n || vp[2] <= 0 || vp[3] <= 0)
        return;
    sx = vp[2] / (float)GAME_W;
    sy = vp[3] / (float)GAME_H;

    glGetIntegerv_(GL_CURRENT_PROGRAM, &prog);
    glGetIntegerv_(GL_DRAW_FRAMEBUFFER_BINDING, &fbo);
    glGetIntegerv_(GL_VERTEX_ARRAY_BINDING, &vao);
    glGetIntegerv_(GL_ACTIVE_TEXTURE, &atex);
    glGetIntegerv_(GL_ARRAY_BUFFER_BINDING, &abuf);
    glGetIntegerv_(GL_PIXEL_UNPACK_BUFFER_BINDING, &ubuf);
    if (glActiveTexture_)
        glActiveTexture_(GL_TEXTURE0);
    glGetIntegerv_(GL_SAMPLER_BINDING, &samp);
    glGetError_();
    if (glUseProgram_) glUseProgram_(0);
    if (glBindFramebuffer_) glBindFramebuffer_(GL_DRAW_FRAMEBUFFER, 0);
    if (glBindVertexArray_) glBindVertexArray_(0);
    if (glBindBuffer_) {
        glBindBuffer_(GL_ARRAY_BUFFER, 0);
        glBindBuffer_(GL_PIXEL_UNPACK_BUFFER, 0);
    }
    if (glBindSampler_) glBindSampler_(0, 0);
    glPushAttrib_(GL_ALL_ATTRIB_BITS);
    glPushClientAttrib_(GL_CLIENT_ALL_ATTRIB_BITS);
    glPixelStorei_(GL_UNPACK_ALIGNMENT, 4);
    glMatrixMode_(GL_TEXTURE); glPushMatrix_(); glLoadIdentity_();
    glMatrixMode_(GL_PROJECTION); glPushMatrix_(); glLoadIdentity_();
    glOrtho_(0, vp[2], vp[3], 0, -1, 1);
    glMatrixMode_(GL_MODELVIEW); glPushMatrix_(); glLoadIdentity_();
    glDisable_(GL_DEPTH_TEST); glDisable_(GL_SCISSOR_TEST); glDisable_(GL_CULL_FACE);
    glDisable_(GL_ALPHA_TEST); glDisable_(GL_STENCIL_TEST); glDisable_(GL_FRAMEBUFFER_SRGB);
    glEnable_(GL_TEXTURE_2D);
    glEnable_(GL_BLEND);
    glBlendFunc_(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glTexEnvi_(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE);

    for (i = 0; i < g_draw_n; i++) {
        const SnapItem *it = &g_draw[i];
        Tex *t = get_tex(it, sy);
        WORD c = it->color;
        float x0, y0, x1, y1, fit, gw = it->w * sx;
        /* keep the game's layout: never wider than the game's own string width */
        fit = t->ink_w > gw ? gw / t->ink_w : 1.0f;
        x0 = it->x * sx - fit;
        x1 = x0 + t->tw * fit;
        y0 = (it->y + g_y_off) * sy + (it->h * sy - t->th) * 0.5f;
        y1 = y0 + t->th;
        glBindTexture_(GL_TEXTURE_2D, t->tex);
        {
            int k = it->dim >= 3 ? 30 : it->dim == 2 ? 45 : it->dim == 1 ? 65 : 100;  /* % brightness */
            glColor4ub_((BYTE)(((c >> 11) & 31) * 255 / 31 * k / 100), (BYTE)(((c >> 5) & 63) * 255 / 63 * k / 100),
                        (BYTE)((c & 31) * 255 / 31 * k / 100), 255);
        }
        glBegin_(GL_QUADS);
        {
            /* one quad per run of strips still on their background (the rest was drawn over) */
            int n = min((it->w + STRIP - 1) / STRIP, MAX_STRIPS), k = 0;
            while (k < n) {
                int e;
                float a, b, ua, ub;
                if (!(it->mask >> k & 1)) {
                    k++;
                    continue;
                }
                for (e = k; e < n && (it->mask >> e & 1); e++)
                    ;
                a = k == 0 ? x0 : (it->x + k * STRIP) * sx;
                b = e == n ? x1 : (it->x + e * STRIP) * sx;
                ua = (a - x0) / (x1 - x0);
                ub = (b - x0) / (x1 - x0);
                glTexCoord2f_(ua, 0); glVertex2f_(a, y0);
                glTexCoord2f_(ub, 0); glVertex2f_(b, y0);
                glTexCoord2f_(ub, 1); glVertex2f_(b, y1);
                glTexCoord2f_(ua, 1); glVertex2f_(a, y1);
                k = e;
            }
        }
        glEnd_();
    }

    glMatrixMode_(GL_TEXTURE); glPopMatrix_();
    glMatrixMode_(GL_PROJECTION); glPopMatrix_();
    glMatrixMode_(GL_MODELVIEW); glPopMatrix_();
    glPopClientAttrib_();
    glPopAttrib_();
    if (glBindSampler_) glBindSampler_(0, samp);
    if (glBindBuffer_) {
        glBindBuffer_(GL_ARRAY_BUFFER, abuf);
        glBindBuffer_(GL_PIXEL_UNPACK_BUFFER, ubuf);
    }
    if (glBindVertexArray_) glBindVertexArray_(vao);
    if (glBindFramebuffer_) glBindFramebuffer_(GL_DRAW_FRAMEBUFFER, fbo);
    if (glUseProgram_) glUseProgram_(prog);
    if (glActiveTexture_) glActiveTexture_(atex);
    {
        unsigned e = glGetError_();
        static int errs;
        if (e != GL_NO_ERROR && errs++ < 5)
            logf("text4k: GL error %04X after overlay", e);
    }
}

typedef BOOL(WINAPI *SwapBuffersFn)(HDC);
static SwapBuffersFn g_real_swap;

static BOOL WINAPI my_swap(HDC dc)
{
    overlay();
    return g_real_swap(dc);
}

/* point ddraw.dll's import of gdi32!SwapBuffers at my_swap */
static int hook_ddraw_swap(void)
{
    BYTE *b = (BYTE *)GetModuleHandleA("ddraw.dll");
    IMAGE_NT_HEADERS *nt;
    IMAGE_IMPORT_DESCRIPTOR *d;
    if (!b)
        return 0;
    nt = (IMAGE_NT_HEADERS *)(b + ((IMAGE_DOS_HEADER *)b)->e_lfanew);
    d = (IMAGE_IMPORT_DESCRIPTOR *)(b + nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; d->Name; d++) {
        IMAGE_THUNK_DATA *names, *addrs;
        if (lstrcmpiA((char *)(b + d->Name), "gdi32.dll"))
            continue;
        names = (IMAGE_THUNK_DATA *)(b + d->OriginalFirstThunk);
        addrs = (IMAGE_THUNK_DATA *)(b + d->FirstThunk);
        for (; names->u1.AddressOfData; names++, addrs++) {
            DWORD old;
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal) ||
                strcmp((char *)((IMAGE_IMPORT_BY_NAME *)(b + names->u1.AddressOfData))->Name, "SwapBuffers"))
                continue;
            g_real_swap = (SwapBuffersFn)addrs->u1.Function;
            VirtualProtect(&addrs->u1.Function, 4, PAGE_READWRITE, &old);
            addrs->u1.Function = (DWORD)my_swap;
            VirtualProtect(&addrs->u1.Function, 4, old, &old);
            return 1;
        }
    }
    return 0;
}

static void patch_jmp5(DWORD at, void *to, int len)
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

static void cfg_str(const char *name, char *dst, int cap)
{
    char v[LF_FACESIZE];
    if (cfg_get(name, v, sizeof(v)) && v[0])
        lstrcpynA(dst, v, cap);
}

static float cfg_float(const char *name, float def)
{
    char v[32];
    return cfg_get(name, v, sizeof(v)) && v[0] ? (float)atof(v) : def;
}

static int CALLBACK font_found(const LOGFONTA *lf, const TEXTMETRICA *tm, DWORD type, LPARAM found)
{
    (void)lf; (void)tm;
    if (type & TRUETYPE_FONTTYPE)
        *(int *)found = 1;
    return 0;
}

/* GDI would silently substitute a missing face (Eras Demi ITC comes with MS Office) */
static int font_installed(const char *face)
{
    LOGFONTA lf;
    int found = 0;
    HDC dc = GetDC(NULL);
    ZeroMemory(&lf, sizeof(lf));
    lf.lfCharSet = DEFAULT_CHARSET;
    lstrcpynA(lf.lfFaceName, face, LF_FACESIZE);
    EnumFontFamiliesExA(dc, &lf, (FONTENUMPROCA)font_found, (LPARAM)&found, 0);
    ReleaseDC(NULL, dc);
    return found;
}

/* called from cmfix's install() once the game code and cnc-ddraw are known to match */
int text4k_install(void)
{
    if (memcmp((void *)T_DRAW, kDraw, 8) || memcmp((void *)T_PRESENT, kGfx, 5) || memcmp((void *)T_PUT, kGfx, 5) ||
        memcmp((void *)T_GRAB, kGfx, 5) || memcmp((void *)T_DIM, kGfx, 5) || memcmp((void *)T_PANEL, kGfx, 5)) {
        logf("text4k: code does not match 3.9.68; off");
        return 0;
    }
    cfg_str("TEXT4K_FONT", g_face_small, LF_FACESIZE);
    cfg_str("TEXT4K_FONT", g_face_large, LF_FACESIZE);
    cfg_str("TEXT4K_FONT_LARGE", g_face_large, LF_FACESIZE);
    g_weight_large = (int)cfg_float("TEXT4K_WEIGHT_LARGE", (float)g_weight_large);
    g_em_k = cfg_float("TEXT4K_EM", g_em_k);
    g_y_off = cfg_float("TEXT4K_YOFF", g_y_off);
    g_debug = cfg_float("TEXT4K_DEBUG", 0) != 0;
    if (!font_installed(g_face_small)) {
        logf("text4k: font \"%s\" is not installed (TrueType); off", g_face_small);
        return 0;
    }
    if (!font_installed(g_face_large)) {
        logf("text4k: font \"%s\" is not installed (TrueType); using \"%s\" for titles", g_face_large, g_face_small);
        lstrcpynA(g_face_large, g_face_small, LF_FACESIZE);
    }
    InitializeCriticalSection(&g_snap_cs);
    if (!hook_ddraw_swap()) {
        logf("text4k: SwapBuffers import not found in ddraw.dll; off");
        return 0;
    }
    patch_jmp5(T_DRAW, draw_hook, 8);
    patch_jmp5(T_PRESENT, present_hook, 5);
    g_orig_grab = (GrabFn)make_tramp(T_GRAB);
    g_orig_put = (PutFn)make_tramp(T_PUT);
    g_orig_dim = (Fn6)make_tramp(T_DIM);  /* all these prologues are 5 position-independent bytes */
    g_orig_panel = (Fn6)make_tramp(T_PANEL);
    if (!g_orig_grab || !g_orig_put || !g_orig_dim || !g_orig_panel)
        return 0;
    patch_jmp5(T_GRAB, my_grab, 5);
    if (cfg_float("TEXT4K_PROBE", 0) != 0)
        probe_install();
    patch_jmp5(T_PUT, my_put, 5);
    patch_jmp5(T_DIM, my_dim, 5);
    patch_jmp5(T_PANEL, my_panel, 5);
    logf("text4k: on (fonts \"%s\" / \"%s\", em x%.2f, y %+.1f)", g_face_small, g_face_large, g_em_k, g_y_off);
    return 1;
}
