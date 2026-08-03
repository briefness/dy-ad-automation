# 可灵 AI 生成与后期

可灵模式根据产品信息、角色和商品参考图生成画面，再进入与本地素材模式共享的后期链路。

## 基本用法

```bash
python one_click_create.py \
  --product-image "/path/to/product.png" \
  --style auto \
  --mode pro
```

发布级商品视频应提供 `--product-image`。仅调试或非商品视频可显式使用 `--allow-no-product-image`。

## 生成过程

1. 根据产品资料生成广告脚本、角色计划和商品视觉约束。
2. 生成或读取角色参考图和商品参考图。
3. 对关键分镜执行图片先行和首帧预检。
4. 调用可灵视频 API 生成分镜；首个候选不达标时按 `--best-of` 补候选。
5. 根据清晰度、主体、产品露出和一致性选择候选。
6. 进入共享的口播、字幕、BGM、转场、调色和质量门禁。

## 电影风格

`--style auto` 会根据品类、脚本和历史观测智能选择风格。也可以显式指定：

```text
hitchcock        kubrick             spielberg
aronofsky        scorsese            nolan
anderson         wong-kar-wai        tarkovsky
zhang-yimou      koreeda             tarantino
jia-zhangke      hou-hsiao-hsien     bong-joon-ho
denis-villeneuve luc-besson          miyazaki
```

使用 `--style none` 关闭电影风格注入，使用 `--list-styles` 查看运行时完整说明。详细参考见 [`references/cinematic_styles/`](../references/cinematic_styles/)。

## 一致性控制

- `--product-image` 约束商品外观和产品露出。
- `--image-fidelity` 控制商品参考图一致性。
- `--human-fidelity` 控制人物参考图一致性。
- `--seed` 提供稳定的随机种子基准。
- `--serial` 让后一段使用前一段尾帧增强连续性。
- `--image-first` 和 `--preflight-keyframe` 在视频生成前验证关键画面。

## 时长限制

`--target-duration` 通过段数和节奏模板规划总时长。单段后期延长仍受可灵原始片段时长和可接受变速范围限制；严格模式会在计划明显超出能力时提前阻断，避免卡顿、截断或字幕错位。

## 共享后期

两种画面模式共享：

- 单条口播和字幕时间轴；
- BGM 选曲、音量闪避和节拍；
- 转场、调色、SFX、稳定化和去闪烁；
- 封面、品牌水印、双比例输出；
- 合规检测和发布质量检测。
