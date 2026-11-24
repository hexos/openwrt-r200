#!/usr/bin/env python3
"""extract_vendor_userland.py —— 把原厂那套 glibc 用户态/固件/内核模块抽出来备用。

为什么需要：R200 的 5G 拨号（netmgrd / qmi_ip_multiclient / dialup / rmnetcli + IPA）
和 WiFi（qcacld `wlan.ko` + 厂商 hostapd）都是 **armhf glibc** 的私有栈，
OpenWrt 默认是 musl，不能直接跑。要做第二阶段，就得先把这些原料按功能分组抽出来。

  python3 tools/extract_vendor_userland.py <target-files-ubi.zip | 已解开的rootfs目录> \
      -o vendor-userland [--groups modem,wifi,switch,modules] [--list]

输出目录里每个文件都带原始权限（符号链接也照原样恢复），并生成 MANIFEST.txt
（sha256 / 大小 / 类型），方便核对与后续打包。
"""
import argparse
import hashlib
import os
import posixpath
import stat
import sys
import zipfile

# 每个组：源路径前缀（相对根文件系统）+ 说明
GROUPS = {
    "modem": {
        "desc": "5G 拨号：QMI/RMNET/IPA 用户态（glibc）",
        "paths": [
            "usr/bin/netmgrd", "usr/bin/dialup", "usr/bin/rmnetcli",
            "usr/bin/qmi_ip_multiclient", "usr/bin/qmi_ip_cfg.sh",
            "usr/bin/at_cmd_task", "usr/bin/mnet", "usr/bin/usb_composition",
            "usr/bin/modm-shutdown", "usr/bin/qmi_shutdown_modem",
            "etc/qapi.conf", "etc/qmi_ip_cfg.xml", "etc/meig_apps_startup",
            "etc/start_pcie", "etc/disconnect",
            "lib/firmware/ipa", "lib/firmware/ipa_fws.b02", "lib/firmware/ipa_fws.b03",
            "lib/firmware/ipa_fws.b04", "lib/firmware/ipa_fws.b05",
            "lib/firmware/ipa_fws.mdt", "lib/firmware/ipa_fws.elf",
        ],
    },
    "wifi": {
        "desc": "WiFi：qcacld 内核模块 + 厂商 hostapd/wpa_supplicant + 配置/校准",
        "paths": [
            "usr/lib/modules/4.14.206-perf/extra/wlan.ko",
            "usr/lib/modules/4.14.206-perf/extra/wlan-mag.ko",
            "lib/firmware/wlan",
            "usr/sbin/hostapd", "usr/sbin/hostapd_cli",
            "usr/sbin/wpa_supplicant", "usr/sbin/wpa_cli",
            "usr/sbin/wlan_services", "usr/sbin/cnss_diag", "usr/sbin/iw",
            "usr/lib/libwpa_client.so", "usr/lib/libwpa_supplicant_8_lib.so.0.0.0",
            "etc/cld-hostapd-wlan0.sh", "etc/cld-hostapd-wlan1.sh",
            "etc/cld-hostapd-wlan2.sh",
            "etc/misc/wifi", "etc/data",
            "etc/initscripts", "etc/start_wlan_services*",
            "etc/systemd/system/start_wlan_services.service",
        ],
    },
    "switch": {
        "desc": "有线：QCA SSDK 交换芯片驱动（QCA8337）",
        "paths": [
            "usr/lib/modules/4.14.206-perf/kernel/drivers/qca-ssdk-nohnat",
            "usr/sbin/ssdk_sh", "usr/bin/ssdk_sh",
        ],
    },
    "modules": {
        "desc": "原厂全部内核模块（含 stub_dlkm / shortcut-fe / apr 等）",
        "paths": ["usr/lib/modules/4.14.206-perf"],
    },
    "glibc": {
        "desc": "armhf glibc 运行时（若要走 glibc 版 OpenWrt 或 chroot 方案）",
        "paths": [
            "lib/ld-linux-armhf.so.3", "lib/libc.so.6", "lib/libm.so.6",
            "lib/libpthread.so.0", "lib/libdl.so.2", "lib/librt.so.1",
            "lib/libresolv.so.2", "lib/libnss_dns.so.2", "lib/libnss_files.so.2",
            "lib/libgcc_s.so.1", "lib/libstdc++.so.6",
        ],
    },
}


