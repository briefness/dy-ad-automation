# 电影感 Prompt 升级深度审查报告

> 审查范围：`cinematic_language.py` + `one_click_create.py` 中的 `apply_cinematic_style()` / `generate_clip_prompts()`
> 审查日期：2026-06-30

---

## 总览评分

| 审查项 | 结论 | 严重问题数 | 次要问题数 |
|--------|------|-----------|-----------|
| 1. cinematic_language.py 质量 | ⚠️ 小问题 | 1 | 3 |
| 2. apply_cinematic_style() 逻辑 | ❌ 有 Bug | 3 | 3 |
| 3. generate_clip_prompts() 集成 | ⚠️ 小问题 | 0 | 3 |
| 4. 实际 Prompt 质量 | ⚠️ 小问题 | 1 | 3 |
| 5. 回归测试 | ✅ 正确 | 0 | 1 |

**严重 Bug 总数：5 个**
**次要问题总数：13 个**

---

## 1. cinematic_language.py 质量

### 1.1 7 大类镜头元素

**结论：✅ 正确**

- 景别 7 项（SHOT_SIZES）：extreme_close_up / close_up / medium_close_up / medium / medium_long / long / extreme_long — 齐全，英文描述准确
- 运镜 11 项（CAMERA_MOVEMENTS）：push_in / pull_out / pan_left / pan_right / tilt_up / tilt_down / orbit_left / handheld / static / zoom_in / tracking — 超出预期，描述准确
- 角度 6 项（CAMERA_ANGLES）：eye_level / low_angle / high_angle / bird_eye / dutch / over_shoulder — 齐全
- 光影 8 项（LIGHTING_STYLES）：golden_hour / studio_soft / rembrandt / backlit / chiaroscuro / neon_night / natural_window / moody_dark — 专业且齐全
- 构图 6 项（COMPOSITIONS）：rule_of_thirds / center / leading_lines / frame_within_frame / negative_space / foreground_blur — 合理
- 景深 3 项（DEPTH_OF_FIELD）：shallow / medium / deep — 齐全
- 胶片 5 项（FILM_LOOKS）：none / 35mm / 16mm / anamorphic / arri — 实用

所有英文描述均为地道专业术语，无语法错误。

### 1.2 10 种深度导演风格

**结论：⚠️ 小问题**

10 种风格：hitchcock / kubrick / spielberg / wong-kar-wai / anderson / nolan / scorsese / denis-villeneuve / koreeda / tarantino

**优点：
- 每种风格均包含完整字段：visual_key / lighting / color_grade / film_look / shot_size_preference / movement_preference / angle_preference / composition_preference / dof_preference / rhythm + 向后兼容字段
- visual_key 各具特色，区分度高
- color_grade 描述具体且有辨识度

