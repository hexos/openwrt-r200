# 品速智联 R200 设备画像

> 本文所有数字都不是从网上抄的，而是用 `tools/fwdump.py` 从本地原厂固件包
> （`R200-6.00.7/Maincode/`）里逐字节解析出来的，可以直接复核。

## 1. 硬件

| 项目 | 型号 / 参数 | 依据 |
|---|---|---|
| 基带+应用处理器 | Qualcomm **SDX55**（平台代号 `sdxprairie`），4×Cortex-A7 1.5 GHz，**armv7 32 位** | boot.img 里是 ARM `zImage`（magic `0x016f2818`）；dtb `compatible = qcom,sdxprairie` |
| 内存 | 512 MiB | 拆机资料 |
| 存储 | 512 MiB NAND，页 4 KiB，块 256 KiB | `rawprogram_nand_p4K_b256K.xml`（131072 扇区 × 4 KiB） |
| 电源管理 | PMX55（PM8150/PM8150B 系列） | dtb `rpmh-regulator` 节点 |
| 射频 | SDR865 | 拆机资料 |
| WiFi | **QCA6391**（WiFi 6），走 PCIe | 内核 `CONFIG_CNSS2=y`、`CONFIG_CNSS_QCA6390=y` |
| 有线 | 3×GbE；厂商用 QCA8337 交换/PHY + Aquantia(`atlantic`) 转发加速 | 内核 `CONFIG_QCA8337_PHY=y`、`CONFIG_AQFWD_QCOM=y`、cmdline `atlantic_fwd.rx_ring_size=1024` |
| 刷机口 | USB 9008（EDL/firehose） | `prog_firehose_sdx55.mbn` |

## 2. 启动链

```
PBL(ROM) → sbl1 → mibib(分区表) → tz/tz_devcfg/aop/qhee(hyp)/abl → uefi → boot(Android boot image, 只有 kernel) → system 分区里的 UBI 根文件系统
```

要点：

* `boot` 分区里是 **Android boot image v0，`ramdisk_size = 0`** —— 没有 initramfs，
  内核直接挂 `system` 分区里的 UBI 卷当 `/`（卷名 `rootfs`）。
  这决定了：**替换根文件系统不需要碰 boot 分区**，这也是本工程"只刷 system"能成立的原因。
* boot.img 的 cmdline（原厂原文）：

  ```
  noinitrd ro rootwait androidboot.hardware=qcom msm_rtb.filter=0x237
  lpm_levels.sleep_disabled=1 firmware_class.path=/lib/firmware/updates
  service_locator.enable=1 net.ifnames=0 atlantic_fwd.rx_ring_size=1024 pci=pcie_bus_perf
  ```

  注意里面**没有 `root=`**：根设备由 bootloader 决定，卷名来自 UBI 卷表（`rootfs`）。
  另外 `ro` 意味着根分区以只读挂载 —— OpenWrt 必须在 preinit 里把它 remount 成 `rw`
  （见 `overlay/lib/preinit/80_mount_root`）。
* 内核镜像尾部被厂商拼了 25 个板级 dtb（apq8064…sdxprairie 都有），其中和本机相关的两个：

  | dtb | model |
  |---|---|
  | 0x6d1ad3 | `Qualcomm Technologies, Inc. SDXPRAIRIE MTP V2 (LE-CPE)` |
  | 0x735a31 | `Qualcomm Technologies, Inc. SDXPRAIRIE MTP V2` |

  所以这个固件用的是 Qualcomm CPE 参考板的 DTS 加美格（MeiG）自己的板级改动
  （`/etc/meig_apps_startup`、`/etc/qapi.conf` 可佐证是美格方案）。

## 3. 分区表（NAND，扇区 4 KiB，共 131072 扇区 = 512 MiB）

