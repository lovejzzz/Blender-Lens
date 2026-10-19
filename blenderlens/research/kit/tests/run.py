#!/usr/bin/env python3
# 统一测试入口（0.8.1，给 AI 与人用）：分级、并行、机器可读报告、指标基线对比。
#   python3 tests/run.py quick            # 纯 Python + 两个快速 Blender 测试，约 2–3 分钟
#   python3 tests/run.py standard         # + 出瞳覆盖、预设、GPU、发腮、鬼影、映射（C），约 15–20 分钟
#   python3 tests/run.py full             # 全部 + 打包 + 干净环境安装，约 1 小时
#   python3 tests/run.py --only mumps     # 只跑名字含 mumps 的
#   python3 tests/run.py --list           # 列出全部测试与分级
#   python3 tests/run.py standard --update-baseline    # 当前结果写成新的基线（确认结果正确之后）
# 报告：renders/test_report.json（每个测试的 pass / 用时 / 指标 / 与基线的偏差 / 失败时的输出尾部）。
# 退出码：0 = 全部通过且没有超出容差的指标漂移；1 = 有失败；2 = 只有指标漂移。
# 基线：tests/baseline.json（只存数值指标；时间类不比较）。容差：相对 5% 或绝对 1e-3，取大者。
import argparse, concurrent.futures as cf, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
REPORT = os.path.join(ROOT, "renders", "test_report.json")
BASELINE = os.path.join(ROOT, "tests", "baseline.json")
TIERS = ("quick", "standard", "full")


def _mapping_ok(d):
    ok = True
    for mode, lim in (("ANALYTIC", 1.0), ("GEOMETRY", 1.5)):
        m = d.get(mode)
        if m:
            errs = [abs(v) for v in list(m["col_err_px"].values()) + list(m["row_err_px"].values())]
            ok &= bool(errs) and max(errs) <= lim
    return ok


def _presets_ok(d):
    return all(not v.get("mesh_problems") and v.get("focus_sync") and not v.get("clashes_at_mfd") for v in d.values())


def _smoke_ok(d):
    return d.get("op") == ["FINISHED"] and d.get("type") == "CUSTOM"


