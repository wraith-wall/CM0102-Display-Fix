"""Check that the in-game Screen Mode switch drives cnc-ddraw (needs profile cnc_play_fix).

  python test_screenmode.py [--start full|windowed] [--profile cnc_play_fix]

Sets Screen Mode in game.cfg, starts the game via play.py's path, then toggles Screen Mode in
Game Settings (Full/Windowed -> Ok) a few times and finally presses Alt+Enter. After every step:
cnc-ddraw state (window/fullscreen from the window style), whether the menu is visible and
clickable at the expected place, and that the display mode never changed. Prints PASS/FAIL.
"""
import argparse
import time

import capture as CAP
import cfg
import common as C
import game
import input as I
import scenario as S
import wrapper

FULL_X, WINDOWED_X, ROW_Y = 445, 523, 250


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", choices=["full", "windowed"], default="windowed")
    ap.add_argument("--profile", default="cnc_play_fix")
    a = ap.parse_args()
    if game.running_pid():
        game.kill()
    wrapper.install(a.profile)
    cfg.set_screen(a.start)
    st = game.launch(exe="cm0102.exe", args="")
    h = st["hwnd"]
    S.MAP = "fit_aspect"
    disp0 = C.display_snapshot()
    results = []

    def mode():
        return "window" if C.user32.GetWindowLongW(h, -16) & 0x00C00000 == 0x00C00000 else "fullscreen"

    def check(label, expect):
        CAP.bring_to_front(h)
        time.sleep(1)
        menu = S.wait_match(h, "main_menu", 15)
        I.click(613, 307, h, mode="fit_aspect")
        clickable = S.wait_match(h, "game_settings", 8)
        I.click(371, 524, h, mode="fit_aspect")  # Cancel
        clickable = clickable and S.wait_match(h, "main_menu", 8)
        ok = mode() == expect and menu and clickable and C.display_snapshot() == disp0
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {label:22s} expect={expect:10s} got={mode():10s} "
              f"client={C.client_rect_screen(h)} menu={menu} clickable={clickable}")

    def set_mode(x):
        I.click(613, 307, h, mode="fit_aspect")
        assert S.wait_match(h, "game_settings", 8), "settings did not open"
        I.click(x, ROW_Y, h, mode="fit_aspect")
        time.sleep(0.8)
        I.click(662, 524, h, mode="fit_aspect")  # Ok
        time.sleep(4)

    assert S.wait_match(h, "main_menu", 120), "main menu not shown"
    first = "fullscreen" if a.start == "full" else "window"
    check(f"start ({a.start})", first)
    for x, expect in ((FULL_X, "fullscreen"), (WINDOWED_X, "window"), (FULL_X, "fullscreen"), (WINDOWED_X, "window")):
        set_mode(x)
        check(f"in-game {'Full' if x == FULL_X else 'Windowed'}", expect)
    CAP.bring_to_front(h)
    I._send(I._key(0x12)); time.sleep(0.05); I._send(I._key(0x0D)); time.sleep(0.05)
    I._send(I._key(0x0D, up=True)); I._send(I._key(0x12, up=True))
    time.sleep(3)
    check("Alt+Enter", "fullscreen")
    game.kill()
    try:
        with open(C.os.path.join(C.GAME_DIR, "cmfix.log"), encoding="utf-8") as f:
            print("--- cmfix.log\n" + f.read())
    except OSError:
        print("no cmfix.log")
    wrapper.uninstall()
    print("RESULT:", "PASS" if all(results) else "FAIL", f"({sum(results)}/{len(results)})")


if __name__ == "__main__":
    main()
