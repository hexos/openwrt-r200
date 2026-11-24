#!/usr/bin/env python3
"""fwdump.py —— 分析品速 R200（SDX55 / sdxprairie）原厂固件包。

用途：
  * 从厂商 9008 刷机包里读出分区表、NAND/UBI 几何、UBI 卷表、boot.img 信息；
  * 输出 device.json 与人类可读摘要，作为本工程其它脚本与文档的事实来源。

用法：
  python3 tools/fwdump.py <固件目录> [--json out.json]

<固件目录> 里需要有 rawprogram_nand_p4K_b256K.xml 与各 *.ubi / *.img/mbn。
只依赖 Python 3 标准库，Windows / Linux 均可运行。
"""
import argparse
import json
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET

EC_HDR = struct.Struct(">I B 3s Q I I I 32s I")           # ubi_ec_hdr, 64B
VID_HDR = struct.Struct(">I B B 2s I I 4s I I I I 4s Q 12s I")  # ubi_vid_hdr, 64B
UBI_VTBL_RECORD_SIZE = 172
UBI_LAYOUT_VOL_ID = 0x7FFFFFFE


def read(path, n=None):
    with open(path, "rb") as f:
        return f.read(n) if n else f.read()


def parse_rawprogram(path):
    """解析 Qualcomm rawprogram XML -> 分区列表（扇区为单位）。"""
    root = ET.parse(path).getroot()
    parts = {}
    for node in root:
        if node.tag != "program":
            continue
        label = node.get("label")
        entry = parts.setdefault(label, {
            "label": label,
            "start_sector": int(node.get("start_sector")),
            "num_sectors": int(node.get("num_partition_sectors")),
            "sector_size": int(node.get("SECTOR_SIZE_IN_BYTES")),
            "files": [],
        })
        fn = node.get("filename")
        if fn:
            entry["files"].append(fn)
    out = sorted(parts.values(), key=lambda p: p["start_sector"])
    for p in out:
        p["offset"] = p["start_sector"] * p["sector_size"]
        p["size"] = p["num_sectors"] * p["sector_size"]
    return out


