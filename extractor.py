"""
extractor.py — 選挙公報から候補者情報を抽出するモジュール
OpenAI Vision API (gpt-4o) を使用し、レイアウトに依存しない柔軟な抽出を行う。
"""

import base64
import io
import json
import re
import sys
from pathlib import Path

import openai
from PIL import Image, ImageEnhance, ImageFilter

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


# ─────────────────────────────────────────
# プロンプト定義
# ─────────────────────────────────────────

SYSTEM_PROMPT = """あなたは選挙公報（選挙広報）の解析専門家です。
画像から候補者情報を正確に抽出し、必ず指定のJSON形式のみで回答してください。
レイアウトがバラバラでも柔軟に対応してください。コードブロックや説明文は不要です。
日本語のOCRに慣れており、手書き・印刷・縦書き・横書きいずれにも対応できます。"""

EXTRACTION_PROMPT = """この選挙公報の画像を解析し、候補者情報を以下のJSON形式で抽出してください。

出力形式（純粋なJSONのみ。コードブロック・説明文は不要）:
{
  "candidates": [
    {
      "name": "候補者名（判読不能な場合は「不明_1」のように連番付きで）",
      "party": "政党名（検出できない場合はnull）",
      "profile": "年齢・経歴・学歴・肩書き・キャッチフレーズなど人物に関する情報すべて（政策は含めない）",
      "policies": [
        "政策・公約・主張の項目1",
        "政策・公約・主張の項目2"
      ],
      "other": "連絡先・電話番号・メール・URL・SNS・事務所住所など上記以外の情報（なければnull）",
      "needs_review": false
    }
  ]
}

抽出ルール:
- 【全フィールド共通】画像に書かれている文字をそのまま転記する。要約・言い換え・解釈・追加は一切禁止
- 【policies 厳守】政策・公約は原文を一字一句そのまま転記する。箇条書きの場合は各項目をそのまま配列に入れる。AIによる整形・補足・まとめは絶対にしない
- profile と policies は必ず分けること
- OCRが不鮮明・読み取り困難な箇所がある場合は needs_review: true にする
- 候補者が見当たらない場合は candidates: [] を返す
- 同一ページに複数候補者がいる場合はすべて抽出する
- 候補者名は姓名をスペースなしで連結（例：「山田太郎」）
- 選挙区名・選挙名は含めない"""

RETRY_PROMPT = """前回の抽出で一部が読み取り困難でした。
この画像をより注意深く解析し、特に以下に気をつけてください：
- 小さい文字・薄い文字も丁寧に読む
- 縦書きテキストも横書きと同様に処理する
- 候補者の区切りを正確に認識する

同じJSON形式で再度抽出してください。"""


# ─────────────────────────────────────────
# ヘルパー関数
# ─────────────────────────────────────────

def _preprocess_image(img: Image.Image) -> Image.Image:
    """画像の前処理：コントラスト・シャープネスを強化してOCR精度を向上"""
    img = img.convert("RGB")
    # シャープネス強化
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    # コントラスト強化
    img = ImageEnhance.Contrast(img).enhance(1.2)
    return img


def _resize_image(img: Image.Image, max_side: int = 2048) -> Image.Image:
    """画像を OpenAI Vision の推奨最大サイズ以内にリサイズ"""
    w, h = img.size
    if max(w, h) <= max_side:
        return img
    ratio = max_side / max(w, h)
    return img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)


def _image_to_base64(img: Image.Image) -> str:
    """PIL 画像を data URL 形式の base64 文字列に変換"""
    buf = io.BytesIO()
    img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=95)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{data}"


def _parse_json_response(text: str) -> dict:
    """レスポンステキストから JSON を抽出してパース"""
    # コードブロック除去
    text = re.sub(r"```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```", "", text)
    # JSON オブジェクト部分を抽出
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text.strip())


def _call_api(
    client: openai.OpenAI,
    image_url: str,
    user_prompt: str,
) -> str:
    """Vision API を呼び出してテキストを返す"""
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=8192,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "high"},
                    },
                    {"type": "text", "text": user_prompt},
                ],
            },
        ],
    )
    return response.choices[0].message.content or ""


