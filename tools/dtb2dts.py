#!/usr/bin/env python3
"""dtb2dts.py —— 把设备树二进制(.dtb)反编译成可读的 .dts 文本。

为什么需要它：R200 的内核源码拿不到，但原厂 boot.img 里内嵌了 25 个板级 dtb，
其中 `sdxprairie-mtp-v2*.dtb` 就是这台机器的板级描述（SoC 节点 + 板级走线）。
用它反编译出 DTS，是后续"真·内核移植"阶段最有用的一手资料。

  python3 tools/dtb2dts.py <输入.dtb> [-o 输出.dts] [--list-compat]

注意：这是**参考用**的反编译，不做 phandle→label 的还原（保留十六进制数值），
所以不能直接拿去 dtc 编译回二进制做等价性验证；它的用途是读结构、抄节点、对走线。
"""
import argparse
import os
import struct
import sys

FDT_BEGIN_NODE = 0x1
FDT_END_NODE = 0x2
FDT_PROP = 0x3
FDT_NOP = 0x4
FDT_END = 0x9

# 这些属性名几乎总是字符串
STRING_PROPS = {
    "compatible", "model", "status", "device_type", "bootargs", "label",
    "name", "stdout-path", "method", "clock-names", "reg-names",
    "interrupt-names", "pinctrl-names", "reset-names", "dma-names",
    "power-domain-names", "phy-names", "sound-name-prefix",
    "firmware-name", "qcom,image-name", "qcom,firmware-name",
}
# 这些属性名几乎总是字节序列
BYTE_PROPS = {
    "mac-address", "local-mac-address", "address-bits",
}
# 这些属性名是 u32 单元数组，即使字节恰好可打印也不能当字符串
CELL_PROPS = {
    "qcom,msm-id", "qcom,board-id", "qcom,pmic-id", "qcom,pmic-rev-id",
    "qcom,hw-sku", "qcom,platform-id", "qcom,rumi_platform",
    "interrupts", "reg", "interrupt-map", "clocks", "resets",
}


class Fdt:
    def __init__(self, data):
        (magic, self.total, self.off_struct, self.off_strings, self.off_rsv,
         self.version, self.last_comp, self.boot_cpuid,
         self.size_strings, self.size_struct) = struct.unpack_from(">10I", data, 0)
        if magic != 0xD00DFEED:
            sys.exit("不是 FDT/DTB 文件（magic 不对）")
        self.blob = data[:self.total]
        self.pos = self.off_struct

    def align4(self):
        self.pos = (self.pos + 3) & ~3

    def u32(self):
        v = struct.unpack_from(">I", self.blob, self.pos)[0]
        self.pos += 4
        return v

    def string_at(self, off):
        start = self.off_strings + off
        end = self.blob.index(b"\x00", start)
        return self.blob[start:end].decode("utf-8", "replace")

    def cstring(self):
        end = self.blob.index(b"\x00", self.pos)
        s = self.blob[self.pos:end].decode("utf-8", "replace")
        self.pos = (end + 1 + 3) & ~3
        return s


def fmt_prop(name, data):
    if not data:
        return ""
    is_text = (
        name in STRING_PROPS
        or (
            name not in CELL_PROPS
            and data.endswith(b"\x00")
            and all(32 <= c < 127 or c == 0 for c in data)
        )
    )
    if is_text:
        parts = [p.decode("utf-8", "replace") for p in data.split(b"\x00") if p]
        if parts:
            return " = " + ", ".join(f'"{p}"' for p in parts)
    if name in BYTE_PROPS:
        return " = [" + " ".join(f"{b:02x}" for b in data) + "]"
    if len(data) % 4 == 0 and len(data) <= 256:
        cells = struct.unpack(">%dI" % (len(data) // 4), data)
        return " = <" + " ".join(f"0x{c:x}" for c in cells) + ">"
    return " = [" + " ".join(f"{b:02x}" for b in data) + "]"


def walk(f, lines, depth, stats):
    """返回时已经把整棵树写完。"""
    while True:
        tok = f.u32()
        if tok == FDT_BEGIN_NODE:
            name = f.cstring()
            stats["nodes"] += 1
            indent = "\t" * depth
            if not name:
                lines.append("/ {")
            else:
                lines.append(f"{indent}{name} {{")
            walk(f, lines, depth + 1, stats)
        elif tok == FDT_END_NODE:
            lines.append("\t" * max(depth - 1, 0) + "};")
            return
        elif tok == FDT_PROP:
            # 注意字段顺序：token(3) -> len -> nameoff -> value（padded 到 4 字节）
            length = f.u32()
            nameoff = f.u32()
            pname = f.string_at(nameoff)
            data = f.blob[f.pos:f.pos + length]
            f.pos += length
            f.align4()
            stats["props"] += 1
            if pname == "compatible":
                stats["compat"].append(data)
            if pname == "model" and depth == 1:
                stats["model"] = data.split(b"\x00")[0].decode("utf-8", "replace")
            lines.append("\t" * depth + pname + fmt_prop(pname, data) + ";")
        elif tok == FDT_NOP:
            continue
        elif tok == FDT_END:
            return
        else:
            raise ValueError(f"未知 token {tok:#x} @ {f.pos - 4:#x}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dtb")
    ap.add_argument("-o", "--out")
    ap.add_argument("--list-compat", action="store_true")
    ap.add_argument("--model-only", action="store_true")
    a = ap.parse_args()

    with open(a.dtb, "rb") as f:
        data = f.read()
    fdt = Fdt(data)
    lines = ["/dts-v1/;", "", "/* 由 tools/dtb2dts.py 从 " + os.path.basename(a.dtb) + " 反编译生成",
             " * 仅供阅读/移植参考：phandle 保持数值，未还原为 &label */", ""]
    stats = {"nodes": 0, "props": 0, "compat": []}
    walk(fdt, lines, 0, stats)

    compat = []
    for blob in stats["compat"]:
        for c in blob.split(b"\x00"):
            if c:
                s = c.decode("utf-8", "replace")
                if s not in compat:
                    compat.append(s)

    if a.model_only or a.list_compat:
        print(f"节点 {stats['nodes']} 个, 属性 {stats['props']} 个, compatible 字符串 {len(compat)} 条")
        if a.list_compat:
            for c in compat:
                print("   ", c)
        return

    out = a.out or (os.path.splitext(a.dtb)[0] + ".dts")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{a.dtb}: 节点 {stats['nodes']}, 属性 {stats['props']}, "
          f"compatible {len(compat)} 条 -> {out} ({os.path.getsize(out)} 字节)")


if __name__ == "__main__":
    main()
