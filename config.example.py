"""
可灵 AI 抖音广告视频 - 自动化项目配置（示例）

使用说明：
1. 复制本文件为 config.py
2. 填入你的可灵 API Key（KLING_API_KEY）
3. 根据需要调整其他配置
4. 运行 one_click_create.py 即可一键成片
"""

import os
import logging
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
# 项目路径
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_ASSET_INDEX_PATH = PROJECT_ROOT / "data" / "local_asset_index"

# ============================================================
# 可灵 API 配置
# ============================================================

# 可灵官方 API 地址
KLING_BASE_URL = os.getenv("KLING_BASE_URL", "https://api-beijing.klingai.com")

# 鉴权方式一（推荐）：AccessKey + SecretKey → 自动生成 JWT
# 在可灵开放平台 https://app.klingai.com/cn/dev 获取
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY", "")   # ak-xxxxxxxx
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY", "")   # sk-xxxxxxxx

# 鉴权方式二（兼容）：直接使用 API Key（Bearer token）
# 留空时自动使用 ACCESS_KEY + SECRET_KEY
KLING_API_KEY = os.getenv("KLING_API_KEY", "")

# API 端点（Omni 多模态统一接口）
KLING_IMAGE_ENDPOINT = "/v1/images/generations"
KLING_VIDEO_ENDPOINT = "/v1/videos/omni-video"          # Omni 体系正确端点
KLING_QUERY_ENDPOINT = "/v1/videos/omni-video/{}"       # 查询端点与创建端点保持一致

# 模型版本（2025-06 当前推荐）
KLING_IMAGE_MODEL = "kling-v2-1"    # 图片生成：kling-v2-1（推荐）/ kling-v3
KLING_VIDEO_MODEL = "kling-v3-omni" # 视频生成：kling-v3-omni（Omni旗舰）/ kling-v1-6（稳定版）

# ============================================================
# API 超时与重试配置（P2 修复：消除硬编码散布）
# ============================================================
API_TIMEOUT_CREATE = 30    # 创建任务超时（秒）
API_TIMEOUT_QUERY  = 30    # 查询任务超时（秒）
API_TIMEOUT_DOWNLOAD = 120 # 下载视频超时（秒）
API_MAX_RETRIES = 3        # 应用层最大重试次数（区分 429 vs 5xx）
API_RETRY_BACKOFF = 2.0    # 重试基础等待（秒），指数退避

# ============================================================
# API 定价配置（可灵官方价格，仅供参考）
# ============================================================
# 图片生成：约 0.05 元/张（std 模式）
# 视频生成：按秒计费，不同模式价格不同
# 以下为估算价格，实际以官方为准
KLING_PRICING = {
    "image": {
        "std": 0.05,  # 元/张
        "pro": 0.10,  # 元/张
    },
    "video": {
        "std": 0.30,   # 元/秒
        "pro": 0.60,   # 元/秒
        "4k":  1.20,   # 元/秒
    },
}

# 生成参数默认值
DEFAULT_VIDEO_DURATION = 5  # 单片段时长（秒）
DEFAULT_ASPECT_RATIO = "9:16"  # 竖屏
DEFAULT_MODE = "std"  # std/pro/4k

# 一致性控制默认值
DEFAULT_IMAGE_FIDELITY = 0.9  # 参考图 fidelity [0,1]
DEFAULT_HUMAN_FIDELITY = 0.9  # 人物 fidelity [0,1]
DEFAULT_SEED = None  # 随机种子（None 表示不固定）

# ============================================================
# LLM 文案生成配置
# ============================================================

LLM_ENABLED = True  # 是否启用 LLM 生成文案（False 时走纯模板）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")  # OpenAI 兼容格式，支持 DeepSeek / Moonshot / ChatAnywhere 等
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # 在此填入你的 API Key，或设置环境变量 LLM_API_KEY
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")  # 模型名称
LLM_TEMPERATURE = 0.8  # 采样温度，越高越随机
LLM_MAX_TOKENS = 2000  # 最大生成 token 数
LLM_TIMEOUT = 60  # 请求超时（秒）；完整结构化脚本请求需要更长响应窗口
LLM_MAX_RETRIES = 2  # 网络错误最大重试次数

# ============================================================
# 本地视频素材视觉分析配置
# ============================================================

# 本地素材模式需要开启视觉分析，否则会中止并提示配置。
VISION_ENABLED = os.getenv("VISION_ENABLED", "false").lower() in ("1", "true", "yes", "on")
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "")  # OpenAI-compatible base URL
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "")
VISION_TIMEOUT = int(os.getenv("VISION_TIMEOUT", "60"))
VISION_MAX_RETRIES = int(os.getenv("VISION_MAX_RETRIES", "2"))

LOCAL_ASSET_WINDOW_SECONDS = float(os.getenv("LOCAL_ASSET_WINDOW_SECONDS", "4"))
LOCAL_ASSET_WINDOW_STRIDE = float(os.getenv("LOCAL_ASSET_WINDOW_STRIDE", "2"))
LOCAL_ASSET_CONTACT_SHEET_FRAMES = int(os.getenv("LOCAL_ASSET_CONTACT_SHEET_FRAMES", "12"))
LOCAL_ASSET_MAX_WINDOWS = int(os.getenv("LOCAL_ASSET_MAX_WINDOWS", "120"))

# ============================================================
# 视频拼接配置
# ============================================================

# 输出视频参数
OUTPUT_RESOLUTION = "1080x1920"  # 9:16 竖屏
OUTPUT_FPS = 30
OUTPUT_BITRATE = "6M"  # 抖音竖屏 1080p 推荐 4-8Mbps

# 转场参数
TRANSITION_DURATION = 0.3  # 默认转场时长（秒）

# 转场风格：fast（快节奏）/ cinematic（电影感）/ default（默认均衡）
TRANSITION_STYLE = "default"

# 默认转场序列（按顺序用于各片段之间，循环使用）
# 可用转场：fade / dissolve / fadeblack / fadewhite / slideright / slideleft /
#   slideup / slidedown / circlecrop / circleclose / zoomin / zoomout /
#   wipeleft / wiperight / rectcrop
DEFAULT_TRANSITIONS = [
    {"type": "dissolve", "duration": 0.3},
    {"type": "slideright", "duration": 0.25},
    {"type": "zoomin", "duration": 0.4},
    {"type": "fadeblack", "duration": 0.5},
]

