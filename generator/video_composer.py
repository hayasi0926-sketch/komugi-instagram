# -*- coding: utf-8 -*-
"""
video_composer.py
==================================================
こむぎ画像・中央テキスト・字幕・ナレーション音声をFFmpegで合成し、
9:16 (1080x1920) 30fps のMP4を書き出す。

ライブラリではなく FFmpeg の CLI を直接呼び出す方式（moviepy等は使わない）。
理由: 依存を最小限にして「確実に1本作れる」ことを優先するため。
"""

import os
import shutil
import subprocess
from typing import List, Optional

from PIL import Image

from subtitle_builder import Beat, SubtitleChunk

VIDEO_W = 1080
VIDEO_H = 1920
FPS = 30

KOMUGI_DISPLAY_W = 380  # こむぎ画像の表示幅(px)
KOMUGI_MARGIN = 24  # 画面端からの余白(px)

CENTER_TEXT_Y = 560
CENTER_LINE_GAP = 40  # 1行ずつ描くときの行間（背景の箱が重ならない程度）
CENTER_TEXT_FONTSIZE = 64

SUBTITLE_FONTSIZE = 54

# こむぎの「しゃべってる感」演出（口パク用の画像素材が無い場合の代替）。
# 字幕（＝声が出ている区間）のタイミングに合わせて、その間だけ縦にリズムよく
# 弾ませる。声が止んでいる間（ビートの合間の"間"）は静止させる。
KOMUGI_BOUNCE_AMPLITUDE_PX = 16  # 弾む高さ(px)
KOMUGI_BOUNCE_CYCLE_SEC = 0.25  # 1往復(上→下)にかかる秒数（口パクっぽい速さの目安）


class VideoComposeError(RuntimeError):
    pass


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise VideoComposeError(
            "ffmpeg が見つかりません。FFmpegをインストールし、PATHに追加してください。"
            "（README.md のセットアップ手順を参照）"
        )


def _ffmpeg_path_escape(path: str) -> str:
    r"""
    Windowsパスをffmpegのdrawtextフィルタ引数で安全に使える形式に変換する。

    drawtextのfontfile/textfileにドライブレター付きパス（C:/...）を渡す場合、
    シングルクォートで囲むだけでは ':' がオプション区切りとして誤認識される
    （実機検証済み）。シングルクォートで囲んだ上で、さらに ':' をバックスラッシュで
    エスケープする必要がある（例: fontfile='C\:/Windows/Fonts/arial.ttf'）。
    """
    p = os.path.abspath(path).replace("\\", "/")
    p = p.replace("'", r"\'")
    p = p.replace(":", r"\:")
    return p


