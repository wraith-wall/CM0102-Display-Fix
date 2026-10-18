"""Build the player release zip of "CM0102 Display Fix" (cnc-ddraw + cmfix), unpacked next to cm0102.exe.

  python release.py              # builds cmfix, writes _release\\CM0102-Display-Fix-<version>.zip + .sha256.txt
  python release.py --no-build   # use _tools\\cmfix\\build\\dsound.dll as it is

ddraw.ini is cnc-ddraw's own ini with the [ddraw] keys of the wrapper profile
_tools\\wrappers\\release.json (the same profile `python play.py release` installs, so the
release can be tried in the dev folder exactly as players get it). The version comes from
CMFIX_VERSION in cmfix.c.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import common as C  # noqa: E402
import wrapper  # noqa: E402

CMFIX = os.path.join(TOOLS, "cmfix")
OUT = os.path.join(C.GAME_DIR, "_release")
SHADER = "Shaders/omniscale/omniscale.glsl"


def version():
    with open(os.path.join(CMFIX, "cmfix.c"), encoding="utf-8") as f:
        return re.search(r'#define CMFIX_VERSION\s+"([^"]+)"', f.read()).group(1)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    if not a.no_build:
        subprocess.run(["cmd", "/c", os.path.join(CMFIX, "build.bat")], check=True)
    ver = version()
    prof = wrapper.load_profile("release")
    base = os.path.join(C.GAME_DIR, prof["base"])
    ini = prof["ini"]
    if prof["ini"]["set"].get("shader") != SHADER:
        raise SystemExit(f"release profile shader is {prof['ini']['set'].get('shader')!r}, expected {SHADER!r}")
    with open(os.path.join(base, ini["file"]), encoding="utf-8") as f:
        ddraw_ini = wrapper.render_ini(f.read(), ini["set"]).replace("\n", "\r\n")
    with open(os.path.join(HERE, "README.txt"), encoding="utf-8") as f:
        readme = f.read().replace("{version}", ver).replace("\n", "\r\n")

    files = {  # path in the zip -> bytes
        "ddraw.dll": open(os.path.join(base, "ddraw.dll"), "rb").read(),
        "ddraw.ini": ddraw_ini.encode("utf-8"),
        SHADER: open(os.path.join(base, SHADER), "rb").read(),
        "dsound.dll": open(os.path.join(CMFIX, "build", "dsound.dll"), "rb").read(),
        "cmfix.ini": open(os.path.join(CMFIX, "cmfix.ini"), "rb").read(),
        "README-CM0102-Display-Fix.txt": readme.encode("utf-8"),
        "licenses/cnc-ddraw-LICENSE.txt": open(os.path.join(HERE, "licenses", "cnc-ddraw-LICENSE.txt"), "rb").read(),
    }
    os.makedirs(OUT, exist_ok=True)
    name = f"CM0102-Display-Fix-{ver}"
    zpath = os.path.join(OUT, name + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
    with open(zpath, "rb") as f:
        zsum = sha256(f.read())
    lines = [f"{sha256(data)}  {path}" for path, data in files.items()] + [f"{zsum}  {name}.zip"]
    with open(os.path.join(OUT, name + ".sha256.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    print(zpath)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