def parse_ubi(path, name=None):
    """解析 UBI 镜像：几何参数 + 卷表（卷名/卷号/占用块数）。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(64)
    if head[:4] != b"UBI#":
        return None
    magic, ver, _pad, ec_total, vid_off, data_off, seq, _pad2, _crc = EC_HDR.unpack(head)

    with open(path, "rb") as f:
        blob = f.read()
    positions = []
    i = -1
    while True:
        i = blob.find(b"UBI#", i + 1)
        if i < 0:
            break
        positions.append(i)
    block = positions[1] - positions[0] if len(positions) > 1 else 0

    volumes = {}
    vol_names = {}
    if block:
        for blk in range(size // block):
            base = blk * block
            try:
                _m, _v, _p, _ec, v_off, _do, _sq, _p2, _c = EC_HDR.unpack(blob[base:base + 64])
                v = VID_HDR.unpack(blob[base + v_off:base + v_off + 64])
            except struct.error:
                continue
            vol_id = v[4]
            volumes.setdefault(vol_id, {"blocks": 0, "vol_type": v[2]})
            volumes[vol_id]["blocks"] += 1
            # 布局卷：128 条卷记录、每条 172 字节，记录序号即 vol_id。
            # 记录结构（大端）：reserved_pebs u32 | alignment u32 | data_pad u32 |
            #                  vol_type u8 | upd_marker u8 | name_len u16 | name[128]
            if vol_id > 0x7FFF0000 or blk == 0:
                # 卷表放在布局卷的 data 区（不是紧跟在 VID 头后面）
                start = base + _do if _do else base + v_off + 64
                data = blob[start:start + UBI_VTBL_RECORD_SIZE * 128]
                for n in range(128):
                    rec = data[n * UBI_VTBL_RECORD_SIZE:(n + 1) * UBI_VTBL_RECORD_SIZE]
                    if len(rec) < UBI_VTBL_RECORD_SIZE:
                        break
                    reserved_pebs, alignment, data_pad = struct.unpack_from(">III", rec, 0)
                    vol_type_u8, upd_marker, name_len = struct.unpack_from(">BBH", rec, 12)
                    raw_name = rec[16:16 + min(name_len, 128)]
                    if not raw_name or not re.fullmatch(rb"[\x20-\x7e]+", raw_name):
                        continue
                    vol_names[raw_name.decode()] = {
                        "vol_id": n,
                        "reserved_pebs": reserved_pebs,
                        "alignment": alignment,
                        "data_pad": data_pad,
                        "vol_type": vol_type_u8,
                        "upd_marker": upd_marker,
                    }
    return {
        "file": name or os.path.basename(path),
        "bytes": size,
        "ubi_version": ver,
        "eraseblock_size": block,
        "eraseblocks": size // block if block else 0,
        "vid_hdr_offset": vid_off,
        "data_offset": data_off,
        "leb_size": block - data_off,
        "image_seq": seq,
        "volumes": {str(k): v for k, v in sorted(volumes.items())},
        "volume_names": vol_names,
    }


def parse_bootimg(path, name=None):
    """解析 Android boot image v0（本机为 kernel-only，无 ramdisk）。"""
    with open(path, "rb") as f:
        hdr = f.read(4096)
    if hdr[:8] != b"ANDROID!":
        return None
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size,
     second_addr, tags_addr, page_size) = struct.unpack_from("<8I", hdr, 8)
    cmdline = hdr[64:576].split(b"\x00")[0].decode("utf-8", "replace")
    info = {
        "file": name or os.path.basename(path),
        "bytes": os.path.getsize(path),
        "kernel_size": kernel_size,
        "ramdisk_size": ramdisk_size,
        "second_size": second_size,
        "page_size": page_size,
        "kernel_addr": hex(kernel_addr),
        "tags_addr": hex(tags_addr),
        "cmdline": cmdline,
    }
    kern = None
    with open(path, "rb") as f:
        f.seek(page_size)
        kern = f.read(min(kernel_size, 0x400))
    if len(kern) > 0x30:
        zimage_magic = struct.unpack_from("<I", kern, 0x24)[0]
        info["kernel_fmt"] = "zImage(arm32)" if zimage_magic == 0x016F2818 else "unknown"
    # 内核镜像尾部追加的 DTB 集合（厂商把多个 dtb 拼在 Image 后面）
    return info


def parse_ikconfig(vmlinux_path):
    """从 vmlinux 里抽出 CONFIG_IKCONFIG 内嵌的内核配置。"""
    import gzip
    with open(vmlinux_path, "rb") as f:
        data = f.read()
    p = data.find(b"IKCFG_ST")
    q = data.find(b"IKCFG_ED", p if p >= 0 else 0)
    if p < 0 or q <= p:
        return None
    blob = data[p + 8:q]
    try:
        return gzip.decompress(blob).decode("utf-8", "replace")
    except OSError:
        return None


def find(prefix, *names):
    for n in names:
        for cand in (n, n.upper(), n.lower()):
            p = os.path.join(prefix, cand)
            if os.path.exists(p):
                return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fwdir", help="原厂固件目录（含 rawprogram_*.xml）")
    ap.add_argument("--json", default="device.json")
    args = ap.parse_args()

    d = args.fwdir
    if not os.path.isdir(d):
        sys.exit(f"目录不存在: {d}")

    rp = find(d, "rawprogram_nand_p4K_b256K.xml")
    info = {"firmware_dir": os.path.abspath(d)}

    if rp:
        parts = parse_rawprogram(rp)
        info["partitions"] = parts
        info["nand_total_bytes"] = sum(p["size"] for p in parts if p["label"] != "sbl")
        print("== 分区表 ==")
        print(f"{'分区':<14}{'起始扇区':>10}{'扇区数':>9}{'大小':>12}  文件")
        for p in parts:
            print(f"{p['label']:<14}{p['start_sector']:>10}{p['num_sectors']:>9}"
                  f"{p['size'] / 1048576:>10.1f}MiB  {' '.join(p['files']) or '-'}")
    else:
        print("!! 未找到 rawprogram XML", file=sys.stderr)

    info["ubi"] = {}
    for name in ("sdxprairie-sysfs.ubi", "sdxprairie-recoveryfs.ubi",
                 "m_webui.ubi", "NON-HLOS.ubi"):
        p = find(d, name)
        if not p:
            continue
        u = parse_ubi(p)
        if not u:
            continue
        info["ubi"][name] = u
        print(f"\n== {name} ==")
        print(f"   {u['bytes']} 字节, 块={u['eraseblock_size']}, LEB={u['leb_size']}, "
              f"vid_hdr={u['vid_hdr_offset']}, data={u['data_offset']}")
        for vid, v in u["volumes"].items():
            entry = next((k for k, x in u["volume_names"].items()
                          if int(vid) >= 0 and x["vol_id"] == int(vid)), None)
            pebs = u["volume_names"][entry]["reserved_pebs"] if entry else 0
            print(f"   卷 vol_id={vid:<3} 占用块={v['blocks']:<4} 预留块={pebs:<4} "
                  f"名称={entry if entry else '(layout/未命名)'}")

    b = find(d, "sdxprairie-boot.img")
    if b:
        bi = parse_bootimg(b)
        info["boot"] = bi
        print("\n== boot.img ==")
        print(f"   kernel={bi['kernel_size']} ramdisk={bi['ramdisk_size']} page={bi['page_size']} 格式={bi.get('kernel_fmt')}")
        print(f"   cmdline: {bi['cmdline']}")

    v = find(d, "vmlinux")
    if v:
        cfg = parse_ikconfig(v)
        if cfg:
            jsondir = os.path.dirname(os.path.abspath(args.json))
            os.makedirs(jsondir, exist_ok=True)
            out = os.path.join(jsondir, "kernel.config")
            with open(out, "w", encoding="utf-8") as f:
                f.write(cfg)
            keys = ["CONFIG_LOCALVERSION=", "CONFIG_OVERLAY_FS", "CONFIG_NF_TABLES",
                    "CONFIG_UBIFS_FS=", "CONFIG_MTD_MSM_QPIC_NAND", "CONFIG_CNSS2="]
            found = {}
            for line in cfg.splitlines():
                for k in keys:
                    if line.startswith(k):
                        found[k.rstrip("=")] = line
            info["kernel_config_extracted"] = out
            info["kernel_config_highlights"] = found
            print(f"\n== 内核配置已导出 -> {out} ==")
            for k, val in found.items():
                print("   ", val)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print(f"\n已写出 {args.json}")


if __name__ == "__main__":
    main()