# (名字, 分级, 类型, 参数, 结果标签, 判据)。类型：py = python3 脚本（退出码）；bl = Blender 脚本（解析「标签 {json}」行）；sh = shell
TESTS = [
    ("optics", "quick", "py", ["tests/test_optics.py"], None, None),
    ("flare", "quick", "py", ["tests/test_flare.py"], None, None),
    ("coating", "quick", "py", ["tests/test_coating.py"], None, None),
    ("glare", "quick", "py", ["tests/test_glare.py"], None, None),
    ("reference", "quick", "py", ["tests/test_reference.py"], None, None),
    ("presets", "quick", "py", ["tests/test_presets.py"], None, None),
    ("gpu_header", "quick", "py", ["tests/test_gpu_lenspack.py"], None, None),
    ("bl_smoke", "quick", "bl", ["tests/bl_smoke.py"], "SMOKE", _smoke_ok),
    ("bl_agent", "quick", "bl", ["tests/bl_agent.py"], "AGENT", None),
    ("pupil", "standard", "py", ["tests/test_pupil.py"], None, None),
    ("bl_presets", "standard", "bl", ["tests/bl_presets_smoke.py"], "PRESETS", _presets_ok),
    ("bl_gpu", "standard", "bl", ["tests/bl_gpu.py"], "GPU", None),
    ("bl_mumps_c", "standard", "bl", ["tests/bl_mumps.py", "--", "c_series_style.json"], "MUMPS", None),
    ("bl_ghosts", "standard", "bl", ["tests/bl_gpu_ghosts.py"], "GHOSTS", None),
    ("bl_units", "standard", "bl", ["tests/bl_units.py"], "UNITS", None),
    ("map_c", "standard", "bl", ["tests/bl_verify_mapping.py", "--", "c_series_style.json"], "MAPPING", _mapping_ok),
    ("bl_spectral", "full", "bl", ["tests/bl_spectral.py"], "SPECTRAL", None),
    ("bl_ghosts_broadband", "full", "bl", ["tests/bl_gpu_ghosts.py", "--", "BROADBAND"], "GHOSTS", None),
    ("bl_mumps_e", "full", "bl", ["tests/bl_mumps.py", "--", "e_series_style.json"], "MUMPS", None),
    ("bl_sampling", "full", "bl", ["tests/bl_sampling.py"], "SAMPLING", None),
    ("bl_dispersion", "full", "bl", ["tests/bl_dispersion.py"], "DISP", None),
    ("bl_percentage", "full", "bl", ["tests/bl_percentage.py"], "PCT", None),
    ("bl_follow", "full", "bl", ["tests/bl_follow.py"], "FOLLOW", None),
    ("bl_frames", "full", "bl", ["tests/bl_frames.py"], "FRAMES", None),
    ("map_e", "full", "bl", ["tests/bl_verify_mapping.py", "--", "e_series_style.json"], "MAPPING", _mapping_ok),
    ("map_g", "full", "bl", ["tests/bl_verify_mapping.py", "--", "g_series_style.json"], "MAPPING", _mapping_ok),
    ("map_pv", "full", "bl", ["tests/bl_verify_mapping.py", "--", "panavision_rear_134.json"], "MAPPING", _mapping_ok),
    ("map_pv_tele", "full", "bl", ["tests/bl_verify_mapping.py", "--", "panavision_rear_134_tele.json"], "MAPPING", _mapping_ok),
    ("map_c_gpu", "full", "bl", ["tests/bl_verify_mapping.py", "--", "c_series_style.json", "--gpu"], "MAPPING", _mapping_ok),
    ("map_pv_tele_gpu", "full", "bl", ["tests/bl_verify_mapping.py", "--", "panavision_rear_134_tele.json", "--gpu"], "MAPPING", _mapping_ok),
    ("bl_acceptance", "full", "bl", ["tests/bl_acceptance.py", "--", "all"], "ACCEPT", None),
    ("package_install", "full", "sh", ["tests/package_install.sh"], "INSTALLED_GPU", None),
]

SKIP_METRIC = re.compile(r"(second|time|_s$|path|file|seed|resolution|px\b)", re.I)


def flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list) and len(d) <= 8 and all(isinstance(x, (int, float)) for x in d):
        for i, x in enumerate(d):
            out.update(flatten(x, f"{prefix}[{i}]"))
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out[prefix] = float(d)
    return out


def parse_tagged(text, tag):
    """所有「标签 {json}」行（一个测试可能输出多行，如 ACCEPT）。"""
    found = []
    for line in text.splitlines():
        if line.startswith(tag + " "):
            body = line[len(tag) + 1:].strip()
            try:
                v = json.loads(body)
            except json.JSONDecodeError:
                try:
                    import ast
                    v = ast.literal_eval(body)          # INSTALLED_GPU 一类用 Python 字面量打印
                except (ValueError, SyntaxError):
                    continue
            found.append(v)
    return found


def run_one(t):
    name, tier, kind, args, tag, judge = t
    t0 = time.time()
    if kind == "py":
        cmd = [sys.executable] + [os.path.join(ROOT, args[0])] + args[1:]
    elif kind == "bl":
        cmd = [BLENDER, "-b", "--factory-startup", "--python", os.path.join(ROOT, args[0])] + args[1:]
    else:
        cmd = ["zsh", os.path.join(ROOT, args[0]), BLENDER] + args[1:]
    try:
        pr = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3600)
        text, code = pr.stdout + pr.stderr, pr.returncode
    except subprocess.TimeoutExpired as e:
        text, code = (e.stdout or "") + "\nTIMEOUT", -1
    res = dict(name=name, tier=tier, kind=kind, seconds=round(time.time() - t0, 1))
    if kind == "py":
        res["pass"] = code == 0 and "FAIL" not in text
        res["checks"] = len(re.findall(r"^ok ", text, re.M))
    else:
        data = parse_tagged(text, tag)
        res["data"] = data
        if not data:
            res["pass"] = False
        elif judge is not None:
            res["pass"] = all(judge(d) for d in data)
        else:
            res["pass"] = all(d.get("PASS", True) for d in data if isinstance(d, dict))
        res["pass"] = bool(res["pass"]) and "Traceback" not in text
        merged = {}
        for i, d in enumerate(data):
            merged.update(flatten(d, f"{i}" if len(data) > 1 else ""))
        res["metrics"] = {k: v for k, v in merged.items() if not SKIP_METRIC.search(k)}
    if not res["pass"]:
        res["tail"] = text[-2500:]
    return res


