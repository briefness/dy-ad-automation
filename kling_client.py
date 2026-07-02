"""
可灵 AI API 客户端

封装可灵官方 API 调用，支持：
- 图片生成（角色定妆照）
- 视频生成（分镜片段）
- 任务查询

鉴权方式（自动选择）：
1. JWT (HS256)：KLING_ACCESS_KEY + KLING_SECRET_KEY → 官方推荐
2. Bearer Token：KLING_API_KEY 非空时直接使用（旧版兼容）
"""

import base64
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import jwt as pyjwt  # pip install pyjwt
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False

from config import (
    KLING_BASE_URL,
    KLING_API_KEY,
    KLING_IMAGE_ENDPOINT,
    KLING_IMAGE_QUERY_ENDPOINT,
    KLING_VIDEO_ENDPOINT,
    KLING_QUERY_ENDPOINT,
    KLING_IMAGE_MODEL,
    KLING_VIDEO_MODEL,
    DEFAULT_VIDEO_DURATION,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_MODE,
    DEFAULT_IMAGE_FIDELITY,
    DEFAULT_HUMAN_FIDELITY,
    NEGATIVE_PROMPT,
    CHARACTER_NEGATIVE_PROMPT,
    OUTPUT_DIR,
    APIKeyError,
    APICallError,
    VideoGenerationError,
    ImageGenerationError,
)


def _validate_http_url(url: str) -> None:
    """校验下载地址必须是 http/https，避免畸形 API 返回值进入下载链路。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoGenerationError(f"视频下载地址无效：{url}")


def _validate_downloaded_video(video_path: Path) -> None:
    """用 ffprobe 校验下载文件确实是可解码视频。"""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type:format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if probe.returncode != 0 or "video" not in probe.stdout:
        raise VideoGenerationError(f"下载的视频不可解码：{video_path}")

# P2 修复：从 config 读取超时/重试常量，不再散落硬编码
try:
    from config import (
        API_TIMEOUT_CREATE,
        API_TIMEOUT_QUERY,
        API_TIMEOUT_DOWNLOAD,
        API_MAX_RETRIES,
        API_RETRY_BACKOFF,
    )
except ImportError:
    # 向后兼容旧版 config（未添加这些常量）
    API_TIMEOUT_CREATE   = 30
    API_TIMEOUT_QUERY    = 30
    API_TIMEOUT_DOWNLOAD = 120
    API_MAX_RETRIES      = 3
    API_RETRY_BACKOFF    = 2.0

try:
    from config import KLING_ACCESS_KEY, KLING_SECRET_KEY
except ImportError:
    KLING_ACCESS_KEY = ""
    KLING_SECRET_KEY = ""


def _build_jwt_token(access_key: str, secret_key: str) -> str:
    """
    使用 HS256 生成可灵官方要求的 JWT token

    Header: {"alg": "HS256", "typ": "JWT"}
    Payload: {"iss": access_key, "exp": now+1800, "nbf": now-5}

    Args:
        access_key: 可灵 AccessKey (ak-xxx)
        secret_key: 可灵 SecretKey (sk-xxx)

    Returns:
        JWT token 字符串
    """
    if not _JWT_AVAILABLE:
        raise ImportError(
            "JWT 鉴权需要 pyjwt 库：pip install pyjwt\n"
            "或改用旧版 Bearer 鉴权：在 .env 中设置 KLING_API_KEY"
        )
    now = int(time.time())
    payload = {
        "iss": access_key,
        "exp": now + 1800,   # 30 分钟有效期
        "nbf": now - 5,      # 允许 5 秒时钟偏差
    }
    return pyjwt.encode(payload, secret_key, algorithm="HS256")


# JWT token 缓存（避免每次请求都重新生成）
# P0 修复：加锁防止并发线程同时刷新 token 产生竞态
_jwt_cache: dict = {"token": "", "expires_at": 0}
_jwt_lock = threading.Lock()


def _get_auth_header(
    api_key: str = "",
    access_key: str = "",
    secret_key: str = "",
) -> str:
    """
    获取 Authorization header 值，自动选择鉴权方式。

    优先级：
    1. api_key 非空 → Bearer {api_key}（旧版兼容）
    2. access_key + secret_key 均非空 → JWT HS256（官方推荐）
    3. 两者均空 → 抛出 APIKeyError

    Args:
        api_key: 旧版 API Key
        access_key: AccessKey
        secret_key: SecretKey

    Returns:
        Authorization header 值，如 "Bearer xxx"
    """
    # 旧版 Bearer 模式（api_key 直接是可用的 token）
    effective_key = api_key or KLING_API_KEY
    if effective_key and effective_key not in ("your_kling_api_key_here", "your_api_key_here", ""):
        return f"Bearer {effective_key}"

    # JWT 模式
    ak = access_key or KLING_ACCESS_KEY
    sk = secret_key or KLING_SECRET_KEY
    if ak and sk and ak not in ("your_access_key_here", "") and sk not in ("your_secret_key_here", ""):
        global _jwt_cache
        now = int(time.time())
        # 先无锁快路径：token 有效则直接返回（避免每次都争锁）
        if _jwt_cache["token"] and _jwt_cache["expires_at"] - now > 60:
            return f"Bearer {_jwt_cache['token']}"
        # P0 修复：加锁后再次检查，避免多线程同时刷新
        with _jwt_lock:
            now = int(time.time())
            if _jwt_cache["token"] and _jwt_cache["expires_at"] - now > 60:
                return f"Bearer {_jwt_cache['token']}"
            token = _build_jwt_token(ak, sk)
            _jwt_cache = {"token": token, "expires_at": now + 1800}
        return f"Bearer {_jwt_cache['token']}"

    raise APIKeyError(
        "可灵 API 鉴权未配置。请在 .env 中设置以下任意一种：\n"
        "  方式一（推荐）：KLING_ACCESS_KEY=ak-xxx 和 KLING_SECRET_KEY=sk-xxx\n"
        "  方式二（兼容）：KLING_API_KEY=your_api_key_here"
    )


def _validate_fidelity(value: Optional[float], name: str) -> Optional[float]:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字类型，收到：{type(value)}")
    if value < 0 or value > 1:
        raise ValueError(f"{name} 必须在 [0, 1] 范围内，收到：{value}")
    return float(value)


def _validate_duration(value) -> int:
    """#11 修复：支持字符串输入，校验上限 15 秒"""
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            raise ValueError(f"duration 无法转换为整数：{value!r}")
    if not isinstance(value, (int, float)):
        raise ValueError(f"duration 必须是数字，收到：{type(value)}")
    value = int(value)
    if value <= 0:
        raise ValueError(f"duration 必须大于 0，收到：{value}")
    if value > 15:
        raise ValueError(f"duration 超过上限 15 秒，收到：{value}")
    return value


