# 厂商栈（glibc）路线：把原厂的 5G / WiFi / 交换芯片用户态塞进 OpenWrt

这是本工程当前的主攻方向（用户选定的「glibc 版 OpenWrt + 厂商栈整体塞进去」）。
一句话：**不重写驱动，而是把原厂那套 armhf glibc 2.28 的用户态连同它的运行时一起搬过来。**

## 1. 为什么会有「musl / glibc」这个坎

| 事实 | 证据 |
|---|---|
| R200 原厂用户态是 **glibc 2.28**（armhf） | 镜像里是 `lib/ld-2.28.so`、`lib/libc-2.28.so`、`lib/ld-linux-armhf.so.3` |
| 5G 拨号是私有 glibc 栈 | `netmgrd`（31 个 ELF 依赖）、`dialup`、`qmi_ip_multiclient`、`rmnetcli` + `libqmi_*` |
| WiFi 是 qcacld 私有栈 | `wlan.ko`(6.8 MiB) + 厂商 `hostapd`/`wpa_supplicant` + `libwpa_supplicant_8_lib` + `/lib/firmware/wlan/qca_cld` |
| 有线是 QCA8337 交换芯片 | `qca-ssdk.ko` + `ssdk_sh`（不是 OpenWrt 的 DSA/swconfig 那套） |
| OpenWrt 发布版是 musl | 官方 rootfs 都是 musl，`/lib/ld-musl-armhf.so.1` |

musl 和 glibc 不能塞进同一个进程，但**不同进程可以各用各的 libc**。
所以有两种落地方式，本工程两种都支持：

### 方式 A：`musl-vendor`（默认，推荐先试）

OpenWrt 用官方 musl 构建，厂商二进制通过**厂商自带的动态加载器**运行：

```sh
/opt/vendor/lib/ld-linux-armhf.so.3 --library-path /opt/vendor/lib /opt/vendor/bin/netmgrd
```

这就是 `overlay/usr/sbin/r200-vendor-exec` 干的事。好处是：

* 不用从源码编 glibc 版 OpenWrt（省 1~2 小时构建时间、也不用赌目标是否支持 glibc）；
* 厂商进程与 OpenWrt 进程彻底隔离，谁的 libc 出问题都不会互相污染；
* 想回退就把 `/opt/vendor` 删掉，本质上是个"plugins 目录"。

### 方式 B：`glibc-vendor`（"根正苗红"，但更重）

用 `config/openwrt-glibc.seed`（`CONFIG_USE_GLIBC=y`）从源码编一份 glibc 版 OpenWrt，
厂商二进制就可以直接 `exec`，不需要 `r200-vendor-exec` 的加载器包装。
`r200-vendor-exec` 会自动检测系统是否为 glibc 并走直接执行分支，所以**同一套 overlay 两种变体通用**。

## 2. 怎么生成厂商栈

在**有原厂固件包的机器上**跑一次（不需要硬件）：

```bash
python3 tools/prepare_vendor_stack.py R200-6.00.7/Maincode/target-files-ubi.zip -o vendor-stack
python3 tools/prepare_vendor_stack.py --verify -o vendor-stack     # 封闭世界自检
tar -czf vendor-stack.tar.gz -C vendor-stack .
```

抽取逻辑不是"整包拷贝"，而是**按 ELF 的 DT_NEEDED 传递闭包**精确搬（`tools/elfdeps.py`）：

* 入口二进制：`netmgrd`、`dialup`、`qmi_ip_multiclient`、`rmnetcli`（5G），
  `hostapd`、`wpa_supplicant`、`hostapd_cli`、`wpa_cli`、`wlan_services`（WiFi），
  `ssdk_sh`（交换芯片）
* 每个 `.so` 按 **SONAME** 命名落盘（厂商有些库没写 SONAME，就用"别人引用它的名字"）
* 连带 glibc 运行时 `ld-linux-armhf.so.3`
* 内核模块 `lib/modules/4.14.206-perf/**`（qcacld、qca-ssdk 等）
* 固件 `lib/firmware/{wlan/qca_cld,ipa*}`（含板级校准 `wlan_mac.bin`）
* 配置 `etc/{misc/wifi,data,qapi.conf,qmi_ip_cfg.xml,cld-hostapd-wlan*.sh}`

本机实测结果（R200-6.00.7）：

```
二进制 10 个, 库 53 个, 数据文件 172 个, 合计 34.5 MiB
检查了 63 个 ELF，问题 0 条
✅ 自检通过
```

