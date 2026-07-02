#!/usr/bin/env python3
"""
可灵 AI 抖音广告视频 - 自动化分镜脚本生成器

使用方法：
    python generate_ad_script.py

功能：
    输入产品信息，自动生成完整的 30s 抖音竖屏广告分镜脚本
    包含：画面描述、运镜、转场、字幕、BGM、可灵 Prompt（中英双语）

输出：
    output/script_<product>_<timestamp>.md
"""

import json
import os
from datetime import datetime
from pathlib import Path

# ============================================================
# 配置区
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# 确保输出目录存在
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 模板库
# ============================================================

# 产品类型预设
PRODUCT_PRESETS = {
    "美妆": {
        "style": "清新自然",
        "lighting": "柔光自然光",
        "scene": "卧室/化妆台",
        "demo_action": "涂抹在手上，展示质地",
        "result": "肌肤水润光泽，自信笑容",
    },
    "食品": {
        "style": "温暖治愈",
        "lighting": "暖色调自然光",
        "scene": "厨房/餐桌",
        "demo_action": "咬一口，展示口感",
        "result": "满足表情，幸福微笑",
    },
    "科技": {
        "style": "极客未来",
        "lighting": "冷色调科技光",
        "scene": "现代办公/数码场景",
        "demo_action": "手指点击屏幕，展示功能",
        "result": "高效完成，满意点头",
    },
    "服装": {
        "style": "时尚潮流",
        "lighting": "棚拍柔光",
        "scene": "简约背景/街景",
        "demo_action": "展示服装细节，转身",
        "result": "自信走秀，完美造型",
    },
    "app": {
        "style": "简洁现代",
        "lighting": "明亮自然光",
        "scene": "咖啡厅/办公桌",
        "demo_action": "手指滑动屏幕，展示界面",
        "result": "任务完成，轻松表情",
    },
    "default": {
        "style": "现代简约",
        "lighting": "自然光",
        "scene": "生活场景",
        "demo_action": "展示产品使用",
        "result": "满意表情",
    },
}

# 运镜映射
CAMERA_MOVEMENTS = {
    "固定": {"en": "static shot", "kling": "固定运镜", "motion": "0.1-0.3"},
    "推进": {"en": "slow push in", "kling": "推进运镜", "motion": "0.3-0.5"},
    "拉远": {"en": "slow pull back", "kling": "拉远运镜", "motion": "0.3-0.5"},
    "环绕": {"en": "camera orbit around", "kling": "环绕运镜", "motion": "0.4-0.6"},
    "手持": {"en": "handheld tracking shot", "kling": "手持运镜", "motion": "0.6-0.8"},
    "升降": {"en": "slow tilt up", "kling": "升降运镜", "motion": "0.3-0.5"},
}

# 转场映射
TRANSITIONS = {
    "黑场": {"duration": "0.3s", "method": "后期加黑场", "kling": "FADE TO BLACK"},
    "白场": {"duration": "0.5s", "method": "屏幕亮光/白场", "kling": "FADE TO WHITE"},
    "叠化": {"duration": "0.3s", "method": "交叉溶解", "kling": "DISSOLVE"},
    "Match Cut": {"duration": "0.3s", "method": "形状/颜色匹配", "kling": "MATCH CUT"},
    "直接切换": {"duration": "0s", "method": "硬切", "kling": "HARD CUT"},
}

# ============================================================
# 核心生成逻辑
# ============================================================

def get_preset(product_type: str) -> dict:
    """根据产品类型获取预设"""
    return PRODUCT_PRESETS.get(product_type, PRODUCT_PRESETS["default"])


