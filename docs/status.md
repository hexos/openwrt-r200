# 进度与限制（请先读这一页）

这份工程是**分阶段**做的。下面每一行都标了"已验证 / 未验证"，不含糊。

## 阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| **0** | 把 OpenWrt 用户空间打包成 R200 能认的 UBI 根，并生成 9008 可刷的刷机包（GitHub Actions 全自动） | ✅ 工程完成，工具已在本地对原厂固件实测 |
| **1** | 刷进去能启动：`/` 可写、网口起、LuCI/SSH 能进 | ⚠️ 需要真机验证 |
| **2/3** | 5G 上行 + WiFi：走「厂商 glibc 栈」路线 | 🟡 **已实现打包与启动编排**（`docs/vendor-stack.md`），运行效果待真机 |
| **4** | 真·内核移植（自编内核 + 自编 kmod，脱离原厂内核） | ❌ 未开始；原料已备（`dts/`、`docs/kernel.config.orig`） |

> 阶段 2/3 的选择：用户已确定走「glibc / 厂商栈」路线，不再走"用 OpenWrt 自带 uqmi 重写拨号"。
> 细节、产物布局、上机后要调的几处，全部在 [`vendor-stack.md`](vendor-stack.md)。

## 为什么是这个路线

R200 的 rootfs 在 `system` 分区的 UBI 卷 `rootfs` 里，`boot` 分区是 **kernel-only、
无 initramfs** 的 Android boot image。也就是说：**引导链只要求根分区是个能挂的 UBI 卷，
并不关心里面是谁做的用户空间**。所以最短路是：

> 保留原厂 bootloader + 原厂 4.14 内核（它带着所有私有驱动：QPIC NAND、PMIC、
> Aquantia 转发、qcacld WiFi、MHI/QMI 基带），只把 `system` 分区的根文件系统换成 OpenWrt。

代价（都已在本工程里处理或标注）：

1. **不能自己编译 kmod**：内核不是 OpenWrt 编的，OpenWrt 的 kmod 包装进去也加载不了。
   → 于是选包时避开一切需要真加载 kmod 的功能。
2. **没有 overlayfs**（`CONFIG_OVERLAY_FS` 未开启）：
   → 不能用 squashfs+overlay 的"固件+配置分离"方案，`/` 就是一个可写 UBIFS，
     刷坏只能 9008 重刷。用 `overlay/lib/preinit/80_mount_root` 把原厂 cmdline 的 `ro` remount 成 `rw`。
3. **没有 nftables**（`CONFIG_NF_TABLES` 未开启）：
   → 用 iptables + `firewall`(fw3)，不能上 fw4/nftables。
4. **CPU 是 Cortex-A7（armv7，无整数除法扩展）**：
   → 必须用 `arm_cortex-a7_neon-vfpv4` 的包。用 `armsr/armv7`
     （`arm_cortex-a15_neon-vfpv4`）会在第一次整数除法时 SIGILL。`tools/fetch_rootfs.sh` 因此
     默认用 `sunxi/cortexa7` 自己打 rootfs。

## 已实现 ≠ 已验证：逐项说明

**已在本机（Windows）对原厂固件实测通过：**

* `tools/fwdump.py`：分区表、UBI 几何、卷表（`rootfs/usrfs/cachefs/systemrw/persist`，
  预留 434/13/459/25/25 个 LEB）、boot.img 头、内嵌内核配置全部正确解出，
  数字与 `docs/device.md` 一致。
* `tools/bootimg.py`：boot.img 解析 + 拆包 + 原样重打包，输出与原文件 **逐字节一致**
  （SHA256 相同）；`patch-cmdline` 也能正确改写 cmdline。
  注意原厂 boot.img 在 kernel 之后挂了 2048 字节校验/签名尾巴，工具会原样保留 ——
  但只要改了内容（cmdline 或内核），签名就不再匹配，开安全启动的机器会拒绝加载。
  本方案不碰 boot 分区，正是为了避开这一点。
* `tools/gen_edl_pkg.py`：生成的 `rawprogram_r200_openwrt.xml` 只有一条 erase + program，
  扇区地址取自原厂文件（`system` @ 64832，66240 扇区，last_sector 131071）。
* `tools/check_ubi.py`：拿原厂 `sdxprairie-sysfs.ubi` 跑通，几何/卷名/UBIFS 超级块
  （落在第 2 块）全部识别正确。
* `tools/mksystemubi.sh`：`-m 4096 -p 256KiB -s 4096`、`-e 253952` 与原厂镜像头完全对齐
  （这个只能到 Linux 上跑，Windows 侧无法验证 mkfs.ubifs/ubinize 的实际产物）。
* `tools/selftest.py`：一条命令 45 项检查全绿 —— 工具语法/`-h`、`device.env` 几何自洽、
  overlay 文件与 shebang、workflow 无 tab 且引用文件都存在、7 篇文档相对链接有效、
  以及对原厂固件的实测核对（32 个分区、system 分区坐标、UBI 几何、卷表、boot.img 往返一致性）。
* `tools/prepare_vendor_stack.py`：厂商栈按 ELF 依赖闭包抽取，**10 个二进制 / 53 个库 /
  172 个数据文件 / 34.5 MiB，63 个 ELF 依赖 0 缺失**；`--verify` 可对产物做封闭世界自检。
  这一步发现并修掉了一个真问题：`dialup` 依赖的 `libmeigbase.so`/`libmal.so`/`libmeigapps.so`
  没有 SONAME，第一版按 SONAME 落盘会漏掉它们。