| 分区 | 起始扇区 | 扇区数 | 大小 | 原厂镜像 |
|---|---|---|---|---|
| `sbl` | 0 | 640 | 2.5 MiB | `sbl1.mbn` / `sbl1_only.mbn` |
| `mibib` | 640 | 640 | 2.5 MiB | `partition_complete_p4K_b256K.mbn` |
| `dynamic_nv` | 1280 | 640 | 2.5 MiB | – |
| `efs2` | 1920 | 2816 | 11.0 MiB | `efs.mbn` |
| `tz` | 4736 | 448 | 1.75 MiB | `tz.mbn` |
| `tz_devcfg` | 5184 | 256 | 1.0 MiB | `devcfg.mbn` |
| `ddr` | 5440 | 384 | 1.5 MiB | – |
| `apdp` | 5824 | 256 | 1.0 MiB | `apdp.mbn` |
| `multi_image` | 6080 | 256 | 1.0 MiB | `multi_image.mbn` |
| `aop` | 6336 | 256 | 1.0 MiB | `aop.mbn` |
| `qhee` | 6592 | 256 | 1.0 MiB | `hyp.mbn` |
| `abl` | 6848 | 256 | 1.0 MiB | `abl.elf` |
| `uefi` | 7104 | 640 | 2.5 MiB | `uefi.elf` |
| `toolsfv` | 7744 | 384 | 1.5 MiB | – |
| `loader_sti` | 8128 | 384 | 1.5 MiB | – |
| **`boot`** | **8512** | **2816** | **11.0 MiB** | `sdxprairie-boot.img` |
| `scrub` | 11328 | 256 | 1.0 MiB | – |
| `modem` | 11584 | 25152 | 98.2 MiB | `NON-HLOS.ubi` |
| `misc` | 36736 | 448 | 1.75 MiB | – |
| `devinfo` | 37184 | 384 | 1.5 MiB | – |
| `recovery` | 37568 | 2816 | 11.0 MiB | `recovery.img` |
| `fota` | 40384 | 448 | 1.75 MiB | – |
| `recoveryfs` | 40832 | 11776 | 46.0 MiB | `sdxprairie-recoveryfs.ubi` |
| `sec` | 52608 | 256 | 1.0 MiB | `sec.elf` |
| `IPA_FW` | 52864 | 192 | 768 KiB | `ipa_fws.elf` |
| `usb_qti` | 53056 | 256 | 1.0 MiB | – |
| `efs2_bak` | 53312 | 768 | 3.0 MiB | `efs.mbn` |
| `m_raw` | 54080 | 640 | 2.5 MiB | – |
| `m_data` | 54720 | 2688 | 10.5 MiB | – |
| `usrdata` | 57408 | 3712 | 14.5 MiB | – |
| `m_webui` | 61120 | 3712 | 14.5 MiB | `m_webui.ubi` |
| **`system`** | **64832** | **66240** | **258.75 MiB** | `sdxprairie-sysfs.ubi` |

`system` 分区是我们唯一要覆盖的分区，它从 **0x0FD40000** 开始，到 512 MiB 结束。

## 4. UBI 几何与卷表

原厂 `sdxprairie-sysfs.ubi` 的 EC/VID 头读出：

| 参数 | 值 | 对应的 ubinize 参数 |
|---|---|---|
| 页大小 (min I/O) | 4096 | `-m 4096` |
| 擦除块 | 262144 (256 KiB) | `-p 256KiB` |
| `vid_hdr_offset` | 4096 | `-s 4096`（子页大小） |
| `data_offset` | 8192 | 自动推出 |
| LEB 大小 | 253952 | `mkfs.ubifs -e 253952` |
| 镜像块数 | 308 | – |

卷表（128 条记录里有效的前 5 条，记录序号即卷号；"预留"是记录里的 `reserved_pebs`）：

| vol_id | 卷名 | 预留 LEB | 实际占用块 | 说明 |
|---|---|---|---|---|
| 0 | **`rootfs`** | 434 | 293 | 根文件系统（Yocto/systemd，约 74 MiB 数据） |
| 1 | `usrfs` | 13 | 13 | 持久化小卷 |
| 2 | `cachefs` | 459 | 0 | 只声明未使用 |
| 3 | `systemrw` | 25 | 0 | 只声明未使用 |
| 4 | `persist` | 25 | 0 | 只声明未使用 |

注意原厂是**固定大小**分配：`rootfs` 预留 434 个 LEB（≈107 MiB）只用了 293 个，
五个卷加起来预留 956 个 LEB，而 `system` 分区有 1035 个块 —— 也就是说原厂留了
约 150 MiB 的空闲未分配空间。我们的做法是给 `rootfs` 打上 `autoresize`，
让整个 `system` 分区都变成可写根（≈251 MiB），不用再预留。

**关键约束**：卷名必须是 `rootfs`。bootloader 按名字找根分区，改名 = kernel panic。

## 5. 原厂内核（决定 OpenWrt 适配边界）

`vmlinux` 里带 `CONFIG_IKCONFIG`，可直接抽出原厂 `.config`：
`python3 tools/fwdump.py R200-6.00.7/Maincode`（会写出 `kernel.config`）。

| 项目 | 值 | 对 OpenWrt 的影响 |
|---|---|---|
| 版本 | `4.14.206-perf`（CAF msm-4.14） | 用户空间只能当"外挂"，不能自己编 kmod |
| 架构 | `CONFIG_ARM=y`，无 LPAE，armv7 32 位 | 必须用 **arm_cortex-a7_neon-vfpv4** 的包（不能用 cortex-a15 的，会 SIGILL） |
| `CONFIG_OVERLAY_FS` | **not set** | 不能用 squashfs+overlayfs，也不能 ubifs+rootfs_data 叠加；根必须直接可写 |
| `CONFIG_NF_TABLES` | **not set** | 不能用 nftables / firewall4 / fw4；要用 iptables + firewall3 |
| `CONFIG_IP_NF_IPTABLES`, `XF_TABLES`, `IP_NF_TARGET_MASQUERADE`, `NF_CONNTRACK_IPV4` | `=y`（内建） | iptables 用户态可直接用，不需要装 kmod |
| `CONFIG_UBIFS_FS`, `CONFIG_MTD_UBI`, `CONFIG_MTD_MSM_QPIC_NAND` | `=y` | UBI/UBIFS 原生支持 |
| `CONFIG_DEVTMPFS`, `PROC_FS`, `SYSFS`, `TMPFS`, `CGROUPS`, `NET_NS`, `SECCOMP` | `=y` | procd / procd-ujail / procd-seccomp 可以跑 |
| `CONFIG_MODULES=y`, `MODVERSIONS=n`, `MODULE_SIG=n` | – | 原厂 .ko 在 `/usr/lib/modules/4.14.206-perf/`，与 OpenWrt 无关 |
| `CONFIG_CNSS2`, `CONFIG_CNSS_QCA6390` | `=y` | WiFi 是 **qcacld** 方案（`/etc/cld-hostapd-wlan*.sh`），不是 mac80211/ath11k |
| `CONFIG_QRTR`, `CONFIG_MHI_BUS`, `CONFIG_MHI_UCI`, `CONFIG_RMNET` | `=y` | 5G 拨号走 QMI over MHI，有 `/dev/mhi_uci` |
| `CONFIG_PPP`, `CONFIG_USB_NET_*` | 部分 `=y` | 注意 `CONFIG_VETH` / `QMI_WWAN` 未开启 |

