"""Play the dev copy with a wrapper profile (for trying a look by hand).

  python play.py                        # = cnc_play_fix: in-game Screen Mode picks fullscreen 4:3 or a
                                        #   resizable window (cmfix dsound.dll); Alt+Enter toggles too
  python play.py cnc_play               # without cmfix: starts fullscreen, Alt+Enter only
  python play.py cnc_window             # starts as a 2400x1800 window instead
  python play.py cmp_F_omniscale        # any _tools\\wrappers\\<profile>.json
  python play.py --stock                # remove the wrapper, run cm0102_GDI.exe windowed as before

Without cmfix, the in-game Screen Mode must stay Full (in-game "Windowed" makes the game ask
for a desktop-sized canvas and draw 800x600 into its corner), so play.py resets it to Full;
with cmfix (profiles with "keep_screen_mode") the in-game switch works and is left alone.

Installs the profile, sets Screen Mode = Full (so the game asks for 800x600 and the wrapper
scales it; the real display mode never changes), and starts cm0102.exe through game.py so the
working directory stays the dev folder. The wrapper stays installed until
`python wrapper.py uninstall` or another `play.py` call.
Do not start cm0102.exe by double-click: it would chdir into C:\\Games\\CM0102 (registry Location).
"""
import argparse

import cfg
import game
import wrapper


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", nargs="?", default="cnc_play_fix")
    ap.add_argument("--stock", action="store_true")
    a = ap.parse_args()
    if game.running_pid():
        game.kill()
    if a.stock:
        wrapper.uninstall()
        game.launch(exe="cm0102_GDI.exe", args="-windowed")
        return
    prof = wrapper.install(a.profile)
    if not prof.get("keep_screen_mode"):
        cfg.set_screen("full")
    game.launch(exe="cm0102.exe", args="")
    print("running; quit from the game menu (Exit Game) or: python game.py kill")


if __name__ == "__main__":
    main()
