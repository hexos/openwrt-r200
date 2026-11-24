# OpenWrt for 品速智联 R200（Qualcomm SDX55 / sdxprairie）

给 **品速智联 R200**（高通 SDX55，4×Cortex-A7，512 MiB NAND，9008/EDL 刷写）做的 OpenWrt 适配工程，
带 **GitHub Actions 全自动构建**，产物是 9008 模式下可直接刷写的镜像。

> 一句话原理：R200 的 `boot` 分区是"只有内核、没有 initramfs"的 Android boot image，
> 引导链只要求 `system` 分区里有一个名为 `rootfs` 的 UBI 卷能挂成 `/`。
> 所以本工程**保留原厂 bootloader 与 4.14 内核**（私有驱动都在里面），
> 只把根文件系统换成 OpenWrt —— 因此**只刷 system 一个分区**，随时可回滚原厂固件。

先读 [`docs/status.md`](docs/status.md)：那一页写清了"哪些已实现、哪些还需要真机验证、哪些还没做"。

## 现在能做到什么

- ✅ 分析原厂固件：分区表、NAND/UBI 几何、UBI 卷表、boot.img、内嵌内核配置（`tools/fwdump.py`）
- ✅ 从 boot.img 里抽出板级 dtb 并反编译成 DTS（`tools/bootimg.py dtbs` + `tools/dtb2dts.py`）
- ✅ 把任意 OpenWrt rootfs 打包成 R200 认的 UBI 镜像（`tools/mksystemubi.sh`）
- ✅ 生成只含 `system` 分区的 9008 刷机包（`tools/gen_edl_pkg.py`），并在刷前自检（`tools/check_ubi.py`）
- ✅ GitHub Actions 一键全自动构建 + 产出 Artifact（三种变体，见下）
- ✅ **把原厂 glibc 的 5G/WiFi/交换芯片用户态精确打包成可叠进 OpenWrt 的"厂商栈"**
  （`tools/prepare_vendor_stack.py` + `tools/elfdeps.py`，含依赖闭包自检）
- ⚠️ 能启动 / 网口 / LuCI 需要真机验证
- ⚠️ 厂商栈能不能真的跑起来（qcacld 初始化、QMI 通道）需要真机验证

## 三种构建变体

| variant | 内容 | 构建耗时 | 适合 |
|---|---|---|---|
| `musl-core` | 纯 OpenWrt（musl） | ~10 分钟 | 第一步：先确认能启动、能进 LuCI |
| `musl-vendor`（默认） | OpenWrt（musl）+ 原厂 glibc 厂商栈，用自带 `ld-linux-armhf.so.3` 运行厂商二进制 | ~10 分钟 | 要 5G / WiFi，又不想赌 glibc 构建 |
| `glibc-vendor` | 源码编 glibc 版 OpenWrt + 厂商栈，厂商二进制可直接执行 | 60~120 分钟 | 想要"最干净"的 libc 一致 |

厂商栈的设计与取舍见 [`docs/vendor-stack.md`](docs/vendor-stack.md)。

## 快速开始（云上）

1. **先在本机生成厂商栈**（只有 vendor 变体需要；不需要硬件）：

   ```powershell
   python tools\prepare_vendor_stack.py R200-6.00.7\Maincode\target-files-ubi.zip -o vendor-stack
   python tools\prepare_vendor_stack.py --verify -o vendor-stack
   tar -czf vendor-stack.tar.gz -C vendor-stack .
   ```
   把 `vendor-stack.tar.gz` 传到 GitHub Release（或任意能直链下载的地方），拿到 URL。

2. 把这个目录推到 GitHub。
3. `Actions` → `build-r200-openwrt` → `Run workflow`：
   - `variant`：先用 `musl-core` 确认能启动，再换 `musl-vendor`（填 `vendor_stack_url`）
   - 其它默认即可（`23.05.5` / `sunxi` / `cortexa7`，关键是 CPU 必须是 cortex-a7）
4. 跑完下载 Artifact `r200-openwrt-<variant>`，解压得到 `edl-r200/`。
5. 按 [`docs/flash.md`](docs/flash.md) 用 QFIL 刷 `rawprogram_r200_openwrt.xml`。

> 本仓库已经把厂商栈放在 Release 资产里（`vendor-stack-2026-11-24`），
> 跑 `musl-vendor` 时直接把它的下载地址填进 `vendor_stack_url` 即可。

## 快速开始（本地 Linux / WSL）

```bash
sudo apt install mtd-utils e2fsprogs xz-utils python3
./tools/build.sh                 # 或逐步执行下面的命令
```

逐步：

```bash
# 1) 取一份 arm_cortex-a7_neon-vfpv4/musl 的 OpenWrt rootfs
./tools/fetch_rootfs.sh out/rootfs-pkg
mkdir -p out/rootfs && tar -xzf out/rootfs-pkg/rootfs.tar.gz -C out/rootfs

# 2) 做成 R200 的 UBI 根（卷名 rootfs，几何与原厂完全一致）
./tools/mksystemubi.sh out/rootfs out/system.ubi

# 3) 生成 9008 刷机包（只含 system 分区）
python3 tools/gen_edl_pkg.py --fwdir firmware --out out/edl-r200 \
        --system out/system.ubi --no-pad
```

