#!/usr/bin/env python3
# /// script
# dependencies = ["h5py", "numpy"]
# ///
"""Mouse vitals from SLEAP output on the frameforge rig: are the mice alive and themselves?

Reads the hour-file predictions (`cam_0N.HH.predictions.slp`) straight from HDF5 and
computes, per cage (physical camera = cam_0N on this rig) and per hour:

  mice seen     median number of gated, de-duplicated instances per frame
                (compare with hcm_core.cohort.n_alive; counts are reliable in the
                dark/active phase, mice huddle in the light phase)
  movement      minutes with sustained movement: >= MOVE_MIN_FRAC of the minute's
                frames have a mouse displaced > MOVE_PX per frame (stable-body
                centroid, nearest instance in the previous frame)
  tail length   polyline TTI -> Tail_0 -> Tail_1 -> Tail_2 -> TailTip over every
                instance whose five tail keypoints are visible and confident; kept as
                a 2-px histogram so the DAY statistics pool all instances (never a
                single point). The headline is the EXTENDED tail length, the 95th
                percentile: the median depends on posture (curled vs stretched) and
                swings 20% between days, the 95th percentile is stable to 1-2%.
                Reported in px and cm (hcm_core rig.toml px_per_cm).

Per-hour results are cached in mouse_vitals_cache.json (keyed by file path + size),
so the daily run only reads the hours that landed since last time (~0.5 s per file).
Writes mouse_vitals.json (last N days + a per-cage verdict from the newest hour) for
the dashboard and the Slack report. Facts (mice per cage, scale, skeleton, light
cycle) come from hcm-core; this script computes, it does not define facts.

    uv run mouse_vitals.py                 # last 3 days (default), print a summary
    uv run mouse_vitals.py --days 7
    uv run mouse_vitals.py --rebuild       # ignore the cache
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, "/home/exx/vast/leo/hcm-core")
from hcm_core import cohort as K, facts  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
INFERENCE_ROOT = Path("/home/exx/vast/leo/datasets/inference-frameforge-HCM")
OUT_FILE = SCRIPT_DIR / "mouse_vitals.json"
CACHE_FILE = SCRIPT_DIR / "mouse_vitals_cache.json"
CAMS = ["cam_01", "cam_02", "cam_03", "cam_04"]

RIG = facts("rig")
PX_PER_CM = float(RIG["optics"]["px_per_cm"])
FPS = float(RIG["video"]["fps_nominal"])
NODES = list(RIG["skeleton"]["nodes"])
STABLE = list(RIG["skeleton"]["stable_body"])
LIGHTS_OFF, LIGHTS_ON = float(RIG["light_cycle"]["lights_off_hour"]), float(RIG["light_cycle"]["lights_on_hour"])
TAIL = ["TTI", "Tail_0", "Tail_1", "Tail_2", "TailTip"]   # base to tip, checked against the data 2026-10-19

SCORE_THR = 0.3        # instance score gate (same as the pose pipeline)
PT_SCORE_THR = 0.5     # keypoint confidence for a tail measurement
DEDUP_PX = 50.0        # 2 cm: two instances closer than this are one mouse
K_MAX = 6              # instances kept per frame
MOVE_PX = 2.0          # px per frame; rest jitter is ~0.5 px, walking is 5+
MOVE_MIN_FRAC = 0.10   # a minute is "active" if >= 10% of its frames show a moving mouse (>= 6 s)
STALE_HOURS = 3.0      # no active minute for this long = alert
TAIL_BINS = np.arange(0, 402, 2.0)  # 2-px histogram, 0-400 px
TAIL_CHANGE_WARN = 5.0              # % day-to-day change of the extended tail length worth a warning
                                    # (observed day-to-day scatter 1-2% on ~100k+ samples)


def is_dark(hour: int) -> bool:
    return LIGHTS_OFF <= hour + 0.5 < LIGHTS_ON


def analyse_file(path: Path) -> dict:
    """Per-hour vitals from one predictions file."""
    with h5py.File(path, "r") as h:
        meta = json.loads(h["metadata"].attrs["json"])
        names = [x["name"] for x in meta["nodes"]]
        frames, inst, pts = h["frames"][:], h["instances"][:], h["pred_points"][:]
    if names != NODES:
        raise ValueError(f"skeleton nodes differ from hcm-core rig.toml in {path.name}")
    N = len(names)
    n_frames = int(frames["frame_idx"].max()) + 1 if len(frames) else 0
    if n_frames == 0 or len(inst) == 0:
        return {"frames": n_frames, "instances": 0, "mice": None, "active_min": 0, "moving_frac": 0.0,
                "last_active_min": None, "tail_n": 0, "tail_hist": [0] * (len(TAIL_BINS) - 1), "tail_sum": 0.0}

    ps = inst["point_id_start"].astype(np.int64)
    full = (inst["point_id_end"].astype(np.int64) - ps) == N
    idx = ps[:, None] + np.arange(N)[None, :]
    idx[~full] = ps[~full, None]
    vis = pts["visible"][idx].astype(bool) & full[:, None]
    x, y, sc = pts["x"][idx], pts["y"][idx], pts["score"][idx]

    # --- tail length (px): all five tail keypoints visible and confident
    ti = [names.index(t) for t in TAIL]
    ok = vis[:, ti].all(1) & (sc[:, ti] > PT_SCORE_THR).all(1) & (inst["score"] > SCORE_THR)
    tail = np.hypot(np.diff(x[:, ti], axis=1), np.diff(y[:, ti], axis=1)).sum(1)[ok]
    tail_hist = np.histogram(tail, bins=TAIL_BINS)[0]

    # --- stable-body centroid per instance; per-frame table of the best K instances
    si = [names.index(s) for s in STABLE]
    sv = vis[:, si]
    nsv = sv.sum(1)
    cx = np.where(nsv > 0, np.where(sv, x[:, si], 0).sum(1) / np.maximum(nsv, 1), np.nan)
    cy = np.where(nsv > 0, np.where(sv, y[:, si], 0).sum(1) / np.maximum(nsv, 1), np.nan)
    fid2 = np.full(int(frames["frame_id"].max()) + 1, -1, np.int64)
    fid2[frames["frame_id"]] = frames["frame_idx"]
    fi = fid2[inst["frame_id"]]
    gate = full & (nsv > 0) & (inst["score"] > SCORE_THR) & (fi >= 0)
    order = np.lexsort((-inst["score"], fi))
    order = order[gate[order]]
    fs = fi[order]
    rank = np.arange(len(order)) - np.searchsorted(fs, fs, side="left")
    keep = rank < K_MAX
    C = np.full((n_frames, K_MAX, 2), np.nan, np.float32)
    C[fs[keep], rank[keep], 0] = cx[order][keep]
    C[fs[keep], rank[keep], 1] = cy[order][keep]

    # --- mice per frame: de-duplicate instances closer than DEDUP_PX
    d = np.sqrt(((C[:, :, None, :] - C[:, None, :, :]) ** 2).sum(-1))
    earlier = np.tril(np.ones((K_MAX, K_MAX), bool), -1)
    valid = np.isfinite(C[:, :, 0])
    count = (valid & ~((d < DEDUP_PX) & earlier[None]).any(2)).sum(1)
    mice = float(np.median(count[valid.any(1)])) if valid.any() else None

    # --- movement: nearest-instance displacement to the previous frame, per minute
    dd = np.sqrt(((C[1:, :, None, :] - C[:-1, None, :, :]) ** 2).sum(-1))
    with np.errstate(invalid="ignore"):
        disp = np.nanmin(np.where(np.isfinite(dd), dd, np.inf), axis=2)
    disp[~np.isfinite(disp)] = np.nan
    frame_max = np.nanmax(np.where(np.isfinite(disp), disp, -1), axis=1)
    moving = frame_max > MOVE_PX                       # a frame where some mouse moved
    per_min = int(FPS * 60)
    n_min = int(np.ceil(len(moving) / per_min))
    act = np.array([moving[m * per_min:(m + 1) * per_min].mean() for m in range(n_min)])
    active = act >= MOVE_MIN_FRAC
    return {
        "frames": n_frames, "instances": int(gate.sum()), "mice": mice,
        "active_min": int(active.sum()), "moving_frac": round(float(moving.mean()), 4),
        "last_active_min": int(np.where(active)[0].max()) if active.any() else None,
        "tail_n": int(ok.sum()), "tail_hist": tail_hist.tolist(), "tail_sum": float(tail.sum()),
    }


def tail_summary(hist: np.ndarray, total: float) -> dict | None:
    n = int(hist.sum())
    if n == 0:
        return None
    cdf = np.cumsum(hist)
    centers = (TAIL_BINS[:-1] + TAIL_BINS[1:]) / 2
    q = lambda p: float(centers[min(len(centers) - 1, np.searchsorted(cdf, p * n))])
    med, p95 = q(0.5), q(0.95)
    return {"n": n, "extended_px": round(p95, 1), "extended_cm": round(p95 / PX_PER_CM, 2),
            "median_px": round(med, 1), "median_cm": round(med / PX_PER_CM, 2),
            "mean_px": round(total / n, 1), "q25_px": round(q(0.25), 1), "q75_px": round(q(0.75), 1),
            "p90_px": round(q(0.90), 1)}


def main():
    ap = argparse.ArgumentParser(description="Mouse vitals from SLEAP output (frameforge rig)")
    ap.add_argument("--days", type=int, default=3, help="days to cover, ending today (default 3)")
    ap.add_argument("--rebuild", action="store_true", help="ignore the per-hour cache")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    args = ap.parse_args()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.days - 1, -1, -1)]
    cache = {}
    if CACHE_FILE.exists() and not args.rebuild:
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            cache = {}

    # --- find hour files, analyse the ones not cached
    jobs = []   # (day, cam, hour, path, key)
    deployments = sorted(d for d in os.listdir(INFERENCE_ROOT) if (INFERENCE_ROOT / d).is_dir())
    for day in days:
        for cam in CAMS:
            for dep in deployments:
                dd = INFERENCE_ROOT / dep / cam / f"{day}-00-00-00"
                if not dd.is_dir():
                    continue
                for f in sorted(os.listdir(dd)):
                    if not f.endswith(".predictions.slp"):
                        continue
                    p = dd / f
                    try:
                        key = f"{p}|{p.stat().st_size}"
                    except OSError:
                        continue
                    jobs.append((day, cam, int(f.split(".")[1]), p, key))
    todo = [j for j in jobs if j[4] not in cache]
    print(f"{len(jobs)} hour files over {len(days)} days, {len(todo)} to analyse")

    def run(job):
        try:
            return job[4], analyse_file(job[3])
        except Exception as exc:
            print(f"  WARNING: {job[3].name}: {exc}", file=sys.stderr)
            return job[4], None
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (key, res) in enumerate(pool.map(run, todo), 1):
            if res is not None:
                cache[key] = res
            if i % 24 == 0:
                print(f"  {i}/{len(todo)}")
    # keep the cache to the files we still look at
    live = {j[4] for j in jobs}
    cache = {k: v for k, v in cache.items() if k in live}
    CACHE_FILE.write_text(json.dumps(cache, separators=(",", ":")))

    # --- per day, per cage
    out_days = {}
    for day in days:
        out_days[day] = {}
        for cam in CAMS:
            hours = {}
            hist = np.zeros(len(TAIL_BINS) - 1)
            tsum = 0.0
            for d, c, h, p, key in jobs:
                if d != day or c != cam or key not in cache:
                    continue
                r = cache[key]
                hours[f"{h:02d}"] = {"active_min": r["active_min"], "moving_frac": r["moving_frac"],
                                    "mice": r["mice"], "tail_n": r["tail_n"], "dark": is_dark(h),
                                    "last_active_min": r["last_active_min"]}
                hist += np.array(r["tail_hist"])
                tsum += r["tail_sum"]
            if not hours:
                continue
            dark_counts = [v["mice"] for v in hours.values() if v["dark"] and v["mice"] is not None]
            all_counts = [v["mice"] for v in hours.values() if v["mice"] is not None]
            last_move = None
            for hh in sorted(hours, reverse=True):
                if hours[hh]["active_min"] > 0:
                    last_move = f"{hh}:{hours[hh]['last_active_min']:02d}"
                    break
            out_days[day][cam] = {
                "expected_mice": K.n_alive(cam, day, disk=False),
                "mice_seen": round(float(np.median(dark_counts)), 1) if dark_counts else None,
                "mice_seen_all_hours": round(float(np.median(all_counts)), 1) if all_counts else None,
                "hours_done": len(hours),
                "active_minutes": sum(v["active_min"] for v in hours.values()),
                "hours_with_movement": sum(1 for v in hours.values() if v["active_min"] > 0),
                "last_movement": last_move,
                "tail": tail_summary(hist, tsum),
                "hours": hours,
            }

    # --- verdict per cage from the newest inferred hour
    latest = {}
    for cam in CAMS:
        rec = None
        for day in reversed(days):
            if cam in out_days.get(day, {}):
                rec = (day, out_days[day][cam])
                break
        if rec is None:
            latest[cam] = {"status": "alert", "note": "no SLEAP output in the window"}
            continue
        day, dv = rec
        hh = max(dv["hours"])
        hv = dv["hours"][hh]
        # hours since the last active minute, measured against the end of the newest hour file
        newest_end = datetime.strptime(day, "%Y-%m-%d") + timedelta(hours=int(hh) + 1)
        last_dt = None
        for d2 in reversed(days):
            r2 = out_days.get(d2, {}).get(cam)
            if r2 and r2["last_movement"]:
                last_dt = datetime.strptime(f"{d2} {r2['last_movement']}", "%Y-%m-%d %H:%M")
                break
        since = round((newest_end - last_dt).total_seconds() / 3600, 1) if last_dt else None
        prev = None
        for d2 in reversed(days):
            if d2 < day and out_days.get(d2, {}).get(cam, {}).get("tail"):
                prev = (d2, out_days[d2][cam]["tail"])
                break
        tail_change = None
        if dv["tail"] and prev:
            tail_change = round((dv["tail"]["extended_px"] / prev[1]["extended_px"] - 1) * 100, 1)
        exp = dv["expected_mice"]
        seen = dv["mice_seen"] if dv["mice_seen"] is not None else dv["mice_seen_all_hours"]
        notes, status = [], "ok"
        if since is None or since > STALE_HOURS:
            status = "alert"; notes.append(f"no movement for {since if since is not None else '>' + str(args.days * 24)} h")
        elif hv["active_min"] == 0:
            status = "warn"; notes.append(f"quiet in the newest hour ({hh}:00), last movement {dv['last_movement']}")
        if exp is not None and seen is not None and seen < exp - 0.5:
            status = "alert" if status != "alert" else status; notes.append(f"{seen:g} of {exp} mice seen")
        if tail_change is not None and abs(tail_change) >= TAIL_CHANGE_WARN:
            if status == "ok": status = "warn"
            notes.append(f"tail length {tail_change:+.1f}% vs {prev[0]}")
        latest[cam] = {
            "date": day, "hour": int(hh), "newest_hour_end": newest_end.isoformat(timespec="minutes"),
            "alive": status != "alert", "status": status, "note": "; ".join(notes),
            "moving_last_hour": hv["active_min"] > 0, "active_min_last_hour": hv["active_min"],
            "last_movement": f"{day} {dv['last_movement']}" if dv["last_movement"] else None,
            "hours_since_movement": since,
            "mice_expected": exp, "mice_seen": seen, "mice_seen_dark": dv["mice_seen"],
            "tail_today": dv["tail"], "tail_prev": prev[1] if prev else None, "tail_prev_date": prev[0] if prev else None,
            "tail_change_pct": tail_change,
        }

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": str(INFERENCE_ROOT),
        "px_per_cm": PX_PER_CM,
        "thresholds": {"move_px_per_frame": MOVE_PX, "active_minute_frac": MOVE_MIN_FRAC, "stale_hours": STALE_HOURS,
                       "tail_pt_score": PT_SCORE_THR, "tail_change_warn_pct": TAIL_CHANGE_WARN, "dedup_px": DEDUP_PX},
        "days": out_days,
        "latest": latest,
    }
    with tempfile.NamedTemporaryFile("w", dir=args.out.parent, suffix=".json", delete=False) as f:
        json.dump(out, f, separators=(",", ":"))
        tmp = f.name
    os.replace(tmp, args.out)

    print(f"\nMouse vitals ({now:%Y-%m-%d %H:%M}), px/cm {PX_PER_CM:g}")
    for cam in CAMS:
        v = latest[cam]
        if "date" not in v:
            print(f"  {cam}: {v['status']} — {v['note']}"); continue
        t, p = v["tail_today"], v["tail_prev"]
        tail = (f"tail {t['extended_cm']:.2f} cm extended (median {t['median_cm']:.2f}, n={t['n']:,})"
                + (f" vs {p['extended_cm']:.2f} cm {v['tail_prev_date'][5:]} ({v['tail_change_pct']:+.1f}%)" if p else "")) if t else "tail: no measurement"
        print(f"  Cam {cam[-1]}: {v['status'].upper():5s} newest hour {v['date']} {v['hour']:02d}:00: "
              f"{v['active_min_last_hour']} active min, last movement {v['last_movement']}, "
              f"{v['mice_seen']} of {v['mice_expected']} mice seen, {tail}{(' — ' + v['note']) if v['note'] else ''}")
    print(f"Written {args.out} ({os.path.getsize(args.out) // 1024} KB)")


if __name__ == "__main__":
    main()
