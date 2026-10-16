"""Static analysis of a PE file: header, imports, manifest, DirectX IIDs, display strings.
Usage: python pe_analyze.py <exe> [--out report.md]
"""
import argparse, re, sys, uuid, pefile

IIDS = {
    "IID_IDirectDraw":        "6C14DB80-A733-11CE-A521-0020AF0BE560",
    "IID_IDirectDraw2":       "B3A6F3E0-2B43-11CF-A2DE-00AA00B93356",
    "IID_IDirectDraw4":       "9C59509A-39BD-11D1-8C4A-00C04FD930C5",
    "IID_IDirectDraw7":       "15E65EC0-3B9C-11D2-B92F-00609797EA5B",
    "IID_IDirectDrawSurface": "6C14DB81-A733-11CE-A521-0020AF0BE560",
    "IID_IDirectDrawSurface2":"57805885-6EEC-11CF-9441-A82303C10E27",
    "IID_IDirectDrawSurface3":"DA044E00-69B2-11D0-A1D5-00AA00B8DFBB",
    "IID_IDirectDrawSurface4":"0B2B8630-AD35-11D0-8EA6-00609797EA5B",
    "IID_IDirectDrawSurface7":"06675A80-3B9B-11D2-B92F-00609797EA5B",
    "IID_IDirectDrawPalette": "6C14DB84-A733-11CE-A521-0020AF0BE560",
    "IID_IDirectDrawClipper": "6C14DB85-A733-11CE-A521-0020AF0BE560",
    "IID_IDirect3D":          "3BBA0080-2421-11CF-A31A-00AA00B93356",
    "IID_IDirect3D7":         "F5049E77-4861-11D2-A407-00A0C90629A8",
    "IID_IDirectInput":       "89521360-AA8A-11CF-BFC7-444553540000",
    "IID_IDirectSound":       "279AFA83-4981-11CE-A521-0020AF0BE560",
    "IID_IDirectSound8":      "C50A7E93-F395-4834-9EF6-7FA99DE50966",
}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("exe"); ap.add_argument("--out")
    a = ap.parse_args()
    pe = pefile.PE(a.exe)
    data = open(a.exe, "rb").read()
    L = []; p = L.append
    fh, oh = pe.FILE_HEADER, pe.OPTIONAL_HEADER
    p(f"## {a.exe}\n")
    p(f"- Machine: 0x{fh.Machine:x} ({'x86 / PE32' if fh.Machine == 0x14c else 'other'}), magic 0x{oh.Magic:x}")
    p(f"- Subsystem: {pefile.SUBSYSTEM_TYPE.get(oh.Subsystem, oh.Subsystem)}")
    p(f"- Linker: {oh.MajorLinkerVersion}.{oh.MinorLinkerVersion}; OS version {oh.MajorOperatingSystemVersion}.{oh.MinorOperatingSystemVersion}")
    import datetime
    p(f"- TimeDateStamp: {datetime.datetime.utcfromtimestamp(fh.TimeDateStamp)} UTC")
    p(f"- ImageBase 0x{oh.ImageBase:x}, DllCharacteristics 0x{oh.DllCharacteristics:x}")
    p(f"- Sections: " + ", ".join(f"{s.Name.rstrip(b'\0').decode(errors='replace')}({s.Misc_VirtualSize:#x})" for s in pe.sections))
    # manifest
    man = None
    if hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        types = [pefile.RESOURCE_TYPE.get(e.id, str(e.name)) for e in pe.DIRECTORY_ENTRY_RESOURCE.entries]
        p(f"- Resource types: {types}")
        for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if e.id == 24:
                d = e.directory.entries[0].directory.entries[0].data.struct
                man = pe.get_data(d.OffsetToData, d.Size).decode(errors="replace")
    p(f"- Manifest: {'present' if man else 'NONE'}")
    if man:
        p("```xml\n" + man + "\n```")
        p(f"- DPI awareness in manifest: {'dpiAware' in man or 'dpiAwareness' in man}")
    # imports
    p("\n### Imports")
    for imp in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        names = [ (i.name.decode() if i.name else f"ord{i.ordinal}") for i in imp.imports]
        p(f"- **{imp.dll.decode()}** ({len(names)}): " + ", ".join(sorted(names)))
    for d in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
        p(f"- (delay) **{d.dll.decode()}**: " + ", ".join(i.name.decode() for i in d.imports if i.name))
    # IIDs
    p("\n### COM IIDs found in binary")
    for name, g in IIDS.items():
        b = uuid.UUID(g).bytes_le
        offs = [m.start() for m in re.finditer(re.escape(b), data)]
        if offs: p(f"- {name}: at file offsets {[hex(o) for o in offs]}")
    # strings
    p("\n### Interesting strings")
    pat = re.compile(rb"[ -~]{4,}")
    kw = re.compile(rb"(?i)ddraw|directdraw|direct3d|d3d|dinput|dsound|windowed|fullscreen|full screen|\b\d{3,4}\s*x\s*\d{3,4}\b|resolution|display mode|\.cfg|registry|software\\|location|savegame|\.sav|GDI|screen mode|surface|blt|flip")
    seen = set()
    for m in pat.finditer(data):
        s = m.group()
        if kw.search(s) and s not in seen and len(s) < 200:
            seen.add(s); p(f"- `0x{m.start():x}` {s.decode(errors='replace')}")
    out = "\n".join(L)
    if a.out: open(a.out, "w", encoding="utf-8").write(out)
    else: sys.stdout.reconfigure(encoding="utf-8"); print(out)

if __name__ == "__main__": main()