def compare(results, baseline):
    drift = []
    for r in results:
        base = baseline.get(r["name"], {})
        for k, v in r.get("metrics", {}).items():
            if k in base:
                b = base[k]
                tol = max(0.05 * abs(b), 1e-3)
                if abs(v - b) > tol:
                    drift.append(dict(test=r["name"], metric=k, baseline=b, now=v, delta=round(v - b, 6)))
    return drift


def main():
    ap = argparse.ArgumentParser(description="BlenderLens 测试")
    ap.add_argument("tier", nargs="?", default="quick", choices=TIERS)
    ap.add_argument("--only", help="只跑名字含这个子串的测试（忽略分级）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--json", action="store_true", help="只在 stdout 打印报告 JSON")
    a = ap.parse_args()
    if a.list:
        for n, tier, kind, args, *_ in TESTS:
            print(f"{tier:9s} {kind:3s} {n:22s} {' '.join(args)}")
        return 0
    lvl = TIERS.index(a.tier)
    todo = [t for t in TESTS if (a.only and a.only in t[0]) or (not a.only and TIERS.index(t[1]) <= lvl)]
    log = (lambda *x: None) if a.json else (lambda *x: print(*x, flush=True))
    log(f"BlenderLens 测试 · {a.only or a.tier} · {len(todo)} 项 · Blender {BLENDER}")
    t0 = time.time()
    results = []
    py = [t for t in todo if t[2] == "py"]
    rest = [t for t in todo if t[2] != "py"]
    with cf.ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 2)) as ex:   # 纯 Python 并行
        for r in ex.map(run_one, py):
            results.append(r); log(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['name']:22s} {r['seconds']:6.1f} s")
    for t in rest:                                                            # Blender 测试串行（争 GPU / CPU）
        r = run_one(t)
        results.append(r); log(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['name']:22s} {r['seconds']:6.1f} s")
    baseline = json.load(open(BASELINE)) if os.path.exists(BASELINE) else {}
    drift = compare(results, baseline)
    version = re.search(r'^version\s*=\s*"([^"]+)"', open(os.path.join(ROOT, "blenderlens", "blender_manifest.toml")).read(), re.M).group(1)
    rep = dict(version=version, tier=a.only or a.tier, seconds=round(time.time() - t0, 1),
               passed=sum(r["pass"] for r in results), failed=[r["name"] for r in results if not r["pass"]],
               metric_drift=drift, results=results, time=time.strftime("%Y-%m-%d %H:%M:%S"))
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    json.dump(rep, open(REPORT, "w"), ensure_ascii=False, indent=1)
    if a.update_baseline:
        for r in results:
            if r["pass"] and r.get("metrics"):
                baseline[r["name"]] = r["metrics"]
        json.dump(baseline, open(BASELINE, "w"), ensure_ascii=False, indent=1, sort_keys=True)
        log(f"基线已更新：{BASELINE}")
    if a.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        log(f"通过 {rep['passed']}/{len(results)}，用时 {rep['seconds']:.0f} s；失败：{rep['failed'] or '无'}")
        for d in drift[:20]:
            log(f"  指标漂移 {d['test']}.{d['metric']}: {d['baseline']} → {d['now']}")
        log(f"报告：{REPORT}")
    return 1 if rep["failed"] else (2 if drift and not a.update_baseline else 0)


if __name__ == "__main__":
    sys.exit(main())
