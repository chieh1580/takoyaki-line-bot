import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import sharp from 'sharp';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ==========================================
// >>> 請填入 LINE Channel Access Token <<<
// ==========================================
const TOKEN = "apl6K1k63estdvxX8x5z7dgjOYonZWjmpnd8r4bWa8tg7F+1eAx4pTmzIdCABE8I15cuUOpIwvCtE0z/4+k2CrzH0gid4oKnEpO/qqoyx349k+nQRpag0cBYh0IOp9asdXQrFpXnspOvSp0lue0JqQdB04t89/1O/w1cDnyilFU=";

// 圖文選單圖片
const IMAGE_PATH = path.join(__dirname, "richmenu.png");

// Step 0: 如果沒有自訂圖片，自動產生預設圖片
if (!fs.existsSync(IMAGE_PATH)) {
  console.log("⚠️ 找不到 richmenu.png，正在產生預設圖片...");

  const svg = `
  <svg width="2500" height="1686" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#e85d04"/>
        <stop offset="100%" style="stop-color:#dc2f02"/>
      </linearGradient>
    </defs>
    <rect width="2500" height="1686" fill="url(#bg)"/>
    <rect x="40" y="40" width="858" height="1606" rx="30" fill="#ffffff20"/>
    <text x="469" y="700" font-family="sans-serif" font-size="140" fill="white" text-anchor="middle">&#x1F6D2;</text>
    <text x="469" y="920" font-family="sans-serif" font-size="80" fill="white" text-anchor="middle" font-weight="bold">&#x9EDE;&#x990C;&#x4E0B;&#x55AE;</text>
    <rect x="938" y="40" width="741" height="783" rx="30" fill="#ffffff20"/>
    <text x="1308" y="370" font-family="sans-serif" font-size="120" fill="white" text-anchor="middle">&#x1F4CB;</text>
    <text x="1308" y="530" font-family="sans-serif" font-size="64" fill="white" text-anchor="middle" font-weight="bold">&#x83DC;&#x55AE;</text>
    <rect x="1719" y="40" width="741" height="783" rx="30" fill="#ffffff20"/>
    <text x="2089" y="370" font-family="sans-serif" font-size="120" fill="white" text-anchor="middle">&#x23F0;</text>
    <text x="2089" y="530" font-family="sans-serif" font-size="64" fill="white" text-anchor="middle" font-weight="bold">&#x71DF;&#x696D;&#x8CC7;&#x8A0A;</text>
    <rect x="938" y="863" width="741" height="783" rx="30" fill="#ffffff20"/>
    <text x="1308" y="1193" font-family="sans-serif" font-size="120" fill="white" text-anchor="middle">&#x1F4E6;</text>
    <text x="1308" y="1353" font-family="sans-serif" font-size="64" fill="white" text-anchor="middle" font-weight="bold">&#x67E5;&#x8A02;&#x55AE;</text>
    <rect x="1719" y="863" width="741" height="783" rx="30" fill="#ffffff20"/>
    <text x="2089" y="1193" font-family="sans-serif" font-size="120" fill="white" text-anchor="middle">&#x1F4AC;</text>
    <text x="2089" y="1353" font-family="sans-serif" font-size="64" fill="white" text-anchor="middle" font-weight="bold">&#x806F;&#x7D61;&#x6211;&#x5011;</text>
  </svg>`;

  await sharp(Buffer.from(svg)).png().toFile(IMAGE_PATH);
  console.log("✅ 已產生預設 richmenu.png");
}

// Step 1: 建立圖文選單結構
console.log("Step 1: 建立圖文選單...");

const richMenuBody = {
  size: { width: 2500, height: 1686 },
  selected: true,
  name: "章魚燒攤主選單",
  chatBarText: "🐙 點我點餐",
  areas: [
    { bounds: { x: 0, y: 0, width: 938, height: 1686 }, action: { type: "message", text: "我要點餐" } },
    { bounds: { x: 938, y: 0, width: 781, height: 843 }, action: { type: "message", text: "菜單" } },
    { bounds: { x: 1719, y: 0, width: 781, height: 843 }, action: { type: "message", text: "營業資訊" } },
    { bounds: { x: 938, y: 843, width: 781, height: 843 }, action: { type: "message", text: "查訂單" } },
    { bounds: { x: 1719, y: 843, width: 781, height: 843 }, action: { type: "message", text: "聯絡我們" } },
  ],
};

let r = await fetch("https://api.line.me/v2/bot/richmenu", {
  method: "POST",
  headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
  body: JSON.stringify(richMenuBody),
});

if (!r.ok) {
  console.log(`❌ 建立失敗: ${r.status} ${await r.text()}`);
  process.exit(1);
}

const { richMenuId } = await r.json();
console.log(`✅ 建立成功，ID: ${richMenuId}`);

// Step 2: 壓縮並上傳圖片
console.log("Step 2: 壓縮並上傳圖片...");
const imgData = await sharp(IMAGE_PATH)
  .resize(2500, 1686, { fit: 'fill' })
  .jpeg({ quality: 80 })
  .toBuffer();
console.log(`   圖片壓縮後大小: ${(imgData.length / 1024).toFixed(0)} KB`);

let r2 = await fetch(`https://api-data.line.me/v2/bot/richmenu/${richMenuId}/content`, {
  method: "POST",
  headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "image/jpeg" },
  body: imgData,
});

if (!r2.ok) {
  console.log(`❌ 圖片上傳失敗: ${r2.status} ${await r2.text()}`);
  process.exit(1);
}

console.log("✅ 圖片上傳成功");

// Step 3: 設為預設選單
console.log("Step 3: 設為預設選單...");
let r3 = await fetch(`https://api.line.me/v2/bot/user/all/richmenu/${richMenuId}`, {
  method: "POST",
  headers: { Authorization: `Bearer ${TOKEN}` },
});

if (r3.ok) {
  console.log("✅ 全部完成！圖文選單已上線");
  console.log(`Rich Menu ID: ${richMenuId}`);
} else {
  console.log(`⚠️ 設預設失敗: ${r3.status} ${await r3.text()}`);
}