def generate_character_ref_prompt(product_info: dict) -> dict:
    """生成角色定妆照 Prompt"""
    preset = get_preset(product_info.get("type", "default"))
    
    age = product_info.get("age", "25")
    gender = product_info.get("gender", "女")
    style = product_info.get("style", preset["style"])
    outfit = product_info.get("outfit", "日常休闲装")
    scene = product_info.get("scene", preset["scene"])
    
    prompt_zh = (
        f"角色定妆照，{product_info.get('name', '产品')}广告用，"
        f"{age}岁{gender}，{style}风格，"
        f"{outfit}，{scene}，"
        f"{preset['lighting']}，半身构图，"
        f"高清细节，面部特征清晰，中性表情"
    )
    
    prompt_en = (
        f"Character reference portrait for {product_info.get('name', 'product')} advertisement, "
        f"{age}-year-old {gender}, {style} style, "
        f"wearing {outfit}, {scene}, "
        f"{preset['lighting']}, half-body composition, "
        f"high detail, clear facial features, front-facing, neutral expression"
    )
    
    return {
        "中文": prompt_zh,
        "英文": prompt_en,
        "negative_prompt": "different person, different face, different outfit, blurry, low quality, distorted face, extra limbs, text watermark, logo, brand mark, multiple people, crowd, group shot",
        "参数": {
            "分辨率": "1080×1920",
            "帧率": "24fps",
            "参考图": "character_ref.png",
            "运动强度": "0.5-0.7",
            "Seed": "固定一个数值"
        }
    }


def generate_clip_prompts(product_info: dict) -> list:
    """生成 5 个分镜片段的 Prompts"""
    preset = get_preset(product_info.get("type", "default"))
    name = product_info.get("name", "产品")
    product_desc = product_info.get("description", name)
    character = product_info.get("character", "same person from reference image")
    
    clips = [
        {
            "镜号": 1,
            "时长": "5s",
            "类型": "钩子",
            "时间段": "0-5s",
            "画面描述_zh": f"{character} 皱眉看着手机，显得困惑和沮丧，{preset['scene']}场景，{preset['lighting']}",
            "画面描述_en": f"{character} looking at phone with frustrated expression, confused and stressed, {preset['scene']} scene, {preset['lighting']}",
            "运镜": "固定→推进",
            "运镜参数": CAMERA_MOVEMENTS["固定"],
            "转场_出画": "手遮镜头",
            "转场方式": "黑场 0.3s",
            "字幕": "你是不是也...？",
            "BGM": "鼓点起",
            "prompt_zh": f"固定镜头，缓慢推进，{character} 皱眉看着手机，显得困惑和沮丧，{preset['scene']}场景，{preset['lighting']}，写实生活风格，情绪紧张，前3秒抓住注意力",
            "prompt_en": f"static shot, slow push in, {character} looking at phone with frustrated expression, confused and stressed, {preset['scene']} scene, {preset['lighting']}, realistic lifestyle style, tense mood, grab attention in first 3 seconds, 9:16 vertical",
        },
        {
            "镜号": 2,
            "时长": "5s",
            "类型": "转折",
            "时间段": "5-10s",
            "画面描述_zh": f"{character} 转身拿起 {name}，眼睛亮起来，露出惊喜表情，{name} 在手中展示",
            "画面描述_en": f"{character} turns and picks up {name}, eyes light up with surprise, {name} displayed in hand",
            "运镜": "手持跟拍",
            "运镜参数": CAMERA_MOVEMENTS["手持"],
            "转场_入画": "手从左侧移入，拿起产品",
            "转场_出画": "产品屏幕亮起，强光照射",
            "转场方式": "光线转场 0.5s",
            "字幕": "直到我用了...",
            "BGM": "鼓点加强",
            "prompt_zh": f"手持跟拍，{character} 转身拿起 {name}，眼睛突然亮起来，露出惊喜表情，{name} 在手中展示，温暖光线，生活摄影风格，情绪转折",
            "prompt_en": f"handheld tracking shot, {character} turns and picks up {name}, eyes light up with surprise, {name} displayed in hand, warm lighting, lifestyle photography style, emotional turning point, same person from reference image, 9:16 vertical",
        },
        {
            "镜号": 3,
            "时长": "8s",
            "类型": "展示",
            "时间段": "10-18s",
            "画面描述_zh": f"环绕运镜，{name} 放置在桌面，{character} 手指操作，{preset['demo_action']}，产品细节特写",
            "画面描述_en": f"camera orbit around {name}, {name} placed on desk, {character} fingers operating, {preset['demo_action']}, close-up on product details",
            "运镜": "环绕",
            "运镜参数": CAMERA_MOVEMENTS["环绕"],
            "转场_入画": "产品屏幕亮光渐弱",
            "转场_出画": "角色抬头看镜头，微笑",
            "转场方式": "Match Cut 0.3s",
            "字幕": product_info.get("selling_point", "核心卖点"),
            "BGM": "节奏高潮",
            "prompt_zh": f"环绕运镜，{name} 放置在桌面，{character} 手指操作，{preset['demo_action']}，产品细节特写，柔光产品照明，商业摄影风格，突出卖点",
            "prompt_en": f"camera orbit around {name}, {name} placed on desk, {character} fingers operating, {preset['demo_action']}, close-up on product details, soft product lighting, commercial photography style, highlight selling point, same person from reference image, 9:16 vertical",
        },
        {
            "镜号": 4,
            "时长": "7s",
            "类型": "结果",
            "时间段": "18-25s",
            "画面描述_zh": f"缓慢拉镜，{character} 满意地微笑，{name} 放在旁边，{preset['result']}，温暖金色光线",
            "画面描述_en": f"slow pull back, {character} smiling with satisfaction, {name} placed beside, {preset['result']}, warm golden lighting",
            "运镜": "缓慢拉远",
            "运镜参数": CAMERA_MOVEMENTS["拉远"],
            "转场_入画": "从产品特写拉远至人物上半身",
            "转场_出画": "角色挥手或指向产品",
            "转场方式": "叠化 0.3s",
            "字幕": "真的绝了",
            "BGM": "鼓点收",
            "prompt_zh": f"缓慢拉镜，{character} 满意地微笑，{name} 放在旁边，{preset['result']}，温暖金色光线，情绪升华，用户满意表情特写",
            "prompt_en": f"slow pull back, {character} smiling with satisfaction, {name} placed beside, {preset['result']}, warm golden hour lighting, emotional climax, close-up on happy expression, same person from reference image, 9:16 vertical",
        },
        {
            "镜号": 5,
            "时长": "5s",
            "类型": "CTA",
            "时间段": "25-30s",
            "画面描述_zh": f"固定镜头，{name} 精美摆放在 {preset['scene']}，品牌 Logo 出现在画面中，干净明亮，产品摄影风格",
            "画面描述_en": f"static wide shot, {name} beautifully placed on clean surface, brand logo appears subtly in corner, clean and bright, product photography style",
            "运镜": "固定",
            "运镜参数": CAMERA_MOVEMENTS["固定"],
            "转场_入画": "从上一镜挥手动作切入",
            "转场_出画": "产品完整展示，Logo 清晰",
            "转场方式": "直接切换",
            "字幕": "点击左下角购买",
            "BGM": "音乐收尾",
            "prompt_zh": f"固定镜头，{name} 精美摆放在 {preset['scene']}，品牌Logo出现在画面中，干净明亮，产品摄影风格，品牌确认，行动号召",
            "prompt_en": f"static wide shot, {name} beautifully placed on clean white surface, brand logo appears subtly in corner, clean and bright, product photography style, brand confirmation, call to action, 9:16 vertical",
        },
    ]
    
    return clips