**问题 1（Bug）：koreeda 的 `movement_preference` 包含无效 key `slow pan`**
- 位置：`cinematic_language.py` 第 417 行
- `movement_preference: ["static", "tracking", "slow pan"]
- `slow pan` 不是 `CAMERA_MOVEMENTS` 的有效 key（应该是 `pan_left` 或 `pan_right`
- 后果：当 koreeda 风格且段落运镜不在其偏好列表中时，取第一偏好 `static` 没问题，但如果需要取第三偏好就会返回空字符串（虽然实际场景中大概率不会走到第三偏好，但仍是隐患
- 修复建议：将 `slow pan` 改为 `pan_left` 或 `pan_right`

**问题 2（次要）：3 对风格偏好维度有 3/5 重合
- kubrick vs denis-villeneuve：lighting (moody_dark) + dof (deep) + film_look (arri/35mm差异）— 都是宏大/冷峻风格，可接受
- spielberg vs scorsese：medium shot + push_in + eye_level — 都是经典好莱坞风格，可接受
- anderson vs koreeda：medium / medium_long / static / eye_level — 都是中景静态观察风格，可接受

结论：偏好首选项虽有重叠，但 visual_key 和 color_grade 差异巨大，整体风格区分度是够的。这不是 bug，只是观察。

### 1.3 SEGMENT_CINEMATIC_PROFILE 分镜节奏曲线

**结论：✅ 正确**

5 段强度曲线：[10, 5, 8, 9, 6]
- hook: intensity=10（最强）
- turning_point: intensity=5（最弱）
- showcase: intensity=8
- result: intensity=9
- cta: intensity=6

节奏合理：Hook 抓眼球 → 中段平实 → 展示回升 → 结果高潮 → CTA 稳定

但注意：intensity 目前只是标签值，未被实际使用来控制电影感元素的多少（详见第 3.2 节）。

### 1.4 build_cinematic_prompt_elements() 逻辑

**结论：⚠️ 小问题**

融合逻辑分析：

- 景别：段落景别在风格偏好里则用段落的，否则用风格第一偏好 ✅
- 运镜：同上 ✅
- 角度：直接用风格第一偏好（完全忽略段落配置）⚠️
- 光影：直接用风格定义的 lighting key（完全忽略段落配置）⚠️
- 构图：直接用风格第一偏好（完全忽略段落配置）⚠️
- 景深：直接用风格偏好（完全忽略段落配置）⚠️
- 胶片：用风格 film_look ✅

**问题 3（次要）：角度/光影/构图/景深 完全忽略段落配置**
- 位置：`cinematic_language.py` 第 520-534 行
- 景别和运镜有"段落优先，风格兜底"的融合逻辑，但角度、光影、构图、景深直接取风格值
- 这意味着段落配置中的 `camera_angle` / `lighting` / `composition` / `dof` 字段形同虚设
- 修复建议：统一融合逻辑，保持与景别/运镜一致的策略

**问题 4（次要）：visual_key 被返回但不被使用**
- 位置：`cinematic_language.py` 第 548 行
- build 函数返回了 `visual_key`，但 `apply_cinematic_style()` 中从未使用
- 10 个风格的 visual_key 是死字段
- 修复建议：要么在 Prompt 中使用 visual_key，要么移除返回值

---

## 2. apply_cinematic_style() 升级后逻辑

### 2.1 深度风格和旧风格路径判断

**结论：✅ 正确**

路径判断逻辑（`one_click_create.py` 第 761-843 行）：
1. `style_key == "none"` → 直接返回 base_prompt ✅
2. 在 `DEEP_CINEMATIC_STYLES` 中 → 走深度路径 ✅
3. 在 `CINEMATIC_STYLES` 中但不在深度库 → 走旧路径 ✅
4. 都不在 → 返回 base_prompt ✅

8 个仅在旧库的风格（aronofsky / bong-joon-ho / hou-hsiao-hsien / jia-zhangke / luc-besson / miyazaki / tarkovsky / zhang-yimou）仍可正常工作。

### 2.2 Prompt 结构：9 元素 vs 实际结构

**结论：⚠️ 小问题**

代码注释说 "9 个元素"，但实际结构是 6 大部分：
1. 风格名 + 景别 + 运镜 + signature + 角度
2. 主体内容（从 base_prompt 提取）
3. 光影
4. 构图 + 景深
5. 胶片质感
6. 色彩调性

build 函数返回 9 个字段（shot_size / camera_movement / camera_angle / lighting / composition / dof / film_look / color_grade / visual_key），但 visual_key 未被使用。实际拼接顺序合理：先镜头语言 → 主体 → 光影 → 构图景深 → 胶片 → 色彩。顺序符合视频生成模型的理解习惯。

### 2.3 重复描述问题

**结论：❌ Bug**

**Bug 1（严重）：static 段 + 动态 signature 运镜冲突**

- 位置：`one_click_create.py` 第 786-789 行
- 当段落运镜是 static（如 turning_point 和 cta），但 signature_camera_move 是从 clip_type 对应的 camera_push/pull/orbit 提取的动态描述
- 例子：hitchcock + turning_point + clip_type=push
  - build 出来的运镜：`locked-off static shot`
  - signature：`dolly zoom`
  - 结果：同时说"固定镜头"和"推轨变焦"，语义矛盾
- 影响范围：所有 static 段（turning_point、cta）+ 所有深度风格
- 根本原因：`generate_clip_prompts()` 中 clip_type 是从 `camera` 字段映射来的（push→push, pull→pull, orbit→orbit, static→push），static 被映射为 push 导致 signature 取 push 的描述
- 修复建议：如果运镜是 static 时，跳过 signature_camera_move，或从风格中添加 static 的 signature

**Bug 2（严重）：foreground_blur + shallow dof 重复**

- 位置：`cinematic_language.py` + `one_click_create.py`
- `foreground_blur` 的描述包含 `"foreground blur, shallow depth of field`
- `shallow` dof 的描述包含 `"shallow depth of field, creamy bokeh, f/1.4"`
- wong-kar-wai 风格同时使用 foreground_blur（构图 + shallow dof → 重复 "shallow depth of field"
- 虽然 `_deduplicate_phrases()` 会按逗号分隔去重，但 "shallow depth of field" 在两个不同短语里，无法去重不了
- 修复建议：将 foreground_blur 改为只说 "foreground element blur"，去掉 "shallow depth of field"

**Bug 3（中等）：signature_camera_move 截断导致语法不完整的短语**