# 字幕参数
SUBTITLE_FONT_SIZE = 70
SUBTITLE_COLOR = "white"
SUBTITLE_STROKE_COLOR = "black"
SUBTITLE_STROKE_WIDTH = 3

# 默认字幕模板（5 段式，按总时长均匀分布）
DEFAULT_SUBTITLE_TEMPLATE = [
    {"text": "你是不是也...？", "segment": 0, "ratio_start": 0.1, "ratio_end": 0.7},
    {"text": "直到我用了...", "segment": 1, "ratio_start": 0.1, "ratio_end": 0.7},
    {"text": "{selling_point}", "segment": 2, "ratio_start": 0.0, "ratio_end": 1.0},
    {"text": "真的绝了", "segment": 3, "ratio_start": 0.2, "ratio_end": 0.8},
    {"text": "点击左下角购买", "segment": 4, "ratio_start": 0.2, "ratio_end": 0.8},
]

# BGM 参数
BGM_VOLUME = 0.6  # BGM 音量比例（纯 BGM 场景推荐 0.5-0.7，有人声时用 0.15-0.25）
BGM_PATH = "assets/bgm.mp3"  # 相对于项目根目录（本地 fallback，优先级低于 API 选曲）
BGM_FADE_IN = 1.0  # BGM 淡入时长（秒）
BGM_FADE_OUT = 2.0  # BGM 淡出时长（秒）
BGM_CACHE_DIR = "output/bgm_cache"  # BGM 本地缓存目录（相对于项目根目录）
SFX_CACHE_DIR = "output/sfx_cache"  # 音效本地缓存目录（相对于项目根目录）

# ============================================================
# 提示词模板配置
# ============================================================

# 负面提示词（固定）
NEGATIVE_PROMPT = (
    "different person, different face, different outfit, "
    "blurry, low quality, distorted face, extra limbs, "
    "text watermark, logo, brand mark, multiple people, crowd, group shot"
)

# 角色定妆照负面提示词（与通用负面提示词一致，统一维护）
CHARACTER_NEGATIVE_PROMPT = NEGATIVE_PROMPT

# 一致性描述模板（用于生成片段 Prompt 时注入）
CONSISTENCY_TEMPLATES = {
    "character": (
        "EXACT same person from reference image, identical facial features, "
        "same hairstyle, same outfit, same {gender} as reference"
    ),
    "product": (
        "{name} with EXACT same {brand_packaging}, identical packaging design, "
        "same color and logo placement, {brand} brand product"
    ),
    "brand": (
        "{brand} brand aesthetic throughout, {primary_color}, "
        "brand elements subtly integrated"
    ),
    "scene": (
        "SAME location and environment as reference image, identical room/scene setup, "
        "same furniture and props in same positions, same lighting direction and color temperature, "
        "same time of day, consistent camera angle and perspective, "
        "continuous scene, seamless transition from previous shot"
    ),
    "lighting": (
        "EXACT same lighting as reference image, same key light position, "
        "same fill light intensity, same color temperature, same shadow direction, "
        "consistent mood and atmosphere"
    ),
}

# 场景连续性策略配置
SCENE_CONTINUITY_CONFIG = {
    # 是否使用全局场景锚点（推荐 True，防止场景漂移）
    "use_scene_anchor": True,
    # 全局场景锚点从第几段提取（默认第 1 段，因为第 1 段场景最完整）
    "anchor_clip_index": 0,
    # 从锚点片段提取几张关键帧作为场景参考
    "anchor_keyframes": 2,
    # 是否同时使用前一段最后一帧（局部衔接）
    "use_previous_last_frame": True,
    # 场景参考图的 fidelity（越低越自由，越高越严格）
    "scene_fidelity": 0.7,
    # 是否在 Prompt 中注入强场景一致性描述
    "inject_scene_prompt": True,
    # 转场类型（用于掩盖跳切）
    "transition_type": "dissolve",
    # 转场时长（秒），越长掩盖效果越好，但越影响节奏
    "transition_duration": 0.6,
}

# ============================================================
# 品牌一致性配置
# ============================================================

BRAND_CONFIG = {
    "name": "我的品牌",
    "logo_path": "assets/logo.png",  # 品牌 Logo 图片路径（PNG 透明底，留空则不显示）
    "logo_description": "minimalist brand logo, clean typography, appears subtly in bottom right corner",
    "primary_color": "#FF6B6B",   # 品牌主色（HEX）
    "secondary_color": "#4ECDC4", # 品牌辅助色（HEX）
    "accent_color": "#4ECDC4",    # 强调色/高亮色（字幕花字用），默认同 secondary_color
    "slogan": "卓越品质，值得拥有",
    "packaging_description": "consistent product packaging, same color and design",
    # Logo 水印配置
    "logo_watermark": {
        "enabled": False,  # 是否启用 Logo 水印（需要配置 logo_path）
        "position": "top_right",  # 位置：top_left / top_right / bottom_left / bottom_right
        "size_ratio": 0.08,  # Logo 宽度占视频宽度的比例
        "margin_ratio": 0.03,  # 边距比例（相对视频宽/高）
        "opacity": 0.9,  # 透明度 0-1
        "fade_in": 0.5,  # 淡入时长（秒）
        "fade_out": 0.5,  # 淡出时长（秒）
    },
}

# ============================================================
# 调色预设
# ============================================================