def generate_full_script(product_info: dict) -> str:
    """生成完整的分镜脚本 Markdown"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    product_name = product_info.get("name", "产品").replace(" ", "_")
    
    # 生成各部分内容
    character_prompts = generate_character_ref_prompt(product_info)
    clip_prompts = generate_clip_prompts(product_info)
    
    # 构建 Markdown
    md = []
    md.append(f"# 可灵 AI 抖音广告分镜脚本")
    md.append(f"")
    md.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md.append(f"> 产品：{product_info.get('name', '未知产品')}")
    md.append(f"")
    md.append(f"---")
    md.append(f"")
    
    # 产品信息
    md.append(f"## 📦 产品信息")
    md.append(f"")
    md.append(f"| 字段 | 内容 |")
    md.append(f"|------|------|")
    md.append(f"| 产品名称 | {product_info.get('name', '-')} |")
    md.append(f"| 产品类型 | {product_info.get('type', '-')} |")
    md.append(f"| 核心卖点 | {product_info.get('selling_point', '-')} |")
    md.append(f"| 目标人群 | {product_info.get('audience', '-')} |")
    md.append(f"| 视频时长 | 30s |")
    md.append(f"| 风格 | {product_info.get('style', '现代简约')} |")
    md.append(f"")
    
    # 角色定妆照
    md.append(f"## 🧑 角色定妆照 Prompt")
    md.append(f"")
    md.append(f"### 中文 Prompt")
    md.append(f"```")
    md.append(character_prompts["中文"])
    md.append(f"```")
    md.append(f"")
    md.append(f"### 英文 Prompt（推荐）")
    md.append(f"```")
    md.append(character_prompts["英文"])
    md.append(f"```")
    md.append(f"")
    md.append(f"### 负面提示词")
    md.append(f"```")
    md.append(character_prompts["negative_prompt"])
    md.append(f"```")
    md.append(f"")
    md.append(f"### 可灵参数")
    md.append(f"")
    for k, v in character_prompts["参数"].items():
        md.append(f"- **{k}**：{v}")
    md.append(f"")
    md.append(f"---")
    md.append(f"")
    
    # 分镜脚本
    md.append(f"## 🎬 分镜脚本（9:16 竖屏，30s）")
    md.append(f"")
    md.append(f"| 镜号 | 时长 | 类型 | 画面 | 运镜 | 转场 | 字幕 | BGM |")
    md.append(f"|------|------|------|------|------|------|------|-----|")
    
    for clip in clip_prompts:
        md.append(f"| {clip['镜号']} | {clip['时长']} | {clip['类型']} | {clip['画面描述_zh'][:30]}... | {clip['运镜']} | {clip['转场方式']} | {clip['字幕']} | {clip['BGM']} |")
    
    md.append(f"")
    md.append(f"---")
    md.append(f"")
    
    # 详细片段 Prompt
    md.append(f"## 📝 详细片段 Prompts")
    md.append(f"")
    
    for clip in clip_prompts:
        md.append(f"### 片段 {clip['镜号']}：{clip['类型']}（{clip['时间段']}）")
        md.append(f"")
        md.append(f"**运镜**：{clip['运镜']}（可灵：{clip['运镜参数']['kling']}，运动强度：{clip['运镜参数']['motion']}）")
        md.append(f"")
        md.append(f"**转场设计**：")
        md.append(f"- 入画：{clip.get('转场_入画', '-')}")
        md.append(f"- 出画：{clip.get('转场_出画', '-')}")
        md.append(f"- 方式：{clip['转场方式']}")
        md.append(f"")
        md.append(f"**中文 Prompt**：")
        md.append(f"```")
        md.append(clip["prompt_zh"])
        md.append(f"```")
        md.append(f"")
        md.append(f"**英文 Prompt（推荐）**：")
        md.append(f"```")
        md.append(clip["prompt_en"])
        md.append(f"```")
        md.append(f"")
        md.append(f"---")
        md.append(f"")
    
    # 拼接指南
    md.append(f"## 🔗 拼接指南（剪映）")
    md.append(f"")
    md.append(f"### 转场时间轴")
    md.append(f"")
    md.append(f"```")
    md.append(f"片段1 (0-5s)  ──[黑场 0.3s]──▶  片段2 (5-10s)")
    md.append(f"片段2 (5-10s) ──[白场 0.5s]──▶  片段3 (10-18s)")
    md.append(f"片段3 (10-18s) ──[叠化 0.3s]──▶  片段4 (18-25s)")
    md.append(f"片段4 (18-25s) ──[Match Cut 0.3s]──▶  片段5 (25-30s)")
    md.append(f"```")
    md.append(f"")
    md.append(f"### 剪映操作步骤")
    md.append(f"")
    md.append(f"1. 导入 5 个视频片段，按顺序排列")
    md.append(f"2. 转场处理：")
    md.append(f"   - 片段1→2：加 0.3s 黑场转场")
    md.append(f"   - 片段2→3：加 0.5s 闪白转场（掩盖光线变化）")
    md.append(f"   - 片段3→4：加 0.3s 叠化转场")
    md.append(f"   - 片段4→5：检查动作衔接，可加 0.3s Match Cut")
    md.append(f"3. 调色统一：导入统一 LUT，调整亮度/对比度")
    md.append(f"4. 字幕覆盖：全片覆盖大字幕（字号 60-80，白色+黑色描边）")
    md.append(f"5. BGM：选择抖音热门 BGM，鼓点对齐转场点")
    md.append(f"6. 导出：1080×1920，30fps，MP4")
    md.append(f"")
    md.append(f"---")
    md.append(f"")
    
    # 检查清单
    md.append(f"## ✅ 检查清单")
    md.append(f"")
    md.append(f"### 前期准备")
    md.append(f"- [ ] 生成角色定妆照，保存为 `character_ref.png`")
    md.append(f"- [ ] 确定产品主色调、品牌 Logo 素材")
    md.append(f"- [ ] 选择 BGM（抖音热门音乐或自制）")
    md.append(f"- [ ] 准备字幕文案")
    md.append(f"")
    md.append(f"### AI 生成")
    md.append(f"- [ ] 按分镜逐条生成，每镜单独生成（5-8s 一段）")
    md.append(f"- [ ] 每段固定 seed 和参考图")
    md.append(f"- [ ] 导出 1080×1920 竖屏")
    md.append(f"- [ ] 人物/服装一致性检查")
    md.append(f"")
    md.append(f"### 后期剪辑")
    md.append(f"- [ ] 用剪映拼接所有片段")
    md.append(f"- [ ] 转场点加特效（黑场/白场/叠化）")
    md.append(f"- [ ] 字幕覆盖全片")
    md.append(f"- [ ] BGM 对齐节奏点")
    md.append(f"- [ ] 品牌 Logo 固定在最后 3s")
    md.append(f"- [ ] CTA 文字 + 箭头动画")
    md.append(f"")
    md.append(f"### 抖音发布前检查")
    md.append(f"- [ ] 前 3s 有钩子（无标题文字遮挡）")
    md.append(f"- [ ] 字幕清晰可读（字号≥60，对比度够高）")
    md.append(f"- [ ] 竖屏 9:16，无黑边")
    md.append(f"- [ ] 时长 ≤ 60s")
    md.append(f"- [ ] 背景音乐音量 > 人声（抖音默认静音播放）")
    md.append(f"")
    
    return "\n".join(md)


def save_script(script_content: str, product_name: str) -> Path:
    """保存脚本到文件"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c for c in product_name if c.isalnum() or c in "-_").strip()
    filename = f"script_{safe_name}_{timestamp}.md"
    filepath = OUTPUT_DIR / filename
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(script_content)
    
    return filepath


