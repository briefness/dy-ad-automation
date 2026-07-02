# 角色定妆照 Prompt 模板（可灵 Kling 适配）

> 使用说明：复制下方模板，替换 `[ ]` 中的内容，生成一张角色定妆照。
> 生成后将此图保存为 `character_ref.png`，后续所有视频片段都将引用此图以保证人物一致性。

---

## 模板（中文版）

```
角色定妆照，[产品类型]广告用，[年龄]岁[性别]，[风格]风格，[服装描述]，[场景描述]，[光线描述]，[构图描述]，[画质要求]
```

## 模板（英文版，推荐用于可灵）

```
Character reference portrait for [product category] advertisement, [age]-year-old [gender], [style] style, wearing [outfit description], [scene description], [lighting description], [composition description], [quality requirements], high detail, consistent lighting, front-facing, neutral expression
```

---

## 示例填充

### 示例 1：美妆产品

**中文：**
```
角色定妆照，美妆广告用，25岁女性，清新自然风格，白色oversize卫衣+淡妆+自然长发，纯白色背景，自然光柔光，半身构图，高清细节，面部特征清晰
```

**英文（推荐）：**
```
Character reference portrait for beauty cosmetic advertisement, 25-year-old female, fresh natural style, wearing white oversized hoodie + light makeup + natural long hair, pure white background, soft natural lighting, half-body composition, high detail, clear facial features, front-facing, neutral expression
```

---

### 示例 2：科技产品

**中文：**
```
角色定妆照，科技产品广告用，28岁男性，极客商务风格，深色衬衫+眼镜+智能手表，现代简约办公场景，冷色调科技光，半身构图，高清细节，专业形象
```

**英文（推荐）：**
```
Character reference portrait for tech product advertisement, 28-year-old male, geek business style, wearing dark shirt + glasses + smart watch, modern minimalist office scene, cool tech lighting, half-body composition, high detail, professional look, front-facing, neutral expression
```

---

### 示例 3：食品饮料

**中文：**
```
角色定妆照，食品广告用，22岁女性，阳光可爱风格，条纹T恤+牛仔裤+运动鞋，户外咖啡厅场景，自然阳光，半身构图，高清细节，亲和力笑容
```

**英文（推荐）：**
```
Character reference portrait for food beverage advertisement, 22-year-old female, sunny cute style, wearing striped T-shirt + jeans + sneakers, outdoor cafe scene, natural sunlight, half-body composition, high detail, friendly smile, front-facing
```

---

## 可灵 Kling 参数设置

| 参数 | 建议值 | 说明 |
|------|--------|------|
| 分辨率 | 1080×1920 | 竖屏 9:16 |
| 帧率 | 24fps | 标准帧率 |
| 生成模式 | 标准模式 | 速度与质量平衡 |
| 参考图 | 上传此定妆照 | 作为首帧或角色参考 |
| 运动强度 | 0.5-0.7 | 避免人物变形 |
| 种子（Seed） | 固定一个数值 | 全片保持一致风格 |

---

## 负面提示词（固定使用）

```
Negative prompt:
different person, different face, different outfit, blurry, low quality, distorted face, extra limbs, text watermark, logo, brand mark, multiple people, crowd, group shot
```

---

## 注意事项

1. **定妆照质量直接影响全片一致性**，建议生成 3-5 张选一张最好的
2. **表情建议中性**，避免大笑或夸张表情，否则后续镜头难以匹配
3. **光线方向固定**，后续所有片段保持相同光线逻辑
4. **保存为 PNG** 无损格式，避免压缩导致细节丢失
5. **文件名统一为** `character_ref.png`，方便脚本自动引用
