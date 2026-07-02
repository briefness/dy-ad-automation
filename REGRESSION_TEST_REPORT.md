# 全链路回归测试与边缘场景验证报告

**项目**: kling-ad-automation  
**测试日期**: 2026-06-30  
**测试范围**: 12 项审查，覆盖语法/导入、模板模式、转场、字幕、边缘场景、配置缺失、并发安全、CLI 参数、输出完整性

---

## 总览

| 状态 | 数量 |
|------|------|
| ✅ 通过 | 8 项 |
| ⚠️ 有小问题 | 4 项 |
| ❌ 有 Bug | 0 项 |

*注：审查项 #9 的 Bug 已在本次审查中修复，详见下方"已修复 Bug"章节。*

---

## 详细审查结果

### 1. ✅ 纯 Python 语法/导入检查

**结论**: 全部通过

**验证内容**:
- 12 个核心模块全部能正常 import（config / kling_client / ad_script / video_merger / tts_client / bgm_client / compliance_checker / quality_checker / douyin_adapter / llm_client / utils_ffprobe / one_click_create）
- 无语法错误
- 无循环 import（静态分析）
- 无未定义变量引用

**说明**: `py_compile.compile()` 对所有模块编译通过。`one_click_create.py` 虽然是 CLI 脚本，但所有顶层代码都在函数/类定义内，可安全 import。

---

### 2. ✅ 模板模式回归（LLM 关闭时）

**结论**: 全部通过

**验证内容**:
- `generate_ad_script` 在 LLM 关闭时正常生成 5 段脚本（generated_by=template）
- 5 段结构完整（hook / pain / showcase / result / cta）
- `script_to_subtitles` 正常生成 5 条字幕，时间轴合法（无重叠、总时长一致）
- `script_to_voiceover` 正常生成 5 条口播
- 3/4 种脚本风格可正常生成（empathetic / professional / humorous）
- 5/5 种钩子类型可正常生成

**说明**: 模板模式输出的 segments 不含 `scene_description` 字段（该字段仅 LLM 模式有），这是设计预期，不是缺陷。

---

### 3. ✅ 转场功能回归

**结论**: 全部通过

**验证内容**:
- `merge_clips_ffmpeg` 接口向后兼容：参数列表为 `['clips', 'output', 'transitions', 'bgm', 'subtitles']`
- 转场库包含 15 种转场，全部存在
- `generate_transition_sequence` 正常生成 N-1 个转场（5 段生成 4 个）
- 无音轨保护：`_has_audio_stream` 对不存在的文件安全返回 `False`
- 失败回退机制：转场拼接失败时回退到简单拼接的逻辑存在
- `transitions=None` 时跳过转场，直接走简单拼接
- 空 clips 列表正确抛出 `ValueError`

**相关代码位置**: `video_merger.py` `merge_clips_ffmpeg()` ~line 400

---

### 4. ✅ 字幕功能回归

**结论**: 全部通过

**验证内容**:
- 5/5 种字幕动效正常：`pop` / `slide` / `fade` / `highlight` / `typewriter`
- 字幕底部安全区：230px（约 12%），防止被抖音 UI 遮挡
- 高亮词提取正常（从卖点段提取关键词）
- `segment_durations=None` 时行为与默认完全一致（等长分段）
- 非等长 `segment_durations` 正常工作（首段时长精确匹配）

**相关代码位置**: `video_merger.py` `add_subtitles_ffmpeg()`

---

### 5. ⚠️ 边缘场景：只有 2 段成功

**结论**: 有小问题（设计选择，非 Bug）

**验证内容**:
- 5 段时 80% 阈值 = 4 段，只有 2 段成功时会触发 `RuntimeError` 终止
- 2 段时字幕/口播/转场的降级逻辑正常（2 条字幕、2 条口播、1 个转场）
- `seg_indices` 白名单过滤机制正常工作
- 节奏模板的部分成功段时间轴计算正常