（`firmware/` 里放的是原厂 `rawprogram_nand_p4K_b256K.xml`，只用来读分区几何，
不含任何二进制。刷机时把 `firmware/` 换成本机原厂包目录也行。）

## 目录结构

```
openwrt-r200/
├─ .github/workflows/build.yml   # 全自动构建
├─ config/device.env             # 从原厂固件实测出来的所有关键参数
├─ docs/
│  ├─ device.md                  # 设备画像：分区表 / UBI 几何 / 卷表 / 内核配置 / 外设连接
│  ├─ vendor-stack.md            # ★ glibc 厂商栈路线：原理、产物、上机后要调什么
│  ├─ kernel.config.orig         # 从原厂 vmlinux 抽出的完整内核配置（阶段 4 必备）
│  ├─ flash.md                   # 9008 刷机与回滚
│  └─ status.md                  # 进度、限制、路线（先看这个）
├─ config/
│  ├─ device.env                 # 从原厂固件实测出来的所有关键参数
│  └─ openwrt-glibc.seed         # glibc 版 OpenWrt 的构建配置
├─ dts/                          # 从原厂 boot.img 反编译出来的板级设备树（阶段 4 资料）
├─ firmware/                     # 原厂 rawprogram/patch XML（只有文本，用来算扇区）
├─ overlay/                      # 打进 rootfs 的定制文件
│  ├─ lib/preinit/80_mount_root   # 没有 overlayfs，把 / remount 成 rw
│  ├─ etc/uci-defaults/99-r200    # 首次启动：主机名 / LAN 网口自动识别 / 服务
│  ├─ etc/uci-defaults/98-r200-vendor  # 检测到厂商栈就自动开启
│  ├─ etc/init.d/r200-vendor      # 加载厂商 .ko、拉起 netmgrd / 厂商 hostapd
│  ├─ usr/sbin/r200-vendor-exec   # 用厂商自带 ld.so 运行厂商二进制（musl 系统的关键）
│  ├─ etc/config/{network,r200}
│  └─ etc/banner
└─ tools/
   ├─ fwdump.py                   # 解析原厂固件 → device.json + kernel.config
   ├─ bootimg.py                  # boot.img 拆/合/改 cmdline/抽内嵌 dtb（逐字节可逆）
   ├─ dtb2dts.py                  # dtb → .dts（阶段 4）
   ├─ elfdeps.py                  # ELF32 依赖解析（厂商栈打包的基础）
   ├─ prepare_vendor_stack.py     # ★ 生成"厂商栈"（按依赖闭包精确搬）+ --verify 自检
   ├─ extract_vendor_userland.py  # 粗粒度按功能分组抽取（看原厂都带了什么时用）
   ├─ fetch_rootfs.sh             # 取 rootfs：ImageBuilder / 直连 URL / 本地 tar
   ├─ extract_rootfs.sh           # 从构建产物里掏出 rootfs 目录（暂存目录/loop/debugfs/unsquashfs）
   ├─ mksystemubi.sh              # mkfs.ubifs + ubinize → system.ubi
   ├─ check_ubi.py                # 刷机前自检：几何/卷名/大小/UBIFS 超级块
   ├─ gen_edl_pkg.py              # 生成 9008 刷机包
   ├─ selftest.py                 # ★ 仓库自检（CI 第一步就跑它，45 项检查）
   └─ build.sh                    # 一条命令跑完本地构建
```

## 自检

改完任何东西都跑一遍（不需要硬件、不需要 Linux 也能跑大部分）：

```bash
python3 tools/selftest.py --fw R200-6.00.7/Maincode
python3 tools/prepare_vendor_stack.py --verify -o vendor-stack
```

本仓库当前状态：**selftest 45 项全绿**，厂商栈依赖闭包 **63 个 ELF / 0 问题**。

## 必须知道的三个坑（都已处理）

1. **CPU 必须是 cortex-a7**：SDX55 的 A7 没有整数除法扩展，
   用 `arm_cortex-a15_neon-vfpv4` 的包（比如官方 `armsr/armv7` rootfs）会 SIGILL。
2. **原厂内核没有 overlayfs**：不能用 squashfs+overlay，`/` 必须是可写 UBIFS 单卷，
   原厂 cmdline 的 `ro` 由 `overlay/lib/preinit/80_mount_root` remount 成 `rw`。
3. **原厂内核没有 nftables**：只能用 iptables + `firewall`(fw3)，不要装 `firewall4`/`nftables`。

## 许可与声明

* 本仓库只包含脚本、配置与文本形式的原厂分区描述 XML，**不含任何 Qualcomm / 美格 / 品速的二进制**。
* 刷机有风险。改坏了用原厂整机 9008 包可完全恢复；请勿在没备份 `efs2` 的情况下乱动校准分区。
