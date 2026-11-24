# dts/ —— 从原厂 dtb 反编译出来的设备树

| 文件 | 来源 | 规模 |
|---|---|---|
| `sdxprairie-mtp-v2.dts` | 原厂 `boot.img` 内核镜像尾部拼接的 dtb（偏移 0x735a31） | 1459 节点 / 5942 属性 |
| `sdxprairie-mtp-v2-le-cpe.dts` | 同上（偏移 0x6d1ad3），`model = ... SDXPRAIRIE MTP V2 (LE-CPE)` | 1353 节点 / 5520 属性 |

## 怎么来的

原厂内核镜像尾部被厂商拼了 25 个板级 dtb（apq8064、msm8974… 一直到 sdxprairie），
所以：

```bash
python3 tools/bootimg.py dtbs Maincode/sdxprairie-boot.img -o out/dtbs
python3 tools/dtb2dts.py out/dtbs/dtb-11-0x6d1ad3.dtb -o dts/sdxprairie-mtp-v2-le-cpe.dts
python3 tools/dtb2dts.py out/dtbs/dtb-23-0x735a31.dtb -o dts/sdxprairie-mtp-v2.dts
```

## 怎么用（以及不能怎么用）

**能用**：查节点、查走线、抄 reg/interrupt/GPIO、看板级差异，
`docs/device.md` 第 6 节那张外设表就是这么得来的。

**不能用**：这些 `.dts` 是"参考版"，不是可编译版 ——

* phandle 保留成数值（`<0x1>`），没有还原成 `&label`，dtc 编译回二进制不等价；
* 厂商的 `qcom,msm-id` / `qcom,board-id` 等单元格已正确还原，但个别二进制属性
  仍然以 `[..]` 字节串形式呈现。

真要做内核移植（阶段 4），正确做法是拿 CAF `msm-4.14` 的
`arch/arm/boot/dts/qcom/sdxprairie-mtp-cpe.dts` 作基底，再用这里的反编译结果去
对齐板级差异（板号、GPIO、WiFi/交换芯片节点、NAND 分区表）。
