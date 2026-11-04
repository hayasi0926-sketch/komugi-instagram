# -*- coding: utf-8 -*-
"""
subtitle_builder.py
==================================================
台本(SCRIPT)とTTS結果(TTSResult)から、
  1. 各ビート（シーン）の開始/終了時刻
  2. 画面下部に出す字幕チャンクの開始/終了時刻
を計算する。

ElevenLabsの文字単位タイムスタンプが取得できた場合はそれを使い、
取得できなかった場合は「文字数に比例して均等割り」する
フォールバック計算を行う（＝タイムスタンプが無くても必ず動画が作れる）。
"""

import re
from dataclasses import dataclass
from typing import List, Tuple

from tts_elevenlabs import TTSResult

BEAT_SEPARATOR = " "  # ビート同士を連結するときに挟む文字

# 字幕チャンクを区切る句読点
_PUNCT_PATTERN = re.compile(r"([、。！？…])")
MAX_SUBTITLE_LEN = 15  # 1チャンクあたりの最大文字数の目安（1080px幅で収まる目安）

# 句読点だけでは分割しきれない長いチャンクを強制分割するとき、
# 単純な文字数カウントではなく「助詞・空白・閉じ括弧の直後」など
# 人が読んで不自然に感じにくい位置を優先して区切るための候補パターン。
# （文字数だけで切ると「「どれくらい」のように括弧の途中で切れてしまい、
#  機械的・AIっぽい印象になってしまうため）
_SOFT_BREAK_PATTERN = re.compile(
    r"(?:は|が|を|に|で|と|も|から|ので|けど|よ|ね|の|や|」|』|\s)"
)


@dataclass
class Beat:
    id: str
    section: str
    narration: str
    screen_text: str
    bg_color: str
    start: float
    end: float


@dataclass
class SubtitleChunk:
    text: str
    start: float
    end: float


def build_full_narration_text(script: List[dict]) -> Tuple[str, List[Tuple[int, int]]]:
    """
    SCRIPTから、ElevenLabsに送る1本のナレーション全文と、
    各ビートの [start_offset, end_offset) 文字インデックスを作る。
    """
    full_text = ""
    offsets: List[Tuple[int, int]] = []
    for i, beat in enumerate(script):
        start_offset = len(full_text)
        full_text += beat["narration"]
        end_offset = len(full_text)
        offsets.append((start_offset, end_offset))
        if i != len(script) - 1:
            full_text += BEAT_SEPARATOR
    return full_text, offsets