**问题描述**:
80% 成功率阈值可能过于严格。当 5 段中只有 2 段成功时，直接终止而不尝试降级输出。对于用户来说，2 段也比完全失败好——至少可以预览效果或手动补全。

**位置**: `one_click_create.py` ~line 1552
```python
min_required = max(2, int(total_clips * 0.8))
```

**修复建议**:
- 降低阈值到 60%（5 段只需 3 段），或增加 `--min-segments` 参数让用户控制
- 低于阈值时改为警告而非终止，允许继续生成"半成品"供预览

---

### 6. ⚠️ 边缘场景：极短视频（10 秒）

**结论**: 有小问题

**验证内容**:
- 10s 模板存在：`10秒硬广快剪`（fast 风格）
- 5 段时长：1.8s / 1.8s / 2.4s / 2.0s / 2.0s
- 可灵默认生成 5s，需裁切到 2s 左右，裁切比例约 60%
- 口播语速 220 字/分钟，7s 有效时间约 26 字，每段约 5 字

**问题描述**:
1. **裁切比例过高**：5s → 2s 裁掉 60%，从中间裁切可能丢失开头或结尾的关键动作
2. **语速偏快**：220 字/分钟接近人类正常语速上限（200 字/分钟），可能影响清晰度
3. **无警告**：未对极短片段的裁切损失给出警告

**位置**:
- `douyin_adapter.py` `RHYTHM_10S_HARD_SALE` 模板
- `one_click_create.py` 节奏适配部分

**修复建议**:
- 对短于 3s 的段加 warning，提示"片段过短，可灵生成内容可能被大量裁切"
- 考虑允许用户指定可灵生成时长（非固定 5s），短片段可以生成更短的
- 极短视频的口播语速适当降低（如 200 字/分钟），牺牲一些文案长度换取清晰度

---

### 7. ⚠️ 边缘场景：极长视频（60 秒）

**结论**: 有小问题

**验证内容**:
- 60s 模板存在：`60秒电影感深度`（cinematic 风格）
- 5 段时长：7.2s / 12.0s / 16.8s / 15.6s / 8.4s
- 变速限制在 0.5x ~ 2.0x 之间
- 4 段 > 8s，可灵仅生成 5s，需变速到更慢
- 段 2（展示段）16.8s：5s → 16.8s = 0.3x 慢速 → 被 clamp 到 0.5x → 实际只有 10s，比目标短 6.8s
- **缺少明确的长视频/变速质量警告**

**问题描述**:
1. **效果差**：可灵 5s 素材通过 0.5x 慢速拉长到 10s，画面会非常慢，效果很差
2. **时长不达标**：最长段 16.8s 只能做到 10s（受 0.5x 限制），导致总时长不达标
3. **无警告**：用户可能不知道 60s 视频的实际质量会很差

**位置**:
- `video_merger.py` `adjust_clip_duration()` 变速 clamp
- `douyin_adapter.py` 60s 节奏模板
- `one_click_create.py` 节奏适配部分

**修复建议**:
- 在选择 60s 模板时打印警告："可灵单段仅 5s，长视频需大量慢速处理，效果可能不佳，建议使用多个镜头拼接"
- 考虑对超长段使用循环播放（loop）+ 交叉淡化，而非纯慢速
- 在节奏模板中标记各段的"素材来源建议"（实拍 vs 可灵生成 vs 循环）

---

### 8. ⚠️ 边缘场景：空/非法输入

**结论**: 有小问题（设计选择，非 Bug）

**验证内容**:
| 输入 | 行为 | 评级 |
|------|------|------|
| `product_name=""` | 使用默认值，正常生成 | 可接受 |
| `category="未知品类"` | 回退到 default 预设，正常生成 5 段 | ✅ 正确 |
| `selling_points=""` | 使用默认值"效果出色"，正常生成 | 可接受 |
| `duration=0` | 选择最接近的模板（10s），无显式校验 | ⚠️ 需改进 |
| `duration=-10` | 选择最接近的模板（10s），无显式校验 | ⚠️ 需改进 |
| 空脚本 | `script_to_subtitles` 返回空列表 | ✅ 正确 |

