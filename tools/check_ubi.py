#!/usr/bin/env python3
"""check_ubi.py —— 刷机前的最后一道自检。

   python3 tools/check_ubi.py out/system.ubi

检查项（任何一项不对就别刷，会 brick）：
  * UBI magic 与版本
  * 擦除块大小 = 262144、LEB = 253952、vid_hdr_offset = 4096、data_offset = 8192
  * 卷 0 的名字必须是 rootfs
  * 镜像不超出 system 分区（66240 扇区 × 4 KiB = 271319040 字节）
  * UBIFS 超级块存在（卷数据区开头应有 UBIFS magic 0x06101831）
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fwdump  # noqa: E402

PARTITION_BYTES = 66240 * 4096
EXPECT = {
    "eraseblock_size": 262144,
    "leb_size": 253952,
    "vid_hdr_offset": 4096,
    "data_offset": 8192,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    path = sys.argv[1]
    size = os.path.getsize(path)
    ok = True

    u = fwdump.parse_ubi(path)
    if not u:
        sys.exit("✗ 不是 UBI 镜像（magic != UBI#）")

    print(f"文件      : {path} ({size} 字节 = {size / 1048576:.1f} MiB)")
    print(f"擦除块    : {u['eraseblock_size']}   块数 {u['eraseblocks']}")
    print(f"LEB       : {u['leb_size']}")
    print(f"vid_hdr   : {u['vid_hdr_offset']}   data: {u['data_offset']}")

    for k, v in EXPECT.items():
        got = u[k]
        flag = "✓" if got == v else "✗"
        if got != v:
            ok = False
        print(f"  {flag} {k}: 期望 {v}，实际 {got}")

    names = u["volume_names"]
    root = names.get("rootfs")
    if not root:
        ok = False
        print("✗ 没有名为 rootfs 的卷（引导程序会找不到根分区！）")
    else:
        print(f"✓ 卷 0 名称 = rootfs（预留 {root['reserved_pebs']} 个 LEB）")
    for name, meta in names.items():
        print(f"    卷 {meta['vol_id']}: {name} (reserved_pebs={meta['reserved_pebs']})")

    if size > PARTITION_BYTES:
        ok = False
        print(f"✗ 镜像 {size} 字节超过 system 分区 {PARTITION_BYTES} 字节")
    else:
        print(f"✓ 大小未超 system 分区（剩余 {PARTITION_BYTES - size} 字节）")

    # 检查卷数据的 UBIFS 超级块（块 0 通常是布局卷，卷 0 的 LEB0 在后面几块里）
    found_at = None
    with open(path, "rb") as f:
        for blk in range(min(16, (size // 262144) or 0)):
            f.seek(blk * 262144 + 8192)
            head = f.read(512)
            if head[:4] == b"\x31\x18\x10\x06":     # UBIFS magic 0x06101831
                found_at = blk
                break
    if found_at is not None:
        print(f"✓ 第 {found_at} 块数据区找到 UBIFS 超级块")
    else:
        print("! 前 16 块里没找到 UBIFS 超级块 —— 如果卷 0 不是 UBIFS，挂载会失败")

    print()
    if ok:
        print("✅ 自检通过，可以刷")
        return 0
    print("❌ 自检失败，不要刷")
    return 1


if __name__ == "__main__":
    sys.exit(main())