## 3. 产物布局（直接叠加到 rootfs 根上）

```
opt/vendor/bin/{netmgrd,dialup,qmi_ip_multiclient,rmnetcli,hostapd,wpa_supplicant,ssdk_sh,...}
opt/vendor/lib/{ld-linux-armhf.so.3,libc.so.6,libqmi_cci.so.1,...}   # 53 个
lib/modules/4.14.206-perf/{extra/wlan.ko,extra/wlan-mag.ko,kernel/drivers/qca-ssdk-nohnat/qca-ssdk.ko}
lib/firmware/wlan/qca_cld/{WCNSS_qcom_cfg.ini,wlan_mac.bin}
lib/firmware/ipa*, etc/{misc/wifi,data,qapi.conf,...}
STACK.json / MANIFEST.txt                                            # 来源与校验和
```

叠加后由 `overlay/etc/init.d/r200-vendor` 负责启动：

1. 按 `uci r200.vendor.{wifi,switch}` 决定是否 `insmod` 对应模块；
2. `procd` 拉起 `netmgrd`（QMI 连接管理，5G 拨号的地基）；
3. WiFi 打开且有 `/etc/misc/wifi/hostapd.conf` 时，拉起**厂商** hostapd。

`overlay/etc/uci-defaults/98-r200-vendor` 会在首次启动时自动检测 `/opt/vendor` 是否存在并开关这些选项，
所以**不叠厂商栈的镜像行为与纯 OpenWrt 完全一致**。

## 4. 三条路线的取舍

| 路线 | 5G | WiFi | 有线 | 构建 | 风险 |
|---|---|---|---|---|---|
| `musl-core` | ✗ | ✗ | 可能（原厂 SSDK 未加载时按单网口） | ImageBuilder，10 分钟 | 最低，最适合先验证"能不能启动" |
| `musl-vendor` | 取决于 QMI 用户态能否在 /dev/mhi_uci 上工作 | 取决于 qcacld 初始化链 | 最好（SSDK + EMAC） | ImageBuilder，10 分钟 | 中：厂商栈的启动顺序要按真机日志调 |
| `glibc-vendor` | 同上，但少了 loader 这层变量 | 同上 | 同上 | 源码构建，60~120 分钟 | 中：目标若不支持 glibc 会直接失败（workflow 会检查） |
| 真内核移植 | ✓ | ✓（换 ath11k 或继续 qcacld） | ✓ | 需要串口 + 数周 | 最高，但是唯一"干净"的终局 |

## 5. 上了真机之后大概率要调的几处

这些是**已知的未知**，没有硬件无法确认，先列出来省时间：

1. **qcacld 的初始化链**：原厂是 systemd 服务 `start_wlan_services` → `wlan_services` 二进制 + `cld-hostapd-wlan*.sh`。
   我们的 init 脚本目前"直接 insmod + hostapd"，如果起不来，优先改成：
   `r200-vendor-exec wlan_services`（厂商编排程序），或照抄 `etc/initscripts/start_wlan_services` 的步骤。
2. **BDF / MAC**：`/lib/firmware/wlan/qca_cld/wlan_mac.bin` 是板级校准文件，必须和本机匹配（我们搬的是本机的）。
3. **网络口形态**：LAN 在 QCA8337 后面，`qca-ssdk.ko` 加载后可能出现新的 `ethX` 或需要 `ssdk_sh` 配置；
   `overlay/etc/uci-defaults/99-r200` 只是自动探测 + 兜底，多半要手工改 `br-lan` 的 `ifname`。
4. **netmgrd 的数据面**：它走 IPA/RMNET（`CONFIG_RMNET=y`、`CONFIG_MHI_UCI=y`），QMI 通道是 `/dev/mhi_uci`。
   如果 netmgrd 起来但没网，先用 `r200-vendor-exec rmnetcli` / `qmi_ip_multiclient` 看 QMI 能不能通。
5. **vermagic**：所有 `.ko` 都是给 `4.14.206-perf` 编的，`insmod` 失败就说明内核被动过（那是阶段 4 的事）。

## 6. 合规与体积

* 仓库里**不放任何厂商二进制**。厂商栈由你自己从原厂固件抽取，作为 Artifact / Release 资产传，
  `vendor_stack_url` 指过去就行。
* 厂商栈约 34.5 MiB（压缩后 ~15 MiB），叠进 rootfs 后 `system.ubi` 仍在 250 MiB 上限内。
