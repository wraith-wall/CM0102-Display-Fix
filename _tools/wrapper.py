"""Install / remove a DirectDraw wrapper (e.g. cnc-ddraw) in the dev game folder, by profile.

  python wrapper.py install PROFILE      # profiles: _tools\\wrappers\\*.json
  python wrapper.py uninstall
  python wrapper.py status
  python wrapper.py ini PROFILE          # print the generated ddraw.ini [ddraw] overrides

Profile JSON:
  {"base": "_downloads/cnc-ddraw-v7.1.0.0/extracted",   // relative to the dev folder
   "files": ["ddraw.dll", "Shaders"],                    // copied as-is (dirs recursively)
   "ini": {"file": "ddraw.ini", "set": {"windowed": "true", ...}},  // copy of base ini with [ddraw] keys overridden
   "extra": [{"src": "_tools/cmfix/build/dsound.dll", "dst": "dsound.dll"}],  // more files, relative to the dev folder
   "cleanup": ["cmfix.log"],                             // files the wrapper creates at runtime; removed on uninstall
   "keep_screen_mode": true,                             // play.py: don't force Screen Mode = Full
   "extends": "other_profile"}                           // optional: inherit + override "set"

Everything installed is recorded in _logs\\wrapper_installed.json (path + sha256), and
uninstall removes exactly those files. A file that changed since install (e.g. the
wrapper rewrote its ini) is still removed, but reported.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys

import common as C

PROFILES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wrappers")
MANIFEST = os.path.join(C.LOGS, "wrapper_installed.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_profile(name):
    with open(os.path.join(PROFILES, name + ".json"), encoding="utf-8") as f:
        p = json.load(f)
    if "extends" in p:
        parent = load_profile(p["extends"])
        merged_set = dict(parent.get("ini", {}).get("set", {}))
        merged_set.update(p.get("ini", {}).get("set", {}))
        parent.update({k: v for k, v in p.items() if k not in ("ini", "extends")})
        parent["ini"] = dict(parent.get("ini", {}), set=merged_set)
        p = parent
    p["name"] = name
    return p


def render_ini(text, overrides, section="ddraw"):
    """Set key=value inside [section] of an ini text, appending keys that are missing."""
    lines = text.splitlines()
    out, in_sec, seen = [], False, set()
    for line in lines:
        m = re.match(r"\s*\[([^\]]+)\]", line)
        if m:
            if in_sec:
                out.extend(f"{k}={v}" for k, v in overrides.items() if k not in seen)
                seen.update(overrides)
            in_sec = m.group(1).lower() == section.lower()
            out.append(line)
            continue
        km = re.match(r"\s*([A-Za-z0-9_]+)\s*=", line)
        if in_sec and km and km.group(1) in overrides:
            k = km.group(1)
            if k in seen:
                continue
            out.append(f"{k}={overrides[k]}")
            seen.add(k)
            continue
        out.append(line)
    if in_sec:
        out.extend(f"{k}={v}" for k, v in overrides.items() if k not in seen)
    return "\n".join(out) + "\n"


def installed():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None


def install(name):
    if installed():
        uninstall()
    p = load_profile(name)
    base = os.path.join(C.ROOT, p["base"])
    records = []

    def put(src, dst):
        if os.path.exists(dst):
            raise SystemExit(f"refusing to overwrite existing {dst} (not installed by this tool)")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        records.append({"path": os.path.relpath(dst, C.GAME_DIR), "sha256": sha256(dst)})

    for item in p.get("files", []):
        src = os.path.join(base, item)
        if os.path.isdir(src):
            for dp, _, fns in os.walk(src):
                for fn in fns:
                    s = os.path.join(dp, fn)
                    put(s, os.path.join(C.GAME_DIR, os.path.relpath(s, base)))
        else:
            put(src, os.path.join(C.GAME_DIR, item))
    for e in p.get("extra", []):
        put(os.path.join(C.ROOT, e["src"]), os.path.join(C.GAME_DIR, e["dst"]))
    if "ini" in p:
        ini = p["ini"]
        with open(os.path.join(base, ini["file"]), encoding="utf-8", errors="replace") as f:
            text = render_ini(f.read(), ini.get("set", {}))
        dst = os.path.join(C.GAME_DIR, ini["file"])
        if os.path.exists(dst):
            raise SystemExit(f"refusing to overwrite existing {dst}")
        with open(dst, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
        records.append({"path": ini["file"], "sha256": sha256(dst)})
    dirs = sorted({os.path.dirname(r["path"]) for r in records if os.path.dirname(r["path"])}, key=len, reverse=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"profile": name, "files": records, "dirs": dirs, "set": p.get("ini", {}).get("set", {}),
                   "cleanup": p.get("cleanup", [])}, f, indent=1)
    C.log(f"wrapper installed: profile={name} files={len(records)} ini={p.get('ini', {}).get('set')}")
    return p


def uninstall():
    m = installed()
    if not m:
        C.log("wrapper: nothing installed")
        return
    changed = []
    for r in m["files"]:
        path = os.path.join(C.GAME_DIR, r["path"])
        if os.path.exists(path):
            if sha256(path) != r["sha256"]:
                changed.append(r["path"])
            os.remove(path)
    for c in m.get("cleanup", []):
        path = os.path.join(C.GAME_DIR, c)
        if os.path.exists(path):
            os.remove(path)
    for d in m["dirs"]:
        full = os.path.join(C.GAME_DIR, d)
        while d and os.path.isdir(full) and not os.listdir(full):
            os.rmdir(full)
            d = os.path.dirname(d)
            full = os.path.join(C.GAME_DIR, d)
    os.remove(MANIFEST)
    C.log(f"wrapper uninstalled: profile={m['profile']} removed={len(m['files'])}"
          + (f" (changed since install: {changed})" if changed else ""))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install"); i.add_argument("profile")
    sub.add_parser("uninstall")
    sub.add_parser("status")
    n = sub.add_parser("ini"); n.add_argument("profile")
    a = ap.parse_args()
    if a.cmd == "install":
        install(a.profile)
    elif a.cmd == "uninstall":
        uninstall()
    elif a.cmd == "status":
        m = installed()
        print(json.dumps(m, indent=1) if m else "no wrapper installed")
    else:
        print(json.dumps(load_profile(a.profile).get("ini", {}).get("set", {}), indent=1))


if __name__ == "__main__":
    main()
