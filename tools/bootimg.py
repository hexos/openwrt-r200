#!/usr/bin/env python3
"""bootimg.py —— 处理 R200 的 Android boot image（v0，只有 kernel，无 ramdisk）。

本机 boot.img 的两个特点决定了这里的实现：
  * ramdisk_size = 0，内核直接从 UBI 根分区挂载 /（没有 initramfs）；
  * 内核镜像尾部被厂商拼了一串 DTB（多套板级 dtb），因此"不要动内核本体"。

常用操作：
  python3 tools/bootimg.py info    boot.img
  python3 tools/bootimg.py unpack  boot.img -o out/
  python3 tools/bootimg.py repack  boot.img --kernel out/kernel.bin -o new-boot.img
  python3 tools/bootimg.py patch-cmdline boot.img -o new-boot.img \\
          --set-cmdline "noinitrd rw rootwait ..."

round-trip（unpack 再 repack，不改任何字段）与原文件逐字节一致，已本地验证。
"""
import argparse
import os
import re
import struct
import sys

HDR_FMT = "<8I"          # magic 之后的 8 个 u32（kernel_size .. page_size）
# Android boot image v0 头的完整布局：
#   0   magic[8]
#   8   8 个 u32: kernel_size/kernel_addr/ramdisk_size/ramdisk_addr/
#                second_size/second_addr/tags_addr/page_size   -> 到 40
#   40  unused/hdr_version+os_version[8]   <-- 很容易漏掉的就是这 8 字节
#   48  name[16]
#   64  cmdline[512]
#   576 id[32]
#   608 extra_cmdline[1024]
HDR_TAIL_OFF = 40
NAME_OFF = 48
CMDLINE_OFF = 64
ID_OFF = 576
EXTRA_OFF = 608
HDR_SIZE = 1632


def round_up(n, page):
    return (n + page - 1) // page * page


class BootImage:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.raw = f.read()
        if self.raw[:8] != b"ANDROID!":
            sys.exit(f"{path} 不是 Android boot image")
        (self.kernel_size, self.kernel_addr, self.ramdisk_size, self.ramdisk_addr,
         self.second_size, self.second_addr, self.tags_addr, self.page_size) = \
            struct.unpack_from(HDR_FMT, self.raw, 8)
        self.extra_hdr = self.raw[HDR_TAIL_OFF:NAME_OFF]
        self.name = self.raw[NAME_OFF:NAME_OFF + 16]
        self.cmdline_raw = self.raw[CMDLINE_OFF:CMDLINE_OFF + 512]
        self.id_raw = self.raw[ID_OFF:ID_OFF + 32]
        self.extra_cmdline = self.raw[EXTRA_OFF:HDR_SIZE]
        off = self.page_size
        self.kernel = self.raw[off:off + self.kernel_size]
        off += round_up(self.kernel_size, self.page_size)
        self.ramdisk = self.raw[off:off + self.ramdisk_size]
        off += round_up(self.ramdisk_size, self.page_size)
        self.second = self.raw[off:off + self.second_size]
        off += round_up(self.second_size, self.page_size)
        # 原厂镜像在 kernel 之后还挂了 2048 字节的校验/签名尾巴
        # （看起来是 Qualcomm 对 boot 镜像的 hash + 签名）。
        # 原样保留，保证 unpack → repack 逐字节还原；但注意：一旦改动 cmdline
        # 或内核，这段签名就不再与内容匹配，开了安全启动的机器会拒绝加载。
        self.tail = self.raw[off:]

    @property
    def cmdline(self):
        return self.cmdline_raw.split(b"\x00")[0].decode("utf-8", "replace")

    def set_cmdline(self, text):
        data = text.encode() + b"\x00"
        if len(data) > 512:
            sys.exit(f"cmdline 过长（{len(data)} > 512）")
        self.cmdline_raw = data.ljust(512, b"\x00")

    def build(self):
        hdr = b"ANDROID!" + struct.pack(
            HDR_FMT, self.kernel_size, self.kernel_addr, self.ramdisk_size, self.ramdisk_addr,
            self.second_size, self.second_addr, self.tags_addr, self.page_size)
        hdr += self.extra_hdr + self.name + self.cmdline_raw + self.id_raw + self.extra_cmdline
        hdr = hdr.ljust(self.page_size, b"\x00")
        # 原厂头部 1632 字节之后到第一页末尾的填充区可能带有校验数据，原样保留
        if len(self.raw) >= self.page_size:
            pad_src = self.raw[HDR_SIZE:self.page_size]
            if len(pad_src) == self.page_size - HDR_SIZE:
                hdr = hdr[:HDR_SIZE] + pad_src
        body = self.kernel.ljust(round_up(len(self.kernel), self.page_size), b"\x00")
        body += self.ramdisk.ljust(round_up(len(self.ramdisk), self.page_size), b"\x00")
        body += self.second.ljust(round_up(len(self.second), self.page_size), b"\x00")
        return hdr + body + self.tail

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.build())


