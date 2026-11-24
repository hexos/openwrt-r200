#!/usr/bin/env python3
"""gen_edl_pkg.py —— 生成 9008(EDL) 刷机包。

做的事：
  1. 从原厂 rawprogram XML 里抽出要刷的分区（默认只有 system），
     把文件名指向我们自己产出的镜像，其余分区一行都不碰 —— 也就是
     "只刷根文件系统分区"，bootloader / 基带 / 出厂参数全部保持原样。
  2. 镜像不足分区大小时用 0xFF 补齐（NAND 擦除后的状态），保证写入确定性。
  3. 生成 QFIL 用的 rawprogram/patch XML、Linux 下 bkerler/edl 的闪写脚本、
     校验和，以及回滚说明（回滚 = 把原厂镜像按同样方式刷回去）。

用法：
  python3 tools/gen_edl_pkg.py --fwdir <原厂固件目录> --out out/edl-r200 \
      --system out/system.ubi [--boot out/boot.img] [--webui out/m_webui.ubi]
"""
import argparse
import hashlib
import os
import shutil
import sys
import xml.etree.ElementTree as ET

SYSTEM_LABEL = "system"


def parse_rawprogram(path):
    root = ET.parse(path).getroot()
    entries = []
    for node in root:
        if node.tag != "program":
            continue
        entries.append({
            "label": node.get("label"),
            "start_sector": int(node.get("start_sector")),
            "num_sectors": int(node.get("num_partition_sectors")),
            "sector_size": int(node.get("SECTOR_SIZE_IN_BYTES")),
            "pages_per_block": int(node.get("PAGES_PER_BLOCK", 64)),
            "phys": node.get("physical_partition_number", "0"),
            "filename": node.get("filename") or "",
        })
    by_label = {}
    for e in entries:
        by_label.setdefault(e["label"], e)   # 同名分区取第一条
    return entries, by_label


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pad_to(path, size):
    cur = os.path.getsize(path)
    if cur == size:
        return 0
    if cur > size:
        sys.exit(f"镜像 {path} 有 {cur} 字节，超过分区大小 {size}，无解。"
                 f"请精简 OpenWrt rootfs 或改用 --no-pad 自行处理。")
    with open(path, "ab") as f:
        f.write(b"\xff" * (size - cur))
    return size - cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwdir", required=True, help="原厂固件目录（提供 rawprogram 与 firehose）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--system", help="自编译的 system.ubi")
    ap.add_argument("--boot", help="可选：替换用的 boot.img")
    ap.add_argument("--webui", help="可选：替换用的 m_webui.ubi")
    ap.add_argument("--rawprogram", default="rawprogram_nand_p4K_b256K.xml")
    ap.add_argument("--programmer", default="prog_firehose_sdx55.mbn")
    ap.add_argument("--no-pad", action="store_true")
    ap.add_argument("--vendor-patch", action="store_true",
                    help="沿用原厂 patch xml（只在本包同时刷 mibib 时才需要）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rp = os.path.join(args.fwdir, args.rawprogram)
    if not os.path.exists(rp):
        sys.exit(f"缺少 {rp}")
    entries, by_label = parse_rawprogram(rp)

    targets = []          # (label, 本地镜像路径, 目标文件名)
    if args.system:
        targets.append((SYSTEM_LABEL, args.system, "system.ubi"))
    if args.boot:
        targets.append(("boot", args.boot, "boot.img"))
    if args.webui:
        targets.append(("m_webui", args.webui, "m_webui.ubi"))
    if not targets:
        sys.exit("至少要给一个镜像（--system/--boot/--webui）")

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!-- OpenWrt for Pinsu R200 (SDX55 / sdxprairie) - selective flash -->',
             '<!-- Only the partitions listed below are touched. -->',
             '<!-- sbl / mibib / efs2 / modem / recoveryfs etc. are left untouched. -->',
             '<data>']
    sha_lines = []
    for label, src, dstname in targets:
        ent = by_label.get(label)
        if not ent:
            sys.exit(f"原厂 rawprogram 里没有分区 {label}")
        dst = os.path.join(args.out, dstname)
        shutil.copy2(src, dst)
        if not args.no_pad:
            padded = pad_to(dst, ent["num_sectors"] * ent["sector_size"])
            if padded:
                print(f"  {dstname}: 已用 0xFF 补齐 {padded} 字节到整分区大小")
        start = ent["start_sector"]
        last = start + ent["num_sectors"] - 1
        lines.append(f'  <erase PAGES_PER_BLOCK="{ent["pages_per_block"]}" '
                     f'SECTOR_SIZE_IN_BYTES="{ent["sector_size"]}" last_sector="{last}" '
                     f'num_partition_sectors="{ent["num_sectors"]}" start_sector="{start}"/>')
        lines.append(f'  <program PAGES_PER_BLOCK="{ent["pages_per_block"]}" '
                     f'SECTOR_SIZE_IN_BYTES="{ent["sector_size"]}" filename="{dstname}" '
                     f'label="{label}" last_sector="{last}" '
                     f'num_partition_sectors="{ent["num_sectors"]}" '
                     f'physical_partition_number="{ent["phys"]}" start_sector="{start}"/>')
        sha_lines.append(f"{sha256(dst)}  {dstname}")
        print(f"  {label:<10} @ 扇区 {start} (+{ent['num_sectors']}) <== {dstname}")
    lines.append("</data>")

    with open(os.path.join(args.out, "rawprogram_r200_openwrt.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # patch XML：原厂那份会去改 MIBIB（partition_complete_p4K_b256K.mbn），
    # 而我们不动 mibib，所以默认给一个空的 patсhes 文件，避免刷写工具去找不存在的分区表文件。
    if args.vendor_patch:
        vendor_patch = None
        for name in os.listdir(args.fwdir):
            if name.startswith("patch") and name.endswith(".xml"):
                vendor_patch = os.path.join(args.fwdir, name)
                break
        if vendor_patch:
            shutil.copy2(vendor_patch, os.path.join(args.out, "patch_r200.xml"))
        else:
            print("!! 未找到原厂 patch xml，改用空 patch", file=sys.stderr)
    if not args.vendor_patch or not os.path.exists(os.path.join(args.out, "patch_r200.xml")):
        with open(os.path.join(args.out, "patch_r200.xml"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" ?>\n<patches>\n</patches>\n')

    prog = os.path.join(args.fwdir, args.programmer)
    if os.path.exists(prog):
        shutil.copy2(prog, os.path.join(args.out, args.programmer))

    with open(os.path.join(args.out, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("\n".join(sha_lines) + "\n")

    with open(os.path.join(args.out, "flash_windows.txt"), "w", encoding="utf-8") as f:
        f.write(WIN_HELP.format(programmer=args.programmer))

    with open(os.path.join(args.out, "flash_linux.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(LINUX_HELP.format(programmer=args.programmer))
    os.chmod(os.path.join(args.out, "flash_linux.sh"), 0o755)

    print(f"\n刷机包已生成: {os.path.abspath(args.out)}")
    for name in sorted(os.listdir(args.out)):
        p = os.path.join(args.out, name)
        if os.path.isfile(p):
            print(f"   {name:<32}{os.path.getsize(p):>12} 字节")


WIN_HELP = """品速 R200 —— OpenWrt 分区刷写（Windows / QFIL）
====================================================
前提：设备进入高通 9008 (EDL) 模式（USB 设备 ID 05c6:9008）。

1. 安装 QFIL（Qualcomm Flash Image Loader，随 Qualcomm QPST 发布）。
2. QFIL 里选择：
     Programmer Path : {programmer}
     Raw Program     : rawprogram_r200_openwrt.xml
     Patch XML       : patch_r200.xml
   （"Flat Build" 模式）
3. 点 Download，等待 "Finish Download"。
4. 拔线、重新上电。首次启动大约 30~60 秒，之后浏览器访问 http://192.168.1.1 。

注意：
  * 本包只写 system 分区，bootloader / 基带 / 出厂校准数据都不动，
    出问题可以随时用原厂固件包整机刷回来。
  * 若 QFIL 报 "The file ... does not exist"，检查本目录里的文件名必须与
    rawprogram 里写的一致（不要改名）。
"""

LINUX_HELP = """#!/usr/bin/env bash
# 品速 R200 —— OpenWrt 分区刷写（Linux / bkerler/edl）
# 依赖：pip install edlclient  （或 git clone https://github.com/bkerler/edl）
set -e
cd "$(dirname "$0")"

echo "[*] 设备需已进入 9008 模式：lsusb 应能看到 05c6:9008"
read -r -p "继续？[y/N] " ok
[ "$ok" = "y" ] || exit 1

edl qfil rawprogram_r200_openwrt.xml patch_r200.xml \\
    --loader {programmer}

echo "[+] 完成，拔线重新上电。"
"""


if __name__ == "__main__":
    main()
