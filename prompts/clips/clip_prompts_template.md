# 分镜片段 Prompt 模板（可灵 Kling 适配）

> 使用说明：以下为 5 个标准分镜模板，对应 30s 抖音竖屏广告。
> 每个模板包含：画面描述、运镜指令、转场设计、可灵 Prompt（中英双语）。

---

## 通用结构

每个片段的 Prompt 结构：

```
[运镜指令] + [场景描述] + [角色动作] + [产品展示] + [光线/氛围] + [风格参考] + [转场提示]
```

---

## 片段 1：钩子（0-5s）— 痛点场景

### 中文 Prompt
```
固定镜头，缓慢推进，[角色] 皱眉看着手机，显得困惑和沮丧，[痛点场景描述]，自然室内光线，写实生活风格，情绪紧张，前3秒抓住注意力
```

### 英文 Prompt（推荐）
```
static shot, slow push in, [character] looking at phone with frustrated expression, confused and stressed, [pain point scene description], natural indoor lighting, realistic lifestyle style, tense mood, grab attention in first 3 seconds, 9:16 vertical
```

### 运镜参数
| 参数 | 值 | 说明 |
|------|-----|------|
| 运镜 | 固定→推进 | 可灵选择「固定运镜」或「推进运镜」 |
| 运动强度 | 0.3-0.5 | 缓慢推进，制造紧张感 |
| 时长 | 5s | 单次生成时长 |

### 转场设计
- **出画动作**：角色将手机拿远，手遮住镜头
- **转场方式**：0.3s 黑场
- **衔接下一镜**：片段 2 从黑场渐亮开始

---

## 片段 2：转折（5-10s）— 产品出现

### 中文 Prompt
```
手持跟拍，[角色] 转身拿起 [产品]，眼睛突然亮起来，露出惊喜表情，[产品] 在手中展示，温暖光线，生活摄影风格，情绪转折
```

### 英文 Prompt（推荐）
```
handheld tracking shot, [character] turns and picks up [product], eyes light up with surprise, [product] displayed in hand, warm lighting, lifestyle photography style, emotional turning point, same person from reference image, 9:16 vertical
```

### 运镜参数
| 参数 | 值 | 说明 |
|------|-----|------|
| 运镜 | 手持跟拍 | 可灵选择「手持运镜」 |
| 运动强度 | 0.6-0.8 | 跟随人物动作，增加动感 |
| 时长 | 5s | 单次生成时长 |

### 转场设计
- **入画动作**：手从画面左侧移入，拿起产品
- **出画动作**：产品屏幕亮起，强光照射
- **转场方式**：0.5s 白场/光线转场
- **衔接下一镜**：片段 3 从光线中渐显

---

## 片段 3：展示（10-18s）— 核心功能

### 中文 Prompt
```
环绕运镜，[产品] 放置在 [场景]，[角色] 手指操作 [核心功能展示]，产品细节特写，柔光产品照明，商业摄影风格，突出卖点
```

### 英文 Prompt（推荐）
```
camera orbit around [product], [product] placed on [scene], [character] fingers operating [core feature demonstration], close-up on product details, soft product lighting, commercial photography style, highlight selling point, same person from reference image, 9:16 vertical
```

### 运镜参数
| 参数 | 值 | 说明 |
|------|-----|------|
| 运镜 | 环绕 | 可灵选择「环绕运镜」 |
| 运动强度 | 0.4-0.6 | 稳定环绕，展示产品全貌 |
| 时长 | 8s | 可灵单次最长时长 |
| 分段建议 | 可拆为 2×4s | 如需更精细控制 |

### 转场设计
- **入画动作**：产品屏幕亮光渐弱，展示完整产品
- **出画动作**：角色抬头看镜头，微笑
- **转场方式**：0.3s Match Cut（产品形状/颜色匹配）
- **衔接下一镜**：片段 4 从同一角度接微笑表情

---

## 片段 4：结果（18-25s）— 使用效果

### 中文 Prompt
```
缓慢拉镜，[角色] 满意地微笑，[产品] 放在旁边，[结果描述]，温暖金色光线，情绪升华，用户满意表情特写
```

### 英文 Prompt（推荐）
```
slow pull back, [character] smiling with satisfaction, [product] placed beside, [result description], warm golden lighting, emotional climax, close-up on happy expression, same person from reference image, 9:16 vertical
```

