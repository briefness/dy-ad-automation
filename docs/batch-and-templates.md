# 批量任务与模板

## 批量生成

从示例开始：

```bash
cp examples/sample_batch.yaml batch.yaml
python batch.py --config batch.yaml
```

覆盖并发数：

```bash
python batch.py --config batch.yaml --concurrent 2
```

YAML 的主要结构：

```yaml
default_style: auto
default_duration: 5
default_mode: pro
default_aspect_ratio: "9:16"

concurrent: 1
fail_fast: true
output_dir: output/batch

tasks:
  - product_name: 示例产品
    product_type: 食品
    selling_point: 已核验卖点
    audience: 目标用户
    product_image: /path/to/product.png
```

任务字段优先级为：单任务配置 → 顶层 `default_*` 配置 → 项目默认值。并发任务使用稳定且唯一的输出名，避免同名任务覆盖。

完整可用字段以 [examples/sample_batch.yaml](../examples/sample_batch.yaml) 和 `batch.py` 的运行时校验为准。

## 参数模板

加载模板：

```bash
python one_click_create.py --load examples/sample_template.json
```

保存当前配置：

```bash
python one_click_create.py --save templates/my-template.json
```

加载后另存：

```bash
python one_click_create.py \
  --load examples/sample_template.json \
  --save templates/my-template-v2.json
```

显式 CLI 参数应优先于智能推荐。模板中的 `target_duration` 会被视为调用方明确配置，而不是当前素材自动推荐。
