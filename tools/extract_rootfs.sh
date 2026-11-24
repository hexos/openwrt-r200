#!/usr/bin/env bash
# extract_rootfs.sh —— 从 ImageBuilder / OpenWrt 源码树的构建产物里掏出 rootfs 目录。
#
#   ./tools/extract_rootfs.sh <搜索根目录> <输出rootfs目录>
#
# 依次尝试（前一步成功就停）：
#   1. 暂存根目录  build_dir/target-*/root-*/         （ImageBuilder 与源码构建都有）
#   2. ext4 镜像   sudo losetup -Pf + mount 后整盘拷贝
#   3. ext4 镜像   debugfs rdump（无 loop 权限时）
#   4. squashfs    unsquashfs -d
set -euo pipefail

ROOT="${1:?用法: extract_rootfs.sh <搜索根目录> <输出目录>}"
OUT="${2:?用法: extract_rootfs.sh <搜索根目录> <输出目录>}"

ok() { [ -x "$1/sbin/init" ] || [ -f "$1/etc/preinit" ]; }

mkdir -p "$OUT"

# ---- 1) 暂存根目录 ----
STAGE="$(find "$ROOT" -maxdepth 4 -type d -name 'root-*' 2>/dev/null | while read -r d; do
	ok "$d" && { echo "$d"; break; }
done)"
if [ -n "${STAGE:-}" ]; then
	echo "[+] 用暂存根目录: $STAGE"
	tar -C "$STAGE" -cf - . | tar -C "$OUT" -xf -
	exit 0
fi
echo "[!] 没找到暂存根目录，改用镜像文件"

# ---- 2/3) ext4 镜像 ----
IMG="$(find "$ROOT" -type f \( -name '*ext4*.img' -o -name '*ext4*.img.gz' \) | head -1)"
if [ -n "$IMG" ]; then
	echo "[*] ext4 镜像: $IMG"
	case "$IMG" in
		*.gz) gunzip -kf "$IMG"; IMG="${IMG%.gz}" ;;
	esac
	OFF="$(python3 - "$IMG" <<'PY'
import struct, sys
d = open(sys.argv[1], 'rb').read(1024)
for i in range(4):
    e = d[446 + 16 * i: 446 + 16 * (i + 1)]
    if len(e) < 16 or e[4] == 0:
        continue
    print(struct.unpack_from('<I', e, 8)[0] * 512)
    break
else:
    print(0)
PY
)"
	echo "    分区偏移 $OFF"
	if command -v losetup >/dev/null 2>&1 && [ "$(id -u)" = "0" -o -n "${SUDO_OK:-}" ]; then
		LOOP="$(sudo losetup -Pf --show "$IMG")"
		MNT="$(mktemp -d)"
		sudo mount -o ro "${LOOP}p1" "$MNT" 2>/dev/null || sudo mount -o ro "$LOOP" "$MNT"
		sudo cp -a "$MNT/." "$OUT/"
		sudo umount "$MNT"
		sudo losetup -d "$LOOP"
		ok "$OUT" && { echo "[+] loop 挂载提取完成"; exit 0; }
	fi
	if command -v debugfs >/dev/null 2>&1; then
		debugfs -R "rdump / $OUT" "$IMG?offset=$OFF" >/dev/null 2>&1 || \
		debugfs -R "rdump / $OUT" "$IMG" >/dev/null 2>&1 || true
		ok "$OUT" && { echo "[+] debugfs 提取完成"; exit 0; }
	fi
fi

# ---- 4) squashfs ----
SQ="$(find "$ROOT" -type f -name '*squashfs*.img' | head -1)"
if [ -n "$SQ" ] && command -v unsquashfs >/dev/null 2>&1; then
	echo "[*] squashfs: $SQ"
	rm -rf "$OUT.tmp"
	unsquashfs -q -d "$OUT.tmp" "$SQ" >/dev/null
	cp -a "$OUT.tmp/." "$OUT/"
	rm -rf "$OUT.tmp"
	ok "$OUT" && { echo "[+] unsquashfs 提取完成"; exit 0; }
fi

echo "✗ 提取失败：$ROOT 里没有可用的 rootfs 产物" >&2
find "$ROOT" -maxdepth 4 -name 'root-*' -o -name '*.img*' 2>/dev/null | head -20 >&2
exit 1
