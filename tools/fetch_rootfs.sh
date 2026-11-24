#!/usr/bin/env bash
# fetch_rootfs.sh —— 取得一份可用于 R200 的 OpenWrt rootfs 目录（展开好的）。
#
#   ./tools/fetch_rootfs.sh <输出目录>
#
# 环境变量：
#   ROOTFS_URL       直接给 rootfs.tar.gz 的下载地址（跳过构建）
#   ROOTFS_TAR       直接用本地的 rootfs tar 包
#   OPENWRT_RELEASE  默认 23.05.5
#   OPENWRT_TARGET   默认 sunxi        （必须 CPU_TYPE=cortex-a7）
#   OPENWRT_SUBTARGET 默认 cortexa7
#   OPENWRT_PROFILE  默认 friendlyarm_nanopi-neo（device_packages 为空，最干净）
#   PACKAGES         覆盖软件包列表
#
# 为什么不用 armsr/armv7 的官方 rootfs.tar.gz：它的包架构是 arm_cortex-a15_neon-vfpv4，
# gcc 会为 Cortex-A15 生成 ARMv7VE 才有的 SDIV/UDIV，而 SDX55 的 Cortex-A7 没有整数除法
# 扩展，跑到除法就 SIGILL。所以必须用 cortex-a7 的包。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$HERE")"
OUTDIR="${1:?用法: fetch_rootfs.sh <输出目录>}"
mkdir -p "$OUTDIR"
ROOTFS="$OUTDIR/rootfs"

if [ -n "${ROOTFS_TAR:-}" ]; then
	echo "[*] 使用本地 rootfs 包 $ROOTFS_TAR"
	rm -rf "$ROOTFS"; mkdir -p "$ROOTFS"
	tar -xzf "$ROOTFS_TAR" -C "$ROOTFS"
	echo "[+] $ROOTFS"; exit 0
fi

if [ -n "${ROOTFS_URL:-}" ]; then
	echo "[*] 下载 $ROOTFS_URL"
	curl -fsSL -o "$OUTDIR/rootfs.tar.gz" "$ROOTFS_URL"
	rm -rf "$ROOTFS"; mkdir -p "$ROOTFS"
	tar -xzf "$OUTDIR/rootfs.tar.gz" -C "$ROOTFS"
	echo "[+] $ROOTFS"; exit 0
fi

REL="${OPENWRT_RELEASE:-23.05.5}"
TGT="${OPENWRT_TARGET:-sunxi}"
SUB="${OPENWRT_SUBTARGET:-cortexa7}"
PROFILE="${OPENWRT_PROFILE:-friendlyarm_nanopi-neo}"
BASE="https://downloads.openwrt.org/releases/$REL/targets/$TGT/$SUB"

# 说明：原厂内核 CONFIG_NF_TABLES 未开启 → 不用 firewall4/nftables；
#       也不装任何"需要真加载"的 kmod。
DEFAULT_PACKAGES="base-files busybox ca-bundle dnsmasq dropbear firewall fstools \
iptables iptables-mod-conntrack-extra iptables-mod-extra iptables-mod-filter \
iptables-mod-nat-extra iptables-mod-ipopt libc libgcc libustream-mbedtls logd mtd \
netifd odhcpd-ipv6only opkg procd procd-seccomp uci uclient-fetch urandom-seed \
luci luci-ssl kmod block-mount"
PACKAGES="${PACKAGES:-$(echo $DEFAULT_PACKAGES)}"

echo "[*] 找 ImageBuilder"
IB=$(curl -fsSL "$BASE/sha256sums" | grep -o 'openwrt-imagebuilder-[^ *]*\.tar\.xz' | head -1)
[ -n "$IB" ] || { echo "!! $BASE 里没有 ImageBuilder" >&2; exit 1; }
echo "    $IB"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
curl -fsSL -o "$WORK/ib.tar.xz" "$BASE/$IB"
tar -xf "$WORK/ib.tar.xz" -C "$WORK"
IBDIR="$(find "$WORK" -maxdepth 1 -type d -name 'openwrt-imagebuilder-*' | head -1)"

echo "[*] 构建（profile=$PROFILE）"
make -C "$IBDIR" image PROFILE="$PROFILE" PACKAGES="$PACKAGES" -j"$(nproc)" 2>&1 | tail -30

rm -rf "$ROOTFS"
bash "$HERE/extract_rootfs.sh" "$IBDIR" "$ROOTFS"
tar -czf "$OUTDIR/rootfs.tar.gz" -C "$ROOTFS" .
echo "[+] rootfs: $ROOTFS ($(du -sh "$ROOTFS" | cut -f1))"