def main():
    """主函数：交互式输入产品信息并生成脚本"""
    print("=" * 60)
    print("可灵 AI 抖音广告视频 - 自动化分镜脚本生成器")
    print("=" * 60)
    print()
    
    # 交互式输入
    print("请输入产品信息（直接回车使用默认值）：")
    print()
    
    product_name = input("产品名称 [我的产品]：").strip() or "我的产品"
    product_type = input("产品类型 [美妆/食品/科技/服装/app]：").strip() or "default"
    selling_point = input("核心卖点 [一句话描述]：").strip() or "卓越品质，值得拥有"
    audience = input("目标人群 [如：18-35岁女性]：").strip() or "18-35岁"
    style = input("广告风格 [现代简约/清新自然/温暖治愈/极客未来]：").strip() or "现代简约"
    character_age = input("角色年龄 [25]：").strip() or "25"
    character_gender = input("角色性别 [女/男]：").strip() or "女"
    outfit = input("服装描述 [日常休闲装]：").strip() or "日常休闲装"
    
    product_info = {
        "name": product_name,
        "type": product_type,
        "selling_point": selling_point,
        "audience": audience,
        "style": style,
        "age": character_age,
        "gender": character_gender,
        "outfit": outfit,
    }
    
    print()
    print("正在生成分镜脚本...")
    
    # 生成脚本
    script_content = generate_full_script(product_info)
    filepath = save_script(script_content, product_name)
    
    print()
    print("=" * 60)
    print("✅ 脚本生成完成！")
    print("=" * 60)
    print(f"📄 文件路径：{filepath}")
    print()
    print("接下来你可以：")
    print("1. 打开脚本文件，查看完整分镜和 Prompts")
    print("2. 复制 Prompts 到可灵 AI 生成视频片段")
    print("3. 按照拼接指南用剪映合成最终视频")
    print()


if __name__ == "__main__":
    main()
