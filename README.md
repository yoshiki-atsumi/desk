# AIボートマッチ — 選挙公報パーサー

選挙公報（PDF・画像）から候補者情報を自動抽出し、AIチャットボット「AIボートマッチ」で有権者に情報を届けるシステムです。

---

## 概要

誰に投票したらいいかわからない有権者を支援するため、選挙公報を構造化データに変換し、チャットで自由に質問できる仕組みを提供します。**公平性・正確性**を大前提に、選挙公報に書かれていることを一字一句そのまま出力します。

---

## システム構成

```
input/（選挙公報PDF・画像）
       ↓
【Step 1】Google Cloud Vision API
         ページ全体のOCR（日本語高精度）
       ↓
【Step 2】OpenAI gpt-4o（Vision + OCRテキスト）
         ページ画像とOCRテキストを同時入力するハイブリッド方式
         → 候補者ごとにプロフィール・政策・その他を構造化
       ↓
output/（JSON / CSV / Markdown）
       ↓
chatbot.html（AIボートマッチ）
```

---

## セットアップ

### 必要なもの
- Python 3.10 以上
- OpenAI API キー（[platform.openai.com](https://platform.openai.com/api-keys)）
- Google Cloud Vision API キー（[console.cloud.google.com](https://console.cloud.google.com/apis/library/vision.googleapis.com)）

### インストール

```bash
pip install -r requirements.txt
```

### 環境変数（.env）

```
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
```

---

## 使い方

```bash
# 1. input/ フォルダに選挙公報ファイルを置く（PDF / JPG / PNG）

# 2. 実行
python main.py

# 3. output/ に結果が生成される

# 4. chatbot.html をブラウザで開いてAIボートマッチを起動
```

---

## ファイル構成

```
.
├── input/                    ← 選挙公報を置くフォルダ
├── output/                   ← 出力先（実行のたびにクリア）
│   ├── candidates.json       ← 全候補者データ（チャットボット連携用）
│   ├── candidates.csv        ← 一覧表（Excel等で確認用）
│   ├── index.md              ← 候補者一覧インデックス
│   └── candidates/
│       └── {候補者名}.md     ← 候補者別Markdown
├── main.py                   ← エントリーポイント
├── extractor.py              ← OCR・AI抽出ロジック
├── writer.py                 ← 出力ロジック
├── chatbot.html              ← AIボートマッチ（フロントエンド）
├── requirements.txt
└── .env
```

---

## 出力スキーマ

```json
{
  "candidates": [
    {
      "name": "山田太郎",
      "party": "無所属",
      "profile": "1975年生まれ。〇〇大学卒業...",
      "policies": [
        "子育て支援の拡充と保育所の整備",
        "地域経済の活性化と雇用創出"
      ],
      "other": "事務所: 菊川市〇〇町...",
      "needs_review": false,
      "source_file": "senkyo_kouhou.pdf",
      "source_page": 1
    }
  ]
}
```

---

## 設計方針

### 公平性・正確性
- 選挙公報に書かれていることを**一字一句そのまま転記**（要約・解釈・追加禁止）
- AIが特定の候補者を推薦・否定しない
- 情報の混在を防ぐため、ページ画像とOCRテキストの両方でダブルチェック

### 精度向上のための工夫
| 工夫 | 内容 |
|------|------|
| ハイブリッド入力 | Google Vision OCR（文字精度）＋ gpt-4o Vision（レイアウト理解）を同時に使用 |
| 高解像度処理 | PDF を 200dpi で画像化、最大 2048px まで拡大 |
| 画像前処理 | シャープネス・コントラスト強化でOCR精度を向上 |
| 重複除去 | 同一人物が複数登録されないよう類似名チェック（difflib 80%閾値） |
| 出力クリーン化 | 実行のたびに candidates/ フォルダをリセット |

---

## AIボートマッチ（chatbot.html）

`chatbot.html` をブラウザで開くだけで動作します（サーバー不要）。

**機能：**
- 左サイドバーに候補者一覧 → クリックで質問
- クイック質問ボタン（子育て・防災・経済・福祉）
- 自由質問チャット
- OpenAI APIキーはブラウザの localStorage に保存

**システムプロンプトの設計：**
- `candidates.json` の全データをコンテキストとして使用
- 公平・中立を保つ指示を明記
- 選挙公報に記載のない情報は答えない

---

## 注意事項

- 出力内容は必ず人の目で確認してから公開・利用してください
- OCR・AI の誤認識が含まれる可能性があります（`needs_review: true` の候補者は要確認）
- API 利用料がかかります（OpenAI: gpt-4o Vision料金、Google: 月1,000回まで無料）