def _write_text_file(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _komugi_geometry(komugi_image_path: str):
    with Image.open(komugi_image_path) as im:
        w, h = im.size
    disp_w = KOMUGI_DISPLAY_W
    disp_h = round(disp_w * (h / w))
    x = VIDEO_W - disp_w - KOMUGI_MARGIN
    y = VIDEO_H - disp_h - KOMUGI_MARGIN
    return disp_w, disp_h, x, y


def compose_video(
    beats: List[Beat],
    subtitles: List[SubtitleChunk],
    komugi_image_path: str,
    audio_path: str,
    output_path: str,
    font_path: str,
    work_dir: str,
    total_duration: Optional[float] = None,
) -> str:
    _check_ffmpeg()

    if not os.path.exists(komugi_image_path):
        raise VideoComposeError(f"こむぎ画像が見つかりません: {komugi_image_path}")
    if not os.path.exists(audio_path):
        raise VideoComposeError(f"ナレーション音声が見つかりません: {audio_path}")
    if not os.path.exists(font_path):
        raise VideoComposeError(
            f"字幕用フォントが見つかりません: {font_path}\n"
            ".env の FONT_PATH を、お使いのWindows環境に存在するフォントファイルに"
            "変更してください（例: C:\\Windows\\Fonts\\YuGothB.ttc）。"
        )

    if total_duration is None:
        total_duration = max(b.end for b in beats) if beats else 1.0

    tmp_dir = os.path.join(work_dir, "_tmp_text")
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    disp_w, disp_h, komugi_x, komugi_y = _komugi_geometry(komugi_image_path)
    subtitle_y = max(komugi_y - 130, 40)

    # --- 入力の組み立て -------------------------------------------------
    args = ["ffmpeg", "-y"]

    # 背景色セグメントは「このビートの開始 〜 次のビートの開始」まで敷き詰める
    # （ビート同士の間の"間"（無音の区切り）もどこかの背景でカバーし、
    #  背景の合計長さが必ず音声の全長と一致するようにするため）
    for i, beat in enumerate(beats):
        bg_start = beat.start
        bg_end = beats[i + 1].start if i + 1 < len(beats) else total_duration
        dur = max(bg_end - bg_start, 0.05)
        args += [
            "-f",
            "lavfi",
            "-i",
            f"color=c={beat.bg_color}:s={VIDEO_W}x{VIDEO_H}:d={dur:.3f}:r={FPS}",
        ]
    n_bg = len(beats)

    komugi_input_index = n_bg
    args += ["-loop", "1", "-t", f"{total_duration:.3f}", "-i", komugi_image_path]

    audio_input_index = n_bg + 1
    args += ["-i", audio_path]

    # --- フィルタグラフの組み立て ----------------------------------------
    filter_parts = []

    concat_inputs = "".join(f"[{i}:v]" for i in range(n_bg))
    filter_parts.append(f"{concat_inputs}concat=n={n_bg}:v=1:a=0[bg]")

    filter_parts.append(f"[{komugi_input_index}:v]scale={disp_w}:{disp_h}[komugi]")

    # 「しゃべっている」区間 = いずれかの字幕チャンクの表示区間中、という判定式。
    # 字幕チャンクは重ならないので、該当区間の between() の総和は 0 か 1 になる。
    if subtitles:
        speaking_terms = "+".join(
            f"between(t,{s.start:.3f},{s.end:.3f})" for s in subtitles
        )
    else:
        speaking_terms = "0"
    bounce_y_expr = (
        f"{komugi_y}-({KOMUGI_BOUNCE_AMPLITUDE_PX})*"
        f"abs(sin(PI*t/{KOMUGI_BOUNCE_CYCLE_SEC}))*({speaking_terms})"
    )
    filter_parts.append(
        f"[bg][komugi]overlay=x={komugi_x}:y='{bounce_y_expr}':shortest=1[v0]"
    )

    font_escaped = _ffmpeg_path_escape(font_path)
    current_label = "v0"
    stage = 0

    # 中央テキスト（各ビートのscreen_text）
    # 新しいFFmpeg（GitHub ActionsのUbuntu）では、テキスト中の改行が「☒」のような
    # 文字化けとして描かれることがあるため、改行を使わず1行ずつ別々に描く。
    line_pitch = int(CENTER_TEXT_FONTSIZE * 1.25) + CENTER_LINE_GAP
    for beat in beats:
        if not beat.screen_text:
            continue
        lines = [ln for ln in beat.screen_text.replace("\r", "").split("\n") if ln.strip()]
        for li, line in enumerate(lines):
            stage += 1
            txt_file = _write_text_file(
                os.path.join(tmp_dir, f"center_{beat.id}_{li}.txt"), line
            )
            txt_escaped = _ffmpeg_path_escape(txt_file)
            next_label = f"v{stage}"
            filter_parts.append(
                f"[{current_label}]drawtext="
                f"fontfile='{font_escaped}':textfile='{txt_escaped}':"
                f"fontsize={CENTER_TEXT_FONTSIZE}:fontcolor=white:"
                f"borderw=5:bordercolor=black@0.7:"
                f"box=1:boxcolor=black@0.30:boxborderw=24:"
                f"x=(w-text_w)/2:y={CENTER_TEXT_Y + li * line_pitch}:"
                f"enable='between(t,{beat.start:.3f},{beat.end:.3f})'"
                f"[{next_label}]"
            )
            current_label = next_label

    # 字幕（画面下部・自動生成）
    for idx, sub in enumerate(subtitles):
        stage += 1
        txt_file = _write_text_file(os.path.join(tmp_dir, f"sub_{idx:03d}.txt"), sub.text)
        txt_escaped = _ffmpeg_path_escape(txt_file)
        next_label = f"v{stage}"
        filter_parts.append(
            f"[{current_label}]drawtext="
            f"fontfile='{font_escaped}':textfile='{txt_escaped}':"
            f"fontsize={SUBTITLE_FONTSIZE}:fontcolor=white:"
            f"borderw=4:bordercolor=black@0.8:"
            f"box=1:boxcolor=black@0.55:boxborderw=18:"
            f"x=(w-text_w)/2:y={subtitle_y}:"
            f"enable='between(t,{sub.start:.3f},{sub.end:.3f})'"
            f"[{next_label}]"
        )
        current_label = next_label

    filter_complex = ";".join(filter_parts)

    args += [
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{current_label}]",
        "-map",
        f"{audio_input_index}:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        output_path,
    ]

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        log_path = os.path.join(work_dir, "ffmpeg_last_error.log")
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("=== command ===\n")
                f.write(" ".join(args))
                f.write("\n\n=== stderr (full) ===\n")
                f.write(result.stderr)
        except OSError:
            log_path = None

        head = result.stderr[:2500]
        tail = result.stderr[-1500:]
        raise VideoComposeError(
            "FFmpegでの動画合成に失敗しました。\n"
            f"詳細ログ全文: {log_path}\n"
            "--- ffmpeg stderr (先頭) ---\n"
            f"{head}\n"
            "--- ffmpeg stderr (末尾) ---\n"
            f"{tail}"
        )

    return output_path