# ─────────────────────────────────────────
# 抽出コア
# ─────────────────────────────────────────

def _extract_from_image(
    client: openai.OpenAI,
    img: Image.Image,
    source_file: str,
    page_num: int,
    unknown_counter: list[int],
) -> list[dict]:
    """単一画像から候補者情報を抽出して返す"""
    img = _preprocess_image(img)
    img = _resize_image(img)
    image_url = _image_to_base64(img)

    candidates = None

    # 1回目の試行
    try:
        text = _call_api(client, image_url, EXTRACTION_PROMPT)
        parsed = _parse_json_response(text)
        candidates = parsed.get("candidates", [])
    except (json.JSONDecodeError, Exception) as e:
        print(f"\n    ↺ パース失敗、再試行中... ({e})", end=" ", flush=True)

    # パース失敗 or needs_review がある場合はリトライ
    if candidates is None or any(c.get("needs_review") for c in candidates):
        if candidates is not None:
            print(f"\n    ↺ 精度不足を検出、再抽出中...", end=" ", flush=True)
        try:
            retry_text = _call_api(client, image_url, RETRY_PROMPT)
            retry_parsed = _parse_json_response(retry_text)
            retry_candidates = retry_parsed.get("candidates", [])
            if candidates is None:
                candidates = retry_candidates
                print("完了")
            else:
                retry_needs_review = sum(1 for c in retry_candidates if c.get("needs_review"))
                orig_needs_review = sum(1 for c in candidates if c.get("needs_review"))
                if retry_candidates and retry_needs_review <= orig_needs_review:
                    candidates = retry_candidates
                    print("改善")
                else:
                    print("変化なし")
        except (json.JSONDecodeError, Exception) as e2:
            print(f"\n  ⚠ 抽出エラー ({source_file} p.{page_num}): {e2}", file=sys.stderr)
            if candidates is None:
                candidates = [{
                    "name": f"不明_{unknown_counter[0]}",
                    "party": None,
                    "profile": "",
                    "policies": [],
                    "other": None,
                    "needs_review": True,
                }]
                unknown_counter[0] += 1

    # 「不明」候補者に連番を割り当て・共通フィールドを付与
    for cand in candidates:
        name = cand.get("name", "")
        if not name or re.match(r"^不明", name):
            cand["name"] = f"不明_{unknown_counter[0]}"
            unknown_counter[0] += 1
        cand.setdefault("party", None)
        cand.setdefault("profile", "")
        cand.setdefault("policies", [])
        cand.setdefault("other", None)
        cand.setdefault("needs_review", False)
        cand["source_file"] = source_file
        cand["source_page"] = page_num

    return candidates


# ─────────────────────────────────────────
# ファイル処理
# ─────────────────────────────────────────

def process_file(
    file_path: Path,
    client: openai.OpenAI,
    unknown_counter: list[int],
) -> list[dict]:
    """1ファイル（PDF/画像）を処理して候補者リストを返す"""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        if not HAS_FITZ:
            print("  ⚠ PyMuPDF 未インストール。PDF をスキップします。", file=sys.stderr)
            return []
        doc = fitz.open(str(file_path))
        results = []
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            print(f"    ページ {page_num}/{len(doc)} ...", end=" ", flush=True)
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            cands = _extract_from_image(client, img, file_path.name, page_num, unknown_counter)
            print(f"{len(cands)} 名")
            results.extend(cands)
        doc.close()
        return results

    elif suffix in (".jpg", ".jpeg", ".png"):
        print(f"    画像処理中 ...", end=" ", flush=True)
        img = Image.open(file_path)
        cands = _extract_from_image(client, img, file_path.name, 1, unknown_counter)
        print(f"{len(cands)} 名")
        return cands

    else:
        print(f"  ⚠ 未対応形式: {suffix}（スキップ）", file=sys.stderr)
        return []
