#!/usr/bin/env python3
"""
Receipt pre-processor for Party World Shop.
- Decodes base64 image
- Runs local OCR via tesseract
- Detects if it's a valid receipt (not a summary note)
- Returns JSON: { is_receipt, reason, raw_text, lines }

This runs BEFORE Gemini, so non-receipts never hit the API.
For real receipts, Gemini only receives the OCR text (not the image),
saving significant vision token costs.
"""

import sys
import json
import base64
import re
import tempfile
import os

def main():
    try:
        import pytesseract
        from PIL import Image
        import io
        import cv2
        import numpy as np
    except ImportError as e:
        print(json.dumps({"error": f"Missing library: {e}"}))
        sys.exit(1)

    # Read input JSON from stdin
    try:
        data = json.load(sys.stdin)
        b64 = data.get("image", "")
        if not b64:
            raise ValueError("No image provided")
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # Decode image
    try:
        img_bytes = base64.b64decode(b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        print(json.dumps({"error": f"Image decode failed: {e}"}))
        sys.exit(1)

    # Pre-process for better OCR: grayscale + threshold
    img_np = np.array(img)
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    processed = Image.fromarray(thresh)

    # Run OCR
    try:
        raw_text = pytesseract.image_to_string(processed, config="--oem 3 --psm 6")
    except Exception as e:
        # Fallback to original image
        try:
            raw_text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
        except Exception as e2:
            print(json.dumps({"error": f"OCR failed: {e2}"}))
            sys.exit(1)

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    text_lower = raw_text.lower()

    # ── Receipt detection heuristics ─────────────────────────────────────────
    # Positive signals
    has_party_world     = "party world" in text_lower or "party wor" in text_lower
    has_particulars     = "particulars" in text_lower or "particul" in text_lower
    has_qty_header      = bool(re.search(r'\bqty\.?\b', text_lower))
    has_shs_header      = "shs" in text_lower
    has_cash_sale       = "cash sale" in text_lower
    has_receipt_number  = bool(re.search(r'\b1[78]\d{2}\b', raw_text))  # 1700-1999 range
    has_goods_note      = "goods once sold" in text_lower
    has_eoe             = "e.&o.e" in text_lower or "e.ao.e" in text_lower or "e&oe" in text_lower

    receipt_score = sum([
        has_party_world * 3,
        has_cash_sale * 3,
        has_goods_note * 2,
        has_particulars * 2,
        has_qty_header * 2,
        has_shs_header * 1,
        has_eoe * 1,
        has_receipt_number * 1,
    ])

    # Negative signals — summary notes look like this
    summary_patterns = [
        r'\bsales\s*[-–]\s*\d{4,}',   # "sales - 11950"
        r'\bphone\s*[-–]\s*\d{4,}',   # "phone - 11700"
        r'\bmumo\s*[-–]\s*\d+',       # "mumo - 100"
        r'\bmomo\s*[-–]\s*\d+',
        r'\btape\s*[-–]\s*\d+',       # "tape - 150"
        r'\bcash\s*[-–]\s*\d+',
    ]
    summary_hits = sum(1 for p in summary_patterns if re.search(p, text_lower))

    # Decision: three states
    #   True  = definitely a receipt (high confidence)
    #   False = definitely NOT a receipt (summary note)
    #   None  = uncertain — let Gemini decide
    if summary_hits >= 2:
        is_receipt = False
        reason = "This appears to be a daily sales summary note, not a cash sale receipt"
    elif receipt_score >= 4:
        is_receipt = True
        reason = f"Valid receipt detected (score {receipt_score}/14)"
    else:
        # Low confidence but not clearly a summary — pass to Gemini
        is_receipt = None
        reason = f"Uncertain (score {receipt_score}/14) — passing to AI for verification"

    result = {
        "is_receipt": is_receipt,
        "reason": reason,
        "raw_text": raw_text,
        "lines": lines,
        "debug": {
            "receipt_score": receipt_score,
            "summary_hits": summary_hits,
            "signals": {
                "party_world": has_party_world,
                "cash_sale": has_cash_sale,
                "particulars": has_particulars,
                "qty": has_qty_header,
                "shs": has_shs_header,
                "goods_note": has_goods_note,
                "receipt_number": has_receipt_number,
                "eoe": has_eoe,
            }
        }
    }

    print(json.dumps(result))

if __name__ == "__main__":
    main()
