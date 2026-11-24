# firmware/ —— 这里放什么

本目录**只放文本形式的分区描述**，不放任何厂商二进制（合规，体积也小）。

已经放好的两个文件是从原厂刷机包里原样拷来的，用来给 `tools/gen_edl_pkg.py`
读分区几何（起始扇区、扇区数、扇区大小）：

| 文件 | 用途 |
|---|---|
| `rawprogram_nand_p4K_b256K.xml` | NAND 分区表 + 每个分区的起止扇区（4 KiB 扇区） |
| `patch_p4K_b256K.xml` | 原厂 patch（改 MIBIB），默认不使用，仅在 `--vendor-patch` 时生效 |

## 你本地刷机时

不需要把 `prog_firehose_sdx55.mbn` 之类的二进制放进仓库。做法是：

1. 构建产物 `edl-r200/` 里有 `system.ubi` + `rawprogram_r200_openwrt.xml` + `patch_r200.xml`；
2. 把这几个文件**复制到你的原厂 9008 刷机包目录**（即 `R200-6.00.7/Maincode/`，
   那里有 `prog_firehose_sdx55.mbn`、`partition_complete_p4K_b256K.mbn` 等）；
3. QFIL 里选 `rawprogram_r200_openwrt.xml` + `patch_r200.xml` 刷入。

## 想自己重新核对几何

```bash
python3 tools/fwdump.py <你的原厂固件目录> --json out/device.json
```

会打印完整分区表、每个 `.ubi` 的块/LEB/卷表，并导出内核配置到 `out/kernel.config`。
本仓库 `docs/device.md` 里的所有数字都来自这一步。