- 位置：`one_click_create.py` 第 889-953 行 `_get_signature_camera_move()`
- 从冒号后提取第一个短语，限制 6 个词，但有些短语被截断在介词上
- 受影响的风格：
  - kubrick.pull: `camera pulls back from subject to`（以 to 结尾）
  - nolan.push: `IMAX push in`（以 in 结尾，虽不完整但勉强可接受）
  - scorsese.pull: `camera pulls back from subject in`（以 in 结尾）
  - denis-villeneuve.push: `Villeneuve push in`（以 in 结尾）
  - koreeda.pull: `pull back from family moment to`（以 to 结尾）
- 修复建议：增加介词结尾检测，如果最后一个词是介词（to/from/of/in/on/with/at/by/for），则多取一个短语或调整截断逻辑

### 2.4 主体内容淹没问题

**结论：✅ 基本合理**

测试结果：
- 电影感相关词约占 20%
- 主体相关词约占 13%
- 一致性描述约占 10-15%

比例合理，主体没有被淹没。但需注意：真实场景下 base_prompt 中还残留 `close-up on face`、`close-up on product details` 等镜头描述，与深度风格注入的景别描述可能产生"景别信息双重出现。

### 2.5 narrative 参数传递

**结论：✅ 正确**

- `apply_cinematic_style(base_prompt, style_key, clip_type, narrative="hook")`
- 默认值 `hook`，不传也能正常工作
- `generate_clip_prompts()` 中从 `clip_def.get("narrative", "hook")` 读取并传递

### 2.6 向后兼容

**结论：✅ 正确**

- 不传 narrative：✅ 可用（默认 hook）
- 不传 cinematic_style：✅ 可用（默认 "none"）
- 旧风格（仅在 CINEMATIC_STYLES 中）：✅ 走旧路径
- 未知风格：✅ 返回原 Prompt

---

## 3. generate_clip_prompts() 集成

### 3.1 narrative 来源

**结论：✅ 正确**

CLIP_STRUCTURE 中 5 段均有 narrative 字段：
- 段 1: hook
- 段 2: turning_point
- 段 3: showcase
- 段 4: result
- 段 5: cta

`generate_clip_prompts()` 第 1070 行通过 `clip_def.get("narrative", "hook")` 读取，有默认兜底。

### 3.2 电影感强度差异

**结论：⚠️ 小问题**

intensity 参数目前只是标签值，没有实际控制电影感元素的多少/强度。5 段的电影感词密度都在 11-15% 之间，没有明显差异。Hook 段词数多是因为 base_prompt 本身长，而非电影感元素更多。

- 位置：`cinematic_language.py` 中 `SEGMENT_CINEMATIC_PROFILE` 的 `intensity` 字段从未被 `build_cinematic_prompt_elements()` 使用。

修复建议：根据 intensity 动态调整 film_look / color_grade / visual_key 的详略程度，或在高强度段落注入更多元素。

### 3.3 clip_type 映射问题

**结论：⚠️ 小问题**

`generate_clip_prompts()` 第 1071-1076 行的映射：
```python
clip_type = {
    "push": "push",
    "pull": "pull",
    "orbit": "orbit",
    "static": "push",  # ← static 被映射为 push
}.get(camera_type, "push")
```

static 被映射为 push 有两个问题：
1. signature_camera_move 取的是 push 的描述，但段落运镜可能是 static → 冲突（已在 2.3 节详述）
2. 如果将来有新的 camera 类型没有对应关系不明确

修复建议：static 应映射到对应的静态 signature，或在运镜是 static 时跳过 signature

---

## 4. 实际 Prompt 质量

### 4.1 词数统计

**结论：⚠️ 小问题**

50 个 Prompt（10 风格 x 5 段，简单 base_prompt）：
- 平均：67.1 词
- 最短：58 词
- 最长：74 词
- 80-180 区间：0/50 (0%)

实际使用中（真实 base_prompt + 一致性描述）：
- Hook 段：约 120 词左右
- 中段：约 80 词
- CTA 段：约 75 词

简单测试场景下词数偏低，但真实场景下基本达标（80-120 词。离 180 上限还有距离。

### 4.2 语法和通顺性

**结论：✅ 正确**

50 个 Prompt 均无连续逗号、空短语、重复短语等格式问题。

但有 signature 截断导致的语法不完整短语（已在 2.3 节详述）。

### 4.3 画面感

**结论：✅ 良好**

抽样 Prompt 结构清晰，元素丰富：
- 景别 + 运镜 + 角度 → 镜头语言明确
- 光影 + 色彩 + 胶片 → 视觉风格鲜明
- 主体 + 动作 + 场景 → 内容具体

能想象出画面。不同风格差异明显。

### 4.4 一致性与电影感比例