COLOR_GRADING_PRESETS = {
    "none": {
        "name": "无调色",
        "brightness": 1.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "temperature": 0,  # -1 冷 ~ 1 暖
        "tint": 0,  # -1 绿 ~ 1 品红
        "gamma": 1.0,
        "color_overlay": None,  # (hex_color, opacity)
    },
    "warm_cinematic": {
        "name": "暖色电影感",
        "brightness": 1.05,
        "contrast": 1.15,
        "saturation": 0.9,
        "temperature": 0.25,
        "tint": 0.05,
        "gamma": 1.05,
        "color_overlay": ("#FFA500", 0.05),  # 橙色叠加
    },
    "cool_cinematic": {
        "name": "冷色电影感",
        "brightness": 0.95,
        "contrast": 1.2,
        "saturation": 0.85,
        "temperature": -0.2,
        "tint": -0.05,
        "gamma": 0.95,
        "color_overlay": ("#1E90FF", 0.05),  # 蓝色叠加
    },
    "vintage": {
        "name": "复古胶片",
        "brightness": 1.0,
        "contrast": 1.1,
        "saturation": 0.7,
        "temperature": 0.15,
        "tint": 0.1,
        "gamma": 1.1,
        "color_overlay": ("#D2B48C", 0.08),  # 棕褐色
    },
    "teal_orange": {
        "name": "青橙色调（好莱坞）",
        "brightness": 1.0,
        "contrast": 1.25,
        "saturation": 1.1,
        "temperature": 0.1,
        "tint": 0,
        "gamma": 1.0,
        "color_overlay": None,
        # 青橙色调通过 colorbalance 实现
        "shadows_red": -0.08,
        "shadows_green": 0.05,
        "shadows_blue": 0.12,
        "highlights_red": 0.1,
        "highlights_green": 0.03,
        "highlights_blue": -0.08,
    },
    "moody": {
        "name": "暗调情绪",
        "brightness": 0.85,
        "contrast": 1.3,
        "saturation": 0.75,
        "temperature": -0.1,
        "tint": 0,
        "gamma": 0.9,
        "color_overlay": ("#2F4F4F", 0.06),
    },
    "bright_clean": {
        "name": "明亮清新",
        "brightness": 1.15,
        "contrast": 1.05,
        "saturation": 1.1,
        "temperature": 0.05,
        "tint": -0.02,
        "gamma": 1.05,
        "color_overlay": None,
    },
    "pastel": {
        "name": "马卡龙",
        "brightness": 1.1,
        "contrast": 0.9,
        "saturation": 0.8,
        "temperature": 0.05,
        "tint": 0.03,
        "gamma": 1.1,
        "color_overlay": ("#FFB6C1", 0.04),  # 粉色
    },
    "noir": {
        "name": "黑色电影",
        "brightness": 0.9,
        "contrast": 1.4,
        "saturation": 0,  # 黑白
        "temperature": 0,
        "tint": 0,
        "gamma": 0.95,
        "color_overlay": None,
    },
}

# 默认调色预设
DEFAULT_COLOR_GRADING = "warm_cinematic"

# ============================================================
# 钩子模板库（Hook Templates）
# ============================================================

HOOK_TEMPLATES = {
    "question": {
        "name": "灵魂拷问式",
        "description": "开头提出痛点问题，引发观众共鸣和好奇",
        "hook_subtitle": "你是不是也...？",
        "hook_prompt": (
            "close-up shot, slow push in, {character} looking directly at camera with frustrated expression, "
            "head slightly shaking, relatable pain point, {preset_scene} scene, {preset_lighting}, "
            "realistic lifestyle style, intense eye contact, grab attention in first 3 seconds, "
            "9:16 vertical, close-up on face, "
            "{character_consistency}, {brand_consistency}"
        ),
        "tone": "empathetic",
        "best_for": ["美妆", "个护", "家居", "食品"],
    },
    "shocking": {
        "name": "震惊反差式",
        "description": "用惊人的事实或对比开场，制造认知冲击",
        "hook_subtitle": "99%的人都不知道！",
        "hook_prompt": (
            "extreme close-up, fast push in, {character} eyes wide with shock and disbelief, "
            "hand covering mouth, dramatic reveal moment, {preset_scene} scene, "
            "dramatic lighting with high contrast, "
            "viral TikTok style, mind-blown expression, 9:16 vertical, "
            "{character_consistency}, {brand_consistency}"
        ),
        "tone": "dramatic",
        "best_for": ["数码", "家居", "食品", "美妆"],
    },
    "before_after": {
        "name": "前后对比式",
        "description": "直接展示使用前后的巨大差异，视觉冲击力强",
        "hook_subtitle": "这变化也太大了吧！",
        "hook_prompt": (
            "split screen style, left side shows {character} with problem/tired look, "
            "right side shows {character} glowing and happy after using product, "
            "dramatic before and after comparison, {preset_scene} scene, "
            "clean composition, side by side comparison, 9:16 vertical, "
            "{character_consistency}, {brand_consistency}"
        ),
        "tone": "transformative",
        "best_for": ["美妆", "个护", "健身", "家居清洁"],
    },
    "demonstration": {
        "name": "效果展示式",
        "description": "直接展示产品最惊艳的效果，用画面说话",
        "hook_subtitle": "这效果绝了！",
        "hook_prompt": (
            "extreme close-up, macro shot of {name} in action, "
            "visually satisfying product demonstration, {demo_action}, "
            "slow motion reveal of amazing result, "
            "soft product lighting, commercial photography style, "
            "ASMR visual, satisfying to watch, 9:16 vertical, "
            "{product_consistency}, {brand_consistency}"
        ),
        "tone": "satisfying",
        "best_for": ["美妆", "食品", "数码", "家居"],
    },
    "story": {
        "name": "故事叙述式",
        "description": "用一个小故事开场，引导观众代入情感",
        "hook_subtitle": "那天我终于明白了...",
        "hook_prompt": (
            "medium shot, cinematic framing, {character} looking thoughtfully into distance, "
            "contemplative expression, storytelling atmosphere, {preset_scene} scene, "
            "soft natural lighting, warm tones, emotional and relatable, "
            "vlog style opening, 9:16 vertical, "
            "{character_consistency}, {brand_consistency}"
        ),
        "tone": "emotional",
        "best_for": ["美妆", "个护", "食品", "家居"],
    },
    "challenge": {
        "name": "挑战测试式",
        "description": "发起一个挑战或极限测试，展现产品硬核实力",
        "hook_subtitle": "敢不敢来挑战？！",
        "hook_prompt": (
            "dynamic action shot, {character} holding {name} with determined expression, "
            "ready for a challenge, intense and energetic, {preset_scene} scene, "
            "dramatic lighting with strong highlights, "
            "experiment/test vibe, bold and confident, 9:16 vertical, "
            "{character_consistency}, {product_consistency}, {brand_consistency}"
        ),
        "tone": "energetic",
        "best_for": ["数码", "家居清洁", "个护", "食品"],
    },
    "celeb_style": {
        "name": "明星同款式",
        "description": "营造明星/博主推荐的信任感和种草感",
        "hook_subtitle": "明星都在用的秘密！",
        "hook_prompt": (
            "glamour shot, {character} with flawless look, holding {name} elegantly, "
            "celebrity endorsement vibe, red carpet aesthetic, "
            "soft ring light, beauty influencer style, polished and aspirational, "
            "9:16 vertical, close-up on face and product, "
            "{character_consistency}, {product_consistency}, {brand_consistency}"
        ),
        "tone": "aspirational",
        "best_for": ["美妆", "个护", "时尚", "食品"],
    },
    "pain_point": {
        "name": "痛点直击式",
        "description": "精准戳中用户日常痛点，让观众觉得'说的就是我'",
        "hook_subtitle": "谁懂啊！这也太烦了",
        "hook_prompt": (
            "close-up, {character} struggling with a daily annoyance, "
            "frustrated and annoyed expression, relatable everyday problem, "
            "{preset_scene} scene, natural lighting, "
            "authentic and real, like looking in a mirror, "
            "9:16 vertical, {character_consistency}, {brand_consistency}"
        ),
        "tone": "relatable",
        "best_for": ["家居", "个护", "食品", "数码"],
    },
}

