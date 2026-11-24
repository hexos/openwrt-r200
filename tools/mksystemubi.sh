#!/usr/bin/env bash
# mksystemubi.sh —— 把 OpenWrt rootfs 目录做成 R200 能直接挂载的 UBI 镜像。
#
#   ./tools/mksystemubi.sh <rootfs目录> <输出文件> [分区字节数]
#
# 关键参数全部取自 config/device.env（由 tools/fwdump.py 从原厂固件实测得到）：
#   page 4 KiB / block 256 KiB / vid_hdr_offset 4 KiB / data_offset 8 KiB
#   => LEB = 262144 - 8192 = 253952
# 卷名固定为 rootfs —— 原厂引导程序按名字挂载根分区，名字错了会 kernel panic。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$HERE")"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/device.env"

ROOTFS="${1:?用法: mksystemubi.sh <rootfs目录> <输出文件> [分区字节数]}"
OUT="${2:?用法: mksystemubi.sh <rootfs目录> <输出文件> [分区字节数]}"
PART_BYTES="${3:-$(( PART_system_sectors * SECTOR_SIZE ))}"

MKFS_UBIFS="${MKFS_UBIFS:-mkfs.ubifs}"
UBINIZE="${UBINIZE:-ubinize}"
COMPR="${UBIFS_COMPR:-zlib}"
# UBIFS 超级块里的 max_leb_cnt 必须 >= 卷**最终**的 LEB 数。
# 卷用了 autoresize，挂载时会从镜像里的 30 个 LEB 长到整个 system 分区的 1033 个，
# 所以这里按"分区全部块数"给（1035），留足余量，避免 UBIFS 报
# "superblock max_leb_cnt less than LEB count" 而挂不上（这只是在超级块里记个数，不占空间）。
MAX_LEB="${MAX_LEB:-$(( PART_BYTES / NAND_BLOCK_SIZE + 2 ))}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[*] rootfs : $ROOTFS"
echo "[*] 几何   : page=$NAND_PAGE_SIZE block=$NAND_BLOCK_SIZE leb=$UBI_LEB_SIZE 分区=$PART_BYTES 字节 ($((PART_BYTES/1024/1024)) MiB)"

if [ ! -x "$ROOTFS/sbin/init" ] && [ ! -f "$ROOTFS/etc/preinit" ]; then
  echo "!! 看起来不是 OpenWrt rootfs（缺 /sbin/init 与 /etc/preinit）" >&2
  exit 1
fi

# 空目录/无用的构建残留会让 UBIFS 变大，顺手清掉
rm -rf "$ROOTFS/tmp"/* "$ROOTFS/var/log"/* 2>/dev/null || true

echo "[*] mkfs.ubifs"
"$MKFS_UBIFS" \
  -r "$ROOTFS" \
  -m "$NAND_PAGE_SIZE" \
  -e "$UBI_LEB_SIZE" \
  -c "$MAX_LEB" \
  -x "$COMPR" \
  -o "$WORK/rootfs.ubifs"

cat > "$WORK/ubinize.cfg" <<EOF
[rootfs]
mode=ubi
image=$WORK/rootfs.ubifs
vol_id=0
vol_type=dynamic
vol_name=$UBI_VOL_0_NAME
vol_alignment=1
# autoresize：卷在 attach 时长到分区末尾，整个 system 分区都成为可写根。
# 原厂是"固定大小 + 预留"（rootfs 预留 434 个 LEB，实际用 293 个，后面的空间空着），
# 我们不需要预留，让根分区吃满整个 system 分区更实用。
vol_flags=autoresize
EOF

echo "[*] ubinize"
# -s 必须等于 NAND 子页大小（4 KiB），ubinize 才会推出 vid_hdr_offset=4096 / data_offset=8192
"$UBINIZE" -o "$WORK/system.ubi" \
  -m "$NAND_PAGE_SIZE" \
  -p "$NAND_BLOCK_SIZE" \
  -s "$NAND_PAGE_SIZE" \
  -O "$NAND_PAGE_SIZE" \
  "$WORK/ubinize.cfg"

SIZE=$(stat -c %s "$WORK/system.ubi")
echo "[*] UBI 镜像 $SIZE 字节"

if [ "$SIZE" -gt "$PART_BYTES" ]; then
  echo "!! UBI($SIZE) 超过 system 分区($PART_BYTES)，请精简 rootfs 或换更小的软件集" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
cp "$WORK/system.ubi" "$OUT"

# PAD_IMAGE=yes（默认）：用 0xFF 补齐到整个 system 分区，写入长度固定、便于逐字节核对。
# PAD_IMAGE=no          ：保留 UBI 自然长度（体积小得多，EFlash 工具写多少算多少，
#                         分区剩余部分本来就是 0xFF 擦除态）。
if [ "${PAD_IMAGE:-yes}" = "yes" ]; then
  python3 - "$OUT" "$PART_BYTES" <<'PY'
import os
import sys

path, size = sys.argv[1], int(sys.argv[2])
cur = os.path.getsize(path)
if cur < size:
    with open(path, "ab") as f:
        f.write(b"\xff" * (size - cur))
    print(f"[*] 已补齐 {size - cur} 字节 -> {size}")
PY
else
  echo "[*] PAD_IMAGE=no，保留自然大小 $SIZE 字节"
fi

echo "[+] 完成: $OUT ($(stat -c %s "$OUT") 字节)"
