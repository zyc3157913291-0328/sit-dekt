# -*- coding: utf-8 -*-
"""本地识别 CAS 验证码（离线 ONNX，不联网、不消耗 token）。

用法: python ocr-captcha.py <验证码图片> <结果输出文件>
结果写入输出文件（避免依赖 stdout，规避管道限制）。
"""
import sys

if len(sys.argv) < 3:
    sys.exit(2)

img_path, out_path = sys.argv[1], sys.argv[2]

try:
    import ddddocr
except Exception as e:                      # 依赖缺失 -> 让调用方走大模型兜底
    open(out_path, 'w', encoding='utf-8').write('')
    sys.exit(3)

try:
    ocr = ddddocr.DdddOcr(show_ad=False)
except TypeError:
    ocr = ddddocr.DdddOcr()

try:
    with open(img_path, 'rb') as f:
        code = ocr.classification(f.read())
    code = ''.join(ch for ch in (code or '') if ch.isalnum())
    open(out_path, 'w', encoding='utf-8').write(code)
    print(code)
except Exception as e:
    open(out_path, 'w', encoding='utf-8').write('')
    print('OCR_ERROR: %s' % e, file=sys.stderr)
    sys.exit(4)