def scan_dtbs(blob):
    """在（可能被拼接过的）内核镜像里找出所有有效的 FDT/DTB 块。

    厂商把多个板级 dtb 直接拼在内核镜像尾部，这里按 header 自洽性筛掉误命中：
    magic 正确、总长合理、且 off_struct + size_struct == off_strings。
    """
    out = []
    i = -1
    while True:
        i = blob.find(b"\xd0\x0d\xfe\xed", i + 1)
        if i < 0:
            break
        if i + 40 > len(blob):
            break
        try:
            (magic, total, off_struct, off_strings, _off_rsv, _ver, _last,
             _cpu, size_strings, size_struct) = struct.unpack_from(">10I", blob, i)
        except struct.error:
            continue
        if magic != 0xD00DFEED or not (64 < total <= 4 << 20):
            continue
        if i + total > len(blob) or off_struct + size_struct != off_strings:
            continue
        out.append((i, blob[i:i + total]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["info", "unpack", "repack", "patch-cmdline", "dtbs"])
    ap.add_argument("image")
    ap.add_argument("-o", "--out", default=".")
    ap.add_argument("--kernel")
    ap.add_argument("--set-cmdline")
    a = ap.parse_args()

    img = BootImage(a.image)

    if a.action == "dtbs":
        os.makedirs(a.out, exist_ok=True)
        found = scan_dtbs(img.kernel)
        print(f"内核镜像里找到 {len(found)} 个 DTB")
        for n, (off, blob) in enumerate(found):
            p = os.path.join(a.out, f"dtb-{n:02d}-0x{off:x}.dtb")
            with open(p, "wb") as f:
                f.write(blob)
            # 板名一般出现在根节点的 model 属性值里，找一个像板名的可打印串
            model = ""
            for m in re.finditer(rb"[ -~]{12,}", blob):
                s = m.group().decode("ascii", "replace")
                if any(k in s for k in ("Qualcomm", "Technologies", "MTP", "CPE", "SBC")):
                    model = s
                    break
            print(f"  {os.path.basename(p):<26} {len(blob):>7} 字节  {model[:60]}")
        print("\n用 tools/dtb2dts.py <dtb> 可反编译成 .dts 查看节点")
        return

    if a.action == "info":
        print(f"文件          : {a.image} ({len(img.raw)} 字节)")
        print(f"page_size     : {img.page_size}")
        print(f"kernel        : {img.kernel_size} 字节 @ {img.kernel_addr:#x}")
        print(f"ramdisk       : {img.ramdisk_size} 字节")
        print(f"second        : {img.second_size} 字节")
        print(f"tags_addr     : {img.tags_addr:#x}")
        print(f"cmdline       : {img.cmdline}")
        magic = struct.unpack_from("<I", img.kernel, 0x24)[0] if len(img.kernel) > 0x30 else 0
        print(f"内核格式      : {'zImage(arm32)' if magic == 0x016f2818 else hex(magic)}")
        # 尾部追加的 DTB 数量
        n = img.kernel.count(b"\xd0\x0d\xfe\xed")
        print(f"内核内 FDT 魔数出现次数: {n}（厂商多板级 dtb 拼接，勿裁剪）")

    elif a.action == "unpack":
        os.makedirs(a.out, exist_ok=True)
        for name, blob in (("kernel.bin", img.kernel), ("ramdisk.bin", img.ramdisk),
                           ("second.bin", img.second)):
            if blob:
                with open(os.path.join(a.out, name), "wb") as f:
                    f.write(blob)
                print(f"写出 {name} ({len(blob)} 字节)")
        with open(os.path.join(a.out, "cmdline.txt"), "w", encoding="utf-8") as f:
            f.write(img.cmdline + "\n")

    elif a.action == "repack":
        if a.kernel:
            with open(a.kernel, "rb") as f:
                img.kernel = f.read()
            img.kernel_size = len(img.kernel)
        if a.set_cmdline:
            img.set_cmdline(a.set_cmdline)
        img.save(a.out)
        print(f"写出 {a.out} ({os.path.getsize(a.out)} 字节)")

    elif a.action == "patch-cmdline":
        if not a.set_cmdline:
            sys.exit("需要 --set-cmdline")
        img.set_cmdline(a.set_cmdline)
        img.save(a.out)
        print(f"cmdline -> {img.cmdline}")
        print(f"写出 {a.out} ({os.path.getsize(a.out)} 字节)")


if __name__ == "__main__":
    main()
