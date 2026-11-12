"""MaAI のオフライン評価。対話エンジンは起動しない。

    python -m scripts.maai_offline work/inputs/small --modes vap,bc,bc_det --out result.json
    python scripts/maai_offline.py work/runs/baseline-1 --out old-vap-rows.json

生成済み入力は各シナリオの先頭（T3/T4 は末尾も）を前1秒・後2秒の無音付きで評価する。
旧 run + vap の JSON は従来の行リストを維持する。他は設定・計測・集計・評価行を保存する。
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.maai_input import SAMPLE_RATE, load_clips, source_kind
from bench.score.maai import SCORES, evaluate, summarize

FRAME = 160  # 旧スクリプトから公開していた入力チャンクのサンプル数


def model_settings(mode: str, frame_rate: float, encoder: str = "cpc") -> tuple[str, float, int]:
    """(model_type, frame_rate, context 秒)。bc_det の日本語の重みは Mimi（12.5 Hz、20 秒）だけにある。"""
    if mode == "bc_det" or encoder == "mimi":
        if mode == "bc_2type":
            raise ValueError("bc_2type には Mimi の重みがない")
        return "normal-ver2", 12.5, 20
    return "normal", frame_rate, 5


def make_model(mode: str, lang: str, frame_rate: float, encoder: str = "cpc") -> Any:
    import torch
    from maai import Maai, MaaiInput

    torch.set_num_threads(2)
    model_type, rate, context = model_settings(mode, frame_rate, encoder)
    # Zero / ch2省略では mono に変わる。無音も Chunk として渡し、stereo を維持する。
    return Maai(
        mode=mode,
        lang=lang,
        frame_rate=rate,
        context_len_sec=context,
        model_type=model_type,
        audio_ch1=MaaiInput.Chunk(),
        audio_ch2=MaaiInput.Chunk(),
        device="cpu",
    )


def run_model(model: Any, audio: np.ndarray, mode: str, bc_user_ch1: bool = False) -> list[dict]:
    """live worker と同じ process を同期で1フレームずつ呼び、必ず結果を回収する。

    start() のキュー消去との競合、100チャンクでの破棄、stop中の推論との競合を避ける。
    モデルの状態はクリップごとにリセットし、時刻は壁時計でなく消費サンプル数で決める。
    """
    model.reset_runtime_state()
    while True:
        try:
            model.result_dict_queue.get_nowait()
        except queue.Empty:
            break
    size = model.audio_samples_per_frame
    silence = np.zeros(size, dtype=np.float32)
    out = []
    for start in range(0, len(audio) - size + 1, size):
        user = audio[start : start + size].astype(np.float32, copy=False)
        # bc系は内部でswapするので公開入力は system,user。vap/bc_det は user,system。
        # bc系の向きは文書とコードで食い違うので、bc_user_ch1 で逆向きも試せるようにする。
        bc_order = (user, silence) if bc_user_ch1 else (silence, user)
        channels = bc_order if mode in {"bc", "bc_2type"} else (user, silence)
        model.process(*channels)
        try:
            result = model.result_dict_queue.get_nowait()
        except queue.Empty as exc:
            raise RuntimeError(f"{mode}: {start + size} サンプル時点の結果がない") from exc
        if not model.result_dict_queue.empty():
            raise RuntimeError(f"{mode}: 1フレームに複数の結果がある")
        frame = {"t": (start + size) / SAMPLE_RATE}
        for key in SCORES[mode]:
            value = result[key]
            # vap は Tomoko（ch2）の活動割合、bc_det はユーザー（ch1）の検出確率。
            if mode in {"vap", "bc_det"}:
                value = value[1 if mode == "vap" else 0]
            frame[key] = float(value)
        out.append(frame)
    return out


def run_vap(audio: np.ndarray, lang: str, frame_rate: int) -> list[tuple[float, float, float]]:
    """旧API: (入力の時刻, p_now[ch2], p_future[ch2])。"""
    model = make_model("vap", lang, frame_rate)
    try:
        return [(f["t"], f["p_now"], f["p_future"]) for f in run_model(model, audio, "vap")]
    finally:
        model.stop(wait=True)


def at(series: list[tuple[float, float, float]], t: float) -> tuple[float, float] | None:
    past = [s for s in series if s[0] <= t + 1e-9]
    return (past[-1][1], past[-1][2]) if past else None


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _dist(value: dict) -> str:
    return " / ".join(_number(value[k]) for k in ("p10", "median", "p90"))


def print_summary(summary: dict) -> None:
    if "vap" in summary:
        print("\nvap（shift が正例。p_now/p_future は Tomoko の相対活動割合）")
        print("axis delay score    shift hold AUC    shift p10/中央値/p90    hold p10/中央値/p90")
        for row in summary["vap"]:
            print(
                f"{row['axis']:4} {row['delay']:.1f}   {row['score']:8} "
                f"{row['shift']['n']:5} {row['hold']['n']:4} {_number(row['auc']):>5}  "
                f"{_dist(row['shift'])}    {_dist(row['hold'])}"
            )
    for mode in ("bc", "bc_2type"):
        if mode in summary:
            print(f"\n{mode}（T5。フレーム単位の分布、終わりは0.4秒の窓）")
            print("region        score          n   p10 / 中央値 / p90")
            for row in summary[mode]:
                print(
                    f"{row['region']:13} {row['score']:13} "
                    f"{row['distribution']['n']:4}  {_dist(row['distribution'])}"
                )
    if "bc_det" in summary:
        row = summary["bc_det"]
        print("\nbc_det（最後の発話の有声区間＋終了後0.4秒。発話ごとの最大値）")
        print("kind          n   p10 / 中央値 / p90")
        for kind in ("backchannel", "interrupt"):
            print(f"{kind:13} {row[kind]['n']:3}  {_dist(row[kind])}")
        print(f"AUC（backchannel が正例）: {_number(row['auc'])}")
        print("条件: Tomoko チャンネルは無音。同時発話での性能は未測定。")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", type=Path, help="run または生成済み入力の組のディレクトリ")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lang", default="jp")
    ap.add_argument("--frame-rate", type=int, default=10)
    ap.add_argument("--axes", help="軸をカンマ区切り（旧run+vapの既定: T1,T2,C2,T5）")
    ap.add_argument("--modes", default="vap", help="vap,bc,bc_2type,bc_det からカンマ区切り")
    ap.add_argument("--encoder", choices=("cpc", "mimi"), default="cpc",
                    help="vap と bc のエンコーダー。bc_det は常に mimi（12.5 Hz、20 秒）")
    ap.add_argument("--bc-user-ch1", action="store_true",
                    help="bc 系で、ユーザーの声を外の ch1 に入れる（既定は ch2）")
    args = ap.parse_args()
    modes = list(dict.fromkeys(m.strip() for m in args.modes.split(",")))
    if any(m not in SCORES for m in modes):
        ap.error("--modes は vap,bc,bc_2type,bc_det から指定する")
    if args.frame_rate <= 0 or 100 % args.frame_rate:
        ap.error("--frame-rate は正数で100を割り切る値が必要（CPCは160サンプル単位）")
    try:
        kind = source_kind(args.run)
        defaults = "T1,T2,C2,T5" if kind == "runs" and modes == ["vap"] else "T1,T2,T3,T4,T5,C2,C3"
        clips = load_clips(args.run, {a.strip() for a in (args.axes or defaults).split(",")})
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    if not clips:
        ap.error("選択した軸の入力がない")
    results, timings = {}, {}
    for mode in modes:
        selected = [
            c
            for c in clips
            if mode == "vap"
            or (mode in {"bc", "bc_2type"} and c.axis == "T5")
            or (mode == "bc_det" and c.detection_voiced)
        ]
        results[mode] = []
        if not selected:
            timings[mode] = {"clips": 0, "frames": 0}
            continue
        start = time.perf_counter()
        model = make_model(mode, args.lang, args.frame_rate, args.encoder)
        loaded = time.perf_counter()
        details = []
        try:
            for clip in selected:
                before = time.perf_counter()
                frames = run_model(model, clip.audio, mode, args.bc_user_ch1)
                elapsed = time.perf_counter() - before
                results[mode].extend(evaluate(clip, mode, frames))
                details.append(
                    {
                        "id": clip.id,
                        "step": clip.step,
                        "frames": len(frames),
                        "elapsed_s": elapsed,
                        "audio_s": len(clip.audio) / SAMPLE_RATE,
                        "trailing_samples": len(clip.audio) % model.audio_samples_per_frame,
                    }
                )
                print(
                    f"{mode} {clip.id} step={clip.step}: {len(frames)} 結果、{elapsed:.2f}秒",
                    flush=True,
                )
        finally:
            model.stop(wait=True)
        timings[mode] = {
            "clips": len(details),
            "frames": sum(d["frames"] for d in details),
            "load_s": loaded - start,
            "inference_s": sum(d["elapsed_s"] for d in details),
            "recordings": details,
            "model": {
                "variant": getattr(model, "variant", None),
                "encoder": getattr(model, "encoder_type", None),
                "frame_samples": model.audio_samples_per_frame,
                "inference_chunk_frames": getattr(model, "inference_chunk_frames", 1),
            },
        }
    summary = summarize(results)
    try:
        package_version = version("maai")
    except PackageNotFoundError:
        package_version = None
    report = {
        "schema_version": 2,
        "source": str(args.run),
        "source_kind": kind,
        "settings": {
            "modes": modes,
            "lang": args.lang,
            "frame_rate": args.frame_rate,
            "encoder": args.encoder,
            "per_mode": {m: dict(zip(("model_type", "frame_rate", "context_s"),
                                     model_settings(m, args.frame_rate, args.encoder), strict=True))
                         for m in modes},
            "torch_threads": 2,
            "maai_version": package_version,
            "system_audio": "silence",
            "leading_silence_s": 1 if kind == "inputs" else 0,
            "trailing_silence_s": 2 if kind == "inputs" else 0,
            "channels": {
                "vap": "user,system; score ch2",
                "bc": "user,system; internal swap" if args.bc_user_ch1 else "system,user; internal swap",
                "bc_2type": "user,system; internal swap" if args.bc_user_ch1 else "system,user; internal swap",
                "bc_det": "user,system; score ch1",
            },
        },
        "timing": timings,
        "summary": summary,
        "results": results,
    }
    payload = results["vap"] if kind == "runs" and modes == ["vap"] else report
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    print_summary(summary)
    print(f"\n保存先: {args.out}")


if __name__ == "__main__":
    main()
