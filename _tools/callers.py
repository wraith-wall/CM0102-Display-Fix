"""Find direct E8 calls to a VA in .text and disassemble a window after each.
Usage: python callers.py <exe> <hexVA> [--before N] [--after N]"""
import argparse, struct, sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
ap = argparse.ArgumentParser(); ap.add_argument("exe"); ap.add_argument("va")
ap.add_argument("--before", type=int, default=64); ap.add_argument("--after", type=int, default=600)
a = ap.parse_args(); sys.stdout.reconfigure(encoding="utf-8")
pe = pefile.PE(a.exe); base = pe.OPTIONAL_HEADER.ImageBase
t = next(s for s in pe.sections if s.Name.startswith(b".text")); code = t.get_data(); tva = base + t.VirtualAddress
tgt = int(a.va, 16); md = Cs(CS_ARCH_X86, CS_MODE_32)
for i in range(len(code) - 5):
    if code[i] == 0xE8 and tva + i + 5 + struct.unpack_from("<i", code, i + 1)[0] == tgt:
        print(f"--- call at {tva+i:#x}")
        s = max(0, i - a.before)
        for ins in md.disasm(code[s:i + a.after], tva + s):
            print(f"{'>>' if ins.address == tva+i else '  '} {ins.address:#x}: {ins.mnemonic} {ins.op_str}")