## 6. 板级信息（从原厂 dtb 反编译得到）

原厂 boot.img 里内嵌了两个和本机有关的板级 dtb，已用 `tools/dtb2dts.py` 反编译成
`dts/sdxprairie-mtp-v2.dts`（1459 节点）和 `dts/sdxprairie-mtp-v2-le-cpe.dts`（1353 节点）。
其中 LE-CPE 那份的根属性是：

```dts
model      = "Qualcomm Technologies, Inc. SDXPRAIRIE MTP V2 (LE-CPE)";
compatible = "qcom,sdxprairie-mtp", "qcom,sdxprairie", "qcom,mtp";
qcom,msm-id   = <0x165 0x20000 0x170 0x20000 0x1a2 0x20000 0x204 0x20000>;
qcom,board-id = <0xb010008 0x0>;
```

从 DTS 里能直接读出这台机器的外设连接（这些都是后续适配要用的硬信息）：

| 功能 | DTS 节点 / compatible | 说明 |
|---|---|---|
| NAND | `nand@1b00000`, `qcom,msm-nand` | QPIC NAND 控制器 |
| WiFi | `qcom,cnss-qca6390@a0000000`, `qcom,cnss-qca6390`；挂在 `qcom,pcie@1c00000`（RC）下 | QCA6391 是 **PCIe 端点**，走 cnss2 |
| 有线 | `qcom,emac@00020000` + `qcom,ess-switch-qca83xx` | SDX55 内建 EMAC 接 **QCA8337 交换芯片**，3 个千兆口在后面 |
| 5G 数据面 | `qcom,ipa@01e00000`, `qcom,rmnet-ipa3`, SMMU AP/WLAN/UC | 蜂窝流量走 IPA/RMNET，不经 CPU |
| 基带通道 | `qcom,mhi_dev@1c03000`, `qcom,msm-mhi-dev-net`, `qcom,qrtr-mhi-dev` | MHI + QRTR，QMI 就走这条 |
| 高速口 | `qcom,pcie@40002000` (`qcom,pcie-ep`), `qcom,dwc-usb3-msm` | PCIe 端点模式 + USB3 DRD |
| 存储副路 | `sdhci@8804000` | 有 SD/eMMC 控制器（本机 NAND 启动，此路未用） |
| 电源 | `rpmh-vrm-regulator` / `rpmh-arc-regulator` 若干 | PM8150 系列 + PMX55 |

注意 `qcom,ess-switch-qca83xx`：LAN 口是**交换芯片**后面的端口，OpenWrt 里要么按
"单网口 + 交换机"处理（`swconfig`/DSA 取决于原厂驱动形态），要么干脆先只用
EMAC 本身的那个口。`overlay/etc/uci-defaults/99-r200` 的网口自动探测对这种情况
只能兜底，**真机确认后需要手工把 br-lan 的 ifname 改对**。

## 7. 原厂用户空间（只是参考）

`target-files-ubi.zip` 里的根文件系统是 Qualcomm CPE SDK 的 **Yocto + systemd**
（`os-release` 写着 `ID="mdm"`，`/sbin/init`、`udev`、`kmod`、`util-linux` 齐全），
不是 OpenWrt。里面有：

* `/usr/lib/modules/4.14.206-perf/`（含 `qcacld`、`stub_dlkm`、`shortcut-fe` 等）
* 5G 相关：`qmi_ip_multiclient`、`netmgrd`、`rmnetcli`、`dialup`、`meig_apps_startup`
* WiFi：`cld-hostapd-wlan{0,1,2}.sh`、`start_wlan_services.service`
* 厂商 Web UI 在独立分区 `m_webui`（15 MiB UBI），与本工程无关

**这些原厂二进制是 glibc 的**，OpenWrt 默认是 musl，互相不能直接混用 ——
这是"第一阶段 5G 拨号 / WiFi 还没打通"的根本原因，详见 `docs/status.md`。
