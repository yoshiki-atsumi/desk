"""
extractor.py — 選挙公報から候補者情報を抽出するモジュール

3段階パイプライン:
  1. gpt-4o (Vision): ページ内の候補者領域を検出・切り出し
  2. Google Cloud Vision: 各候補者領域のOCR
  3. gpt-4o (Vision + OCRテキスト): 画像とOCR両方を使ってハイブリッド構造化
"""

import base64
import io
import json
import re
import sys
from pathlib import Path

import openai
import requests
from PIL import Image, ImageEnhance

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


# ─────────────────────────────────────────
# プロンプト定義
# ─────────────────────────────────────────

DETECT_PROMPT = """この選挙公報のページに掲載されている候補者の領域を検出してください。

出力形式（JSONのみ）:
{
  "candidates": [
    {
      "top": 0.0,
      "bottom": 0.5,
      "left": 0.0,
      "right": 1.0
    }
  ]
}

ルール:
- top/bottom/left/right は画像全体に対する比率（0.0〜1.0）
- 各候補者の氏名・プロフィール・政策が含まれる領域全体を囲む
- 候補者が1人だけの場合は candidates に1件だけ返す
- ヘッダー（選挙名・日付など）は含めない"""

STRUCTURE_PROMPT = """以下の画像と、その画像から取得したOCRテキストを使って、選挙公報に掲載されている候補者情報を構造化してください。
1名または複数名の候補者が含まれている場合があります。全員を抽出してください。

【OCRテキスト（文字の正確さの参考）】
{ocr_text}

【画像（レイアウト・文脈・OCR誤りの修正に使用）】

出力形式（JSONのみ）:
{{
  "candidates": [
    {{
      "name": "候補者名（OCRと画像を照合して正確に）",
      "party": "政党名（記載なければ null）",
      "profile": "年齢・経歴・学歴・肩書き・キャッチフレーズなど人物に関する情報すべて",
      "policies": [
        "政策・公約の項目をそのまま転記"
      ],
      "other": "連絡先・電話・メール・URL・SNS・事務所住所など（なければ null）",
      "needs_review": false
    }}
  ]
}}

厳守ルール:
- OCRテキストを基本とし、画像で内容を照合・補完・誤り訂正する
- OCRが明らかに誤っている文字（特に候補者名）は画像を見て修正する
- profile・policies・other は書かれている内容をそのまま転記する（要約・解釈・追加禁止）
- policies は箇条書きの各項目をそのまま配列に入れる
- OCRと画像で判断がつかない箇所は needs_review: true にする
- 選挙区名・選挙名は含めない
- 候補者が見当たらない場合は candidates: [] を返す"""


# ─────────────────────────────────────────
# ヘルパー関数
# ─────────────────────────────────────────

def _preprocess_image(img: Image.Image) -> Image.Image:
    """画像の前処理：コントラスト・シャープネスを強化"""
    img = img.convert("RGB")
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    return img


def _resize_image(img: Image.Image, max_side: int = 2048) -> Image.Image:
    """画像を最大サイズ以内にリサイズ"""
    w, h = img.size
    if max(w, h) <= max_side:
        return img
    ratio = max_side / max(w, h)
    return img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)


def _image_to_base64_url(img: Image.Image) -> str:
    """PIL 画像を data URL 形式の base64 文字列に変換"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=95)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{data}"


def _image_to_base64_raw(img: Image.Image) -> str:
    """PIL 画像を生の base64 文字列に変換（Google Vision用）"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def _parse_json_response(text: str) -> dict:
    """レスポンステキストから JSON を抽出してパース"""
    text = re.sub(r"```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text.strip())


# ─────────────────────────────────────────
# Stage 1: 候補者領域の検出
# ─────────────────────────────────────────

def _detect_regions(client: openai.OpenAI, img: Image.Image) -> list[Image.Image]:
    """gpt-4o で候補者領域を検出し、切り出した画像リストを返す"""
    image_url = _image_to_base64_url(img)
    w, h = img.size

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1024,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                    {"type": "text", "text": DETECT_PROMPT},
                ],
            }
        ],
    )
    parsed = _parse_json_response(response.choices[0].message.content or "")
    regions = parsed.get("candidates", [])

    if not regions:
        return [img]  # 検出失敗時はページ全体を使う

    crops = []
    for r in regions:
        pad = 0.01  # 少しパディングを追加
        x1 = max(0, int((r.get("left", 0) - pad) * w))
        y1 = max(0, int((r.get("top", 0) - pad) * h))
        x2 = min(w, int((r.get("right", 1) + pad) * w))
        y2 = min(h, int((r.get("bottom", 1) + pad) * h))
        crops.append(img.crop((x1, y1, x2, y2)))

    return crops