**必须真机才能验证（我这边没有硬件）：**

* 内核能不能挂上我们做的 UBIFS 卷（理论上可以：卷名/几何都与原厂一致）。
* remount `rw` 是否成功（看 dmesg / `mount` 输出）。
* 网口名字（`eth0`? `swconfig`? DSA?）。`/etc/uci-defaults/99-r200` 里是自动探测 + 兜底，
  但具体板级走线得实测。
* 原厂内核里 Aquantia/QCA8337 那套转发是不是要求厂商特定用户态配合（`aqfwd`）。

## 阶段 2/3（5G + WiFi）：厂商 glibc 栈路线 —— 已落地打包与启动编排

原厂 5G 拨号（`netmgrd` / `dialup` / `qmi_ip_multiclient` / `rmnetcli`）和 WiFi
（qcacld `wlan.ko` + 厂商 `hostapd`）都是 **armhf glibc 2.28** 的私有栈，
OpenWrt 发布版是 musl，这是唯一真正的障碍。解决办法不是重写驱动，而是把厂商栈连运行时一起搬过来：

```bash
# 在有原厂固件的机器上（不需要硬件）
python3 tools/prepare_vendor_stack.py Maincode/target-files-ubi.zip -o vendor-stack
python3 tools/prepare_vendor_stack.py --verify -o vendor-stack     # 封闭世界自检
tar -czf vendor-stack.tar.gz -C vendor-stack .                     # 传到 Release / 网盘
```

抽取是**按 ELF 的 DT_NEEDED 传递闭包**精确搬（`tools/elfdeps.py`），不是整包拷贝；每个库按
SONAME 落盘，并带上厂商自带的 `ld-linux-armhf.so.3`。于是 musl 的 OpenWrt 也能跑厂商二进制：

```sh
/opt/vendor/lib/ld-linux-armhf.so.3 --library-path /opt/vendor/lib /opt/vendor/bin/netmgrd
```

本机实测（R200-6.00.7 原厂包）：**二进制 10 个 / 库 53 个 / 数据文件 172 个 / 34.5 MiB，
63 个 ELF 依赖全部闭合，自检 0 问题**。

构建流程（全自动）：`variant=musl-vendor`（默认，ImageBuilder musl + 厂商栈）
或 `variant=glibc-vendor`（源码编 glibc 版 OpenWrt + 厂商栈），两者叠加同一个 `overlay/`，
由 `overlay/etc/init.d/r200-vendor` 按 `uci r200.vendor.*` 决定加载哪些 .ko、拉起哪些守护进程；
`overlay/etc/uci-defaults/98-r200-vendor` 在首次启动时自动开关。**没叠厂商栈的镜像行为完全不变。**

设计细节、产物布局、上机后大概率要调的 5 处，见 [`vendor-stack.md`](vendor-stack.md)。

> 另外还保留一个更粗的抽取器 `tools/extract_vendor_userland.py`（按功能分组整目录拷贝），
> 适合"我想先看看原厂都带了什么"，不适合直接用来组固件 —— 组固件请用 `prepare_vendor_stack.py`。

## 第四阶段（真内核移植）需要什么

要彻底摆脱原厂内核，需要：

1. **CAF `msm-4.14` 的 arm（32 位）内核树** + `sdxprairie-mtp-cpe` DTS（可从公开的
   Qualcomm 派生树/其他模块厂 GPL 包里找），再加上本机 UBI/PMIC/网口/WiFi 的板级差异。
   → 板级差异这块**已经提前做完一部分**：`tools/bootimg.py dtbs` 从原厂 boot.img 里抽出
   25 个板级 dtb，`tools/dtb2dts.py` 把其中两个 sdxprairie 的（含 `LE-CPE` 变体）反编译成
   `dts/*.dts`（1459 / 1353 个节点）。板号、外设连接（QPIC NAND、PCIe 上的 QCA6391、
   EMAC+QCA8337 交换、IPA/RMNET、MHI/QRTR）都已整理进 `docs/device.md` 第 6 节。
2. **串口**（拆机焊 TTL，SDX55 的 debug UART 通常在测试点上），否则内核移植是盲调。
3. 把这些做成 OpenWrt 的 `target/linux/sdxprairie`，用 `LINUX_KERNEL_HASH` + 自己的 patch 集，
   才能真正编 kmod、用 squashfs+overlay、用 nftables。

至于当前内核的完整 `.config`，已经从 `vmlinux` 里抽出来放在 `docs/kernel.config.orig`
（133 KB，`CONFIG_IKCONFIG` 内嵌），移植时照着它对齐驱动选型最省事。

这不是几小时能完成的事。本工程先把"能刷、能回滚、能自动构建"这条地基打好。

## 回滚

本方案只写 `system` 分区，`bootloader` / `tz` / `modem` / `efs2`（IMEI 等校准数据）/
`recoveryfs` 全都不动。任何时候用**原厂整机 9008 包**重刷即可完全恢复。
所以：**动手前先确认手里有原厂包**（就是 `R200-6.00.7/` 这一份）。
