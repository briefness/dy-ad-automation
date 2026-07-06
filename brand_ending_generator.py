#!/usr/bin/env python3
"""
品牌尾帧生成器（Brand Ending Generator）

参考用户参考图中的"品牌尾帧"概念。

核心特点：
1. 自动生成品牌logo动画尾帧
2. 支持自定义品牌信息（logo、slogan、联系信息）
3. 提供多种尾帧模板（简约、电影、科技、温暖等）
4. 支持渐变动画和文字动画
"""

import subprocess
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from config import setup_logger, OUTPUT_DIR

logger = setup_logger(__name__)


@dataclass
class BrandInfo:
    """品牌信息"""
    brand_name: str
    logo_path: Optional[Path] = None
    slogan: str = ""
    contact_info: str = ""
    website: str = ""
    color_primary: str = "#FFFFFF"
    color_secondary: str = "#000000"
    font_family: str = "Arial"


@dataclass
class EndingTemplate:
    """尾帧模板"""
    name: str
    description: str
    duration: float
    background_type: str  # solid, gradient, blur, video
    animation_type: str   # fade_in, slide_up, scale_in, cinematic
    text_style: str       # bold, elegant, modern, minimal


class BrandEndingGenerator:
    """品牌尾帧生成器主类"""

    # 尾帧模板库
    TEMPLATES = {
        "cinematic": EndingTemplate(
            name="电影感尾帧",
            description="黑底+金色文字，电影结束风格",
            duration=3.0,
            background_type="solid",
            animation_type="fade_in",
            text_style="elegant",
        ),
        "minimal": EndingTemplate(
            name="简约尾帧",
            description="简洁干净，突出品牌logo",
            duration=2.5,
            background_type="solid",
            animation_type="scale_in",
            text_style="minimal",
        ),
        "warm": EndingTemplate(
            name="温暖尾帧",
            description="暖色调渐变，适合家庭/温馨主题",
            duration=3.0,
            background_type="gradient",
            animation_type="fade_in",
            text_style="elegant",
        ),
        "tech": EndingTemplate(
            name="科技尾帧",
            description="深色科技感，适合数码产品",
            duration=2.5,
            background_type="gradient",
            animation_type="slide_up",
            text_style="modern",
        ),
        "commercial": EndingTemplate(
            name="商业尾帧",
            description="专业商务风格，适合企业宣传",
            duration=3.0,
            background_type="solid",
            animation_type="fade_in",
            text_style="bold",
        ),
    }

    def __init__(self):
        pass

    def generate_ending_video(
        self,
        brand_info: BrandInfo,
        template_name: str = "cinematic",
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        生成品牌尾帧视频。
        
        使用FFmpeg生成动态尾帧，支持多种模板。
        """
        template = self.TEMPLATES.get(template_name, self.TEMPLATES["cinematic"])

        if output_path is None:
            output_path = OUTPUT_DIR / "brand_endings" / f"{brand_info.brand_name}_ending.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # 根据模板生成FFmpeg命令
        cmd = self._build_ffmpeg_command(brand_info, template, output_path)

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"🏷️ 品牌尾帧已生成：{output_path}")
            return output_path
        except subprocess.CalledProcessError as e:
            logger.error(f"生成品牌尾帧失败：{e.stderr.decode()}")
            raise

    def _build_ffmpeg_command(
        self,
        brand_info: BrandInfo,
        template: EndingTemplate,
        output_path: Path,
    ) -> List[str]:
        """构建FFmpeg命令"""
        cmd = ["ffmpeg", "-y", "-f", "lavfi"]

        # 设置背景
        if template.background_type == "solid":
            if template.name == "cinematic":
                cmd.extend(["-i", f"color=c=black:s=1080x1920:r=30:d={template.duration}"])
            elif template.name == "warm":
                cmd.extend(["-i", f"color=c=0x3d2914:s=1080x1920:r=30:d={template.duration}"])
            else:
                cmd.extend(["-i", f"color=c={self._hex_to_rgb(brand_info.color_secondary)}:s=1080x1920:r=30:d={template.duration}"])
        elif template.background_type == "gradient":
            if template.name == "warm":
                cmd.extend([
                    "-i",
                    f"gradients=c0=0x3d2914:c1=0x6b4423:s=1080x1920:r=30:d={template.duration}"
                ])
            else:  # tech
                cmd.extend([
                    "-i",
                    f"gradients=c0=0x0a0a0a:c1=0x1a1a2e:s=1080x1920:r=30:d={template.duration}"
                ])

        # 添加logo（如果有）
        if brand_info.logo_path and brand_info.logo_path.exists():
            cmd.extend(["-i", str(brand_info.logo_path)])

        # 构建滤镜链
        filters = []

        # Logo动画
        if brand_info.logo_path and brand_info.logo_path.exists():
            if template.animation_type == "fade_in":
                filters.append("[1]scale=300:300,fade=t=in:st=0:d=0.5[logo]")
            elif template.animation_type == "scale_in":
                filters.append("[1]scale=300:300,zoompan=z='min(zoom+0.01,1.5)':d=30*0.5[logo]")
            elif template.animation_type == "slide_up":
                filters.append("[1]scale=300:300,translate=y='max(-H, -H+(t)*H)':d=30*0.5[logo]")
            else:
                filters.append("[1]scale=300:300[logo]")

            filters.append("[0][logo]overlay=(W-w)/2:(H-h)/2-100[bg_with_logo]")
            input_ref = "[bg_with_logo]"
        else:
            input_ref = "[0]"

        # 品牌名称文字
        font_size = 60 if template.text_style in ["elegant", "bold"] else 50
        text_color = brand_info.color_primary
        text_filter = f"drawtext=text='{brand_info.brand_name}':fontsize={font_size}:fontcolor={text_color}:x=(W-text_w)/2:y=(H-text_h)/2:enable='between(t,0.5,{template.duration})'"
        
        if template.animation_type == "fade_in":
            text_filter += ":alpha='min(t*2,1)'"
        elif template.animation_type == "slide_up":
            text_filter += ":y='(H-text_h)/2+100*exp(-t*3)'"
        
        filters.append(f"{input_ref}{text_filter}[text1]")
        input_ref = "[text1]"

        # Slogan文字
        if brand_info.slogan:
            slogan_filter = f"drawtext=text='{brand_info.slogan}':fontsize=36:fontcolor={text_color}:x=(W-text_w)/2:y=(H-text_h)/2+80:enable='between(t,0.8,{template.duration})':alpha='min((t-0.8)*3,1)'"
            filters.append(f"{input_ref}{slogan_filter}[text2]")
            input_ref = "[text2]"

        # 联系信息/网址
        if brand_info.website or brand_info.contact_info:
            bottom_text = brand_info.website if brand_info.website else brand_info.contact_info
            bottom_filter = f"drawtext=text='{bottom_text}':fontsize=28:fontcolor={text_color}:x=(W-text_w)/2:y=H-100:enable='between(t,1.0,{template.duration})':alpha='min((t-1.0)*3,1)'"
            filters.append(f"{input_ref}{bottom_filter}[text3]")
            input_ref = "[text3]"

        # 最终输出
        cmd.extend(["-filter_complex", ";".join(filters)])
        cmd.extend(["-c:v", "libx264", "-crf", "18", "-preset", "medium"])
        cmd.append(str(output_path))

        return cmd

    def _hex_to_rgb(self, hex_color: str) -> str:
        """将十六进制颜色转换为RGB格式"""
        hex_color = hex_color.lstrip("#")
        return f"0x{hex_color}"

    def generate_ending_image(
        self,
        brand_info: BrandInfo,
        template_name: str = "cinematic",
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        生成品牌尾帧静态图片（用于封面）。
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.error("PIL未安装，请先安装pillow")
            raise

        template = self.TEMPLATES.get(template_name, self.TEMPLATES["cinematic"])

        if output_path is None:
            output_path = OUTPUT_DIR / "brand_endings" / f"{brand_info.brand_name}_ending.png"
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建画布
        width, height = 1080, 1920
        image = Image.new("RGB", (width, height), self._hex_to_pil_color(brand_info.color_secondary))

        if template.background_type == "gradient":
            image = self._create_gradient_image(width, height, template)

        draw = ImageDraw.Draw(image)

        # 添加logo
        if brand_info.logo_path and brand_info.logo_path.exists():
            with Image.open(brand_info.logo_path) as logo:
                logo = logo.resize((300, 300), Image.LANCZOS)
                x = (width - logo.width) // 2
                y = (height - logo.height) // 2 - 100
                image.paste(logo, (x, y), logo if logo.mode == "RGBA" else None)

        # 添加品牌名称
        try:
            font = ImageFont.truetype("Arial.ttf", 60)
        except Exception:
            font = ImageFont.load_default()

        text_bbox = draw.textbbox((0, 0), brand_info.brand_name, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        x = (width - text_w) // 2
        y = (height - text_h) // 2
        draw.text((x, y), brand_info.brand_name, fill=self._hex_to_pil_color(brand_info.color_primary), font=font)

        # 添加slogan
        if brand_info.slogan:
            try:
                slogan_font = ImageFont.truetype("Arial.ttf", 36)
            except Exception:
                slogan_font = ImageFont.load_default()

            slogan_bbox = draw.textbbox((0, 0), brand_info.slogan, font=slogan_font)
            slogan_w = slogan_bbox[2] - slogan_bbox[0]
            x = (width - slogan_w) // 2
            y = (height - text_h) // 2 + 80
            draw.text((x, y), brand_info.slogan, fill=self._hex_to_pil_color(brand_info.color_primary), font=slogan_font)

        image.save(output_path, "PNG")
        logger.info(f"🏷️ 品牌尾帧图片已生成：{output_path}")
        return output_path

    def _hex_to_pil_color(self, hex_color: str) -> tuple:
        """将十六进制颜色转换为PIL颜色"""
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _create_gradient_image(self, width: int, height: int, template: EndingTemplate) -> Image.Image:
        """创建渐变背景"""
        image = Image.new("RGB", (width, height))
        pixels = image.load()

        if template.name == "warm":
            c1 = (61, 41, 20)
            c2 = (107, 68, 35)
        else:  # tech
            c1 = (10, 10, 10)
            c2 = (26, 26, 46)

        for y in range(height):
            for x in range(width):
                ratio = y / height
                r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
                g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
                b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
                pixels[x, y] = (r, g, b)

        return image

    def get_template_preview(self, template_name: str) -> Dict[str, Any]:
        """获取模板预览信息"""
        template = self.TEMPLATES.get(template_name)
        if not template:
            return {}

        return {
            "name": template.name,
            "description": template.description,
            "duration": template.duration,
            "background": template.background_type,
            "animation": template.animation_type,
            "text_style": template.text_style,
        }