# ─────────────────────────────────────────
# Stage 2: Google Cloud Vision OCR
# ─────────────────────────────────────────

def _ocr_google_vision(google_api_key: str, img: Image.Image) -> str:
    """Google Cloud Vision API で OCR テキストを取得"""
    content = _image_to_base64_raw(img)
    resp = requests.post(
        f"https://vision.googleapis.com/v1/images:annotate?key={google_api_key}",
        json={
            "requests": [{
                "image": {"content": content},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": ["ja"]},
            }]
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    annotation = result["responses"][0].get("fullTextAnnotation", {})
    return annotation.get("text", "")


# ─────────────────────────────────────────
# Stage 3: テキストから構造化
# ─────────────────────────────────────────

def _structure_from_text(
    client: openai.OpenAI,
    ocr_text: str,
    crop_img: Image.Image,
    source_file: str,
    page_num: int,
    unknown_counter: list[int],
) -> list[dict]:
    """OCRテキスト＋画像をgpt-4oに渡してハイブリッド構造化し候補者dictのリストを返す"""
    image_url = _image_to_base64_url(crop_img)
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=8192,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": STRUCTURE_PROMPT.format(ocr_text=ocr_text)},
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                ],
            }
        ],
    )
    parsed = _parse_json_response(response.choices[0].message.content or "")
    candidates = parsed.get("candidates", [])

    for cand in candidates:
        name = cand.get("name") or ""
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
# 抽出コア（3段階パイプライン）
# ─────────────────────────────────────────

def _extract_from_image(
    client: openai.OpenAI,
    google_api_key: str,
    img: Image.Image,
    source_file: str,
    page_num: int,
    unknown_counter: list[int],
) -> list[dict]:
    """1ページ画像から3段階パイプラインで候補者情報を抽出"""
    img = _preprocess_image(img)
    img = _resize_image(img)

    # Stage 1: 候補者領域を検出・切り出し
    try:
        crops = _detect_regions(client, img)
    except Exception as e:
        print(f"\n    ⚠ 領域検出失敗、ページ全体で処理: {e}", file=sys.stderr)
        crops = [img]

    candidates = []
    for crop in crops:
        try:
            # Stage 2: Google Vision OCR
            ocr_text = _ocr_google_vision(google_api_key, crop)
            if not ocr_text.strip():
                continue

            # Stage 3: ハイブリッド構造化（画像＋OCRテキスト）
            cands = _structure_from_text(client, ocr_text, crop, source_file, page_num, unknown_counter)
            candidates.extend(cands)

        except Exception as e:
            print(f"\n    ⚠ 抽出エラー ({source_file} p.{page_num}): {e}", file=sys.stderr)
            candidates.append({
                "name": f"不明_{unknown_counter[0]}",
                "party": None,
                "profile": "",
                "policies": [],
                "other": None,
                "needs_review": True,
                "source_file": source_file,
                "source_page": page_num,
            })
            unknown_counter[0] += 1

    return candidates


# ─────────────────────────────────────────
# ファイル処理
# ─────────────────────────────────────────

def process_file(
    file_path: Path,
    client: openai.OpenAI,
    google_api_key: str,
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
            cands = _extract_from_image(client, google_api_key, img, file_path.name, page_num, unknown_counter)
            print(f"{len(cands)} 名")
            results.extend(cands)
        doc.close()
        return results

    elif suffix in (".jpg", ".jpeg", ".png"):
        print(f"    画像処理中 ...", end=" ", flush=True)
        img = Image.open(file_path)
        cands = _extract_from_image(client, google_api_key, img, file_path.name, 1, unknown_counter)
        print(f"{len(cands)} 名")
        return cands

    else:
        print(f"  ⚠ 未対応形式: {suffix}（スキップ）", file=sys.stderr)
        return []