# 默认钩子类型
DEFAULT_HOOK_TYPE = "question"

# ============================================================
# 产品类型预设
# ============================================================

PRODUCT_PRESETS = {
    "美妆": {
        "style": "清新自然",
        "lighting": "soft natural lighting",
        "scene": "clean vanity table with soft light",
        "demo_action": "applying product on hand, showing texture",
        "result": "skin looks radiant and glowing, confident smile",
    },
    "食品": {
        "style": "warm and cozy",
        "lighting": "warm natural sunlight",
        "scene": "wooden kitchen table",
        "demo_action": "taking a bite, showing texture",
        "result": "satisfied expression, happy smile",
    },
    "家居": {
        "style": "warm cozy",
        "lighting": "soft natural window light",
        "scene": "clean modern living room",
        "demo_action": "using the product, showing cleaning effect",
        "result": "space looks tidy and fresh, relaxed expression",
    },
    "数码": {
        "style": "modern tech",
        "lighting": "cool tech lighting",
        "scene": "minimalist modern desk",
        "demo_action": "touching screen, showing features",
        "result": "task completed, satisfied nod",
    },
    "个护": {
        "style": "clean fresh",
        "lighting": "soft natural lighting",
        "scene": "clean bathroom counter",
        "demo_action": "applying product, showing texture",
        "result": "feeling fresh and confident, relaxed smile",
    },
    "服饰": {
        "style": "fashion forward",
        "lighting": "studio soft lighting",
        "scene": "clean white background",
        "demo_action": "showing outfit details, turning around",
        "result": "confident pose, perfect look",
    },
    "app": {
        "style": "clean modern",
        "lighting": "bright natural light",
        "scene": "coffee shop table",
        "demo_action": "swiping phone screen, showing interface",
        "result": "task done, relaxed expression",
    },
    "汽车": {
        "style": "dynamic luxury",
        "lighting": "golden hour sunlight",
        "scene": "coastal highway or city skyline",
        "demo_action": "driving smoothly, showing dashboard and exterior",
        "result": "powerful and free, confident smile",
    },
    "房产": {
        "style": "warm homey",
        "lighting": "soft window light",
        "scene": "modern living room with natural light",
        "demo_action": "walking through space, touching furniture",
        "result": "peaceful and content, feeling at home",
    },
    "教育": {
        "style": "bright inspiring",
        "lighting": "classroom natural light",
        "scene": "modern classroom or library",
        "demo_action": "studying intently, raising hand, interacting",
        "result": "enlightened and motivated, smiling with understanding",
    },
    "医疗": {
        "style": "clean professional",
        "lighting": "bright clinical lighting",
        "scene": "modern clinic or lab",
        "demo_action": "professional demonstration, caring interaction",
        "result": "trustworthy and reassuring, professional smile",
    },
    "default": {
        "style": "modern minimalist",
        "lighting": "natural lighting",
        "scene": "lifestyle setting",
        "demo_action": "using the product naturally",
        "result": "satisfied expression",
    },
}

# ============================================================
# 分镜结构配置
# ============================================================
# 每个片段定义：
#   camera: 运镜类型（push/pull/orbit/static），对应电影风格中的 camera_push/camera_pull/camera_orbit
#   narrative: 叙事角色（hook/turning_point/showcase/result/cta）
#   base_prompt: 基础 prompt 模板，可用 {character} {name} {preset_scene} {preset_lighting} {demo_action} {selling_point} {preset_result} 占位
#   可通过增删改此列表来调整分镜数量、节奏和内容

CLIP_STRUCTURE = [
    {
        "camera": "push",
        "narrative": "hook",
        "base_prompt": (
            "static shot, slow push in, {character} looking at phone with frustrated expression, "
            "confused and stressed, {preset_scene} scene, {preset_lighting}, "
            "realistic lifestyle style, tense mood, grab attention in first 3 seconds, "
            "9:16 vertical, close-up on face, "
            "{character_consistency}, {brand_consistency}"
        ),
    },
    {
        "camera": "push",
        "narrative": "turning_point",
        "base_prompt": (
            "handheld tracking shot, {character} turns and picks up {name}, "
            "eyes light up with surprise, {name} displayed in hand, "
            "warm lighting, lifestyle photography style, emotional turning point, "
            "same person from reference image, 9:16 vertical"
        ),
    },
    {
        "camera": "orbit",
        "narrative": "showcase",
        "base_prompt": (
            "camera orbit around {name}, {name} placed on {preset_scene}, "
            "{character} fingers operating, {demo_action}, "
            "close-up on product details, soft product lighting, commercial photography style, "
            "highlight {selling_point}, same person from reference image, 9:16 vertical"
        ),
    },
    {
        "camera": "pull",
        "narrative": "result",
        "base_prompt": (
            "slow pull back, {character} smiling with satisfaction, {name} placed beside, "
            "{preset_result}, warm golden hour lighting, emotional climax, "
            "close-up on happy expression, same person from reference image, 9:16 vertical"
        ),
    },
    {
        "camera": "pull",
        "narrative": "cta",
        "base_prompt": (
            "static wide shot, {name} beautifully placed on clean white surface, "
            "brand logo appears subtly in corner, clean and bright, "
            "product photography style, brand confirmation, call to action, 9:16 vertical"
        ),
    },
]

