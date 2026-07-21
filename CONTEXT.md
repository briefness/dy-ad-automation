# Domain Context

## Global Material Capability Pool

An aggregate of the whole local-footage set: available semantic roles, usable duration, visible concepts, actions, and independently trusted product facts. It intentionally contains no selected window IDs, so copy generation cannot turn into shot-by-shot narration.

## Evidence Anchor

A stable identifier and text description for information available to sales copy. An anchor comes from visible video evidence or trusted product facts; candidates cite anchor identifiers instead of relying on product-category keyword lists.

## Marketing Device

The persuasive function performed by a line, such as contrast, curiosity, proof, reason, convenience, reveal, or action. The Material Copy Contract determines which devices fit each narrative intent.

## Buyer Value

The purchase-relevant meaning created from an Evidence Anchor. It explains why the visible or verified information matters to a buyer without adding unsupported factual claims.

## Script Arc

The full sequence of segment copy. A valid arc preserves continuity, adds information between adjacent segments, and progresses from hook through evidence or value to CTA.

## Continuous Narration Contract

The local-material workflow authors one ordered `voiceover_cues` sequence as its only copy source. The pipeline deterministically derives `voiceover_full`, segment `voiceover`, and punctuation-free subtitles from those cues; the complete text is sent in one TTS request while cues only control shot-locked subtitle timing.

The LLM receives the global material capability pool and returns a continuous sales narration split into semantic beats with desired material roles and visual queries. No asset window is bound during writing. The existing global clip planner performs the first real window selection afterward, then writes the chosen window IDs and actual visual requirements back into the script and edit report.

## User Script Quality Policy

Subjective script-quality rules originate only from explicit user feedback tied to a concrete generated video and its saved script. The policy is empty at cold start. Automatic quality scores, LLM judgments, reference-video patterns, and developer-authored heuristics cannot create or promote a user rule. A rule remains provisional until feedback from multiple distinct videos supports it; active rules and their positive and negative examples may rank future candidates but do not become evidence, structure, or duration facts.

## Non-Negotiable Local Video Principles

- 素材是真相来源：视频理解和可信产品资料决定能说什么、何时说，默认用户提供的本地素材与产品相关，但证据强度决定文案职责。
- 爆款参考只迁移创作机制：借鉴钩子、节奏、证据推进、口语风格和 CTA 压力，不复制未经独立核验的具体事实或原句。
- 字幕是带货文案，不是画面解说：素材证据必须转译成观众在意的购买理由，不能逐帧描述画面。
- 全视频只生成一条连续 TTS：字幕和口播共享同一文案源，CTA 属于同一次演绎，不单独拼接口播。
- 禁止低质量兜底：事实证据、结构完整性、真实时长或后期执行不可行时阻断输出，不用模板、硬编码文案或弱效果伪装成功；主观创意质量不由程序预设门槛阻断，只能由使用者真实反馈逐步学习和排序。
- 脚本远程调用必须有共享上限：结构生成、JSON 修复和语义重写共用同一预算；格式损坏可有界重试，网络超时立即停止，禁止开放式修复循环。
