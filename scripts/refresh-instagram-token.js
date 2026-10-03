/**
 * .env の IG_ACCESS_TOKEN（長期トークン、60日間有効）を更新するスクリプト。
 * 長期トークンは発行から24時間以上経過していれば、有効期限が切れる前なら
 * 何度でも更新（延長）できる。60日ごとに実行するのが目安。
 *
 * （神社インスタグラム(jinja-instagram)の同名スクリプトと同じ内容）
 *
 * 使い方: node scripts/refresh-instagram-token.js
 */
const fs = require('fs');
const path = require('path');

const ENV_PATH = path.join(__dirname, '..', '.env');
loadDotEnv(ENV_PATH);

async function main() {
  const { IG_ACCESS_TOKEN } = process.env;
  if (!IG_ACCESS_TOKEN) {
    console.error('IG_ACCESS_TOKEN が .env に設定されていません。');
    process.exit(1);
  }

  const url = `https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=${encodeURIComponent(IG_ACCESS_TOKEN)}`;
  const res = await fetch(url);
  const json = await res.json();

  if (!res.ok || !json.access_token) {
    console.error('更新に失敗しました:', JSON.stringify(json));
    process.exit(1);
  }

  console.log(`更新に成功しました。有効期限: 約${Math.round(json.expires_in / 86400)}日後`);

  // GitHub Actions 上では .env が無いので、ログに出ないようマスクしてステップ出力に渡す
  if (process.env.GITHUB_OUTPUT) {
    console.log(`::add-mask::${json.access_token}`);
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `token=${json.access_token}\n`);
    console.log('新しいトークンをステップ出力に書き出しました。');
    return;
  }

  const envContent = fs.readFileSync(ENV_PATH, 'utf8');
  const updated = envContent.replace(/^IG_ACCESS_TOKEN=.*$/m, `IG_ACCESS_TOKEN=${json.access_token}`);
  fs.writeFileSync(ENV_PATH, updated);
  console.log('.env の IG_ACCESS_TOKEN を更新しました。');
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
