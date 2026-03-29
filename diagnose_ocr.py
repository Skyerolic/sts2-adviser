"""
diagnose_ocr.py
诊断工具：截取当前游戏窗口并做全图 OCR，输出识别到的所有文字
用法：在游戏显示选卡/商店界面时运行
  python diagnose_ocr.py
"""
import sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from PIL import Image, ImageDraw

from vision.ocr_engine import WindowsOcrEngine
from vision.screen_detector import ScreenDetector, ScreenType
from vision.card_extractor import CardExtractor
from vision.window_capture import WindowCapture

def main():
    # 1. 找游戏窗口（使用 PrintWindow，不受遮挡影响）
    cap = WindowCapture()
    info = cap.find_window()
    if not info:
        print('未找到 STS2 窗口，请先启动游戏')
        return

    w, h = info.width, info.height
    print(f'窗口: {w}x{h} @ ({info.left},{info.top})  hwnd={info.hwnd}')

    # 2. 截图
    img_bgr = cap.capture()
    if img_bgr is None:
        print('截图失败')
        return

    # 保存原图
    pil_full = Image.fromarray(img_bgr[:, :, ::-1])
    pil_full.save('diagnose_full.png')
    print(f'原始截图已保存: diagnose_full.png')

    # 3. 全图 OCR
    ocr = WindowsOcrEngine()
    ocr.initialize()
    print(f'\nOCR 语言: {ocr.language}')

    result = ocr.recognize(img_bgr)
    print(f'\n=== 全图 OCR 结果 ===')
    print(result.full_text if result.full_text else '(空)')

    # 4. 垂直分段扫描（每10%一段），定位卡名所在高度
    print(f'\n=== 垂直分段扫描（定位卡名高度）===')
    segments = 10
    for i in range(segments):
        y0 = int(h * i / segments)
        y1 = int(h * (i + 1) / segments)
        seg = img_bgr[y0:y1, :]
        seg_result = ocr.recognize(seg)
        text = seg_result.full_text.replace('\n', ' ').strip()
        if text:
            print(f'  y={i*10}%~{(i+1)*10}% ({y0}~{y1}px): {text[:80]}')

    # 5. 界面检测
    print(f'\n=== 界面检测 ===')
    detector = ScreenDetector(vote_frames=1)
    det = detector.detect(img_bgr)
    print(f'类型: {det.screen_type.value}')
    print(f'置信度: {det.confidence:.2f}')
    print(f'匹配词: {det.matched_keywords}')

    # 6. 卡名区域裁剪预览（动态定位）
    print(f'\n=== 卡名区域裁剪（动态定位）===')
    extractor = CardExtractor()
    regions = extractor.extract_from_ocr(img_bgr, result)

    # 绘制裁剪框
    annotated = pil_full.copy()
    draw = ImageDraw.Draw(annotated)
    colors = [(0, 255, 0), (0, 200, 255), (255, 100, 0)]
    for r in regions:
        rl, rt, rr, rb = r.abs_rect
        draw.rectangle([rl, rt, rr, rb], outline=colors[r.index], width=3)
        draw.text((rl, max(rt-20, 0)), f'Card{r.index+1}', fill=colors[r.index])

    annotated.save('diagnose_annotated.png')
    print(f'带标注截图已保存: diagnose_annotated.png')

    for region in regions:
        if region.image.size == 0:
            print(f'  卡{region.index+1}: 区域无效')
            continue
        r_img = Image.fromarray(region.image[:, :, ::-1])
        r_img.save(f'diagnose_card{region.index+1}.png')
        hint = f' (hint={repr(region.ocr_hint)})' if region.ocr_hint else ''
        r_result = ocr.recognize(region.image)
        print(f'  卡{region.index+1} OCR: {repr(r_result.full_text)}{hint}')

    print('\n诊断完成。请查看 diagnose_*.png 文件。')

if __name__ == '__main__':
    main()
