# komugi-instagram

こむぎ（AI_Komugiプロジェクト）のInstagram Reelsを、毎日21:00(JST)に自動投稿するリポジトリです。
神社インスタグラム（jinja-instagram）と同じ「GitHub Actions方式」を採用しています。PCの電源が入っていなくても、GitHub側で自動実行されます。

## 仕組み

1. `C:\AI_Komugi` 側（ローカルPC）で `scripts/auto_run.py` がこむぎの動画を自動生成する
2. 生成された動画を、このリポジトリの `videos/` フォルダにアップロードする（公開URLが必要なため）
3. `posts/manifest.json` に、その動画の投稿情報（キャプション・ハッシュタグ等）を `status:"ready"` として追加する
4. 毎日21:00(JST)、GitHub Actions（`.github/workflows/daily-post.yml`）が自動的に起動し、`status:"ready"` の中で最もidの若い1件をInstagramに投稿する
5. 投稿が成功すると、`posts/manifest.json` の該当エントリが `status:"posted"` に書き換えられ、ワークフロー自身がコミットする

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