# ============================================================
# 电影风格预设
# ============================================================

CINEMATIC_STYLES = {
    "hitchcock": {
        "name": "希区柯克",
        "name_en": "Hitchcock",
        "description": "心理悬疑大师，推轨变焦制造焦虑",
        "camera_push": "Hitchcock dolly zoom: camera pushes in slowly while background visually stretches, Vertigo effect, psychological tension",
        "camera_pull": "Hitchcock pull back: camera pulls back to reveal isolation, subject looks small and vulnerable, voyeuristic tension",
        "camera_orbit": "Hitchcock orbit: camera circles subject at distance, paranoid surveillance, voyeuristic tension, 1960s thriller style",
        "transition_match": "Spiral focus transition: camera spirals into subject's face, Vertigo-style, obsession theme",
        "transition_light": "Lens flare transition: camera moves toward bright light, lens flare washes out, mystery and discovery",
        "lighting": "cold gray tones, high contrast, chiaroscuro, side-backlight creating deep shadows on face",
        "color": "desaturated colors, green tint, high contrast",
        "bgm_keywords": ['suspense', 'thriller', 'mysterious', 'tension', 'dark ambient'],
        "mood": "psychological tension, dread, voyeuristic unease",
    },
    "kubrick": {
        "name": "库布里克",
        "name_en": "Kubrick",
        "description": "对称构图大师，单点透视，仪式感",
        "camera_push": "Kubrick one-point perspective dolly: camera pushes straight down center of symmetrical corridor, everything aligns to vanishing point, 2001 style, cosmic dread",
        "camera_pull": "Kubrick dolly out: camera pulls back from subject to reveal vast symmetrical space, Barry Lyndon style, isolation and scale",
        "camera_orbit": "Kubrick symmetrical orbit: camera circles subject maintaining perfect center composition, 2001-style, cosmic grandeur",
        "transition_match": "Match cut from 2001: A Space Odyssey: cut from object flying up to spaceship, matching shape and movement, Kubrick style, evolution theme",
        "transition_light": "Kubrick time jump: sudden cut from bright daylight to dark night, same location, same composition, The Shining style",
        "lighting": "strong frontal key light, deep shadows on sides, dramatic chiaroscuro, Barry Lyndon candlelight style",
        "color": "high saturation red/blue/green, or warm candlelight yellow",
        "bgm_keywords": ['classical', 'epic', 'dramatic', 'orchestral', 'cinematic'],
        "mood": "cosmic dread, meticulous precision, fatalistic grandeur",
    },
    "spielberg": {
        "name": "斯皮尔伯格",
        "name_en": "Spielberg",
        "description": "娱乐与情感大师，拉轨揭示，平民英雄",
        "camera_push": "Spielberg push in: slow push toward subject, wonder building, Jaws style, anticipation and dread",
        "camera_pull": "Spielberg dolly out reveal: camera slowly pulls back from subject's face to reveal vast isolation, Jaws style, awe and wonder, subject small against environment",
        "camera_orbit": "Spielberg orbit: camera circles subject with rim light, E.T. style, magical halo, wonder and discovery",
        "transition_match": "Spielberg lens flare transition: camera moves toward bright light source, lens flare washes out image, Jaws/Close Encounters style",
        "transition_light": "Wide reveal: camera pulls back from extreme close-up to show full context, Jurassic Park style, awe and spectacle",
        "lighting": "rim light creating halo around subject, side-backlight, magical glow, warm golden hour",
        "color": "warm yellow + teal blue contrast, or soft natural light",
        "bgm_keywords": ['adventure', 'magical', 'orchestral', 'wonder', 'cinematic'],
        "mood": "wonder, awe, emotional catharsis, Spielberg magic",
    },
    "aronofsky": {
        "name": "阿伦诺夫斯基",
        "name_en": "Aronofsky",
        "description": "心理惊悚大师，快速推轨，分裂半透镜",
        "camera_push": "Aronofsky rapid push-in: fast dolly toward subject's face, Requiem for a Dream style, claustrophobic tension, split diopter, foreground and background both sharp, frantic energy",
        "camera_pull": "Aronofsky pull back: fast pull back from extreme close-up, paranoid atmosphere, Pi style, obsession and compulsion",
        "camera_orbit": "Aronofsky tight orbit: camera circles rapidly, split diopter, compressed space, Requiem drug sequence style, paranoid surveillance",
        "transition_match": "Aronofsky jump cut: rapid succession of similar frames, subject's expression intensifies, time compression, disorienting",
        "transition_light": "Eye match cut: cut from one character's eye to object, Aronofsky style, obsession theme, Pi/Requiem style",
        "lighting": "hard light, heavy shadows, high contrast, cold green or warm yellow, split diopter keeping foreground and background sharp",
        "color": "high contrast, desaturated with occasional vivid colors, green or yellow tint",
        "bgm_keywords": ['intense', 'dark electronic', 'experimental', 'psychological', 'tension'],
        "mood": "claustrophobic tension, paranoia, obsession, frantic energy",
    },
    "scorsese": {
        "name": "斯科塞斯",
        "name_en": "Scorsese",
        "description": "跟踪镜头大师， steadicam，街头叙事",
        "camera_push": "Scorsese push in: slow push through crowded scene, Goodfellas style, tracking through space, narrative momentum",
        "camera_pull": "Scorsese pull back: camera pulls back from subject in crowded environment, Taxi Driver style, urban isolation",
        "camera_orbit": "Scorsese tracking orbit: Steadicam tracking shot circling subject through environment, Goodfellas Copacabana style, smooth narrative flow",
        "transition_match": "Scorsese freeze frame: sudden freeze frame on subject's face, then cut to next scene, Goodfellas style, narrative punctuation",
        "transition_light": "Scorsese iris out: iris closes on subject, then opens on next scene, vintage cinema style, Taxi Driver style",
        "lighting": "practical lights from environment, neon signs, street lamps, naturalistic urban lighting, Raging Bull style",
        "color": "warm golden tones or cool blue urban night, vintage film stock look",
        "bgm_keywords": ['rock', 'classic rock', 'blues', 'urban', 'street'],
        "mood": "urban energy, narrative momentum, violent beauty, Catholic guilt",
    },
    "nolan": {
        "name": "诺兰",
        "name_en": "Nolan",
        "description": "实景特效大师， IMAX 比例，时间 Manipulation",
        "camera_push": "Nolan IMAX push in: massive IMAX push in on subject's face, practical effects, Inception style, overwhelming scale",
        "camera_pull": "Nolan pull back: camera pulls back from small subject to reveal massive practical set, Inception dream fold style, scale and disorientation",
        "camera_orbit": "Nolan IMAX orbit: camera circles subject on massive practical set, Interstellar style, cosmic scale, IMAX grandeur",
        "transition_match": "Nolan cross-cut: cut between two simultaneous actions, Inception style, parallel narrative tension",
        "transition_light": "Nolan practical effect transition: cut from dream to reality through practical effect, Inception kick style, temporal distortion",
        "lighting": "practical lights only, naturalistic, IMAX quality, high dynamic range, Dunkirk natural light",
        "color": "desaturated cool tones or warm practical lights, IMAX film stock",
        "bgm_keywords": ['epic', 'dramatic', 'orchestral', 'cinematic', 'powerful'],
        "mood": "grand scale, temporal disorientation, practical awe, intellectual tension",
    },
    "anderson": {
        "name": "韦斯·安德森",
        "name_en": "Wes Anderson",
        "description": "对称构图女王， pastel 色彩，复古布景",
        "camera_push": "Wes Anderson push in: symmetrical push in on centered subject, Grand Budapest Hotel style, meticulous composition, pastel colors",
        "camera_pull": "Wes Anderson pull back: symmetrical pull back to reveal perfect tableau, Royal Tenenbaums style, emotional distance and beauty",
        "camera_orbit": "Wes Anderson orbit: camera circles subject maintaining perfect symmetry, Moonrise Kingdom style, nostalgic whimsy",
        "transition_match": "Wes Anderson wipe: iris wipe or horizontal wipe to next scene, Fantastic Mr. Fox style, playful transition",
        "transition_light": "Wes Anderson chapter title: text card with symmetrical framing, Grand Budapest style, narrative whimsy",
        "lighting": "soft even lighting, no harsh shadows, pastel color palette, vintage aesthetic",
        "color": "pastel pinks, yellows, blues, symmetrical color blocking",
        "bgm_keywords": ['whimsical', 'vintage', 'acoustic', 'indie folk', 'quirky'],
        "mood": "nostalgic whimsy, emotional distance, meticulous beauty, bittersweet",
    },
    "wong-kar-wai": {
        "name": "王家卫",
        "name_en": "Wong Kar-wai",
        "description": "霓虹美学大师，慢快门，都市孤独与浪漫",
        "camera_push": "Wong Kar-wai push in: slow push with step-printing effect, Chungking Express style, neon lights blur, urban loneliness and romance",
        "camera_pull": "Wong Kar-wai pull back: slow pull back from extreme close-up, rain-soaked streets, In the Mood for Love style, unspoken desire",
        "camera_orbit": "Wong Kar-wai orbit: camera circles through narrow hallway, Fallen Angels style, neon reflections, chaotic intimacy",
        "transition_match": "Wong Kar-wai match cut: cut from one character's face to another's matching expression, In the Mood for Love style, parallel longing",
        "transition_light": "Wong Kar-wai light leak: lens flare and light leak transition, 2001: A Space Odyssey style, temporal distortion",
        "lighting": "neon signs, rain reflections, low-key lighting, warm tungsten mixed with cool neon",
        "color": "saturated reds, greens, blues, high contrast, teal and orange",
        "bgm_keywords": ['jazz', 'blues', 'moody', 'atmospheric', 'noir'],
        "mood": "urban loneliness, unspoken desire, nostalgic romance, temporal dislocation",
    },
    "tarkovsky": {
        "name": "塔可夫斯基",
        "name_en": "Tarkovsky",
        "description": "诗意长镜头，自然意象，时间雕塑",
        "camera_push": "Tarkovsky slow push: extremely slow dolly, long take, nature imagery, Mirror style, poetic realism, water and fire motifs",
        "camera_pull": "Tarkovsky pull back: slow pull back from intimate detail to vast landscape, Stalker style, Zone atmosphere, desolate beauty",
        "camera_orbit": "Tarkovsky orbit: camera circles subject with long take, Solaris style, oceanic imagery, psychological depth",
        "transition_match": "Tarkovsky match cut: cut from one natural element to another, water to fire, Mirror style, thematic resonance",
        "transition_light": "Tarkovsky light transition: gradual light change over long take, Nostalghia style, passage of time",
        "lighting": "natural lighting, candlelight, overcast sky, long shadows, poetic realism",
        "color": "muted earth tones, desaturated, occasional vivid natural colors, water reflections",
        "bgm_keywords": ['ambient', 'minimal', 'meditative', 'atmospheric', 'classical'],
        "mood": "poetic realism, spiritual longing, temporal meditation, desolate beauty",
    },
    "zhang-yimou": {
        "name": "张艺谋",
        "name_en": "Zhang Yimou",
        "description": "东方色彩美学，对称构图，民俗仪式",
        "camera_push": "Zhang Yimou push in: symmetrical push in on vibrant color field, Hero style, red and gold dominance, poetic martial arts",
        "camera_pull": "Zhang Yimou pull back: pull back from intimate face to vast crowd, Raise the Red Lantern style, symmetrical courtyard",
        "camera_orbit": "Zhang Yimou orbit: camera circles subject in symmetrical frame, House of Flying Daggers style, peacock umbrella dance",
        "transition_match": "Zhang Yimou color match: cut from one vibrant color to another, Hero style, emotional progression through color",
        "transition_light": "Zhang Yimou lantern light: warm lantern light transition, Raise the Red Lantern style, intimate to vast",
        "lighting": "strong directional light, vibrant color blocking, lantern light, natural sunlight through lattice",
        "color": "vibrant reds, golds, yellows, blues, saturated earth tones, Chinese color symbolism",
        "bgm_keywords": ['chinese traditional', 'epic', 'orchestral', 'dramatic', 'folk'],
        "mood": "poetic martial arts, collective ritual, tragic romance, visual grandeur",
    },
    "koreeda": {
        "name": "是枝裕和",
        "name_en": "Koreeda",
        "description": "日常诗意，家庭纽带，克制温情",
        "camera_push": "Koreeda push in: slow push in on mundane detail, After the Storm style, domestic poetry, quiet observation",
        "camera_pull": "Koreeda pull back: pull back from family moment to reveal context, Shoplifters style, chosen family bonds",
        "camera_orbit": "Koreeda orbit: camera circles family at table, Our Little Sister style, seaside town, gentle rhythm",
        "transition_match": "Koreeda match cut: cut from one family member to another, After Life style, memory and loss",
        "transition_light": "Koreeda window light: soft window light transition, Nobody Knows style, childhood resilience",
        "lighting": "soft natural window light, overcast sky, indoor practical lights, gentle and diffused",
        "color": "muted earth tones, soft pastels, desaturated blues and greens, Japanese aesthetic",
        "bgm_keywords": ['piano', 'gentle', 'acoustic', 'peaceful', 'emotional'],
        "mood": "quiet observation, familial bonds, gentle melancholy, everyday heroism",
    },
    "tarantino": {
        "name": "昆汀",
        "name_en": "Tarantino",
        "description": "类型拼贴，暴力美学，流行文化引用",
        "camera_push": "Tarantino push in: steady push in on tense face, Pulp Fiction style, extreme close-up, pop culture reference",
        "camera_pull": "Tarantino pull back: pull back from trunk reveal, Pulp Fiction style, surprise and dark humor",
        "camera_orbit": "Tarantino orbit: camera circles with steadycam, Reservoir Dogs style, warehouse tension, pop soundtrack",
        "transition_match": "Tarantino match cut: cut from one violent act to another, Kill Bill style, genre pastiche",
        "transition_light": "Tarantino title card: bold text card transition, Pulp Fiction style, chapter break, irreverence",
        "lighting": "practical lights, neon signs, car headlights, high contrast, noir influences",
        "color": "saturated primary colors, black and white interludes, retro film stock",
        "bgm_keywords": ['rock', 'funk', 'soul', 'retro', 'classic'],
        "mood": "dark humor, genre pastiche, pop culture bravado, stylized violence",
    },
    "jia-zhangke": {
        "name": "贾樟柯",
        "name_en": "Jia Zhangke",
        "description": "中国社会变迁，纪实美学，边缘人物",
        "camera_push": "Jia Zhangke push in: slow push in on weathered face, Still Life style, documentary realism, Chinese social change",
        "camera_pull": "Jia Zhangke pull back: pull back from intimate moment to demolition site, Platform style, economic transformation",
        "camera_orbit": "Jia Zhangke orbit: camera circles in crowded public space, A Touch of Sin style, social tension",
        "transition_match": "Jia Zhangke match cut: cut from traditional to modern China, Still Life style, Fengjie and Three Gorges",
        "transition_light": "Jia Zhangke train light: train window light transition, Platform style, journey and displacement",
        "lighting": "available light, harsh sunlight, fluorescent lights, documentary realism, Chinese urban landscapes",
        "color": "desaturated earth tones, occasional vivid reds, documentary palette, transitional China",
        "bgm_keywords": ['ambient', 'documentary', 'minimal', 'atmospheric', 'realistic'],
        "mood": "social realism, melancholic observation, displaced persons, quiet resilience",
    },
    "hou-hsiao-hsien": {
        "name": "侯孝贤",
        "name_en": "Hou Hsiao-hsien",
        "description": "长镜头美学，历史记忆，东方哲思",
        "camera_push": "Hou Hsiao-hsien push in: extremely slow push, long take, Three Times style, historical layering, contemplative pace",
        "camera_pull": "Hou Hsiao-hsien pull back: pull back from intimate gesture to historical context, The Assassin style, Tang dynasty atmosphere",
        "camera_orbit": "Hou Hsiao-hsien orbit: camera circles through architectural space, Millennium Mambo style, temporal layering",
        "transition_match": "Hou Hsiao-hsien match cut: cut from one historical period to another, Three Times style, temporal meditation",
        "transition_light": "Hou Hsiao-hsien candle light: candle and lantern light transition, The Assassin style, period authenticity",
        "lighting": "natural window light, candlelight, lantern light, diffused and atmospheric, historical authenticity",
        "color": "muted earth tones, desaturated blues and greens, historical palette, Japanese and Chinese aesthetics",
        "bgm_keywords": ['minimal', 'ambient', 'traditional', 'meditative', 'peaceful'],
        "mood": "temporal meditation, historical memory, quiet observation, philosophical melancholy",
    },
    "bong-joon-ho": {
        "name": "奉俊昊",
        "name_en": "Bong Joon-ho",
        "description": "类型混合，社会讽刺，空间政治",
        "camera_push": "Bong Joon-ho push in: slow push with vertical movement, Parasite style, semi-basement to mansion, social stratification",
        "camera_pull": "Bong Joon-ho pull back: pull back to reveal spatial metaphor, Parasite style, rain flood, class warfare",
        "camera_orbit": "Bong Joon-ho orbit: camera circles through vertical space, Snowpiercer style, train cars, dystopian hierarchy",
        "transition_match": "Bong Joon-ho match cut: cut from one social class to another, Parasite style, vertical metaphor",
        "transition_light": "Bong Joon-ho rain light: rain and flood light transition, Parasite style, social deluge",
        "lighting": "practical lights, fluorescent tubes, natural window light, high contrast, social realism",
        "color": "desaturated earth tones, occasional vivid greens, social class color coding, Korean aesthetic",
        "bgm_keywords": ['tension', 'dark', 'satirical', 'dramatic', 'thriller'],
        "mood": "social satire, genre hybridity, dark humor, class consciousness, tension between spaces",
    },
    "denis-villeneuve": {
        "name": "维伦纽瓦",
        "name_en": "Denis Villeneuve",
        "description": "宏大尺度，静谧恐惧，宇宙诗意",
        "camera_push": "Villeneuve push in: massive push in on subject's face, Arrival style, linguistic alienation, cosmic scale",
        "camera_pull": "Villeneuve pull back: camera pulls back from small human to massive alien craft, Arrival style, overwhelming scale",
        "camera_orbit": "Villeneuve orbit: camera circles subject in vast desert, Dune style, sandworm scale, epic silence",
        "transition_match": "Villeneuve match cut: cut from human to alien perspective, Arrival style, non-linear time",
        "transition_light": "Villeneuve light burst: sudden light burst transition, Dune style, spice revelation, cosmic awe",
        "lighting": "natural harsh light, desert sun, soft interior light, chiaroscuro, IMAX scale",
        "color": "desaturated oranges and blues, Dune desert palette, muted cosmic tones",
        "bgm_keywords": ['epic', 'ambient', 'dramatic', 'cinematic', 'powerful'],
        "mood": "cosmic awe, linguistic mystery, political tension, quiet dread, epic scale",
    },
    "luc-besson": {
        "name": "卢贝松",
        "name_en": "Luc Besson",
        "description": "视觉诗歌，街头诗学，少女与杀手",
        "camera_push": "Besson push in: slow push in with lyrical camera, Léon style, flower and plant motifs, visual poetry",
        "camera_pull": "Besson pull back: pull back from intimate moment to urban violence, La Femme Nikita style, redemption",
        "camera_orbit": "Besson orbit: camera circles through stylized city, The Fifth Element style, retro-futurism, visual excess",
        "transition_match": "Besson match cut: cut from flower to gun, Léon style, visual metaphor, beauty and violence",
        "transition_light": "Besson light burst: golden light burst transition, The Fifth Element style, opera and space opera",
        "lighting": "golden hour sunlight, stylized neon, cinematic lighting, French visual poetry",
        "color": "vivid primary colors, saturated reds and blues, retro-futuristic palette",
        "bgm_keywords": ['electronic', 'cinematic', 'dramatic', 'urban', 'thriller'],
        "mood": "visual poetry, stylized violence, redemption, romantic fatalism, European cool",
    },
    "miyazaki": {
        "name": "宫崎骏",
        "name_en": "Miyazaki",
        "description": "手绘诗意，飞行幻想，自然崇拜",
        "camera_push": "Miyazaki push in: gentle push in with hand-painted detail, Spirited Away style, magical transformation, nature spirits",
        "camera_pull": "Miyazaki pull back: pull back from character to vast landscape, Castle in the Sky style, floating island, pastoral beauty",
        "camera_orbit": "Miyazaki orbit: camera circles with gentle flight, My Neighbor Totoro style, forest canopy, ecological harmony",
        "transition_match": "Miyazaki match cut: cut from human to spirit world, Spirited Away style, magical realism",
        "transition_light": "Miyazaki light transition: soft golden light through trees, Princess Mononoke style, forest spirit glow",
        "lighting": "soft natural light, dappled sunlight through trees, hand-painted lighting, Studio Ghibli aesthetic",
        "color": "soft pastels, lush greens, sky blues, hand-painted watercolor texture",
        "bgm_keywords": ['piano', 'orchestral', 'magical', 'whimsical', 'fantasy'],
        "mood": "magical realism, ecological wonder, childhood innocence, gentle melancholy, hand-crafted beauty",
    },
}

