#!/usr/bin/env python3
"""prepare_vendor_stack.py —— 把原厂 glibc 那套 5G/WiFi/交换芯片用户态整理成可直接塞进 OpenWrt 的目录。

做法：
  1. 给定若干"入口二进制"（netmgrd / hostapd / ssdk_sh …），用 tools/elfdeps.py 解析
     每个二进制的 DT_NEEDED 传递闭包 —— 只搬真正需要的 .so，不猜、不整包拷贝；
  2. 每个 .so 按 **SONAME** 命名放进 `opt/vendor/lib/`，glibc 的 ld.so 就能按名字找到；
     连带把厂商自带的 `ld-linux-armhf.so.3`（glibc 2.28）一起带上；
  3. 内核模块（qcacld `wlan.ko`、`qca-ssdk.ko` …）、固件（`/lib/firmware/{wlan,ipa*}`）、
     配置（`/etc/misc/wifi`、`qapi.conf` …）按目标路径原样铺开；
  4. 生成 `STACK.json` / `MANIFEST.txt` 记录来源与校验和。

产物目录结构（直接叠加到 OpenWrt rootfs 根上）：

    opt/vendor/bin/<入口二进制>
    opt/vendor/lib/<SONAME>            # 含 ld-linux-armhf.so.3
    opt/vendor/share/...               # 原厂配置/脚本
    lib/modules/4.14.206-perf/...      # 原厂内核模块
    lib/firmware/...                   # WiFi / IPA 固件
    etc/r200-vendor/...                # 原厂 etc 下的配置与启动脚本

  这样在 musl 的 OpenWrt 上也能跑厂商二进制：
    /opt/vendor/lib/ld-linux-armhf.so.3 --library-path /opt/vendor/lib /opt/vendor/bin/netmgrd
  而如果 OpenWrt 本身就是 glibc 版，则可以直接执行 /opt/vendor/bin/netmgrd。

  python3 tools/prepare_vendor_stack.py Maincode/target-files-ubi.zip -o out/vendor-stack
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elfdeps  # noqa: E402

# 入口二进制 -> 目标组
ENTRY_BINARIES = {
    "modem": ["usr/bin/netmgrd", "usr/bin/dialup", "usr/bin/qmi_ip_multiclient",
              "usr/bin/rmnetcli"],
    "wifi": ["usr/sbin/hostapd", "usr/sbin/wpa_supplicant", "usr/sbin/hostapd_cli",
             "usr/sbin/wpa_cli", "usr/sbin/wlan_services"],
    "switch": ["usr/bin/ssdk_sh", "usr/sbin/ssdk_sh"],
}

# 需要原样铺开的数据目录/文件（相对原厂 rootfs 根）
DATA_PATHS = [
    "lib/firmware/wlan",
    "lib/firmware/ipa",
    "lib/firmware/ipa_fws.b02",
    "lib/firmware/ipa_fws.b03",
    "lib/firmware/ipa_fws.b04",
    "lib/firmware/ipa_fws.b05",
    "lib/firmware/ipa_fws.mdt",
    "lib/firmware/ipa_fws.elf",
    "usr/lib/modules/4.14.206-perf",
    "etc/misc/wifi",
    "etc/data",
    "etc/initscripts",
    "etc/cld-hostapd-wlan0.sh",
    "etc/cld-hostapd-wlan1.sh",
    "etc/cld-hostapd-wlan2.sh",
    "etc/qapi.conf",
    "etc/qmi_ip_cfg.xml",
    "etc/meig_apps_startup",
    "etc/start_pcie",
    "etc/systemd/system/start_wlan_services.service",
]


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


class Src:
    """统一读取：zip 或目录。"""

    def __init__(self, root):
        self.zip = zipfile.ZipFile(root) if root.lower().endswith(".zip") else None
        self.root = None if self.zip else root
        self.names = []
        if self.zip:
            self.names = [n for n in self.zip.namelist() if not n.endswith("/")]
        else:
            for dirpath, _d, files in os.walk(root):
                for f in files:
                    self.names.append(os.path.relpath(os.path.join(dirpath, f), root)
                                      .replace("\\", "/"))

    def rel_names(self):
        return [n[len("SYSTEM/"):] if n.startswith("SYSTEM/") else n for n in self.names]

    def entry(self, rel):
        if self.zip:
            return rel if rel.startswith("SYSTEM/") else "SYSTEM/" + rel
        return rel

    def exists(self, rel):
        return self.entry(rel) in (self.zip.namelist() if self.zip else self.names)

    def read(self, rel):
        if self.zip:
            return self.zip.read(self.entry(rel))
        return open(os.path.join(self.root, rel), "rb").read()

    def mode(self, rel):
        if self.zip:
            return self.zip.getinfo(self.entry(rel)).external_attr >> 16
        return os.stat(os.path.join(self.root, rel)).st_mode

    def walk(self, rel_dir):
        prefix = rel_dir.rstrip("/") + "/"
        return [n for n in self.rel_names() if n.startswith(prefix)]


def verify_bundle(out_dir):
    """封闭世界自检：产物里的每个 ELF，其 DT_NEEDED 是否都能在 opt/vendor/lib 里找到。

    这一步不依赖原厂固件，纯粹检查"搬出来的东西自己是不是自洽的"，
    所以可以直接在 CI 里对成品跑一遍。
    """
    bin_dir = os.path.join(out_dir, "opt/vendor/bin")
    lib_dir = os.path.join(out_dir, "opt/vendor/lib")
    if not os.path.isdir(bin_dir):
        print(f"✗ {bin_dir} 不存在")
        return 1
    libs = {}
    for name in os.listdir(lib_dir):
        p = os.path.join(lib_dir, name)
        if os.path.isfile(p):
            libs[name] = p
    bad = 0
    checked = 0
    print(f"== 自检 {out_dir} ==")
    print(f"库目录: {len(libs)} 个文件")
    if "ld-linux-armhf.so.3" not in libs:
        print("✗ 缺 ld-linux-armhf.so.3（musl 系统上跑不起来）")
        bad += 1
    todo = [(n, p) for n, p in sorted(libs.items())]
    for name in sorted(os.listdir(bin_dir)):
        p = os.path.join(bin_dir, name)
        if os.path.isfile(p):
            todo.append((f"bin/{name}", p))
    seen = set()
    while todo:
        name, path = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            info = elfdeps.parse_elf32(open(path, "rb").read())
        except Exception as exc:                     # noqa: BLE001
            print(f"  ! {name}: 解析失败 {exc}")
            bad += 1
            continue
        if info is None:
            continue
        checked += 1
        for need in info["needed"]:
            if need in libs:
                if need not in seen:
                    todo.append((need, libs[need]))
            elif need in ("ld-linux-armhf.so.3",):
                pass
            else:
                print(f"  ✗ {name} 需要 {need}，但 opt/vendor/lib 里没有")
                bad += 1
    print(f"检查了 {checked} 个 ELF，问题 {bad} 条")
    print("✅ 自检通过" if bad == 0 else "❌ 自检失败")
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help="target-files-ubi.zip 或已解开的原厂 rootfs 目录")
    ap.add_argument("-o", "--out", default="vendor-stack")
    ap.add_argument("--groups", default="modem,wifi,switch")
    ap.add_argument("--with-data", action="store_true", default=True)
    ap.add_argument("--no-data", dest="with_data", action="store_false")
    ap.add_argument("--verify", action="store_true",
                    help="只对已有产物做自检（不做抽取）")
    a = ap.parse_args()

    if a.verify:
        sys.exit(verify_bundle(a.out))
    if not a.source:
        sys.exit(__doc__)

    src = Src(a.source)
    fs = elfdeps.VendorFS(a.source)
    groups = [g.strip() for g in a.groups.split(",") if g.strip() in ENTRY_BINARIES]

    out_bin = os.path.join(a.out, "opt/vendor/bin")
    out_lib = os.path.join(a.out, "opt/vendor/lib")
    os.makedirs(out_bin, exist_ok=True)
    os.makedirs(out_lib, exist_ok=True)

    stack = {"source": os.path.abspath(a.source), "groups": groups,
             "binaries": {}, "libraries": {}, "data": [], "missing": []}

    libs_copied = {}          # soname -> bytes
    for group in groups:
        for rel in ENTRY_BINARIES[group]:
            hit = fs.find(rel)
            if not hit:
                if src.exists(rel):
                    hit = (src.entry(rel), rel)
                else:
                    continue
            info = elfdeps.parse_elf32(fs.read(hit[1]))
            if info is None:
                continue
            # 落入口二进制
            blob = fs.read(hit[1])
            dst = os.path.join(out_bin, os.path.basename(rel))
            with open(dst, "wb") as f:
                f.write(blob)
            os.chmod(dst, 0o755)
            stack["binaries"][os.path.basename(rel)] = {
                "group": group, "from": hit[1], "size": len(blob), "sha256": sha(blob),
            }

            seen, missing, aliases = elfdeps.resolve_closure(fs, hit[1])
            for dep_rel, dep_info in seen.items():
                # 落盘名字优先用 SONAME；厂商有些 .so（libmeigbase.so / libmal.so …）
                # 根本没写 SONAME，那就用"别人引用它时用的名字"。
                name = dep_info.get("soname")
                if not name:
                    alias = sorted(aliases.get(dep_rel) or [])
                    name = alias[0] if alias else os.path.basename(dep_rel)
                dep_blob = fs.read(dep_rel)
                if name in libs_copied:
                    if libs_copied[name] != dep_blob:
                        print(f"  ! 同名库内容不同，保留先到的: {name}", file=sys.stderr)
                    continue
                libs_copied[name] = dep_blob
                with open(os.path.join(out_lib, name), "wb") as f:
                    f.write(dep_blob)
                os.chmod(os.path.join(out_lib, name), 0o755)
                stack["libraries"][name] = {
                    "from": dep_rel, "size": len(dep_blob), "sha256": sha(dep_blob),
                }
            for who, need in missing:
                stack["missing"].append({"who": who, "need": need})

    # glibc 的动态加载器必须以这个名字存在，musl 系统上要用它来跑厂商二进制
    loader = os.path.join(out_lib, "ld-linux-armhf.so.3")
    if os.path.exists(loader):
        print(f"[+] glibc 加载器就位: {loader}")
    else:
        print("[!] 没抽到 ld-linux-armhf.so.3 —— musl 系统上将无法运行厂商二进制", file=sys.stderr)

    if a.with_data:
        for rel in DATA_PATHS:
            if src.exists(rel):
                files = [rel]
            else:
                files = src.walk(rel)
            for f in files:
                blob = src.read(f)
                dst = os.path.join(a.out, f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as fh:
                    fh.write(blob)
                mode = src.mode(f)
                os.chmod(dst, (mode & 0o777) or 0o644)
                stack["data"].append({"path": f, "size": len(blob), "sha256": sha(blob)})

    with open(os.path.join(a.out, "STACK.json"), "w", encoding="utf-8") as f:
        json.dump(stack, f, ensure_ascii=False, indent=2)

    total = sum(x["size"] for x in stack["binaries"].values()) + \
        sum(x["size"] for x in stack["libraries"].values()) + \
        sum(x["size"] for x in stack["data"])
    print(f"\n二进制 {len(stack['binaries'])} 个, 库 {len(stack['libraries'])} 个, "
          f"数据文件 {len(stack['data'])} 个, 合计 {total / 1048576:.1f} MiB")
    if stack["missing"]:
        print(f"缺失依赖 {len(stack['missing'])} 条（原厂镜像里就没有）:")
        for m in stack["missing"][:10]:
            print(f"   {m['who']} -> {m['need']}")
    print(f"[+] 产物: {os.path.abspath(a.out)}")


if __name__ == "__main__":
    main()