**结论：✅ 合理**

真实场景下：
- 电影感短语：约 11-14 个
- 一致性短语：约 2-5 个
- 主体内容：剩余部分

比例合理，一致性描述没有喧宾夺主。

---

## 5. 回归测试

### 5.1 default (none) 风格

**结论：✅ 正确**

`apply_cinematic_style(base, "none", "push")` 原样返回 base_prompt。

### 5.2 未知风格回退

**结论：✅ 正确**

未知风格原样返回 base_prompt。

### 5.3 旧 CINEMATIC_STYLES 兼容性

**结论：✅ 正确**

- 旧库 18 种风格全部可用
- 10 种已深度化（走新路径）
- 8 种仍走旧路径（aronofsky / bong-joon-ho / hou-hsiao-hsien / jia-zhangke / luc-besson / miyazaki / tarkovsky / zhang-yimou）

### 5.4 负面提示词

**结论：✅ 不受影响**

NEGATIVE_PROMPT 是独立配置，与 apply_cinematic_style 无关联。

### 5.5 单元测试

**结论：✅ 全部通过**

41 个测试全部通过。

**但测试覆盖不足：
- 没有测试深度风格的 Prompt 结构
- 没有测试 narrative 参数
- 没有测试 _extract_subject_from_prompt
- 没有测试 _deduplicate_phrases
- 没有测试 build_cinematic_prompt_elements

---

## 严重 Bug 汇总（按优先级排序）

### Bug 1：static 段运镜与 signature 动态运镜冲突 ⭐⭐⭐⭐⭐

- **位置**：`one_click_create.py` 第 786-789 行 + 第 1071-1076 行
- **现象**：turning_point 和 cta 段运镜是 static，但 signature_camera_move 描述的是 push/pull 动态运镜
- **影响**：所有深度风格下，这两段的 Prompt 同时出现"固定镜头"和"推轨变焦"等矛盾描述
- **修复建议**：
  方案 A（推荐）：在 `apply_cinematic_style()` 中，如果运镜包含 "static" 时，跳过 signature_camera_move
  方案 B：在 `generate_clip_prompts()` 中，static 段不映射为 push，而是映射为一个空值或特定 static signature

### Bug 2：foreground_blur + shallow dof 重复 "shallow depth of field" ⭐⭐⭐⭐

- **位置**：`cinematic_language.py` COMPOSITIONS["foreground_blur"] + wong-kar-wai 风格
- **现象**：foreground_blur 描述包含 "shallow depth of field"，shallow dof 也包含
- **影响**：wong-kar-wai 风格下 Prompt 中重复 "shallow depth of field" 出现两次
- **修复建议**：将 foreground_blur 描述改为 `"foreground element blur, soft out-of-focus foreground"

### Bug 3：koreeda movement_preference 包含无效 key "slow pan" ⭐⭐⭐

- **位置**：`cinematic_language.py` 第 417 行
- **现象**：`slow pan` 不在 CAMERA_MOVEMENTS 的有效 key 列表中
- **影响**：如果需要取第三偏好时返回空字符串（低概率，但仍是隐患
- **修复建议**：改为 `pan_left` 或 `pan_right`

### Bug 4：signature_camera_move 截断导致语法不完整 ⭐⭐⭐

- **位置**：`one_click_create.py` 第 934-945 行
- **现象**：部分 signature 以介词结尾（to/from/in 等），语法不完整
- **影响**：5 个风格的 pull/push 描述有瑕疵
- **修复建议**：增加介词结尾检测，截断在介词则多取一个短语，或调整为取词数限制

### Bug 5：visual_key 死字段 ⭐⭐

- **位置**：`cinematic_language.py` 第 548 行 + `one_click_create.py` apply_cinematic_style
- **现象**：build 函数返回 visual_key，但 apply 中从未使用
- **影响**：10 个风格的 visual_key 是死代码
- **修复建议**：要么在 Prompt 适当位置注入 visual_key（建议放在风格名后或开头），要么移除

---

## 次要改进建议

1. **角度/光影/构图/景深 融合逻辑不统一**：景别和运镜有"段落优先，风格兜底"的融合逻辑，但其他维度直接取风格值，建议统一
2. **intensity 未生效**：intensity 目前只是标签，建议根据强度动态调整电影感元素详略
3. **base_prompt 残留镜头描述**：`close-up on face`、`close-up on product details` 等在主体中残留，与景别描述重复
4. **测试覆盖不足**：缺少深度风格、narrative、_extract_subject_from_prompt 等测试
5. **注释 "9 元素" 与实际 6 大部分不符**：建议更新注释
6. **clip_type static→push 映射不清晰**：建议添加注释说明原因
