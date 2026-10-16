"""Find call sites of imported functions and string references in a PE32, disassemble around them.
Usage: python xrefs.py <exe> --import DirectDrawCreate --string game.cfg [--before 40 --after 120]
"""
import argparse, struct, sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("exe")
    ap.add_argument("--import", dest="imps", action="append", default=[])
    ap.add_argument("--string", dest="strs", action="append", default=[])
    ap.add_argument("--va", action="append", default=[], help="hex VA to reference")
    ap.add_argument("--before", type=int, default=48); ap.add_argument("--after", type=int, default=160)
    ap.add_argument("--max", type=int, default=6)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    pe = pefile.PE(a.exe); base = pe.OPTIONAL_HEADER.ImageBase
    text = next(s for s in pe.sections if s.Name.startswith(b".text"))
    code = text.get_data(); tva = base + text.VirtualAddress
    data = open(a.exe, "rb").read()
    targets = {}
    for imp in pe.DIRECTORY_ENTRY_IMPORT:
        for i in imp.imports:
            if i.name and i.name.decode() in a.imps: targets[i.name.decode()] = i.address
    for s in a.strs:
        off = data.find(s.encode() + b"\0")
        if off >= 0: targets[s] = pe.get_rva_from_offset(off) + base
    for v in a.va: targets[v] = int(v, 16)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    for name, va in targets.items():
        needle = struct.pack("<I", va); pos = 0; n = 0
        print(f"===== {name} @ {va:#x}")
        while n < a.max:
            pos = code.find(needle, pos)
            if pos < 0: break
            start = max(0, pos - a.before)
            print(f"--- ref at {tva+pos:#x}")
            # resync: disassemble from start, print instructions
            for ins in md.disasm(code[start:pos + a.after], tva + start):
                mark = ">>" if ins.address <= tva+pos < ins.address+ins.size else "  "
                print(f"{mark} {ins.address:#x}: {ins.mnemonic} {ins.op_str}")
            pos += 4; n += 1
if __name__ == "__main__": main()