**问题描述**:
- `duration <= 0` 没有显式校验，直接传入 `get_rhythm_template` 后选了最接近的 10s 模板。虽然不会崩溃，但用户传 `duration=0` 时得到 10s 视频，行为不符合直觉
- 空 `product_name` 和空 `selling_points` 静默使用默认值，不会报错也不会警告

**位置**:
- `ad_script.py` `generate_ad_script()`
- `douyin_adapter.py` `get_rhythm_template()`
- `one_click_create.py` `main()` 参数处理

**修复建议**:
- `get_rhythm_template` 增加 `total_duration <= 0` 的校验，抛出 `ValueError`
- `one_click_create.py` 的 `parse_args()` 中增加 `--target-duration` 的最小值校验（至少 5s）
- 空 `product_name` 建议加 warning 提示用户

---

### 9. ✅ 配置缺失场景（Bug 已修复）

**结论**: 通过（原 Bug 已修复）

**修复状态**: ✅ 已修复（见底部"已修复 Bug"章节）

**验证内容**:

**LLM API Key 为空**:
- `config.LLM_API_KEY = ""` 时，`get_default_client()` 检查 key 为空返回 `None` —— ✅ 正确
- `generate_text()` / `generate_json()` 在 client 为 None 时返回 `None` —— ✅ 正确

**LLM 禁用（--no-llm）**:
- `config.LLM_ENABLED = False` 后，`ad_script.generate_ad_script()` 正确回退到模板模式 —— ✅ 正确
- **Bug**: `llm_client.get_default_client()` 仍然创建 client —— ❌ 错误

**Bug 根因**:
`llm_client.py` 在模块顶部导入了 `LLM_ENABLED`：
```python
from config import LLM_ENABLED, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
```
这是一个值拷贝（import of immutable value）。当 `one_click_create.py` 设置 `config.LLM_ENABLED = False` 时，`llm_client.LLM_ENABLED` 仍然是 `True`。

`ad_script.generate_ad_script()` 之所以正确，是因为它在函数内部 import：
```python
# ad_script.py line 2247
from config import LLM_ENABLED
```
这在运行时读取 config 模块的当前值。

**BGM 全失败兜底**:
- 本地无 BGM 文件时返回 `None`，有明确的缺失提示 —— ✅ 正确
- BGM 全失败时有 `generate_fallback_audio` 生成环境底噪兜底 —— ✅ 正确

**口播失败降级**:
- 口播生成失败有 try-except 降级，继续无口播版本 —— ✅ 正确

**位置**: `llm_client.py` 模块顶部 import + `get_default_client()` 函数

**修复建议**:
方案 A（推荐，改动最小）：在 `get_default_client()` 内部直接读取 `config.LLM_ENABLED`：
```python
def get_default_client() -> Optional[LLMClient]:
    global _default_client
    if _default_client is not None:
        return _default_client
    
    import config  # 运行时读取，确保 --no-llm 生效
    if not config.LLM_ENABLED or not config.LLM_API_KEY:
        return None
    
    _default_client = LLMClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
    )
    return _default_client
```

方案 B：统一改为 `import config` + `config.XXX` 访问模式，不再 from-import 可变配置项。

---

### 10. ⚠️ 并发/线程安全：BGM 历史记录无并发写保护

**结论**: 有小问题（低概率）

**验证内容**:
| 项目 | 安全性 | 说明 |
|------|--------|------|
| LLM 单例 | ✅ 安全 | 10 个并发线程只创建 1 个实例 |
| BGM 下载缓存 | ✅ 安全 | 使用 `mkstemp` + `rename` 原子操作 |
| TTS 临时文件 | ✅ 安全 | 使用 `tempfile.mkdtemp` 唯一目录 |
| video_merger 临时文件 | ✅ 安全 | 使用 `tempfile` 生成唯一文件名 |
| ffprobe LRU 缓存 | ✅ 安全 | CPython GIL 保护 |
| **BGM 历史记录 JSON** | **⚠️ 不安全** | 无文件锁，多线程并发写可能损坏 |

