#!/usr/bin/env python3
"""selftest.py —— 仓库自检：不用硬件、不用 Linux，一条命令验证工程是否自洽。

CI 在真正开始构建前先跑这个，本地改完东西也可以随时跑。

  python3 tools/selftest.py [--fw <原厂固件目录>]

检查项：
  1. 所有 python 工具语法正确、能 --help
  2. config/device.env 的几何参数自洽（LEB = 块大小 - data_offset、分区字节数…）
  3. overlay 关键文件齐全（preinit / uci-defaults / init.d / r200-vendor-exec）
  4. workflow 里引用的路径与脚本真实存在；YAML 无 tab；关键 key 齐全
  5. 文档里引用的相对链接都指向存在的文件
  6. 给了 --fw 时：对原厂固件跑 fwdump，核对 device.env 的数字；boot.img 往返一致性
"""
import argparse
import glob
import hashlib
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OK, FAIL, WARN = [], [], []


def ok(msg):
    OK.append(msg)
    print(f"  ✅ {msg}")


def fail(msg):
    FAIL.append(msg)
    print(f"  ❌ {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  ⚠️  {msg}")


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = re.sub(r"\s+#.*$", "", v).strip()          # 去掉行内注释
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            elif v.startswith("$"):
                continue          # 派生值（如 $(( ... ))），跳过
            env[k] = v
    return env


def check_tools():
    print("\n== 1) 工具语法与可用性 ==")
    tmp = os.path.join(tempfile.gettempdir(), "_r200_selftest.pyc")
    for path in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py"))):
        name = os.path.basename(path)
        r = subprocess.run([PY, "-c",
                            "import py_compile,sys;py_compile.compile(sys.argv[1],doraise=True,"
                            f"cfile=r'{tmp}')", path],
                           capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} 语法错误: {r.stderr.strip().splitlines()[-1:]}")
            continue
        r = subprocess.run([PY, path, "-h"], capture_output=True, text=True)
        if r.returncode:
            fail(f"{name} -h 失败: {r.stderr.strip().splitlines()[-1:]}")
        else:
            ok(f"{name} 语法 + -h 正常")


def check_env():
    print("\n== 2) config/device.env 自洽性 ==")
    env = load_env(os.path.join(ROOT, "config", "device.env"))
    need = ["NAND_PAGE_SIZE", "NAND_BLOCK_SIZE", "UBI_VID_HDR_OFFSET", "UBI_DATA_OFFSET",
            "UBI_LEB_SIZE", "SECTOR_SIZE", "PART_system_start", "PART_system_sectors",
            "UBI_VOL_0_NAME", "KERNEL_VERSION"]
    miss = [k for k in need if k not in env]
    if miss:
        fail(f"device.env 缺字段: {miss}")
        return env
    page = int(env["NAND_PAGE_SIZE"])
    blk = int(env["NAND_BLOCK_SIZE"])
    voff = int(env["UBI_VID_HDR_OFFSET"])
    doff = int(env["UBI_DATA_OFFSET"])
    leb = int(env["UBI_LEB_SIZE"])
    sec = int(env["SECTOR_SIZE"])
    start = int(env["PART_system_start"])
    cnt = int(env["PART_system_sectors"])
    checks = [
        ("LEB = 块大小 - data_offset", leb == blk - doff),
        ("vid_hdr_offset = 页大小", voff == page),
        ("data_offset = 2 × 页大小", doff == 2 * page),
        ("system 分区按块对齐", (cnt * sec) % blk == 0),
        ("卷名 = rootfs（引导程序按名字找根）", env["UBI_VOL_0_NAME"] == "rootfs"),
        ("system 结束于 512 MiB 边界", (start + cnt) * sec == 512 * 1024 * 1024),
    ]
    for label, cond in checks:
        ok(label) if cond else fail(label)
    print(f"     system 分区: {cnt * sec / 1048576:.2f} MiB, {cnt * sec // blk} 个块")
    return env


def check_overlay():
    print("\n== 3) overlay 关键文件 ==")
    must = [
        "overlay/lib/preinit/80_mount_root",
        "overlay/etc/uci-defaults/99-r200",
        "overlay/etc/config/network",
        "overlay/etc/config/r200",
        "overlay/etc/init.d/r200-vendor",
        "overlay/usr/sbin/r200-vendor-exec",
    ]
    for rel in must:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            fail(f"缺 {rel}")
        elif os.path.getsize(p) < 20:
            fail(f"{rel} 内容过短")
        else:
            ok(rel)
    # 可执行脚本的第一行应是 shebang
    for rel in ("overlay/etc/init.d/r200-vendor", "overlay/usr/sbin/r200-vendor-exec",
                "overlay/lib/preinit/80_mount_root", "overlay/etc/uci-defaults/99-r200"):
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                first = f.readline()
            if not first.startswith("#!"):
                fail(f"{rel} 缺 shebang")
    ok("脚本 shebang 检查通过")
    # 脚本里引用的路径要能对得上厂商栈产物
    with open(os.path.join(ROOT, "overlay/usr/sbin/r200-vendor-exec"), encoding="utf-8") as f:
        t = f.read()
    if "/opt/vendor/lib/ld-linux-armhf.so.3" in t:
        ok("r200-vendor-exec 使用厂商自带 glibc 加载器")
    else:
        fail("r200-vendor-exec 里找不到 glibc 加载器路径")


