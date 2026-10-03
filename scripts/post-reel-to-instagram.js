/**
 * posts/manifest.json の中で status:"ready" になっている、最もidの若い動画を1件
 * Instagram Reels として公開する。
 *
 * （神社インスタグラム(jinja-instagram)の post-to-instagram.js と同じ仕組み。
 *   カルーセル画像4枚 → Reels動画1本に変えてある）
 *
 * 前提:
 *   - videos/<slug>.mp4 が GitHub の公開リポジトリに push 済みで、
 *     GITHUB_RAW_BASE からそのまま https で読めること
 *     （Instagram Graph API は動画を「公開URL」でしか受け取れないため）。
 *   - .env に以下が設定されていること（.env.example 参照）
 *       IG_ACCESS_TOKEN         長期のInstagramアクセストークン
 *       IG_BUSINESS_ACCOUNT_ID  InstagramビジネスアカウントID
 *       GITHUB_RAW_BASE         例: https://raw.githubusercontent.com/OWNER/REPO/main
 *
 * 使い方:
 *   node scripts/post-reel-to-instagram.js            実際に投稿する
 *   node scripts/post-reel-to-instagram.js --dry-run   API を呼ばずに内容を確認するだけ
 *
 * GitHub Actions (daily-post.yml) から毎日21:00(JST)に実行される想定（1回の実行で1投稿のみ）。
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const MANIFEST_PATH = path.join(ROOT, 'posts', 'manifest.json');
const GRAPH_API_VERSION = 'v21.0';

loadDotEnv(path.join(ROOT, '.env'));

const DRY_RUN = process.argv.includes('--dry-run');

async function main() {
  const manifest = readManifest();
  const post = manifest.posts.find((p) => p.status === 'ready');

  if (!post) {
    console.log('status:"ready" の動画が見つかりません。先に動画を生成してpushしてください。');
    return;
  }

  console.log(`投稿対象: #${post.id} ${post.topic} (${post.slug})`);

  const { IG_ACCESS_TOKEN, IG_BUSINESS_ACCOUNT_ID, GITHUB_RAW_BASE } = process.env;
  if (!DRY_RUN) {
    for (const key of ['IG_ACCESS_TOKEN', 'IG_BUSINESS_ACCOUNT_ID', 'GITHUB_RAW_BASE']) {
      if (!process.env[key]) {
        console.error(`環境変数 ${key} が設定されていません（.env を確認してください）。`);
        process.exit(1);
      }
    }
  }

  const videoUrl = `${GITHUB_RAW_BASE || '<GITHUB_RAW_BASE未設定>'}/${post.videoPath}`;
  const caption = `${post.caption}\n\n${post.hashtags}`;

  console.log('動画URL: ' + videoUrl);
  console.log('キャプション:\n' + caption);

  if (DRY_RUN) {
    console.log('\n[--dry-run] 実際の投稿は行いませんでした。');
    return;
  }

  const container = await graphPost(`${IG_BUSINESS_ACCOUNT_ID}/media`, {
    media_type: 'REELS',
    video_url: videoUrl,
    caption,
    access_token: IG_ACCESS_TOKEN,
  });
  console.log('メディアコンテナを作成:', container.id);

  await waitUntilFinished(container.id, IG_ACCESS_TOKEN);

  const published = await graphPost(`${IG_BUSINESS_ACCOUNT_ID}/media_publish`, {
    creation_id: container.id,
    access_token: IG_ACCESS_TOKEN,
  });
  console.log('公開しました。media id:', published.id);

  post.status = 'posted';
  post.postedAt = new Date().toISOString();
  post.instagramMediaId = published.id;
  writeManifest(manifest);
  console.log(`posts/manifest.json を更新しました（#${post.id} -> posted）。`);
}

async function waitUntilFinished(containerId, accessToken) {
  // 動画は画像より処理に時間がかかるため、カルーセル版より待機を長めにしている
  for (let i = 0; i < 40; i++) {
    const status = await graphGet(containerId, { fields: 'status_code', access_token: accessToken });
    if (status.status_code === 'FINISHED') return;
    if (status.status_code === 'ERROR') {
      throw new Error(`メディアコンテナの処理に失敗しました: ${JSON.stringify(status)}`);
    }
    await sleep(3000);
  }
  throw new Error('メディアコンテナの処理がタイムアウトしました。');
}

async function graphPost(edge, params) {
  const url = `https://graph.instagram.com/${GRAPH_API_VERSION}/${edge}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(params).toString(),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(`Graph API エラー (${edge}): ${JSON.stringify(json)}`);
  return json;
}

async function graphGet(nodeId, params) {
  const url = `https://graph.instagram.com/${GRAPH_API_VERSION}/${nodeId}?${new URLSearchParams(params).toString()}`;
  const res = await fetch(url);
  const json = await res.json();
  if (!res.ok) throw new Error(`Graph API エラー (${nodeId}): ${JSON.stringify(json)}`);
  return json;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
}

function writeManifest(manifest) {
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + '\n');
}

function loadDotEnv(envPath) {
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