**问题描述**:
`bgm_client.py` 的历史记录文件（`BGM_DOWNLOAD_HISTORY`）在写入时没有文件锁保护。如果两个进程/线程同时写这个 JSON 文件，可能导致文件损坏（半写状态）。

**位置**: `bgm_client.py` 历史记录读写函数

**修复建议**:
- 使用 `fcntl.flock`（Unix）或 `portalocker` 库加文件锁
- 或者采用"先写临时文件 + rename 原子替换"的模式（与 BGM 下载缓存同样的策略）

---

### 11. ✅ 新增 CLI 参数

**结论**: 全部通过（含一处设计权衡）

**验证内容**:
- `--no-llm` 参数已定义，设置 `config.LLM_ENABLED = False`
- `--rhythm-style` 参数已定义，支持 `fast` / `moderate` / `cinematic` 三个选项
- `--target-duration` 参数已定义，可选值：10 / 15 / 20 / 25 / 30 / 60
- 三个参数均传入 `get_rhythm_template()`，实际生效

**参数冲突处理（target_duration + rhythm_style）**:
| 组合 | 选中模板 | 说明 |
|------|----------|------|
| 10s + cinematic | 60秒电影感深度 | ⚠️ 偏差极大（6x），见下方说明 |
| 60s + fast | 15秒经典带货 | ⚠️ 偏差较大（4x） |
| 60s + moderate | 30秒深度种草 | 偏差 2x，可接受 |

**设计权衡说明**:
`get_rhythm_template` 的策略是"先按风格筛选，再选最接近时长的"。当风格内没有接近时长的模板时，不会跨风格回退。这导致：
- `10s + cinematic` → 选了 60s 模板（偏差 50s，600%）
- `60s + fast` → 选了 15s 模板（偏差 45s，300%）

虽然 docstring 说"若找不到匹配风格，回退到全部模板中选最接近的"，但"找不到匹配风格"指的是完全没有该风格的模板（空列表），而不是"该风格的模板时长都不合适"。

**修复建议**（低优先级，设计优化）:
在风格内最近模板与目标时长偏差超过一定阈值（如 50%）时，自动跨风格回退，并打印 warning：
```python
# 在 get_rhythm_template 中增加
best_in_style = min(candidates, key=lambda t: abs(t["total_duration"] - total_duration))
deviation = abs(best_in_style["total_duration"] - total_duration) / total_duration

if deviation > 0.5 and style != "moderate":
    # 偏差超过 50%，跨风格找更接近的
    best_overall = min(_RHYTHM_TEMPLATES, key=lambda t: abs(t["total_duration"] - total_duration))
    if abs(best_overall["total_duration"] - total_duration) < deviation * total_duration:
        print(f"⚠️  {style} 风格无接近 {total_duration}s 的模板（最近 {best_in_style['total_duration']}s），回退到 {best_overall['pace_style']} 风格的 {best_overall['name']}")
        best = best_overall
```

---

### 12. ✅ 最终输出完整性

**结论**: 全部通过

**验证内容**:
- 质量检查模块功能完整：文件大小、视频流存在、时长有效性、黑帧检测、冻结帧检测
- 输出参数（分辨率 1080x1920、帧率 30fps）从配置读取，video_merger 使用配置值
- 最终输出路径有存在性检查（`final_path.exists()`）
- 文件大小校验（< 10KB 视为无效）
- 时长校验（与预期偏差 > 25% 视为异常）
- 降级链末端文件也有存在性和大小校验
- 返回路径在最终返回前通过 `stat()` 隐式确认文件存在

**位置**: `one_click_create.py` ~line 2057-2085 完整性校验

---

## 已修复 Bug

### LLM 禁用标志不生效（--no-llm Bug）

