# -*- coding: utf-8 -*-
"""
tts_elevenlabs.py
==================================================
ElevenLabs APIを使って、こむぎのナレーション音声を1本のmp3として生成し、
可能な場合は文字単位のタイムスタンプ（字幕の自動同期用）も取得する。

必要な環境変数（.env）:
  ELEVENLABS_API_KEY   : ElevenLabsのAPIキー（必須）
  ELEVENLABS_VOICE_ID  : 使用する音声のVoice ID（必須）
  ELEVENLABS_MODEL_ID  : 使用するモデルID（省略可。既定: eleven_multilingual_v2）
"""

import base64
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

import requests

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


class ElevenLabsError(RuntimeError):
    """ElevenLabs呼び出しに関する分かりやすいエラー"""


@dataclass
class TTSResult:
    audio_path: str
    duration_sec: float
    # 文字単位のタイムスタンプ（取得できた場合のみ中身が入る）
    characters: List[str] = field(default_factory=list)
    char_start_times: List[float] = field(default_factory=list)
    char_end_times: List[float] = field(default_factory=list)
    used_alignment: bool = False


def _get_env(name: str, required: bool = True, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name, default)
    if required and not value:
        raise ElevenLabsError(
            f"環境変数 {name} が設定されていません。.env ファイルを確認してください。"
        )
    return value


def _ffprobe_duration(audio_path: str) -> float:
    """ffprobeで音声ファイルの長さ（秒）を取得する"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as exc:
        raise ElevenLabsError(
            "ffprobe で音声の長さを取得できませんでした。FFmpeg (ffprobeを含む) が"
            "インストールされ、PATHが通っているか確認してください。"
            f" 詳細: {exc}"
        ) from exc


def generate_narration(
    full_text: str,
    output_audio_path: str,
    voice_settings: Optional[dict] = None,
) -> TTSResult:
    """
    full_text をElevenLabsに送信し、ナレーション音声(mp3)を生成する。
    可能であれば文字単位のタイムスタンプも取得する（with-timestampsエンドポイント）。
    失敗した場合は通常のTTSエンドポイントにフォールバックする。
    """
    api_key = _get_env("ELEVENLABS_API_KEY")
    voice_id = _get_env("ELEVENLABS_VOICE_ID")
    model_id = _get_env("ELEVENLABS_MODEL_ID", required=False, default="eleven_multilingual_v2")

    if voice_settings is None:
        voice_settings = {"stability": 0.5, "similarity_boost": 0.75}

    payload = {
        "text": full_text,
        "model_id": model_id,
        "voice_settings": voice_settings,
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    os.makedirs(os.path.dirname(output_audio_path) or ".", exist_ok=True)

    # --- 1. タイムスタンプ付きエンドポイントを試す -------------------
    url_with_timestamps = f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}/with-timestamps"
    try:
        resp = requests.post(url_with_timestamps, headers=headers, json=payload, timeout=120)
    except requests.RequestException as exc:
        raise ElevenLabsError(
            f"ElevenLabs APIに接続できませんでした（ネットワークエラー）: {exc}"
        ) from exc

    if resp.status_code == 200:
        data = resp.json()
        audio_b64 = data.get("audio_base64")
        alignment = data.get("alignment") or {}
        characters = alignment.get("characters") or []
        starts = alignment.get("character_start_times_seconds") or []
        ends = alignment.get("character_end_times_seconds") or []

        if not audio_b64:
            raise ElevenLabsError("ElevenLabsのレスポンスに音声データが含まれていませんでした。")

        with open(output_audio_path, "wb") as f:
            f.write(base64.b64decode(audio_b64))

        duration = _ffprobe_duration(output_audio_path)

        if characters and starts and ends and len(characters) == len(starts) == len(ends):
            return TTSResult(
                audio_path=output_audio_path,
                duration_sec=duration,
                characters=characters,
                char_start_times=starts,
                char_end_times=ends,
                used_alignment=True,
            )
        else:
            print(
                "[tts_elevenlabs] タイムスタンプの形式が想定と異なるため、"
                "均等割り当てにフォールバックします。",
                file=sys.stderr,
            )
            return TTSResult(audio_path=output_audio_path, duration_sec=duration, used_alignment=False)

    # --- 2. タイムスタンプ付きエンドポイントが使えない場合は通常TTSにフォールバック ---
    print(
        f"[tts_elevenlabs] with-timestamps エンドポイントが失敗しました "
        f"(status={resp.status_code})。通常のTTSにフォールバックします。 "
        f"レスポンス: {resp.text[:300]}",
        file=sys.stderr,
    )

    url_basic = f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"
    try:
        resp2 = requests.post(url_basic, headers=headers, json=payload, timeout=120)
    except requests.RequestException as exc:
        raise ElevenLabsError(
            f"ElevenLabs APIに接続できませんでした（ネットワークエラー）: {exc}"
        ) from exc

    if resp2.status_code != 200:
        raise ElevenLabsError(
            "ElevenLabs APIの呼び出しに失敗しました。"
            f" status={resp2.status_code} body={resp2.text[:500]}\n"
            "考えられる原因: APIキーが間違っている / Voice IDが間違っている / "
            "利用可能な文字数枠を使い切っている、など。"
        )

    with open(output_audio_path, "wb") as f:
        f.write(resp2.content)

    duration = _ffprobe_duration(output_audio_path)
    return TTSResult(audio_path=output_audio_path, duration_sec=duration, used_alignment=False)