def _validate_seed(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"seed 必须是整数，收到：{type(value)}")
    return value


def _clean_base64(image_data: str) -> str:
    """
    清理 Base64 图片数据的前缀（如 data:image/png;base64,）

    可灵 API 要求 Base64 不带前缀，直接传编码字符串。
    """
    if not image_data:
        return image_data
    if image_data.startswith("data:"):
        # 去掉 data:image/xxx;base64, 前缀
        comma_idx = image_data.find(",")
        if comma_idx > 0:
            return image_data[comma_idx + 1:]
    return image_data


class KlingClient:
    """可灵 AI API 客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        初始化客户端

        鉴权优先级：
        1. api_key 非空 → Bearer 直传（旧版兼容）
        2. access_key + secret_key → JWT HS256（官方推荐）
        3. 自动从环境变量读取上述两种配置

        Args:
            api_key: 旧版 API Key（直接 Bearer 传递）
            access_key: AccessKey (ak-xxx)
            secret_key: SecretKey (sk-xxx)
            base_url: API 基础地址，默认从 config 读取
        """
        self._api_key = api_key or ""
        self._access_key = access_key or ""
        self._secret_key = secret_key or ""
        self.base_url = (base_url or KLING_BASE_URL).rstrip("/")

        # 验证至少有一种鉴权方式可用（提前失败，避免到 API 调用时才发现）
        try:
            _get_auth_header(self._api_key, self._access_key, self._secret_key)
        except APIKeyError:
            raise

        # P2 修复：用 threading.local() 为每个线程维护独立的 Session
        # requests.Session 内部的连接池在多线程并发时存在竞争，同一个 KlingClient
        # 在 ThreadPoolExecutor 中被多线程共享会触发连接泳问题
        self._local = threading.local()  # 线程局部存储，每线程独立一个 session

    @property
    def session(self) -> requests.Session:
        """P2 修复：返回当前线程独属的 Session，不存在则新建。
        每次新建独立的 Retry 实例，避免多线程共享可变计数器导致重试状态错乱。"""
        if not hasattr(self._local, "session"):
            adapter = HTTPAdapter(
                max_retries=Retry(
                    total=3,
                    backoff_factor=1.0,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["HEAD", "GET", "OPTIONS", "TRACE", "POST"],
                    raise_on_status=False,
                )
            )
            sess = requests.Session()
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            self._local.session = sess
        return self._local.session

    @property
    def headers(self) -> dict:
        """每次请求时动态生成鉴权头（JWT token 有过期时间，需动态刷新）"""
        return {
            "Authorization": _get_auth_header(self._api_key, self._access_key, self._secret_key),
            "Content-Type": "application/json",
        }

    # ============================================================
    # 图片生成（角色定妆照）
    # ============================================================

    def generate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        reference_image: Optional[str] = None,
        image_reference: Optional[str] = None,
        image_fidelity: float = DEFAULT_IMAGE_FIDELITY,
        resolution: str = "2k",
        n: int = 1,
        aspect_ratio: str = "9:16",
        model: Optional[str] = None,
        wait: bool = True,
        timeout: int = 120,
        poll_interval: int = 5,
    ) -> Dict[str, Any]:
        """
        调用可灵图片生成 API

        Args:
            prompt: 正向提示词
            negative_prompt: 负向提示词
            reference_image: 参考图（URL 或 Base64，不带 data:image/... 前缀）
            image_reference: 参考类型（subject/face）
            image_fidelity: 参考强度 [0,1]
            resolution: 分辨率（1k/2k）
            n: 生成数量
            aspect_ratio: 画面比例
            model: 模型名称
            wait: 是否等待结果（同步）
            timeout: 等待超时时间（秒）
            poll_interval: 轮询间隔（秒）

        Returns:
            生成结果字典，data 中包含 task_result.images
        """
        model = model or KLING_IMAGE_MODEL
        image_fidelity = _validate_fidelity(image_fidelity, "image_fidelity")

        payload = {
            "model_name": model,
            "prompt": prompt,
            "n": n,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        }

        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        if reference_image:
            payload["image"] = _clean_base64(reference_image)
            if image_reference:
                payload["image_reference"] = image_reference
            if image_fidelity is not None:
                payload["image_fidelity"] = image_fidelity

        url = f"{self.base_url}{KLING_IMAGE_ENDPOINT}"
        response = self.session.post(url, headers=self.headers, json=payload, timeout=API_TIMEOUT_CREATE)
        response.raise_for_status()
        result = response.json()

        # P0 修复：检查业务错误码（与 create_video_task 保持一致）
        code = result.get("code", 0)
        if code != 0:
            msg = result.get("message") or result.get("msg") or str(result)
            raise ImageGenerationError(f"图片任务创建失败（code={code}）：{msg}")

        if "data" not in result or not result["data"]:
            raise ImageGenerationError(f"图片生成任务创建失败：{result}")

        task_data = result["data"]
        task_id = task_data.get("task_id") or task_data.get("id")

        if not wait:
            return result

        if not task_id:
            raise APICallError(f"无法获取图片任务 ID：{task_data}")

        waited = 0
        last_status = ""
        while waited < timeout:
            status = self.query_image_task(task_id)
            task_status = status.get("data", {}).get("task_status", "")

            if task_status != last_status:
                last_status = task_status
                status_label = {
                    "submitted": "⏳ 已提交",
                    "processing": "🎨 生成中",
                    "succeed": "✅ 完成",
                    "failed": "❌ 失败",
                }.get(task_status, f"🔄 {task_status}")
                print(f"    图片{status_label}（已等待 {waited}s）", end="\r")

            if task_status == "succeed":
                print()
                return status
            elif task_status == "failed":
                print()
                err_msg = status.get("data", {}).get("task_status_msg", "未知错误")
                raise ImageGenerationError(f"图片生成失败：{err_msg}")

            time.sleep(poll_interval)
            waited += poll_interval

        print()
        raise TimeoutError(f"图片生成超时（{timeout}s）：task_id={task_id}")

    def query_image_task(self, task_id: str) -> Dict[str, Any]:
        """
        查询图片生成任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态和结果
        """
        # P0-2 修复：使用独立的图片查询端点，而非用 KLING_IMAGE_ENDPOINT + task_id 拼接
        url = f"{self.base_url}{KLING_IMAGE_QUERY_ENDPOINT.format(task_id)}"
        response = self.session.get(url, headers=self.headers, timeout=API_TIMEOUT_QUERY)
        response.raise_for_status()
        return response.json()

    def generate_character_ref(
        self,
        prompt: str,
        save_path: Optional[Path] = None,
        n: int = 1,
    ) -> Path:
        """
        生成角色定妆照并保存

        Args:
            prompt: 角色描述 Prompt
            save_path: 保存路径，默认保存到 output/character_ref/character_ref.png
            n: 生成数量，取第 1 张

        Returns:
            保存的文件路径
        """
        if save_path is None:
            save_path = OUTPUT_DIR / "character_ref" / "character_ref.png"

        result = self.generate_image(
            prompt=prompt,
            negative_prompt=CHARACTER_NEGATIVE_PROMPT,
            aspect_ratio="2:3",
            resolution="2k",
            n=n,
        )

        images = result.get("data", {}).get("task_result", {}).get("images", [])
        if not images:
            raise ImageGenerationError(f"图片生成结果为空：{result}")

        image_url = images[0].get("url")
        if not image_url:
            raise ImageGenerationError(f"未获取到图片 URL：{images[0]}")

        img_response = self.session.get(image_url, timeout=30)
        img_response.raise_for_status()
        image_bytes = img_response.content

        # P1 修复：验证下载内容确实是合法图片，防止 CDN 返回错误页
        try:
            from PIL import Image
            import io

            Image.open(io.BytesIO(image_bytes)).verify()
        except Exception as verify_err:
            raise ImageGenerationError(f"角色定妆照下载内容不是有效图片：{verify_err}")

        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(image_bytes)

        return save_path

    # ============================================================
    # 视频生成（分镜片段）
    # ============================================================

    def create_video_task(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        duration: int = DEFAULT_VIDEO_DURATION,
        mode: str = DEFAULT_MODE,
        model: Optional[str] = None,
        reference_image: Optional[str] = None,
        reference_images: Optional[List[str]] = None,
        reference_video: Optional[str] = None,
        refer_type: Optional[str] = None,
        multi_shot: bool = False,
        shot_type: str = "intelligence",
        external_task_id: Optional[str] = None,
        callback_url: Optional[str] = None,
        image_fidelity: Optional[float] = None,
        human_fidelity: Optional[float] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建视频生成任务（异步）

        Args:
            prompt: 视频描述 Prompt
            aspect_ratio: 画面比例
            duration: 视频时长（秒）
            mode: 生成模式（std/pro/4k）
            model: 模型名称
            reference_image: 参考图 URL/Base64（单张，兼容旧调用）
            reference_images: 参考图列表（优先于 reference_image）
            reference_video: 参考视频 URL
            refer_type: 参考视频类型（feature/base）
            multi_shot: 是否多镜头
            shot_type: 分镜方式（customize/intelligence）
            external_task_id: 自定义任务 ID
            callback_url: 回调通知地址
            image_fidelity: 参考图 fidelity [0,1]
            human_fidelity: 人物 fidelity [0,1]
            seed: 随机种子，相同种子+相同 prompt 可复现结果
            negative_prompt: 负向提示词（避免出现的元素）

        Returns:
            任务信息字典，包含 task_id 等
        """
        model = model or KLING_VIDEO_MODEL
        duration = _validate_duration(duration)
        # #4 修复：image_fidelity/human_fidelity/seed 对 Omni 接口无效，保留参数签名但不传入
        # _validate_fidelity / _validate_seed 仍可在调用侧用于校验，不影响 payload

        payload = {
            "model_name": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "duration": str(duration),  # Omni 接口 duration 传字符串
            "mode": mode,
            "sound": "off",  # #13 修复：关闭 Omni 内嵌音频，避免干扰后期混音
        }

        # 参考图（支持多张）
        images = reference_images or ([reference_image] if reference_image else [])
        if images:
            valid_images = [_clean_base64(img) for img in images if img]
            payload["image_list"] = [
                {"image_url": img} for img in valid_images
            ]
            # Bug2 修复：one_click_create 已在 prompt 中精准注入 <<<image_N>>> tag
            # 若 prompt 里已含 <<<image_1>>> 则跳过末尾二次追加，避免同一 tag 出现两次
            # 导致模型无法区分产品图 vs 角色图，严重影响人物/产品一致性
            _already_injected = "<<<image_1>>>" in payload["prompt"]
            if not _already_injected:
                refs = "".join(f" <<<image_{i+1}>>>" for i in range(len(valid_images)))
                payload["prompt"] = payload["prompt"].rstrip() + refs

        # 参考视频（Omni 体系：放在 video_list 数组内）
        if reference_video:
            video_item = {"video_url": reference_video}
            if refer_type:
                video_item["refer_type"] = refer_type
            payload["video_list"] = [video_item]

        # 多镜头
        if multi_shot:
            payload["multi_shot"] = True
            payload["shot_type"] = shot_type

        # 可选参数（不含 image_fidelity/human_fidelity/seed，Omni 不支持）
        if external_task_id:
            payload["external_task_id"] = external_task_id
        if callback_url:
            payload["callback_url"] = callback_url
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        url = f"{self.base_url}{KLING_VIDEO_ENDPOINT}"

        # P1 修复：应用层重试，区分 429（配额限制）vs 5xx（服务器瞬断）
        # POST 任务创建不走 Retry 策略（避免重复计费），改为应用层幂等重试
        last_exc: Exception = RuntimeError("未知错误")
        for attempt in range(1, API_MAX_RETRIES + 1):
            try:
                # P1 修复：每次重试生成独立请求 ID，服务端可据此幂等去重
                headers = {**self.headers, "X-Api-Request-Id": str(uuid.uuid4())}
                response = self.session.post(
                    url, headers=headers, json=payload,
                    timeout=API_TIMEOUT_CREATE,
                )
                response.raise_for_status()
                result = response.json()
                break  # 成功，退出重试循环
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else 0
                if status_code == 429:
                    # 配额限制：等更长时间再重试
                    wait = API_RETRY_BACKOFF * (2 ** attempt)
                    print(f"  ⚠️  429 配额限制，{wait:.0f}s 后重试（{attempt}/{API_MAX_RETRIES}）")
                    time.sleep(wait)
                    last_exc = e
                elif 500 <= status_code < 600:
                    # 服务器错误：短暂等待后重试
                    wait = API_RETRY_BACKOFF * attempt
                    print(f"  ⚠️  HTTP {status_code}，{wait:.0f}s 后重试（{attempt}/{API_MAX_RETRIES}）")
                    time.sleep(wait)
                    last_exc = e
                else:
                    raise  # 4xx 客户端错误不重试
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                # 网络瞬断：短暂等待后重试
                wait = API_RETRY_BACKOFF * attempt
                print(f"  ⚠️  网络瞬断（{e.__class__.__name__}），{wait:.0f}s 后重试（{attempt}/{API_MAX_RETRIES}）")
                time.sleep(wait)
                last_exc = e
            except (json.JSONDecodeError, requests.exceptions.ChunkedEncodingError) as e:
                # P1 修复：响应解析失败也应重试（服务器已收到请求但响应 body 损坏）
                wait = API_RETRY_BACKOFF * attempt
                print(f"  ⚠️  响应解析失败（{e.__class__.__name__}），{wait:.0f}s 后重试（{attempt}/{API_MAX_RETRIES}）")
                time.sleep(wait)
                last_exc = e
        else:
            raise VideoGenerationError(f"视频任务创建失败（重试 {API_MAX_RETRIES} 次后放弃）：{last_exc}")

        # 检查 code 字段业务错误（code != 0 表示 API 层面报错）
        code = result.get("code", 0)
        if code != 0:
            msg = result.get("message") or result.get("msg") or str(result)
            raise VideoGenerationError(f"视频任务创建失败（code={code}）：{msg}")

        if "data" not in result:
            raise VideoGenerationError(f"视频任务创建失败（无 data 字段）：{result}")

        return result["data"]

    def query_video_task(self, task_id: str) -> Dict[str, Any]:
        """
        查询视频生成任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态和结果
        """
        url = f"{self.base_url}{KLING_QUERY_ENDPOINT.format(task_id)}"
        response = self.session.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def generate_video(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        duration: int = DEFAULT_VIDEO_DURATION,
        mode: str = DEFAULT_MODE,
        reference_image: Optional[str] = None,
        reference_images: Optional[List[str]] = None,
        poll_interval: int = 10,
        max_wait: int = 600,
        image_fidelity: Optional[float] = None,
        human_fidelity: Optional[float] = None,
        seed: Optional[int] = None,
        negative_prompt: Optional[str] = None,
        # P2-C 高级参数
        model: Optional[str] = None,
        multi_shot: bool = False,
        shot_type: str = "intelligence",
    ) -> Dict[str, Any]:
        """
        生成视频并等待完成（同步）

        Args:
            prompt: 视频描述 Prompt
            aspect_ratio: 画面比例
            duration: 视频时长（秒）
            mode: 生成模式
            reference_image: 参考图 URL/Base64（单张，兼容旧调用）
            reference_images: 参考图列表（优先于 reference_image）
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
            image_fidelity: 参考图 fidelity [0,1]
            human_fidelity: 人物 fidelity [0,1]
            seed: 随机种子

        Returns:
            视频结果字典，包含视频 URL 等
        """
        # 创建任务
        task = self.create_video_task(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            duration=duration,
            mode=mode,
            reference_image=reference_image,
            reference_images=reference_images,
            image_fidelity=image_fidelity,
            human_fidelity=human_fidelity,
            seed=seed,
            negative_prompt=negative_prompt,
            # P2-C 高级参数透传
            model=model,
            multi_shot=multi_shot,
            shot_type=shot_type,
        )

        task_id = task.get("task_id") or task.get("id")
        if not task_id:
            raise APICallError(f"无法获取任务 ID：{task}")

        # 轮询等待完成（带进度提示）
        waited = 0
        last_status = ""
        status_dots = 0
        _poll_fail_count = 0  # P1 修复：容忍轮询瞬断
        _POLL_MAX_FAILS = 3   # 连续失败 3 次才真正放弃
        while waited < max_wait:
            try:
                status = self.query_video_task(task_id)
                _poll_fail_count = 0  # 成功则重置失败计数
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                _poll_fail_count += 1
                if _poll_fail_count >= _POLL_MAX_FAILS:
                    raise VideoGenerationError(
                        f"轮询网络连续失败 {_POLL_MAX_FAILS} 次（task_id={task_id}）：{e}"
                    )
                print(f"  ⚠️  轮询网络瞬断（{_poll_fail_count}/{_POLL_MAX_FAILS}），{poll_interval}s 后重试")
                time.sleep(poll_interval)
                waited += poll_interval
                continue
            task_status = status.get("data", {}).get("task_status", "")

            # 状态变化时打印
            if task_status != last_status:
                last_status = task_status
                status_label = {
                    "submitted": "⏳ 已提交",    # #12 修复：补全 submitted
                    "processing": "🎨 处理中",   # #12 修复：补全 processing
                    "queueing": "⏳ 排队中",
                    "pending": "⏳ 准备中",
                    "running": "🎬 生成中",
                    "succeed": "✅ 完成",
                    "completed": "✅ 完成",
                    "done": "✅ 完成",
                    "failed": "❌ 失败",
                    "error": "❌ 失败",
                }.get(task_status, f"🔄 {task_status}")
                print(f"    {status_label}（已等待 {waited}s）", end="\r")

            if task_status in ("succeed", "completed", "done"):
                print()  # 换行
                # 从 task_result.videos[0] 中提取视频 URL，放到顶层方便调用方使用
                data = status["data"]
                task_result = data.get("task_result", {})
                videos = task_result.get("videos", [])
                if videos:
                    data["video_url"] = videos[0].get("url") or videos[0].get("video_url")
                    data["url"] = data["video_url"]  # 兼容字段
                return data
            elif task_status in ("failed", "error"):
                print()
                raise VideoGenerationError(f"视频生成失败：{status}")

            time.sleep(poll_interval)
            waited += poll_interval

            # 每 5 秒更新一次等待时间
            if waited % 5 == 0:
                status_label = {
                    "submitted": "⏳ 已提交",
                    "processing": "🎨 处理中",
                    "queueing": "⏳ 排队中",
                    "pending": "⏳ 准备中",
                    "running": "🎬 生成中",
                }.get(task_status, "🔄 处理中")
                print(f"    {status_label}（已等待 {waited}s）", end="\r")

        print()
        raise TimeoutError(f"视频生成超时（{max_wait}s）：task_id={task_id}")

    def download_video(self, video_url: str, save_path: Path) -> Path:
        """
        下载视频到本地

        Args:
            video_url: 视频 URL
            save_path: 保存路径

        Returns:
            保存的文件路径
        """
        _validate_http_url(video_url)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # P1 修复：下载加重试，网络中断时清理残缺文件后重试
        last_exc: Exception = RuntimeError("未知错误")
        for attempt in range(1, API_MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    video_url, stream=True, timeout=API_TIMEOUT_DOWNLOAD
                )
                response.raise_for_status()
                expected_size = int(response.headers.get("Content-Length", "0") or 0)
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                # 验证文件非空且完整
                actual_size = save_path.stat().st_size
                if actual_size == 0:
                    raise VideoGenerationError("下载视频为空文件")
                if expected_size > 0 and actual_size != expected_size:
                    raise VideoGenerationError(f"下载视频不完整（{actual_size}/{expected_size} bytes）")
                _validate_downloaded_video(save_path)
                return save_path
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.Timeout,
                    VideoGenerationError) as e:
                # 清理残缺文件
                if save_path.exists():
                    save_path.unlink()
                wait = API_RETRY_BACKOFF * attempt
                print(f"  ⚠️  视频下载失败（{e.__class__.__name__}），{wait:.0f}s 后重试（{attempt}/{API_MAX_RETRIES}）")
                time.sleep(wait)
                last_exc = e

        raise VideoGenerationError(
            f"视频下载失败（重试 {API_MAX_RETRIES} 次后放弃）：{last_exc}"
        )

    # ============================================================
    # 便捷方法
    # ============================================================

    def generate_character_ref_and_videos(
        self,
        character_prompt: str,
        clip_prompts: List[str],
        output_dir: Optional[Path] = None,
        duration: int = DEFAULT_VIDEO_DURATION,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        mode: str = DEFAULT_MODE,
    ) -> Dict[str, Path]:
        """
        一键生成角色定妆照 + 所有分镜片段

        Args:
            character_prompt: 角色定妆照 Prompt
            clip_prompts: 分镜片段 Prompt 列表
            output_dir: 输出目录
            duration: 单片段时长
            aspect_ratio: 画面比例
            mode: 生成模式

        Returns:
            字典：{"character_ref": Path, "clips": [Path, ...]}
        """
        if output_dir is None:
            output_dir = OUTPUT_DIR

        output_dir = Path(output_dir)
        char_dir = output_dir / "character_ref"
        clips_dir = output_dir / "clips"
        char_dir.mkdir(parents=True, exist_ok=True)
        clips_dir.mkdir(parents=True, exist_ok=True)

        results = {"character_ref": None, "clips": []}

        # 1. 生成角色定妆照
        print(f"[1/6] 生成角色定妆照...")
        char_path = char_dir / "character_ref.png"
        self.generate_character_ref(prompt=character_prompt, save_path=char_path)
        results["character_ref"] = char_path
        print(f"  ✅ 角色定妆照已保存：{char_path}")

        # 读取定妆照为 Base64（用于后续参考）
        char_b64 = base64.b64encode(char_path.read_bytes()).decode("utf-8")
        char_data_url = f"data:image/png;base64,{char_b64}"

        # 2. 生成分镜片段
        for idx, clip_prompt in enumerate(clip_prompts, 1):
            print(f"[{idx+1}/6] 生成片段 {idx}/{len(clip_prompts)}...")
            clip_path = clips_dir / f"clip_{idx:02d}.mp4"

            try:
                video_result = self.generate_video(
                    prompt=clip_prompt,
                    aspect_ratio=aspect_ratio,
                    duration=duration,
                    mode=mode,
                    reference_image=char_data_url,
                )

                task_result = video_result.get("task_result", video_result)
                videos = task_result.get("videos", [])
                video_url = videos[0].get("url") if videos else (video_result.get("video_url") or video_result.get("url"))
                if not video_url:
                    raise VideoGenerationError(f"片段 {idx} 未返回视频 URL：{video_result}")

                self.download_video(video_url, clip_path)
                results["clips"].append(clip_path)
                print(f"  ✅ 片段 {idx} 已保存：{clip_path}")

            except Exception as e:
                print(f"  ❌ 片段 {idx} 生成失败：{e}")
                results["clips"].append(None)

        return results
