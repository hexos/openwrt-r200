#!/usr/bin/env python3
"""publish_github.py —— 把本工程推到 GitHub（不需要装 git）。

用 GitHub REST API 完成：建仓库 → 逐文件上传 → 打印 Actions 地址。
token 只从环境变量读，不落盘、不打印、不进任何配置文件。

用法（PowerShell，token 用 windows 凭据/临时环境变量给）：

    $env:GITHUB_TOKEN = "<你的 token>"
    python tools\\publish_github.py --owner hexos --repo openwrt-r200
    Remove-Item Env:GITHUB_TOKEN        # 用完立刻清掉

参数：
  --owner        账号或组织
  --repo         仓库名（默认 openwrt-r200）
  --token-env    环境变量名（默认 GITHUB_TOKEN）
  --private      建成私有仓库（默认）
  --public       建成公开仓库
  --branch       默认 main
  --dry-run      只打印将要上传哪些文件
  --skip-existing 已存在的文件跳过（默认覆盖更新）
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"out", "__pycache__", ".git", "vendor-stack", "out-test"}
SKIP_SUFFIX = {".pyc"}


def req(method, path, token, payload=None):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"token {token}")
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "r200-openwrt-publisher")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            msg = json.loads(body).get("message", body[:200])
        except Exception:                                # noqa: BLE001
            msg = body[:200]
        return e.code, {"message": msg}


def collect_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in SKIP_SUFFIX:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            if rel.split("/")[0] in SKIP_DIRS:
                continue
            out.append((rel, full))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", default="openwrt-r200")
    ap.add_argument("--token-env", default="GITHUB_TOKEN")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--private", dest="private", action="store_true", default=True)
    ap.add_argument("--public", dest="private", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()

    token = os.environ.get(a.token_env, "").strip()
    if not token:
        sys.exit(f"环境变量 {a.token_env} 里没有 token")
    if not token.startswith(("ghp_", "github_pat_")):
        print("! token 看起来不像 GitHub PAT，继续尝试…", file=sys.stderr)

    files = collect_files()
    total = sum(os.path.getsize(p) for _r, p in files)
    print(f"待上传 {len(files)} 个文件，合计 {total / 1024:.0f} KiB")
    for rel, _ in files:
        print("   ", rel)
    if a.dry_run:
        return

    status, me = req("GET", "/user", token)
    if status != 200:
        sys.exit(f"token 无效（HTTP {status}: {me.get('message')}），"
                 f"请确认没有被吊销、且带 repo 权限")
    login = me.get("login")
    print(f"\n[+] 认证通过: {login}")

    status, repo = req("POST", "/user/repos" if a.owner == login else f"/orgs/{a.owner}/repos",
                       token, {"name": a.repo, "private": a.private,
                               "description": "OpenWrt for Pinsu R200 (Qualcomm SDX55 / sdxprairie), "
                                              "9008/EDL flashable",
                               "has_issues": True, "has_wiki": False,
                               "auto_init": False})
    if status == 201:
        print(f"[+] 已创建仓库 {a.owner}/{a.repo}（{'私有' if a.private else '公开'}）")
    elif status == 422:
        print(f"[=] 仓库 {a.owner}/{a.repo} 已存在，继续往里推")
    else:
        sys.exit(f"建仓库失败 HTTP {status}: {repo.get('message')}")

    okc = skip = 0
    for rel, full in files:
        with open(full, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        payload = {"message": f"add {rel}", "content": content, "branch": a.branch}
        if a.skip_existing:
            st, exist = req("GET", f"/repos/{a.owner}/{a.repo}/contents/{rel}?ref={a.branch}", token)
            if st == 200:
                skip += 1
                continue
        st, resp = req("PUT", f"/repos/{a.owner}/{a.repo}/contents/{rel}", token, payload)
        if st in (200, 201):
            okc += 1
            print(f"   ✓ {rel}")
        elif st == 422 and "sha" in str(resp.get("message", "")).lower():
            # 文件已存在：取 sha 后覆盖
            st2, exist = req("GET", f"/repos/{a.owner}/{a.repo}/contents/{rel}?ref={a.branch}", token)
            if st2 == 200:
                payload["sha"] = exist["sha"]
                st3, r3 = req("PUT", f"/repos/{a.owner}/{a.repo}/contents/{rel}", token, payload)
                if st3 in (200, 201):
                    okc += 1
                    print(f"   ↻ {rel}（覆盖）")
                    continue
            print(f"   ✗ {rel}: {resp.get('message')}")
        else:
            print(f"   ✗ {rel}: HTTP {st} {resp.get('message')}")

    print(f"\n[+] 完成：上传/更新 {okc} 个文件" + (f"，跳过 {skip} 个" if skip else ""))
    print(f"    仓库   https://github.com/{a.owner}/{a.repo}")
    print(f"    Actions https://github.com/{a.owner}/{a.repo}/actions/workflows/build.yml")
    print("    下一步：Actions → build-r200-openwrt → Run workflow（先 musl-core）")


if __name__ == "__main__":
    main()
