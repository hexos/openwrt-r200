# 刷机 / 回滚（高通 9008 EDL）

> **一分钟版（照着做）**
>
> 0. 确认分区表一致（很关键，尤其你的原厂包不是 6.00.7 时）：
>    ```powershell
>    powershell -NoProfile -ExecutionPolicy Bypass -File tools\check-partition-table.ps1 -FirmwareDir <原厂Maincode目录> -Diff
>    ```
>    看到 `[PASS]` 才继续。或者最省事：直接看原厂 XML 里 system 那行的
>    `start_sector="64832"` / `num_partition_sectors="66240"` 对不对得上。
> 1. 建目录 `C:\R200-openwrt\`，放 4 个文件：`rawprogram_r200_openwrt.xml`、
>    `system.ubi`（这两个来自 CI 的 `edl-r200/`）、
>    `prog_firehose_sdx55.mbn`（从原厂目录复制）。
> 2. QFIL：Programmer = 那个 mbn；Search Path = `C:\R200-openwrt`；
>    **Load XML** 只选 `rawprogram_r200_openwrt.xml`，**Patch 留空**；**Download**。
> 3. 拔线重上电，浏览器开 <http://192.168.1.1>。
>
> 出问题 → 用原厂整机包重刷（见文末"回滚"）。它只写了 `system` 一个分区，随时可退。

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
   这样做的好处：firehose 程序就在手边，不会缺文件。（本包不需要 patch 文件。）
3. QFIL → `Select Build Type: Flat Build`，然后：

   | 项 | 选什么 |
   |---|---|
   | Programmer Path | `prog_firehose_sdx55.mbn` |
   | Raw Program | `rawprogram_r200_openwrt.xml` |
   | Patch XML | **留空**（本包不需要；不能给空 `<patches></patches>`，fh_loader 会报错） |

4. 点 **Download**。写入量约 60~100 MiB（只写 system 分区），一般 1~3 分钟。
5. 看到 `Finish Download` 后拔线、重新上电。
6. 浏览器打开 <http://192.168.1.1>，或 `ssh root@192.168.1.1`（首次无密码）。

> 提示：`rawprogram_r200_openwrt.xml` 里只有 `system` 一条 erase + program，
> 不会碰 sbl/mibib/efs2/modem/recoveryfs。想同时换 `boot`，用
> `gen_edl_pkg.py --boot <你的 boot.img>` 重新生成一份即可。

## 2.5 QFIL 报 `Download Fail:FireHose Fail:FHLoader Fail:Process fail` 怎么办

### 先看清楚：那次尝试根本还没开始写

`port_trace.txt` 里 fh_loader 是被这样调起来的：

```
fh_loader.exe --port=\\.\COM10 --setactivepartition=0 --noprompt --showpercentagecomplete --zlpawarehost=1 --memoryname=nand
```

**没有 `--sendxml`** —— 这是 QFIL 的"预处理"步骤（设置活动启动分区），不是刷写步骤。
也就是说这次尝试连一个分区都没动，接着才是真正带 `--sendxml=rawprogram...` 的刷写调用。

它失败在这里：

```
<configure MemoryName="nand" .../>                        <- 通了
<setbootablestoragedrive value="0" />                     <- 预处理命令
TARGET: 'INFO: NAND was previously open, returning existing handle 0'
TARGET: 'ERROR: Set Bootable Failed 1'   -> NAK
```

关键就是那句 **`NAND was previously open, returning existing handle 0`**：
programmer 还握着上一次（失败/中断的）会话留下的 NAND 句柄，状态不干净。
**原厂包用同一套 QFIL 能刷成功，说明这一步在干净状态下是能过的**
（原厂包和我们包的差别只在 rawprogram 内容，而这个预处理步骤跟 rawprogram 无关）。

### 正确做法：每次尝试前彻底断电重进

1. 拔掉 **12V 适配器** 和 **USB**，等 10 秒（让 programmer 会话彻底消失）；
2. 重新进入 9008：按住复位键 / 短接 EDL 点 → 上电 → 设备管理器出现 `Qualcomm HS-USB QDLoader 9008`；
3. **第一次点 Download 就要成功**。一旦失败过，必须回到第 1 步重来，
   不能连着点第二次 —— "previously open" 就是这么来的；
4. 判断是否真的在写：trace 里应该出现 `Sending <program>` / `<erase>` 和进度百分比，
   而不是停在 `Set Bootable`。

> 注意 QFIL 每次运行都会覆盖 `port_trace.txt`，所以你现在看到的是**最后一次**尝试的日志。

### 如果断电重进后仍然卡在 Set Bootable

那就绕开 QFIL 的预处理步骤：

**A. 直接用 fh_loader（包里已带 `flash_fh_loader.bat`，不用装 Python）**

让 QFIL 点一次 Download、停在 `Set Bootable` 报错（此时 programmer 已加载、别拔线），
然后在包目录里执行 `flash_fh_loader.bat 10`（10 = COM 号）。它做的就是去掉
`--setactivepartition`：

```bat
fh_loader.exe --port=\\.\COM10 --search_path="<包目录>" ^
  --sendxml=rawprogram_r200_openwrt.xml --noprompt ^
  --showpercentagecomplete --zlpawarehost=1 --memoryname=nand --verbose=2
```

**B. 用开源 edl（长期推荐，自己带 Sahara→Firehose）**

> edl 的 `qfil` 命令要求两个 XML 参数，但 edl 用 Python 解析、能接受空 patch。
> 随便建一个 `patch_empty.xml`，内容就一行 `<patches/>`
> （**别在 QFIL 里选它** —— fh_loader 不认这种写法，会报 `Could not find closing />`）。

```powershell
winget install -e --id Python.Python.3.12
python -m pip install edlclient
edl qfil rawprogram_r200_openwrt.xml patch_empty.xml --loader=prog_firehose_sdx55.mbn --memory=nand
```

## 3. Linux：bkerler/edl

```bash
pip install edlclient            
edl printgpt                     # 确认 9008 通了
edl qfil rawprogram_r200_openwrt.xml patch_empty.xml \
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
