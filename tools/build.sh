#!/usr/bin/env bash
# build.sh —— 本地一条命令跑完：rootfs → overlay(+厂商栈) → system.ubi → 9008 刷机包
#
#   ./tools/build.sh [输出目录，默认 out] [厂商栈目录，可选]
#
# 例：
#   ./tools/build.sh                                  # 纯 OpenWrt
#   ./tools/build.sh out vendor-stack                 # 叠加厂商栈（5G/WiFi）
#
# 需要：mtd-utils(mkfs.ubifs/ubinize)、curl、tar、xz、python3、e2fsprogs、
#       squashfs-tools（按需）、以及 sudo（loop 挂载时用）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$HERE")"
OUT="${1:-$ROOT_DIR/out}"
STACK="${2:-}"

mkdir -p "$OUT"
cd "$ROOT_DIR"

echo "=========== 1/5 取 OpenWrt rootfs ==========="
if [ -f "$OUT/rootfs.tar.gz" ]; then
	echo "复用已有 $OUT/rootfs.tar.gz"
	rm -rf "$OUT/rootfs"; mkdir -p "$OUT/rootfs"
	tar -xzf "$OUT/rootfs.tar.gz" -C "$OUT/rootfs"
else
	ROOTFS_TAR="" bash tools/fetch_rootfs.sh "$OUT/rootfs-pkg"
	cp -a "$OUT/rootfs-pkg/rootfs" "$OUT/rootfs"
fi

echo "=========== 2/5 规整 ==========="
test -x "$OUT/rootfs/sbin/init" -o -f "$OUT/rootfs/etc/preinit" || {
	echo "!! rootfs 不完整" >&2; exit 1; }
rm -rf "$OUT"/rootfs/lib/modules/5.15* 2>/dev/null || true
ls "$OUT"/rootfs/lib/ld-* 2>/dev/null || true
du -sh "$OUT/rootfs"

echo "=========== 3/5 叠加 overlay ==========="
cp -a overlay/. "$OUT/rootfs/"
chmod +x "$OUT/rootfs/usr/sbin/r200-vendor-exec" \
         "$OUT/rootfs/etc/init.d/r200-vendor" 2>/dev/null || true
chmod +x "$OUT/rootfs"/etc/uci-defaults/* 2>/dev/null || true

if [ -n "$STACK" ]; then
	echo "=========== 4/5 叠加厂商栈 ($STACK) ==========="
	[ -x "$STACK/opt/vendor/lib/ld-linux-armhf.so.3" ] || {
		echo "!! $STACK 里没有 opt/vendor/lib/ld-linux-armhf.so.3" >&2; exit 1; }
	python3 tools/prepare_vendor_stack.py --verify -o "$STACK"
	cp -a "$STACK/." "$OUT/rootfs/"
else
	echo "=========== 4/5 跳过厂商栈（纯 OpenWrt 变体）==========="
fi

echo "=========== 5/5 生成 UBI + 刷机包 ==========="
bash tools/mksystemubi.sh "$OUT/rootfs" "$OUT/system.ubi"
python3 tools/check_ubi.py "$OUT/system.ubi"
python3 tools/gen_edl_pkg.py --fwdir firmware --out "$OUT/edl-r200" \
	--system "$OUT/system.ubi" --no-pad

echo
echo "完成。刷机包：$OUT/edl-r200"
echo "把该目录里的文件放到原厂 9008 刷机包目录，用 QFIL 选 rawprogram_r200_openwrt.xml 刷入。"