def _split_oversized_chunk(chunk: str, max_len: int) -> List[str]:
    """
    max_len を超えるチャンクを、なるべく自然な位置（助詞や空白の後ろ）で
    分割する。

    1回切るたびに「残りの文字列」に対して必要パーツ数を再計算しながら
    先頭から順に切っていく。各カット位置は「先頭から max_len 文字以内」
    という制約の中にある自然な区切り候補（助詞・空白・閉じ括弧の直後）の
    うち、理想の分割位置に一番近いものを選ぶ。範囲内に候補が無ければ、
    その時だけ理想位置で強制的に区切る。
    こうすることで、どのピースも必ず max_len 以内に収まる。
    """
    pieces: List[str] = []
    remaining = chunk
    while len(remaining) > max_len:
        n_parts_remaining = -(-len(remaining) // max_len)  # ceil division
        part_size = -(-len(remaining) // n_parts_remaining)  # ceil division
        ideal = min(part_size, len(remaining) - 1)
        upper = min(max_len, len(remaining) - 1)

        pos = None
        best_dist = None
        for m in _SOFT_BREAK_PATTERN.finditer(remaining, 1, upper + 1):
            cand = m.end()
            if cand <= 0 or cand > upper:
                continue
            dist = abs(cand - ideal)
            if pos is None or dist < best_dist:
                pos = cand
                best_dist = dist

        if pos is None:
            pos = ideal  # 自然な区切りが無ければ強制分割（max_len以内は保証）

        pieces.append(remaining[:pos])
        remaining = remaining[pos:]

    if remaining:
        pieces.append(remaining)
    return pieces


def _split_into_chunks(text: str, max_len: int = MAX_SUBTITLE_LEN) -> List[Tuple[str, int, int]]:
    """
    テキストを字幕として読みやすい長さのチャンクに分割する。
    句読点を優先して区切り、長すぎる場合は max_len で強制的に区切る。
    戻り値: [(チャンク文字列, ローカル開始index, ローカル終了index), ...]
    """
    if not text:
        return []

    # 句読点の直後で分割候補を作る
    pieces = _PUNCT_PATTERN.split(text)
    raw_chunks = []
    buf = ""
    for piece in pieces:
        buf += piece
        if _PUNCT_PATTERN.fullmatch(piece):
            raw_chunks.append(buf)
            buf = ""
    if buf:
        raw_chunks.append(buf)

    # 長すぎるチャンクは、なるべく均等かつ「自然な位置」で分割する
    # （例: 16文字・max_len=15 のとき「15+1」ではなく「8+8」のように分け、
    #  かつ助詞や空白の直後など不自然に見えにくい位置を優先する）
    final_chunks: List[str] = []
    for chunk in raw_chunks:
        if len(chunk) <= max_len:
            final_chunks.append(chunk)
        else:
            final_chunks.extend(_split_oversized_chunk(chunk, max_len))

    # 短すぎるチャンク（句読点のみ等）は、max_lenを超えない範囲で前のチャンクに結合
    merged: List[str] = []
    for chunk in final_chunks:
        is_punct_only = len(chunk.strip("、。！？… ")) == 0
        if merged and is_punct_only and len(merged[-1]) + len(chunk) <= max_len:
            merged[-1] += chunk
        elif merged and len(merged[-1]) + len(chunk) <= max_len // 2:
            merged[-1] += chunk
        else:
            merged.append(chunk)

    # ローカルindexを計算
    result = []
    cursor = 0
    for chunk in merged:
        start = text.index(chunk, cursor)
        end = start + len(chunk)
        cursor = end
        result.append((chunk, start, end))
    return result


def _lookup_time_with_alignment(
    tts: TTSResult, global_start: int, global_end: int
) -> Tuple[float, float]:
    """アライメント(文字単位タイムスタンプ)から時刻を引く"""
    global_end = min(global_end, len(tts.char_start_times))
    global_start = min(global_start, global_end - 1) if global_end > 0 else 0
    start_time = tts.char_start_times[global_start]
    end_time = tts.char_end_times[global_end - 1]
    return start_time, end_time


def build_timeline(
    script: List[dict], full_text: str, beat_offsets: List[Tuple[int, int]], tts: TTSResult
) -> Tuple[List[Beat], List[SubtitleChunk]]:
    """
    ビートごとの開始/終了時刻と、字幕チャンクの開始/終了時刻を計算する。
    """
    total_chars = len(full_text) if full_text else 1

    beats: List[Beat] = []
    subtitles: List[SubtitleChunk] = []

    for beat_data, (g_start, g_end) in zip(script, beat_offsets):
        if tts.used_alignment:
            beat_start, beat_end = _lookup_time_with_alignment(tts, g_start, g_end)
        else:
            # 文字数比例のフォールバック
            beat_start = tts.duration_sec * (g_start / total_chars)
            beat_end = tts.duration_sec * (g_end / total_chars)

        beats.append(
            Beat(
                id=beat_data["id"],
                section=beat_data["section"],
                narration=beat_data["narration"],
                screen_text=beat_data.get("screen_text"),
                bg_color=beat_data.get("bg_color", "0x222222"),
                start=beat_start,
                end=beat_end,
            )
        )

        # このビート内の字幕チャンクを計算
        narration = beat_data["narration"]
        chunks = _split_into_chunks(narration)
        beat_char_span = max(g_end - g_start, 1)
        for chunk_text, local_start, local_end in chunks:
            if tts.used_alignment:
                c_start, c_end = _lookup_time_with_alignment(
                    tts, g_start + local_start, g_start + local_end
                )
            else:
                ratio_start = local_start / beat_char_span
                ratio_end = local_end / beat_char_span
                c_start = beat_start + (beat_end - beat_start) * ratio_start
                c_end = beat_start + (beat_end - beat_start) * ratio_end
            subtitles.append(SubtitleChunk(text=chunk_text, start=c_start, end=c_end))

    # 最後のビートの終了時刻を音声全体の長さに揃える（丸め誤差の吸収）
    if beats:
        beats[-1].end = max(beats[-1].end, tts.duration_sec)
    if subtitles:
        subtitles[-1].end = max(subtitles[-1].end, tts.duration_sec)

    return beats, subtitles
