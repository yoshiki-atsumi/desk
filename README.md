# 選挙公報 AI パーサー

選挙公報（PDF・画像）を読み込み、候補者ごとに**プロフィール**と**政策**を自動抽出するツールです。
AIボートマッチ（Q&Aシステム）への組み込みを前提とした構造化データを出力します。

---

## 特徴

- **PDF・画像対応** — PDF、JPG、PNG を一括処理
- **レイアウト自由** — 候補者ごとにデザインが異なっていても柔軟に対応
- **プロフィール／政策を分離** — 混在させず別フィールドで出力
- **複数形式で出力** — JSON / CSV / Markdown（候補者別）
- **RAG 対応設計** — 政策を1項目1要素の配列で格納し、チャンク分割しやすい構造
- **エラー耐性** — OCR不鮮明・候補者名不明の場合もフラグ付きで出力

---

## 必要環境

- Python 3.10 以上
- OpenAI API キー（[platform.openai.com](https://platform.openai.com/api-keys) で取得）

---

## セットアップ

```bash
# 1. 依存パッケージをインストール
pip install -r requirements.txt

# 2. API キーを設定
cp .env.example .env
# .env を開いて OPENAI_API_KEY=sk-... を記入
```

---

## 使い方

```bash
# 1. 選挙公報ファイルを input/ フォルダに置く
#    （PDF / JPG / PNG、複数ファイル可）

# 2. 実行
python main.py
```

出力は `output/` フォルダに生成されます。

---

## ファイル構成

```
.
├── input/                    ← 選挙公報を置くフォルダ
├── output/                   ← 出力先（自動生成）
│   ├── candidates.json       ← 全候補者データ（RAG連携用）
│   ├── candidates.csv        ← 一覧表（Excel等で確認用）
│   ├── index.md              ← 候補者一覧インデックス
│   └── candidates/
│       ├── 山田太郎_profile.md
│       ├── 山田太郎_policies.md
│       └── ...
├── main.py                   ← エントリーポイント
├── extractor.py              ← OpenAI Vision による抽出ロジック
├── writer.py                 ← 出力書き込みロジック
├── requirements.txt
└── .env.example
```

---

## 出力スキーマ

### JSON（`candidates.json`）

```json
{
  "candidates": [
    {
      "name": "山田 太郎",
      "party": "〇〇党",
      "profile": "1975年生まれ。〇〇大学卒業後、△△会社に入社...",
      "policies": [
        "子育て支援の拡充と保育所の整備",
        "地域経済の活性化と雇用創出",
        "再生可能エネルギーへの移行推進"
      ],
      "needs_review": false,
      "source_file": "senkyo_kouhou.pdf",
      "source_page": 1
    }
  ]
}
```

### CSV（`candidates.csv`）

| 候補者名 | 政党 | プロフィール | 政策テキスト | 元ファイル | ページ | 要確認 |
|---------|------|-------------|-------------|-----------|--------|--------|

---

## 処理フロー

```
input/ の PDF・画像
       ↓
PDF → ページ単位で画像変換（150dpi）
       ↓
OpenAI gpt-4o（Vision）で解析
       ↓
候補者ごとにプロフィール・政策を抽出
       ↓
output/ に JSON / CSV / Markdown を出力
```

---

## エッジケース対応

| 状況 | 対応 |
|------|------|
| 1ページに複数候補者 | 全員を抽出 |
| 候補者名が読めない | `不明_1`、`不明_2`… と連番でラベル付け |
| OCR精度が低い | `needs_review: true` フラグを付与 |
| 未対応の形式 | スキップしてログに記録 |

---

## AIボートマッチへの連携イメージ

```
candidates.json
       ↓
各候補者の policies 配列をチャンク分割
       ↓
ベクトルDBに格納（RAG）
       ↓
有権者の質問 → 類似政策を検索 → 候補者ごとの回答を生成
```

`policies` は1項目1要素の配列になっているため、そのままチャンクとして扱えます。

---

## 注意事項

- 処理コストは画像1枚あたり OpenAI API の利用料がかかります（gpt-4o の Vision 料金）
- 大量ページの PDF は処理時間・コストが増加します
- 出力内容は必ず人の目で確認してから公開・利用してください