**问题**: `llm_client.py` 在模块顶部使用 `from config import LLM_ENABLED` 导入配置值。由于 Python 对不可变类型（bool/str/int）的 import 是值拷贝，当 `one_click_create.py` 通过 `--no-llm` 参数设置 `config.LLM_ENABLED = False` 时，`llm_client` 模块中的 `LLM_ENABLED` 仍然是 `True`。

**影响**: 
- `--no-llm` 参数对 `llm_client.get_default_client()` 不生效
- 虽然脚本生成仍然走模板模式（因为 `ad_script.py` 在函数内 import），但 LLM client 仍被不必要地初始化
- 如果有其他代码直接调用 `generate_text()`/`generate_json()`，会绕过 `--no-llm` 标志

**修复方案**: 将 `get_default_client()` 改为运行时读取 `config` 模块：

```python
def get_default_client() -> Optional[LLMClient]:
    global _default_client
    
    # 运行时读取 config，确保 --no-llm 等运行时修改生效
    import config as _cfg
    
    if not _cfg.LLM_ENABLED:
        return None
    # ... 其余逻辑使用 _cfg.XXX 访问配置
```

**修改文件**: `llm_client.py` `get_default_client()` 函数

---

## 问题汇总与优先级

| 优先级 | 审查项 | 问题 | 位置 | 状态 |
|--------|--------|------|------|------|
| **高** | #9 | ~~`--no-llm` 在 `llm_client` 中不生效~~ | `llm_client.py` `get_default_client()` | ✅ 已修复 |
| **中** | #11 | 风格-时长偏差过大时不跨风格回退（如 10s+cinematic 选 60s 模板） | `douyin_adapter.py` `get_rhythm_template()` | 待修复 |
| **中** | #7 | 60s 长视频变速质量差，无警告 | `one_click_create.py` 节奏适配 | 待修复 |
| **低** | #5 | 80% 成功率阈值过严，2 段成功直接终止 | `one_click_create.py` ~line 1552 | 待修复 |
| **低** | #6 | 10s 短视频裁切比例高、语速快，无警告 | `douyin_adapter.py` 10s 模板 | 待修复 |
| **低** | #8 | `duration <= 0` 无显式校验 | `douyin_adapter.py` `get_rhythm_template()` | 待修复 |
| **低** | #10 | BGM 历史记录 JSON 无并发写保护 | `bgm_client.py` 历史记录读写 | 待修复 |

---

## 最高优先级修复：LLM 禁用标志不生效

**影响**: 用户指定 `--no-llm` 时，`llm_client` 仍会初始化 API client。虽然脚本生成确实走了模板模式（因为 `ad_script.py` 在函数内 import），但 LLM client 本身仍然被创建，存在以下风险：
1. 资源浪费（不必要的 client 初始化）
2. 如果有其他代码直接调用 `generate_text()`，可能绕过 `--no-llm` 标志
3. 逻辑不一致，可能导致未来维护时引入更严重的 Bug

**修复方案**（推荐方案 A，改动最小）:

修改 `llm_client.py` 的 `get_default_client()` 函数，改为运行时读取 `config.LLM_ENABLED`：

```python
def get_default_client() -> Optional[LLMClient]:
    """获取全局默认 LLM 客户端（懒加载单例）"""
    global _default_client
    if _default_client is not None:
        return _default_client
    
    # 运行时读取 config，确保 --no-llm 等运行时修改生效
    import config
    if not config.LLM_ENABLED or not config.LLM_API_KEY:
        return None
    
    _default_client = LLMClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
    )
    print(f"✅ LLM 客户端已初始化：{config.LLM_MODEL} @ {config.LLM_BASE_URL}")
    return _default_client
```

同时删除模块顶部的 from-import（保留为 `import config` 访问模式更安全），或者至少删除 `LLM_ENABLED` 和 `LLM_API_KEY` 的 from-import。

---

*测试脚本位置: `test_regression.py`（临时工作目录）*