def zip_mode(info):
    return info.external_attr >> 16


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def collect_from_zip(z, prefixes):
    """返回 [(rootfs路径, ZipInfo)]，按前缀匹配（前缀也可以是目录）。"""
    names = z.namelist()
    out = []
    for prefix in prefixes:
        p = prefix.lstrip("/")
        for cand in (f"SYSTEM/{p}", f"SYSTEM/{p}/"):
            if cand in names:
                if cand.endswith("/"):
                    root = cand
                    out += [(n[len("SYSTEM/"):], z.getinfo(n)) for n in names
                            if n.startswith(root) and not n.endswith("/")]
                else:
                    out.append((p, z.getinfo(cand)))
                break
        else:
            # 目录前缀通配
            root = f"SYSTEM/{p.rstrip('/')}/"
            hits = [n for n in names if n.startswith(root) and not n.endswith("/")]
            out += [(n[len("SYSTEM/"):], z.getinfo(n)) for n in hits]
    return out


def collect_from_dir(rootdir, prefixes):
    out = []
    for prefix in prefixes:
        p = os.path.join(rootdir, prefix.lstrip("/"))
        if os.path.isdir(p):
            for dirpath, _dirnames, filenames in os.walk(p):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    out.append((os.path.relpath(full, rootdir).replace("\\", "/"), full))
        elif os.path.exists(p):
            out.append((prefix.lstrip("/"), p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="target-files-ubi.zip 或已解开的原厂 rootfs 目录")
    ap.add_argument("-o", "--out", default="vendor-userland")
    ap.add_argument("--groups", default="modem,wifi,switch",
                    help="逗号分隔：modem,wifi,switch,modules,glibc")
    ap.add_argument("--list", action="store_true", help="只列出会抽取的文件，不写盘")
    a = ap.parse_args()

    wanted = [g.strip() for g in a.groups.split(",") if g.strip() in GROUPS]
    if not wanted:
        sys.exit(f"未知分组，可用: {', '.join(GROUPS)}")
    prefixes = []
    for g in wanted:
        print(f"[组] {g}: {GROUPS[g]['desc']}")
        prefixes += GROUPS[g]["paths"]

    is_zip = a.source.lower().endswith(".zip") and os.path.isfile(a.source)
    if is_zip:
        z = zipfile.ZipFile(a.source)
        items = collect_from_zip(z, prefixes)
    elif os.path.isdir(a.source):
        z = None
        items = collect_from_dir(a.source, prefixes)
    else:
        sys.exit("source 必须是 zip 或目录")
    # 去重
    seen = set()
    uniq = []
    for rel, meta in items:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((rel, meta))
    items = uniq
    print(f"\n共 {len(items)} 个文件，来自分组 {','.join(wanted)}")

    if a.list:
        for rel, _meta in items[:200]:
            print("   ", rel)
        if len(items) > 200:
            print(f"    ... 还有 {len(items) - 200} 个")
        return

    manifest = []
    pending_links = []
    for rel, meta in items:
        data = z.read(meta) if z is not None else open(meta, "rb").read()
        dst = os.path.join(a.out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        mode = zip_mode(meta) if z is not None else os.stat(meta).st_mode
        if stat.S_ISLNK(mode):
            pending_links.append((rel, data.decode("utf-8", "replace")))
            continue
        with open(dst, "wb") as f:
            f.write(data)
        os.chmod(dst, (mode & 0o777) or 0o644)
        manifest.append(f"{rel}\t{len(data)}\t{sha256_bytes(data)}")

    # 符号链接：优先按原样建；Windows 没权限时退化成"拷贝真实内容"
    for rel, target in pending_links:
        dst = os.path.join(a.out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            if os.path.lexists(dst):
                os.remove(dst)
            os.symlink(target, dst)
            manifest.append(f"{rel}\tsymlink -> {target}")
            continue
        except OSError:
            pass
        src = os.path.join(a.out, target.lstrip("/")) if target.startswith("/") \
            else os.path.normpath(os.path.join(os.path.dirname(dst), target))
        if os.path.isfile(src):
            import shutil
            shutil.copy2(src, dst)
            manifest.append(f"{rel}\tsymlink -> {target} (已改为拷贝)")
            print(f"  ! 建不了符号链接，改为拷贝内容: {rel} -> {target}")
        else:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(target)
            manifest.append(f"{rel}\tsymlink -> {target} (目标未抽取，留占位文本)")

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "MANIFEST.txt"), "w", encoding="utf-8") as f:
        f.write("# 从原厂固件抽取的用户态/模块清单（rel\\tsize\\tsha256）\n")
        f.write("\n".join(manifest) + "\n")
    print(f"\n[+] 已抽取到 {os.path.abspath(a.out)}（含 MANIFEST.txt）")
    print("    下一步思路见 docs/status.md 的『第二阶段』：")
    print("    路由 A：直接用 OpenWrt 的 uqmi 试 /dev/mhi_uci（改动最小，成功率未知）")
    print("    路由 B：编 glibc 版 OpenWrt，把本目录的二进制 + 模块 + 固件整体塞进去")


if __name__ == "__main__":
    main()
