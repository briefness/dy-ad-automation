# CLI 参数参考

以运行时帮助为准：

```bash
python one_click_create.py --help
```

## 基础参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--style` | `auto` | 智能匹配电影风格；也可指定风格或使用 `none` |
| `--duration` | `5` | 可灵单片段生成时长，单位为秒 |
| `--mode` | `pro` | `std`、`pro` 或 `4k` |
| `--aspect-ratio` | `9:16` | 输出画面比例 |
| `--target-duration` | 智能决定 | `10`、`15`、`20`、`25`、`30` 或 `60` 秒 |
| `--rhythm-style` | `moderate` | `fast`、`moderate` 或 `cinematic` |
| `--video-style` | `auto` | 带货、个人 vlog、种草、测评、开箱或自定义文本 |
| `--output-name` | 自动生成 | 指定输出名前缀并支持稳定资产复用 |
| `--resume` / `--no-resume` | 开启 | 控制断点续跑和已生成资产复用 |

本地素材模式中，`--target-duration` 是受可用素材约束的规划偏好；不传时使用智能自然时长。可灵模式中，它用于节奏模板规划。

## 画面来源

| 参数 | 说明 |
| --- | --- |
| `--local-assets FOLDER` | 使用本地视频目录，跳过可灵画面生成 |
| `--reference-video PATH` | 分析同产品参考广告的可观察结构和节奏 |
| `--product-image PATH` | 商品参考图路径或 URL |
| `--allow-no-product-image` | 允许调试或非商品视频跳过商品参考图 |

`--local-assets` 与可灵画面生成链路互斥。参考视频只提供结构、语气和可观察机制，不提供未经独立验证的产品事实。

## 一致性与生成质量

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--image-fidelity` | `0.9` | 商品参考图 fidelity，范围 `[0,1]` |
| `--human-fidelity` | `0.9` | 人物参考图 fidelity，范围 `[0,1]` |
| `--seed` | 自动 | 随机种子基准，各分镜自动递增 |
| `--best-of` | `1` | 每个分镜最多候选数；首条不达标时补候选 |
| `--quality-frames` | `12` | 候选质量评估抽帧数 |
| `--keep-candidates` | 关闭 | 保留未选中的候选片段 |
| `--min-clips` | `3` | 可灵模式最少成功片段数 |
| `--max-workers` | `4` | 并行生成最大线程数 |
| `--stabilize` / `--no-stabilize` | 开启 | 视频稳定化和去闪烁 |

## 脚本、钩子与口播

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--hook` | `question` | 钩子类型；用 `--list-hooks` 查看全部 |
| `--script-style` | `pain_point_solution` | 脚本风格；用 `--list-script-styles` 查看全部 |
| `--voiceover` | 关闭 | 启用口播配音 |
| `--voiceover-style` | `standard` | `standard`、`emotional`、`energetic`、`professional` 或 `storytelling` |
| `--voice` | `auto` | 自动选音色或指定预设；用 `--list-voices` 查看全部 |
| `--ab-versions` | `1` | A/B 版本数量，范围 1-3 |
| `--ab-dim` | 未指定 | `hook`、`style` 或 `script` |

带货风格会映射到 `demonstration` 脚本、`energetic` 口播和相应节奏；本地素材流程仍会使用证据合同限制具体主张。

## 贴图与输出

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--stickers` | `auto` | `auto`、`on` 或 `off` |
| `--dual-output` | 关闭 | 同时导出 9:16 和 16:9 |
| `--brand-intro-outro` | 关闭 | 添加品牌开场和收尾动画 |

`auto` 只在带货/转化风格或对应营销配置下自动启用贴图。即使使用 `on`，缺少证据或安全展示区域的候选也会被跳过。

## 可灵模式参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--serial` | 关闭 | 串行生成，并使用上一段尾帧增强连续性 |
| `--kling-model` | 配置值 | 指定可灵模型版本 |
| `--multi-shot` | 关闭 | 启用可灵多镜头模式 |
| `--preflight-keyframe` / `--no-preflight-keyframe` | 开启 | 视频生成前执行首帧预检 |
| `--image-first` / `--no-image-first` | 开启 | 关键分镜先生成图片候选再生成视频 |
| `--image-first-mode` | `standard` | `minimal`、`standard` 或 `full` |
| `--image-first-variants` | `2` | 每个关键分镜的图片候选数 |

这些参数不改变本地素材模式的画面来源。

## 执行控制

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--preview` / `-p` | 关闭 | 只生成第一段，并保留字幕、口播和 BGM 等完整后期 |
| `--strict` / `--no-strict` | 开启 | 关键步骤失败时阻断，不静默降级 |
| `--force` | 关闭 | 跳过 high 风险合规拦截；critical 始终阻断 |
| `--no-llm` | 关闭 | 禁用 LLM 并使用模板；本地素材带货流程不支持 |
| `--manual` | 关闭 | 手动填写产品字段，不使用主题自动展开 |
| `--save TEMPLATE.json` | - | 保存参数模板 |
| `--load TEMPLATE.json` | - | 从模板加载参数并跳过对应交互 |

## 查询命令

```bash
python one_click_create.py --list-styles
python one_click_create.py --list-hooks
python one_click_create.py --list-script-styles
python one_click_create.py --list-voices
```