### 运镜参数
| 参数 | 值 | 说明 |
|------|-----|------|
| 运镜 | 缓慢拉远 | 可灵选择「拉远运镜」 |
| 运动强度 | 0.3-0.5 | 缓慢拉远，情绪升华 |
| 时长 | 7s | 单次生成时长 |

### 转场设计
- **入画动作**：从产品特写拉远至人物上半身
- **出画动作**：角色挥手或指向产品
- **转场方式**：0.3s 叠化
- **衔接下一镜**：片段 5 从挥手动作接 CTA

---

## 片段 5：CTA（25-30s）— 品牌露出

### 中文 Prompt
```
固定镜头，[产品] 精美摆放在 [场景]，[品牌Logo] 出现在画面中，干净明亮，产品摄影风格，品牌确认，行动号召
```

### 英文 Prompt（推荐）
```
static wide shot, [product] beautifully placed on [scene], [brand logo] appears in frame, clean and bright, product photography style, brand confirmation, call to action, 9:16 vertical
```

### 运镜参数
| 参数 | 值 | 说明 |
|------|-----|------|
| 运镜 | 固定 | 可灵选择「固定运镜」 |
| 运动强度 | 0.1-0.2 | 几乎静止，干净利落 |
| 时长 | 5s | 单次生成时长 |

### 转场设计
- **入画动作**：从上一镜挥手动作切入
- **出画动作**：产品完整展示，Logo 清晰
- **转场方式**：无转场，直接结束
- **衔接**：进入后期剪辑，添加字幕和 CTA

---

## 可灵运镜参数速查表

| 运镜类型 | 可灵设置 | 适用片段 | 运动强度 |
|---------|---------|---------|---------|
| 固定 | 固定运镜 | 片段 1、5 | 0.1-0.3 |
| 推进 | 推进运镜 | 片段 1 | 0.3-0.5 |
| 手持 | 手持运镜 | 片段 2 | 0.6-0.8 |
| 环绕 | 环绕运镜 | 片段 3 | 0.4-0.6 |
| 拉远 | 拉远运镜 | 片段 4 | 0.3-0.5 |

---

## 转场设计速查表

| 转场类型 | 时长 | 实现方式 | 适用位置 |
|---------|------|---------|---------|
| 黑场转场 | 0.3s | 后期加黑场 | 片段 1→2 |
| 光线转场 | 0.5s | 屏幕亮光/白场 | 片段 2→3 |
| Match Cut | 0.3s | 形状/颜色匹配 | 片段 3→4 |
| 叠化 | 0.3s | 交叉溶解 | 片段 4→5 |
| 直接切换 | 0s | 硬切 | 片段 5 结尾 |

---

## 完整 Prompt 示例（30s 抖音广告）

### 片段 1：钩子
```
static shot, slow push in, young Asian woman looking at phone with frustrated expression, confused and stressed, sitting on couch in messy room, phone screen shows error message, natural indoor lighting, realistic lifestyle style, tense mood, grab attention in first 3 seconds, 9:16 vertical
```

### 片段 2：转折
```
handheld tracking shot, same person from reference image, young Asian woman turns and picks up wireless earbuds, eyes light up with surprise, earbuds displayed in hand, warm golden lighting, lifestyle photography style, emotional turning point, 9:16 vertical
```

### 片段 3：展示
```
camera orbit around wireless earbuds, earbuds placed on wooden desk, fingers operating touch controls, close-up on product details, soft product lighting, commercial photography style, highlight active noise cancellation feature, same person from reference image, 9:16 vertical
```

### 片段 4：结果
```
slow pull back, same person from reference image, young Asian woman smiling with satisfaction, wireless earbuds placed beside, peaceful expression, warm golden hour lighting, emotional climax, close-up on happy face, 9:16 vertical
```

### 片段 5：CTA
```
static wide shot, wireless earbuds beautifully placed on clean white surface, brand logo appears subtly in corner, clean and bright, product photography style, brand confirmation, call to action, 9:16 vertical
```

---

## 注意事项

1. **每个片段独立生成**，不要试图一次性生成全片
2. **固定参考图**：每个片段都引用同一张 `character_ref.png`
3. **固定种子**：全片使用同一 seed 值，保持风格一致
4. **光线逻辑统一**：所有片段保持相同的光线方向（如右前方主光）
5. **动作衔接设计**：前一镜结尾动作与后一镜开头动作要连续
6. **时长控制**：单片段不超过可灵单次生成上限（建议 5-8s）
