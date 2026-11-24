#!/usr/bin/env python3
"""elfdeps.py —— 极简 ELF32(ARM, little-endian) 依赖解析器。

用途：R200 原厂用户态是 glibc 的，要把某个二进制（netmgrd / hostapd / ssdk_sh …）
连同它真正需要的 .so 一起搬走，就必须先读它 ELF 的 DT_NEEDED 链。
不依赖 readelf / patchelf，纯标准库。

  python3 tools/elfdeps.py <二进制> --root <原厂rootfs目录或target-files.zip>
  python3 tools/elfdeps.py --self-test
"""
import argparse
import os
import struct
import sys

PT_LOAD, PT_DYNAMIC = 1, 2
DT_NEEDED, DT_STRTAB, DT_SONAME, DT_RPATH, DT_RUNPATH = 1, 5, 14, 15, 29


def parse_elf32(data):
    if len(data) < 52 or data[:4] != b"\x7fELF":
        return None
    ei_class, ei_data = data[4], data[5]
    if ei_class != 1:
        raise ValueError("不是 32 位 ELF")
    if ei_data != 1:
        raise ValueError("不是小端 ELF")
    (e_type, e_machine) = struct.unpack_from("<HH", data, 16)
    e_phoff, = struct.unpack_from("<I", data, 28)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
    loads, dynamic = [], None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = \
            struct.unpack_from("<8I", data, off)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
    info = {"type": e_type, "machine": e_machine,
            "machine_name": {40: "ARM"}.get(e_machine, hex(e_machine)),
            "needed": [], "soname": None, "rpath": [], "runpath": []}
    if not dynamic:
        return info          # 静态链接
    dyn_off, dyn_size = dynamic
    strtab_addr = None
    entries = []
    for i in range(dyn_size // 8):
        tag, val = struct.unpack_from("<iI", data, dyn_off + i * 8)
        if tag == 0:
            break
        entries.append((tag, val))
    for tag, val in entries:
        if tag == DT_STRTAB:
            strtab_addr = val
    if strtab_addr is None:
        return info

    def vaddr_to_off(addr):
        for vaddr, offset, filesz in loads:
            if vaddr <= addr < vaddr + filesz:
                return offset + (addr - vaddr)
        return None

    strtab_off = vaddr_to_off(strtab_addr)
    if strtab_off is None:
        return info

    def read_str(o):
        start = strtab_off + o
        end = data.index(b"\x00", start)
        return data[start:end].decode("utf-8", "replace")

    for tag, val in entries:
        if tag == DT_NEEDED:
            info["needed"].append(read_str(val))
        elif tag == DT_SONAME:
            info["soname"] = read_str(val)
        elif tag in (DT_RPATH, DT_RUNPATH):
            info["rpath"] += read_str(val).split(":")
    return info


class VendorFS:
    """在原厂 rootfs（目录或 target-files zip）里按库名查文件。"""

    SEARCH_DIRS = ["lib", "usr/lib", "usr/local/lib", "lib/arm-linux-gnueabihf",
                   "usr/lib/arm-linux-gnueabihf"]

    def __init__(self, root):
        self.zip = None
        self.root = None
        self.index = {}
        if root.lower().endswith(".zip"):
            import zipfile
            self.zip = zipfile.ZipFile(root)
            for n in self.zip.namelist():
                if n.endswith("/"):
                    continue
                rel = n[len("SYSTEM/"):] if n.startswith("SYSTEM/") else n
                self.index.setdefault(os.path.basename(rel), []).append(n)
        else:
            self.root = root
            for dirpath, _d, files in os.walk(root):
                for f in files:
                    rel = os.path.relpath(os.path.join(dirpath, f), root).replace("\\", "/")
                    self.index.setdefault(f, []).append(rel)

    def read(self, entry):
        if self.zip is not None:
            if entry not in self.zip.namelist():
                alt = "SYSTEM/" + entry.lstrip("/")
                if alt in self.zip.namelist():
                    entry = alt
            return self.zip.read(entry)
        return open(os.path.join(self.root, entry), "rb").read()

    def _link_target(self, entry):
        """若该条目是符号链接，返回链接指向的（相对 rootfs 的）路径，否则 None。"""
        if self.zip is not None:
            info = self.zip.getinfo(entry)
            mode = info.external_attr >> 16
            if not (mode & 0o170000) == 0o120000:      # S_ISLNK
                return None
            target = self.zip.read(entry).decode("utf-8", "replace")
            base_rel = entry[len("SYSTEM/"):] if entry.startswith("SYSTEM/") else entry
        else:
            full = os.path.join(self.root, entry)
            if not os.path.islink(full):
                return None
            target = os.readlink(full)
            base_rel = entry
        if target.startswith("/"):
            return target.lstrip("/")
        return os.path.normpath(os.path.join(os.path.dirname(base_rel), target)).replace("\\", "/")

    def _entry_for(self, rel):
        if self.zip is not None:
            return rel if rel.startswith("SYSTEM/") else "SYSTEM/" + rel
        return rel

    def resolve(self, entry, depth=0):
        """跟随符号链接，返回真正存放内容的归档路径（以及它对应的相对路径）。"""
        target = self._link_target(entry)
        if target is None or depth > 8:
            rel = entry[len("SYSTEM/"):] if entry.startswith("SYSTEM/") else entry
            return entry, rel
        nxt = self._entry_for(target)
        if self.zip is not None and nxt not in self.zip.namelist():
            hit = self.find(target)
            if not hit:
                rel = entry[len("SYSTEM/"):] if entry.startswith("SYSTEM/") else entry
                return entry, rel
            nxt = hit[0]
        elif self.zip is None and not os.path.exists(os.path.join(self.root, target)):
            hit = self.find(target)
            if not hit:
                rel = entry[len("SYSTEM/"):] if entry.startswith("SYSTEM/") else entry
                return entry, rel
            nxt = hit[0]
        return self.resolve(nxt, depth + 1)

    def find(self, name):
        """返回 (归档内路径, 相对路径) 或 None；自动跟随符号链接到真实文件。"""
        if name.startswith("/"):
            cand = name.lstrip("/")
            hits = self.index.get(os.path.basename(cand))
            for h in hits or []:
                rel = h[len("SYSTEM/"):] if h.startswith("SYSTEM/") else h
                if rel == cand:
                    return self.resolve(h)
            return None
        hits = self.index.get(name)
        if not hits:
            return None
        # 优先按标准库目录顺序
        for d in self.SEARCH_DIRS:
            for h in hits:
                rel = h[len("SYSTEM/"):] if h.startswith("SYSTEM/") else h
                if os.path.dirname(rel) == d:
                    return self.resolve(h)
        return self.resolve(hits[0])


def resolve_closure(fs, entry_rel, verbose=False):
    """从某个二进制出发，解析它的全部 DT_NEEDED 依赖（传递闭包）。

    返回 (seen, missing, aliases)：
      seen    : rel -> ELF 信息
      missing : [(谁, 缺哪个名字)]
      aliases : rel -> {它被别人引用的名字}（没有 SONAME 的 .so 靠这个确定落盘名字）
    """
    seen = {}
    aliases = {}
    queue = [entry_rel]
    missing = []
    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        entry = rel if fs.zip is None else (
            rel if rel.startswith("SYSTEM/") else "SYSTEM/" + rel)
        try:
            data = fs.read(entry)
            info = parse_elf32(data)
        except Exception as exc:                      # noqa: BLE001
            if verbose:
                print(f"    ! 跳过 {rel}: {exc}")
            continue
        if info is None:
            continue
        seen[rel] = info
        for need in info["needed"]:
            hit = fs.find(need)
            if not hit:
                missing.append((rel, need))
                continue
            aliases.setdefault(hit[1], set()).add(need)
            queue.append(hit[1])
    return seen, missing, aliases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", nargs="?")
    ap.add_argument("--root", default=".")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        # 自检：构造一个最小 ELF32 LE，验证解析器返回空依赖且不崩
        hdr = bytearray(52)
        hdr[:4] = b"\x7fELF"
        hdr[4] = 1          # 32 位
        hdr[5] = 1          # 小端
        struct.pack_into("<HH", hdr, 16, 2, 40)          # ET_EXEC / ARM
        struct.pack_into("<I", hdr, 28, 52)              # e_phoff
        struct.pack_into("<HH", hdr, 42, 32, 0)          # phentsize / phnum=0
        info = parse_elf32(bytes(hdr))
        assert info["machine_name"] == "ARM" and info["needed"] == []
        print("elfdeps self-test OK")
        return

    if not a.binary:
        sys.exit(__doc__)
    fs = VendorFS(a.root)
    hit = fs.find(a.binary) or fs.find("/" + a.binary)
    if not hit:
        sys.exit(f"在原厂 rootfs 里找不到 {a.binary}")
    seen, missing, _aliases = resolve_closure(fs, hit[1], verbose=True)
    print(f"\n{hit[1]} 依赖闭包：{len(seen)} 个 ELF")
    for rel in sorted(seen):
        info = seen[rel]
        print(f"  {rel:<48} soname={info['soname']}")
    if missing:
        print("\n缺失依赖（原厂镜像里也没有）：")
        for who, need in missing:
            print(f"  {who} -> {need}")


if __name__ == "__main__":
    main()