DEFAULT_CINEMATIC_STYLE = "none"

# ============================================================
# 辅助函数
# ============================================================

def get_preset(product_type: str) -> dict:
    """获取产品类型预设"""
    return PRODUCT_PRESETS.get(product_type, PRODUCT_PRESETS["default"])


def ensure_dirs(output_dir: Path = OUTPUT_DIR) -> None:
    """确保所有必要目录存在

    Args:
        output_dir: 输出根目录，默认 OUTPUT_DIR
    """
    dirs = [
        output_dir / "character_ref",
        output_dir / "clips",
        output_dir / "final",
        output_dir / "batch",
        output_dir / "bgm_cache",
        output_dir / "sfx_cache",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


# ============================================================
# 日志配置
# ============================================================

LOG_LEVEL = os.getenv("KLING_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str = "kling_ad", level: str = LOG_LEVEL) -> logging.Logger:
    """创建并配置 logger 实例

    Args:
        name: logger 名称
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）

    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = setup_logger()


# ============================================================
# 自定义异常类
# ============================================================

class KlingAIError(Exception):
    """可灵 AI 项目基础异常类"""
    error_code = "E0000"
    message = "未知错误"

    def __init__(self, message: str = "", error_code: str = ""):
        if error_code:
            self.error_code = error_code
        if message:
            self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class APIKeyError(KlingAIError):
    """API Key 配置错误"""
    error_code = "E1001"
    message = "可灵 API Key 未配置"


class APICallError(KlingAIError):
    """API 调用失败"""
    error_code = "E1002"
    message = "可灵 API 调用失败"


class VideoGenerationError(KlingAIError):
    """视频生成失败"""
    error_code = "E1003"
    message = "视频生成失败"


class ImageGenerationError(KlingAIError):
    """图片生成失败"""
    error_code = "E1004"
    message = "图片生成失败"


class FFmpegError(KlingAIError):
    """FFmpeg 处理失败"""
    error_code = "E2001"
    message = "FFmpeg 处理失败"


class ConfigError(KlingAIError):
    """配置错误"""
    error_code = "E3001"
    message = "配置错误"
