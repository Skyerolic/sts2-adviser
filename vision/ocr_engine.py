"""
vision/ocr_engine.py
Windows 内置 OCR 引擎封装（Windows.Media.Ocr）

特性：
  - 零额外模型下载，使用系统语言包
  - 支持中文简体（zh-Hans）和英文（en-US）
  - 自动选择最合适的语言包
  - 异步 API 转同步封装
  - 输入：PIL Image 或 numpy BGR array
  - 输出：OcrResult（含文字、行、单词、置信度估算）

依赖：
  - winrt-runtime（pip install winrt-Windows.Media.Ocr 等）
  - Pillow
  - numpy

Windows OCR 语言包安装（如识别效果差）：
  设置 → 时间和语言 → 语言 → 添加语言 → 选择中文（简体）→ 可选功能 → 手写
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

# OpenCV 可选依赖懒加载（首次调用时检测一次）
_CV2_AVAILABLE: Optional[bool] = None


def _check_cv2() -> bool:
    global _CV2_AVAILABLE
    if _CV2_AVAILABLE is None:
        try:
            import cv2  # noqa: F401
            _CV2_AVAILABLE = True
        except ImportError:
            _CV2_AVAILABLE = False
            log.info("opencv-python 未安装，使用 PIL 预处理回退")
    return _CV2_AVAILABLE


# 优先尝试的语言标签顺序（中文优先，用于界面关键词检测）
_PREFERRED_LANGS = ["zh-Hans-CN", "zh-CN", "zh-Hans", "zh", "en-US", "en"]

# 英文专用语言标签顺序（用于卡名 OCR）
_EN_PREFERRED_LANGS = ["en-US", "en-GB", "en"]

# Windows.Media.Ocr 文档规定的图像尺寸硬上限：任一维 > 2600 px 即抛 E_FAIL
# 4K 全屏截图（3840 × 2160）会触发该限制，必须先缩小
_WINRT_MAX_DIM = 2600
_DOWNSCALE_TARGET = 2400        # 留 200 px 余量


@dataclass
class OcrWord:
    text: str
    confidence: float = 1.0   # Windows OCR 不直接给置信度，此处为估算值
    # 在图像中的归一化边界框 (left, top, right, bottom)，值域 0~1
    # 若未能提取则为 None
    bbox: Optional[tuple[float, float, float, float]] = None


@dataclass
class OcrLine:
    text: str
    words: list[OcrWord] = field(default_factory=list)
    # 行的归一化边界框 (left, top, right, bottom)
    bbox: Optional[tuple[float, float, float, float]] = None


@dataclass
class OcrResult:
    """OCR 识别结果"""
    full_text: str                    # 全文（换行分隔）
    lines: list[OcrLine] = field(default_factory=list)
    language: str = ""                # 实际使用的语言标签
    success: bool = True
    error: str = ""

    @property
    def words(self) -> list[str]:
        """所有识别到的单词文本列表"""
        result = []
        for line in self.lines:
            result.extend(w.text for w in line.words)
        return result


class WindowsOcrEngine:
    """
    Windows 内置 OCR 引擎封装。

    用法：
        engine = WindowsOcrEngine()
        result = engine.recognize(pil_image)
        print(result.full_text)
    """

    def __init__(self, preferred_langs: Optional[list[str]] = None) -> None:
        self._preferred_langs = preferred_langs or _PREFERRED_LANGS
        self._ocr_engine = None       # winrt OcrEngine 实例
        self._language_tag: str = ""
        self._initialized = False
        self._available = False
        import threading
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """
        初始化 OCR 引擎，选择最合适的语言包。
        返回 True 表示成功，False 表示不可用。
        """
        if self._initialized:
            return self._available

        self._initialized = True

        try:
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.globalization import Language

            # 遍历优先语言列表，找第一个系统支持的
            for lang_tag in self._preferred_langs:
                try:
                    lang = Language(lang_tag)
                    if OcrEngine.is_language_supported(lang):
                        self._ocr_engine = OcrEngine.try_create_from_language(lang)
                        if self._ocr_engine is not None:
                            self._language_tag = lang_tag
                            self._available = True
                            log.info(f"Windows OCR 初始化成功，语言: {lang_tag}")
                            return True
                except Exception as e:
                    log.debug(f"语言 {lang_tag} 不可用: {e}")
                    continue

            # fallback：使用系统用户语言
            try:
                engine = OcrEngine.try_create_from_user_profile_languages()
                if engine is not None:
                    self._ocr_engine = engine
                    self._language_tag = "user-profile"
                    self._available = True
                    log.info("Windows OCR 使用系统用户语言初始化")
                    return True
            except Exception as e:
                log.debug(f"用户语言 fallback 失败: {e}")

            log.error("Windows OCR 无可用语言包")
            return False

        except ImportError:
            log.error(
                "winrt 未安装，请运行: "
                "pip install winrt-Windows.Media.Ocr "
                "winrt-Windows.Globalization "
                "winrt-Windows.Graphics.Imaging"
            )
            return False
        except Exception as e:
            log.error(f"Windows OCR 初始化失败: {e}")
            return False

    def recognize(self, image: "Image.Image | np.ndarray") -> OcrResult:
        """
        识别图像中的文字。

        Args:
            image: PIL Image（RGB/RGBA/L）或 numpy BGR array

        Returns:
            OcrResult
        """
        if not self._initialized:
            self.initialize()

        if not self._available or self._ocr_engine is None:
            return OcrResult(
                full_text="",
                success=False,
                error="OCR 引擎不可用",
            )

        # 统一转为 PIL Image
        pil_img = self._to_pil(image)
        if pil_img is None:
            return OcrResult(full_text="", success=False, error="图像转换失败")

        # 预处理：放大小图像（Windows OCR 对小尺寸识别效果差）
        pil_img = self._preprocess(pil_img)

        # 执行 OCR（同步封装异步调用，加锁防止并发 RecognizeAsync 冲突）
        with self._lock:
            try:
                result = self._run_ocr_sync(pil_img)
                return result
            except Exception as e:
                log.warning(f"OCR 识别失败: {e}")
                return OcrResult(full_text="", success=False, error=str(e))

    def is_available(self) -> bool:
        if not self._initialized:
            self.initialize()
        return self._available

    @property
    def language(self) -> str:
        return self._language_tag

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _run_ocr_sync(self, pil_img: "Image.Image") -> OcrResult:
        """将异步 OCR 转为同步调用（每次新建 loop，但只压制一次 IocpProactor 日志）"""
        # 只在首次时压制 asyncio debug 日志（IocpProactor 消息）
        if not getattr(self, "_asyncio_log_suppressed", False):
            logging.getLogger("asyncio").setLevel(logging.WARNING)
            self._asyncio_log_suppressed = True

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(self._run_ocr_async(pil_img))
            return result
        except Exception as e:
            raise RuntimeError(f"OCR 异步执行失败: {e}") from e
        finally:
            loop.close()

    async def _run_ocr_async(self, pil_img: "Image.Image") -> OcrResult:
        """异步执行 Windows OCR"""
        try:
            import winrt.windows.graphics.imaging as wgi
            from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter

            # PIL → BGRA bytes（Windows OCR 接受 Bgra8 格式）
            img_bgra = pil_img.convert("RGBA")
            # RGBA → BGRA（交换 R 和 B）
            r, g, b, a = img_bgra.split()
            img_bgra_swapped = Image.merge("RGBA", (b, g, r, a))
            raw_bytes = bytes(img_bgra_swapped.tobytes())

            width, height = pil_img.size

            # 构建 SoftwareBitmap
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(raw_bytes)
            await writer.store_async()
            writer.detach_stream()
            stream.seek(0)

            bitmap = wgi.SoftwareBitmap(
                wgi.BitmapPixelFormat.BGRA8,
                width,
                height,
                wgi.BitmapAlphaMode.PREMULTIPLIED,
            )

            # 直接从 bytes 创建 SoftwareBitmap（更简洁的方式）
            bitmap = wgi.SoftwareBitmap.create_copy_from_buffer(
                stream.get_input_stream_at(0),
                wgi.BitmapPixelFormat.BGRA8,
                width,
                height,
                wgi.BitmapAlphaMode.PREMULTIPLIED,
            )

        except Exception:
            # fallback：使用 BitmapDecoder 方式
            return await self._run_ocr_via_encoder(pil_img)

        return await self._ocr_from_bitmap(bitmap)

    async def _run_ocr_via_encoder(self, pil_img: "Image.Image") -> OcrResult:
        """通过 PNG 编码方式传递图像给 Windows OCR（更兼容）"""
        import io
        import winrt.windows.graphics.imaging as wgi
        from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter

        # PIL → PNG bytes
        buf = io.BytesIO()
        pil_img.convert("RGB").save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # 写入 WinRT stream
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)

        # 用 BitmapDecoder 解码
        decoder = await wgi.BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        return await self._ocr_from_bitmap(bitmap)

    async def _ocr_from_bitmap(self, bitmap) -> OcrResult:
        """从 SoftwareBitmap 执行 OCR，提取文字内容及归一化坐标"""
        ocr_result = await self._ocr_engine.recognize_async(bitmap)

        img_w = bitmap.pixel_width
        img_h = bitmap.pixel_height

        lines: list[OcrLine] = []
        line_texts: list[str] = []

        for line in ocr_result.lines:
            words: list[OcrWord] = []
            for w in line.words:
                word_bbox = None
                try:
                    r = w.bounding_rect
                    if img_w > 0 and img_h > 0:
                        word_bbox = (
                            r.x / img_w,
                            r.y / img_h,
                            (r.x + r.width) / img_w,
                            (r.y + r.height) / img_h,
                        )
                except Exception:
                    pass
                words.append(OcrWord(text=w.text, bbox=word_bbox))

            line_text = " ".join(w.text for w in words)

            # 行 bbox = 所有词的 bbox 并集
            line_bbox = None
            valid_bboxes = [w.bbox for w in words if w.bbox is not None]
            if valid_bboxes:
                line_bbox = (
                    min(b[0] for b in valid_bboxes),
                    min(b[1] for b in valid_bboxes),
                    max(b[2] for b in valid_bboxes),
                    max(b[3] for b in valid_bboxes),
                )

            lines.append(OcrLine(text=line_text, words=words, bbox=line_bbox))
            line_texts.append(line_text)

        full_text = "\n".join(line_texts)
        return OcrResult(
            full_text=full_text,
            lines=lines,
            language=self._language_tag,
            success=True,
        )

    @staticmethod
    def _to_pil(image: "Image.Image | np.ndarray") -> Optional["Image.Image"]:
        """将输入统一转换为 PIL RGB Image"""
        try:
            if isinstance(image, np.ndarray):
                # numpy BGR → PIL RGB
                if image.ndim == 3 and image.shape[2] == 3:
                    rgb = image[:, :, ::-1]  # BGR → RGB
                    return Image.fromarray(rgb.astype(np.uint8))
                elif image.ndim == 3 and image.shape[2] == 4:
                    rgb = image[:, :, 2::-1]  # BGRA → RGB
                    return Image.fromarray(rgb.astype(np.uint8))
                elif image.ndim == 2:
                    return Image.fromarray(image.astype(np.uint8)).convert("RGB")
                else:
                    log.warning(f"不支持的 numpy shape: {image.shape}")
                    return None
            elif isinstance(image, Image.Image):
                return image.convert("RGB")
            else:
                log.warning(f"不支持的图像类型: {type(image)}")
                return None
        except Exception as e:
            log.warning(f"图像转换失败: {e}")
            return None

    @staticmethod
    def _preprocess(img: "Image.Image") -> "Image.Image":
        """
        OCR 前预处理：
        - 超大图（任一维 > 2600px，如 4K 全屏）：用 INTER_AREA 缩小到长边 2400px，
          避开 Windows.Media.Ocr 的尺寸硬上限（否则抛 E_FAIL）
        - 卡名截图（h < 300px）：放大 + CLAHE/锐化（cv2 路径）或 LANCZOS+对比度增强（PIL 回退）
        - 其他尺寸：直接返回，避免对全图截图做插值导致文字畸变
        """
        from PIL import ImageEnhance
        w, h = img.size
        max_dim = max(w, h)

        # 1. 超大图缩小（修复 4K 下的 E_FAIL）
        if max_dim > _WINRT_MAX_DIM:
            scale = _DOWNSCALE_TARGET / max_dim
            new_w = max(int(w * scale), 1)
            new_h = max(int(h * scale), 1)
            if _check_cv2():
                import cv2
                bgr = np.array(img.convert("RGB"))[:, :, ::-1].copy()
                bgr = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
                img = Image.fromarray(bgr[:, :, ::-1])
            else:
                img = img.resize((new_w, new_h), Image.LANCZOS)
            log.debug(f"OCR 缩小: {w}x{h} → {new_w}x{new_h} (避开 WinRT 2600px 上限)")
            return img

        # 2. 卡名截图（小高度）：现有路径
        if h < 300:
            target_h = 300
            if _check_cv2():
                return WindowsOcrEngine._preprocess_cv(img, target_h)
            scale = target_h / max(h, 1)
            new_w = int(w * scale)
            img = img.resize((new_w, target_h), Image.LANCZOS)
            img = ImageEnhance.Contrast(img).enhance(1.8)
            img = ImageEnhance.Sharpness(img).enhance(1.5)
            log.debug(f"OCR 卡名放大: {w}x{h} → {new_w}x{target_h} (PIL 回退)")
            return img

        return img

    @staticmethod
    def _preprocess_cv(img: "Image.Image", target_h: int = 200) -> "Image.Image":
        """OpenCV 卡名截图预处理：放大 → 灰度 → CLAHE → 去噪 → 锐化"""
        import cv2

        # PIL RGB → numpy BGR
        bgr = np.array(img.convert("RGB"))[:, :, ::-1].copy()
        h, w = bgr.shape[:2]

        # 1. LANCZOS4 放大至目标高度（对文字边缘最锐利、伪影最少）
        if h < target_h:
            scale = target_h / h
            new_w = int(w * scale)
            bgr = cv2.resize(bgr, (new_w, target_h), interpolation=cv2.INTER_LANCZOS4)
            log.debug(f"cv2 放大: {w}x{h} → {new_w}x{target_h}")

        # 2. 转灰度
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        # 3. CLAHE 自适应对比度（tileGridSize=(8,4) 适合横向文字横幅）
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 4))
        enhanced = clahe.apply(gray)

        # 4. 高斯去噪（3×3，在锐化前去除噪点）
        denoised = cv2.GaussianBlur(enhanced, (3, 3), 0)

        # 5. 锐化内核（unsharp mask）
        kernel = np.array([[ 0, -1,  0],
                           [-1,  5, -1],
                           [ 0, -1,  0]], dtype=np.float32)
        sharpened = cv2.filter2D(denoised, -1, kernel)

        # 6. 灰度 → RGB 给 Windows OCR
        rgb = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2RGB)
        log.debug(f"cv2 预处理完成: 输出 {rgb.shape[1]}x{rgb.shape[0]}")
        return Image.fromarray(rgb)


# 模块级单例
_ocr_engine: Optional[WindowsOcrEngine] = None
_en_ocr_engine: Optional[WindowsOcrEngine] = None


def get_ocr_engine() -> WindowsOcrEngine:
    """获取全局 OCR 引擎单例（中文优先，用于界面关键词检测）"""
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = WindowsOcrEngine()
        _ocr_engine.initialize()
    return _ocr_engine


def get_en_ocr_engine() -> WindowsOcrEngine:
    """获取英文 OCR 引擎单例（专用于英文卡名识别）"""
    global _en_ocr_engine
    if _en_ocr_engine is None:
        _en_ocr_engine = WindowsOcrEngine(preferred_langs=_EN_PREFERRED_LANGS)
        _en_ocr_engine.initialize()
    return _en_ocr_engine
