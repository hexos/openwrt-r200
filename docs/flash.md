# 刷机 / 回滚（高通 9008 EDL）

> 先把这句话读完：**动手之前，确认你手里有原厂整机包**（本目录同级的
> `R200-6.00.7/Maincode/` 就是）。本方案只覆盖 `system` 分区，
> 出任何问题都能用原厂包整机刷回来。

## 1. 进入 9008 模式

EDL 是 SoC 内部固化的下载模式，只要进了 9008，砖机也能救。

* USB 侧识别为 `Qualcomm HS-USB QDLoader 9008`，VID:PID = `05c6:9008`
  （Windows 设备管理器 / Linux `lsusb` 可确认）。
* R200 各批次进入方式不完全一样，常见有：
  * 断电状态下按住复位键（机身 RST 孔）再上电，保持 3~5 秒；
  * 拆机后短接主板上的 EDL 测试点（NAND 附近两个焊盘）再上电；
  * 若系统还能起来：`reboot edl`（需要 root / adb）或串口里 `fastboot oem edl`。
* 供电路径：R200 是 12V DC 适配器 + 3 个千兆网口；刷机走设备上的 USB 口
  （部分批次是 Type-C）。插上后 QFIL 才看得到端口。

## 2. Windows：QFIL 刷写（推荐）

1. 装 QFIL（随 Qualcomm QPST 发布）。
2. 把构建产物 `edl-r200/` 里的所有文件，复制到**原厂刷机包目录**
   （即含 `prog_firehose_sdx55.mbn`、`rawprogram_nand_p4K_b256K.xml` 的那个目录）。
   这样做的好处：firehose 程序、patch 文件都在手边，不会缺文件。
3. QFIL → `Select Build Type: Flat Build`，然后：

   | 项 | 选什么 |
   |---|---|
   | Programmer Path | `prog_firehose_sdx55.mbn` |
   | Raw Program | `rawprogram_r200_openwrt.xml` |
   | Patch XML | `patch_r200.xml` |

4. 点 **Download**。写入量约 60~100 MiB（只写 system 分区），一般 1~3 分钟。
5. 看到 `Finish Download` 后拔线、重新上电。
6. 浏览器打开 <http://192.168.1.1>，或 `ssh root@192.168.1.1`（首次无密码）。

> 提示：`rawprogram_r200_openwrt.xml` 里只有 `system` 一条 erase + program，
> 不会碰 sbl/mibib/efs2/modem/recoveryfs。想同时换 `boot`，用
> `gen_edl_pkg.py --boot <你的 boot.img>` 重新生成一份即可。

## 3. Linux：bkerler/edl

```bash
pip install edlclient            # 或 git clone https://github.com/bkerler/edl
cd edl-r200
edl printgpt                     # 确认 9008 通了
edl qfil rawprogram_r200_openwrt.xml patch_r200.xml \
    --loader ../Maincode/prog_firehose_sdx55.mbn
```

只想写一个分区时也可以：

```bash
edl w system system.ubi --loader ../Maincode/prog_firehose_sdx55.mbn
```

## 4. 刷完不起来怎么办

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 完全无反应、无 Web、无 DHCP | 根分区没挂上（卷名/几何不对） | 回滚原厂包；核对 `docs/device.md` 第 4 节的卷名必须为 `rootfs` |
| 能 ping 通但 LuCI 打不开 | 防火墙/服务没起，或网口名不对 | 串口或 `ssh` 进去看 `logread`；检查 `/etc/config/network` 的 `ifname` |
| 起来后改配置重启丢失 | remount rw 失败 | `mount` 看 `/` 是否 `rw`；检查 UBI 卷是否写满 |
| 反复重启 | 内核 panic（大概率还是根分区问题） | 回滚原厂包，并保留串口日志 |

**回滚**：用原厂整机 9008 包（`rawprogram_nand_p4K_b256K.xml`）整机刷回。
注意原厂包里最后两条 erase/program 会把 `sbl` 再写一遍，属于正常流程。

## 5. 刷机前建议（很重要）

1. **先备份**：进 9008 后用 `edl rl dump --loader prog_firehose_sdx55.mbn` 把
   `efs2`、`efs2_bak`、`persist`、`m_raw` 等分区读出来留档 —— 这些里有 IMEI/校准数据，
   丢了很麻烦（有些批次只能换板）。
2. **只刷 system**：本工程默认如此。先别急着换 `boot`，先用原厂内核把 OpenWrt 跑通。
3. **保留串口日志**：如果能焊出 debug UART，第一次刷机务必接上，
   内核版本的任何不匹配都会在这里一眼看出来。
