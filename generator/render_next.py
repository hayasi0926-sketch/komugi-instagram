# -*- coding: utf-8 -*-
"""
render_next.py
==================================================
GitHub Actions 上で、台本から こむぎのリール動画を作り、投稿キューに追加する。

PC版（C:\\AI_Komugi\\scripts\\auto_run.py）と同じパイプライン
（ElevenLabsでナレーション → 字幕タイムライン → FFmpeg合成）を使う。

流れ:
  1. posts/manifest.json の status:"ready" の本数を数える
  2. 足りなければ（既定: 2本未満）、script_queue/ の中で
     まだ manifest に入っていない台本を、ファイル名の若い順に取り出す
  3. 動画を videos/komugi_reel_<slug>.mp4 に書き出す
  4. manifest.json に status:"ready" で追加する（キャプション・ハッシュタグは台本ファイルから）
  5. 台本ファイルは script_queue/done/ に移す

環境変数:
  ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID   （必須、GitHub Secrets）
  ELEVENLABS_MODEL_ID                         （任意）
  FONT_PATH                                   （任意。既定は Noto Sans CJK JP Bold）
  READY_TARGET                                （任意。ready をこの本数まで作る。既定 2）
  MAX_RENDER_PER_RUN                          （任意。1回の実行で作る上限。既定 2）

終了コード:
  0 = 問題なし（作った・作る必要がなかった）
  1 = 動画生成に失敗した
  3 = 台本の在庫がなく、ready も 0 本（このままだと今日は投稿されない）
"""

import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import subtitle_builder  # noqa: E402
import video_composer  # noqa: E402
from tts_elevenlabs import ElevenLabsError, generate_narration  # noqa: E402
from video_composer import VideoComposeError  # noqa: E402

ROOT = os.path.dirname(_THIS_DIR)
MANIFEST_PATH = os.path.join(ROOT, "posts", "manifest.json")
QUEUE_DIR = os.path.join(ROOT, "script_queue")
DONE_DIR = os.path.join(QUEUE_DIR, "done")
KOMUGI_IMAGE = os.path.join(ROOT, "assets", "komugi", "komugi_normal.png")
WORK_DIR = os.path.join(ROOT, ".render_tmp")

DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def _load_manifest():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(manifest):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _pending_scripts(manifest):
    known = {p["slug"] for p in manifest["posts"]}
    files = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
    pending = []
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data["video_slug"] in known:
            continue
        pending.append((path, data))
    return pending


def _validate(data):
    for key in ("video_slug", "video_title", "caption", "hashtags", "script"):
        if not data.get(key):
            raise ValueError(f"台本ファイルに {key} がありません")
    for beat in data["script"]:
        for key in ("id", "section", "narration", "bg_color"):
            if key not in beat:
                raise ValueError(f"ビート {beat.get('id')} に {key} がありません")


def render_one(data, font_path):
    _validate(data)
    slug = data["video_slug"]
    script = data["script"]
    os.makedirs(WORK_DIR, exist_ok=True)

    full_text, beat_offsets = subtitle_builder.build_full_narration_text(script)
    print(f"  ビート数: {len(script)} / 文字数: {len(full_text)}")

    audio_path = os.path.join(WORK_DIR, "_narration.mp3")
    tts = generate_narration(full_text, audio_path)
    print(f"  音声: {tts.duration_sec:.1f}秒（タイムスタンプ同期: {'有効' if tts.used_alignment else '均等割り'}）")

    beats, subtitles = subtitle_builder.build_timeline(script, full_text, beat_offsets, tts)

    video_rel = f"videos/komugi_reel_{slug}.mp4"
    video_composer.compose_video(
        beats=beats,
        subtitles=subtitles,
        komugi_image_path=KOMUGI_IMAGE,
        audio_path=audio_path,
        output_path=os.path.join(ROOT, video_rel),
        font_path=font_path,
        work_dir=WORK_DIR,
        total_duration=tts.duration_sec,
    )
    return video_rel


def main():
    font_path = os.environ.get("FONT_PATH") or DEFAULT_FONT
    ready_target = int(os.environ.get("READY_TARGET", "2"))
    max_render = int(os.environ.get("MAX_RENDER_PER_RUN", "2"))

    manifest = _load_manifest()
    ready = sum(1 for p in manifest["posts"] if p["status"] == "ready")
    pending = _pending_scripts(manifest)
    print(f"ready: {ready}本 / 台本の在庫: {len(pending)}本")

    to_make = min(max(ready_target - ready, 0), max_render, len(pending))
    if to_make == 0:
        if ready == 0:
            print("::error::投稿できる動画も台本もありません。台本を script_queue/ に追加してください。")
            return 3
        print("作る必要はありません。")
        return 0

    os.makedirs(DONE_DIR, exist_ok=True)
    made = 0
    for path, data in pending[:to_make]:
        print(f"[{made + 1}/{to_make}] 「{data['video_title']}」({data['video_slug']})")
        start = time.time()
        try:
            video_rel = render_one(data, font_path)
        except (ElevenLabsError, VideoComposeError, ValueError, FileNotFoundError) as exc:
            print(f"::error::動画生成に失敗しました: {exc}")
            break

        next_id = max((p["id"] for p in manifest["posts"]), default=0) + 1
        manifest["posts"].append(
            {
                "id": next_id,
                "slug": data["video_slug"],
                "topic": data["video_title"],
                "status": "ready",
                "videoPath": video_rel,
                "caption": data["caption"],
                "hashtags": data["hashtags"],
                "renderedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        _save_manifest(manifest)
        shutil.move(path, os.path.join(DONE_DIR, os.path.basename(path)))
        made += 1
        print(f"  完成: {video_rel}（{time.time() - start:.0f}秒）→ manifest #{next_id} ready")

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    ready_now = ready + made
    if made < to_make and ready_now == 0:
        return 1
    if made < to_make:
        # 失敗したが、投稿できる動画は残っているので投稿は続行させる
        print("::warning::一部の動画生成に失敗しましたが、投稿できる動画は残っています。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
