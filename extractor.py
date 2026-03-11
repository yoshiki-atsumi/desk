"""
extractor.py — 選挙公報から候補者情報を抽出するモジュール

2段階パイプライン:
  1. Google Cloud Vision: ページ全体のOCR
  2. gpt-4o (Vision + OCRテキスト): ページ画像＋OCRテキストで全候補者を一括ハイブリッド構造化
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

STRUCTURE_PROMPT = """以下の画像と、その画像から取得したOCRテキストを使って、選挙公報に掲載されている全候補者の情報を構造化してください。

【OCRテキスト（文字の正確さの参考）】
{ocr_text}

【画像（候補者の区切り・レイアウト・OCR誤りの修正に使用）】

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
- 画像でレイアウトを確認し、各候補者の区切りを正確に判断する（情報の混在禁止）
- OCRテキストを基本とし、画像で誤りを修正する（特に候補者名）
- profile・policies・other は書かれている内容をそのまま転記する（要約・解釈・追加禁止）
- policies は箇条書きの各項目をそのまま・漏れなく配列に入れる
- OCRと画像で判断がつかない箇所は needs_review: true にする
- 選挙区名・選挙名は含めない
- 候補者が見当たらない場合は candidates: [] を返す"""


# ─────────────────────────────────────────
# ヘルパー関数
# ─────────────────────────────────────────

def _preprocess_image(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    return img


def _resize_image(img: Image.Image, max_side: int = 2048) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_side:
        return img
    ratio = max_side / max(w, h)
    return img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)


def _image_to_base64_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=95)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{data}"


def _image_to_base64_raw(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def _parse_json_response(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text.strip())


# ─────────────────────────────────────────
# Stage 1: Google Cloud Vision OCR
# ─────────────────────────────────────────

def _ocr_google_vision(google_api_key: str, img: Image.Image) -> str:
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
    annotation = resp.json()["responses"][0].get("fullTextAnnotation", {})
    return annotation.get("text", "")


# ─────────────────────────────────────────
# Stage 2: ハイブリッド構造化
# ─────────────────────────────────────────

def _structure_hybrid(
    client: openai.OpenAI,
    ocr_text: str,
    page_img: Image.Image,
    source_file: str,
    page_num: int,
    unknown_counter: list[int],
) -> list[dict]:
    """ページ画像＋OCRテキストをgpt-4oに渡して全候補者を構造化"""
    image_url = _image_to_base64_url(page_img)
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
# 抽出コア
# ─────────────────────────────────────────

def _extract_from_image(
    client: openai.OpenAI,
    google_api_key: str,
    img: Image.Image,
    source_file: str,
    page_num: int,
    unknown_counter: list[int],
) -> list[dict]:
    """1ページ画像からOCR→ハイブリッド構造化で候補者を抽出"""
    img = _preprocess_image(img)
    img = _resize_image(img)

    try:
        # Stage 1: ページ全体をOCR
        ocr_text = _ocr_google_vision(google_api_key, img)

        # Stage 2: 画像＋OCRでハイブリッド構造化
        return _structure_hybrid(client, ocr_text, img, source_file, page_num, unknown_counter)

    except Exception as e:
        print(f"\n    ⚠ 抽出エラー ({source_file} p.{page_num}): {e}", file=sys.stderr)
        unknown_counter[0] += 1
        return [{
            "name": f"不明_{unknown_counter[0] - 1}",
            "party": None,
            "profile": "",
            "policies": [],
            "other": None,
            "needs_review": True,
            "source_file": source_file,
            "source_page": page_num,
        }]


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
