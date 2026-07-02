# Assets 目录说明

## 必要文件（影响成片质量）

### `bgm.mp3` — 背景音乐（**必须提供**）

- 格式：MP3 / AAC，建议 320kbps
- 时长：≥ 30s（短于视频时长会自动循环）
- BPM 建议：90-160 BPM（抖音广告主流节奏）
  - 快节奏展示类：120-140 BPM
  - 中节奏故事类：100-120 BPM
  - 慢节奏情感类：80-100 BPM
- **版权注意**：商用请确认授权（推荐使用 Epidemic Sound / Artlist / 抖音商业版权库）

> ⚠️ 缺少此文件时系统会自动生成粉红底噪兜底，但抖音算法会将其判定为无声视频，推荐权重极低。

---

### `sfx/` — 音效文件目录（可选，有则用真实音效替代合成音效）

将以下文件放入此目录，系统会自动优先使用真实音效：

| 文件名 | 用途 | 建议时长 |
|-------|------|---------|
| `whoosh.wav` | 转场嗖声 | 0.4-0.6s |
| `ding.wav` | 强调叮声 | 0.2-0.4s |
| `impact.wav` | 冲击重击声 | 0.3-0.5s |

推荐来源：
- [Freesound.org](https://freesound.org)（CC0 授权）
- [Pixabay Sound Effects](https://pixabay.com/sound-effects/)（免费商用）
- [Zapsplat](https://www.zapsplat.com)（注册免费）

---

### `logo.png` — 品牌 Logo（可选）

- 格式：PNG，透明背景
- 尺寸：建议 400×400px 以上
- 启用方式：在 `config.py` 中设置 `BRAND_CONFIG["logo_watermark"]["enabled"] = True`
