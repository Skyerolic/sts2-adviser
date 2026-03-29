"""
diagnose_ocr.py
诊断工具：截取当前游戏窗口，保存截图并做分段 OCR 扫描
用法：在游戏显示选卡界面时运行
  python diagnose_ocr.py
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vision.ocr_engine import WindowsOcrEngine
from vision.screen_detector import ScreenDetector
from vision.window_capture import WindowCapture


def main():
    # 1. 找游戏窗口
    cap = WindowCapture()
    info = cap.find_window()
    if not info:
        print('未找到 STS2 窗口，请先启动游戏并切换到选卡界面')
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
    print(f'原始截图: diagnose_full.png  ({w}x{h})')

    # 3. 全图 OCR
    ocr = WindowsOcrEngine()
    ocr.initialize()
    print(f'OCR 语言: {ocr.language}')

    result = ocr.recognize(img_bgr)
    print(f'\n=== 全图 OCR ===')
    print(result.full_text if result.full_text else '(空)')

    # 4. 垂直分段扫描，每 5% 一段
    print(f'\n=== 垂直分段扫描（每5%）===')
    segments = 20
    for i in range(segments):
        y0 = int(h * i / segments)
        y1 = int(h * (i + 1) / segments)
        seg = img_bgr[y0:y1, :]
        seg_result = ocr.recognize(seg)
        text = seg_result.full_text.replace('\n', ' ').strip()
        marker = ' ◄◄◄' if text else ''
        print(f'  y={i*5:3d}%~{(i+1)*5:3d}% ({y0:4d}~{y1:4d}px): {text[:60]}{marker}')

    # 5. 水平三等分 × 垂直分段，寻找卡名区域
    print(f'\n=== 水平三列 × 垂直扫描（每10%）===')
    cols = [(0.0, 0.34), (0.33, 0.67), (0.66, 1.0)]
    col_names = ['左', '中', '右']
    for ci, (x0r, x1r) in enumerate(cols):
        x0 = int(w * x0r)
        x1 = int(w * x1r)
        col_texts = []
        for i in range(10):
            y0 = int(h * i / 10)
            y1 = int(h * (i + 1) / 10)
            cell = img_bgr[y0:y1, x0:x1]
            cell_result = ocr.recognize(cell)
            text = cell_result.full_text.replace('\n', ' ').strip()
            if text:
                col_texts.append(f'y={i*10}%~{(i+1)*10}%: {text[:40]}')
        if col_texts:
            print(f'  [{col_names[ci]}列 x={x0r*100:.0f}%~{x1r*100:.0f}%]')
            for t in col_texts:
                print(f'    {t}')

    # 6. 保存网格截图（3列×5行，共15个区域）
    print(f'\n=== 保存网格截图（帮助定位卡名）===')
    annotated = pil_full.copy()
    draw = ImageDraw.Draw(annotated)
    grid_cols = [(0.05, 0.38), (0.35, 0.67), (0.63, 0.96)]
    grid_rows = [(0.1, 0.25), (0.25, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 0.85)]
    colors_grid = [(255,80,80),(80,255,80),(80,80,255)]
    saved = []
    for ri, (y0r, y1r) in enumerate(grid_rows):
        for ci, (x0r, x1r) in enumerate(grid_cols):
            x0, x1 = int(w*x0r), int(w*x1r)
            y0, y1 = int(h*y0r), int(h*y1r)
            cell = img_bgr[y0:y1, x0:x1]
            fname = f'diagnose_r{ri}c{ci}.png'
            Image.fromarray(cell[:,:,::-1]).save(fname)
            draw.rectangle([x0,y0,x1,y1], outline=colors_grid[ci], width=2)
            draw.text((x0+4, y0+4), f'r{ri}c{ci}', fill=colors_grid[ci])
            cell_result = ocr.recognize(cell)
            txt = cell_result.full_text.replace('\n',' ').strip()
            if txt:
                saved.append(f'r{ri}c{ci} ({x0r*100:.0f}%~{x1r*100:.0f}%, {y0r*100:.0f}%~{y1r*100:.0f}%): {txt[:50]}')

    annotated.save('diagnose_grid.png')
    print('带网格标注: diagnose_grid.png')
    print('有文字的格子:')
    for s in saved:
        print(f'  {s}')

    # 7. 界面检测
    print(f'\n=== 界面检测 ===')
    detector = ScreenDetector(vote_frames=1)
    det = detector.detect(img_bgr)
    print(f'类型: {det.screen_type.value}  置信度: {det.confidence:.2f}  关键词: {det.matched_keywords}')

    print('\n诊断完成。请将 diagnose_full.png 和 diagnose_grid.png 发给开发者分析。')


if __name__ == '__main__':
    main()