def check_workflow():
    print("\n== 4) workflow ==")
    p = os.path.join(ROOT, ".github/workflows/build.yml")
    if not os.path.isfile(p):
        fail("缺 .github/workflows/build.yml")
        return
    text = open(p, encoding="utf-8").read()
    if "\t" in text:
        fail("workflow 里有 tab（YAML 不允许）")
    else:
        ok("无 tab 字符")
    for key in ("on:", "jobs:", "runs-on:", "steps:", "actions/checkout", "upload-artifact"):
        ok(f"包含 {key}") if key in text else fail(f"缺 {key}")
    # 引用的仓库内文件必须存在
    refs = set(re.findall(r"tools/[A-Za-z0-9_./-]+\.(?:py|sh)", text))
    refs |= set(re.findall(r"config/[A-Za-z0-9_.-]+", text))
    for ref in sorted(refs):
        if os.path.exists(os.path.join(ROOT, ref)):
            ok(f"引用存在: {ref}")
        else:
            fail(f"workflow 引用了不存在的 {ref}")


def check_docs():
    print("\n== 5) 文档内部链接 ==")
    docs = glob.glob(os.path.join(ROOT, "*.md")) + glob.glob(os.path.join(ROOT, "docs", "*.md")) \
        + glob.glob(os.path.join(ROOT, "dts", "*.md")) + glob.glob(os.path.join(ROOT, "firmware", "*.md"))
    bad = 0
    for doc in docs:
        base = os.path.dirname(doc)
        text = open(doc, encoding="utf-8").read()
        for link in re.findall(r"\]\(([^)#]+)\)", text):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, link))):
                fail(f"{os.path.basename(doc)} -> 链接失效: {link}")
                bad += 1
    if not bad:
        ok(f"{len(docs)} 篇文档的相对链接全部有效")


def check_firmware(fwdir):
    print("\n== 6) 对原厂固件实测 ==")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import fwdump                                   # noqa: E402
    rp = os.path.join(fwdir, "rawprogram_nand_p4K_b256K.xml")
    if not os.path.isfile(rp):
        fail(f"{fwdir} 里没有 rawprogram XML")
        return
    parts = {p["label"]: p for p in fwdump.parse_rawprogram(rp)}
    env = load_env(os.path.join(ROOT, "config", "device.env"))
    ok(f"解到 {len(parts)} 个分区")
    sysp = parts.get("system")
    if sysp and sysp["start_sector"] == int(env["PART_system_start"]) \
            and sysp["num_sectors"] == int(env["PART_system_sectors"]):
        ok("device.env 里的 system 分区与原厂 rawprogram 一致")
    else:
        fail("device.env 的 system 分区与原厂 rawprogram 不一致")

    ubi = os.path.join(fwdir, "sdxprairie-sysfs.ubi")
    if os.path.isfile(ubi):
        u = fwdump.parse_ubi(ubi)
        good = (u["eraseblock_size"] == int(env["NAND_BLOCK_SIZE"])
                and u["leb_size"] == int(env["UBI_LEB_SIZE"])
                and u["vid_hdr_offset"] == int(env["UBI_VID_HDR_OFFSET"]))
        ok("原厂 UBI 几何与 device.env 一致") if good else fail("原厂 UBI 几何与 device.env 不一致")
        names = u["volume_names"]
        ok(f"原厂卷表: {list(names)}") if "rootfs" in names else fail("原厂卷表里没有 rootfs")

    boot = os.path.join(fwdir, "sdxprairie-boot.img")
    if os.path.isfile(boot):
        import bootimg                              # noqa: E402
        img = bootimg.BootImage(boot)
        rebuilt = img.build()
        same = hashlib.sha256(rebuilt).hexdigest() == hashlib.sha256(img.raw).hexdigest()
        ok("boot.img 往返重建逐字节一致") if same else fail("boot.img 往返重建不一致")
        ok(f"boot.img: kernel={img.kernel_size} ramdisk={img.ramdisk_size} page={img.page_size}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fw", help="原厂固件目录（给了就顺便做实测核对）")
    a = ap.parse_args()

    print(f"仓库: {ROOT}")
    env = check_env()
    check_tools()
    check_overlay()
    check_workflow()
    check_docs()
    if a.fw:
        check_firmware(a.fw)
    else:
        warn("没给 --fw，跳过原厂固件实测核对")

    print("\n" + "=" * 60)
    print(f"通过 {len(OK)} 项, 警告 {len(WARN)} 项, 失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("✅ 自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
