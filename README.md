# komugi-instagram

こむぎ（AI_Komugiプロジェクト）のInstagram Reelsを、毎日21:00(JST)に自動投稿するリポジトリです。
神社インスタグラム（jinja-instagram）と同じ「GitHub Actions方式」を採用しています。PCの電源が入っていなくても、GitHub側で自動実行されます。

## 仕組み（2026年11月〜：台本から投稿まで全自動）

1. **台本の補充**：Claudeの週1回のスケジュールタスクが `script_queue/` の在庫を確認し、2週間分（14本）を切っていたら、既存と重ならないテーマで台本（ナレーション・画面テキスト・キャプション・ハッシュタグ）を書き足してpushする
2. **動画づくり**：毎日21:00(JST)、GitHub Actions（`.github/workflows/daily-post.yml`）が起動し、`status:"ready"` の動画が2本未満なら、`script_queue/` の若い番号の台本から動画を作る（`generator/render_next.py`。ElevenLabsでナレーション → 字幕 → FFmpeg合成）
   - できた動画は `videos/` に置かれ、`posts/manifest.json` に `status:"ready"` で追加される。使った台本は `script_queue/done/` に移る
3. **投稿**：続けて、`status:"ready"` の中で最もidの若い1件をInstagramに投稿し、`status:"posted"` に書き換えてコミットする
4. 投稿できる動画も台本もなくなった日は、ワークフローが失敗扱いになり、GitHubからメールが届く

PC（`C:\AI_Komugi`）は不要です。投稿したくない動画は、manifest.json でその動画の `status` を `"blocked"` にすれば飛ばされます。

### 台本ファイルの形式（`script_queue/NNN_slug.json`）

`video_slug`・`video_title`・`caption`・`hashtags`・`script`（PC版 queue.json と同じビート配列）を持つJSON。ナレーションでは「AI」とアルファベットで書かず「エーアイ」とする（音声合成の読み間違い防止）。

## フォルダ構成

```
komugi-instagram/
├── .github/workflows/
│   ├── daily-post.yml       毎日21:00(JST)に実行、1件投稿してmanifestをコミット
│   └── token-refresh.yml    毎月1回、長期アクセストークンを更新
├── scripts/
│   ├── post-reel-to-instagram.js   実際の投稿処理（Reels / 動画1本）
│   └── refresh-instagram-token.js  トークン更新処理
├── posts/
│   └── manifest.json        投稿の状態管理（ready → posted）
├── videos/                  動画本体（*.mp4）。初回セットアップ時に手動で、以後は自動で追加
├── package.json
└── .env.example             ローカル実行用の環境変数サンプル（本物の値は書かない）
```

## 必要なGitHub Secrets

このリポジトリの Settings → Secrets and variables → Actions で設定します。

| Secret名 | 内容 | 必須 |
|---|---|---|
| `IG_ACCESS_TOKEN` | Instagramの長期アクセストークン | ○ |
| `IG_BUSINESS_ACCOUNT_ID` | InstagramビジネスアカウントID | ○ |
| `GITHUB_RAW_BASE` | 省略時は `https://raw.githubusercontent.com/<このリポジトリ>/main` が自動で使われる | 任意 |
| `ELEVENLABS_API_KEY` | ElevenLabsのAPIキー（動画のナレーション生成用） | ○ |
| `ELEVENLABS_VOICE_ID` | こむぎの声のVoice ID | ○ |
| `ELEVENLABS_MODEL_ID` | 省略時は `eleven_multilingual_v2` | 任意 |
| `GH_PAT` | トークン自動更新用（Secrets: Read and write 権限のfine-grained PAT） | token-refresh.ymlを使う場合のみ |

## ローカルでの動作確認

```bash
npm install   # 依存なし（Node標準のfetchのみ使用）なので実質不要
cp .env.example .env   # 値を書き込んでから
npm run post:dry-run   # 実際には投稿せず、内容だけ確認
npm run post            # 実際に投稿
npm run refresh-token    # トークン更新（ローカル実行用）
```

## 注意

- 動画はInstagram Graph APIの仕様上、**公開URL**から読める必要があるため、`videos/` フォルダへのpushが投稿の前提になります。
- 1回のワークフロー実行で投稿されるのは1件のみです（`status:"ready"` のうち最もidの若いもの）。
