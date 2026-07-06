#!/usr/bin/env python3
"""
工作流编排器（Workflow Orchestrator）

参考行业最佳实践：
- Airflow: Workflow orchestration
- Prefect: Workflow management
- video-use: AI video workflow
- Argo Workflows: Parallel execution

核心特点：
1. 统一管理视频生成全流程
2. 支持步骤间数据传递
3. 支持错误处理和重试
4. 支持并行执行（关键步骤并行加速）
5. 提供完整的进度追踪
6. 智能重试策略（指数退避）
7. 错误恢复和降级处理
8. 实时进度回调
"""

import json
import time
import random
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from threading import Lock


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class ExecutionMode(Enum):
    SERIAL = "serial"
    PARALLEL = "parallel"
    HYBRID = "hybrid"


@dataclass
class RetryPolicy:
    max_retries: int = 3
    initial_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 30.0


@dataclass
class WorkflowStep:
    name: str
    description: str
    function: Callable
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    error: str = ""
    result: Optional[Any] = None
    execution_time: float = 0.0
    retry_count: int = 0
    retry_policy: RetryPolicy = field(default_factory=lambda: RetryPolicy())
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    priority: int = 0
    max_workers: int = 1


@dataclass
class WorkflowContext:
    data: Dict[str, Any] = field(default_factory=dict)
    workflow_id: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: WorkflowStatus = WorkflowStatus.PENDING


class WorkflowOrchestrator:

    def __init__(self, max_workers: int = 4):
        self.steps: Dict[str, WorkflowStep] = {}
        self.context = WorkflowContext()
        self.max_workers = max_workers
        self._lock = Lock()
        self._progress_callbacks: List[Callable] = []

    def add_step(
        self,
        name: str,
        description: str,
        function: Callable,
        inputs: List[str] = None,
        outputs: List[str] = None,
        dependencies: List[str] = None,
        max_retries: int = 0,
        execution_mode: ExecutionMode = ExecutionMode.SERIAL,
        priority: int = 0,
        max_workers: int = 1,
    ):
        self.steps[name] = WorkflowStep(
            name=name,
            description=description,
            function=function,
            inputs=inputs or [],
            outputs=outputs or [],
            dependencies=dependencies or [],
            retry_policy=RetryPolicy(max_retries=max_retries),
            execution_mode=execution_mode,
            priority=priority,
            max_workers=max_workers,
        )

    def add_progress_callback(self, callback: Callable):
        self._progress_callbacks.append(callback)

    def _notify_progress(self, step_name: str, status: StepStatus, progress: float = 0.0):
        for callback in self._progress_callbacks:
            try:
                callback(step_name, status.value, progress)
            except Exception:
                pass

    def run(self, mode: ExecutionMode = ExecutionMode.HYBRID) -> WorkflowContext:
        self.context.start_time = datetime.now()
        self.context.status = WorkflowStatus.RUNNING
        self.context.workflow_id = f"wf_{int(time.time())}"

        print(f"\n🚀 开始工作流: {self.context.workflow_id}")
        print("-" * 60)

        if mode == ExecutionMode.PARALLEL:
            self._run_parallel()
        elif mode == ExecutionMode.HYBRID:
            self._run_hybrid()
        else:
            self._run_serial()

        self.context.end_time = datetime.now()
        all_completed = all(s.status == StepStatus.COMPLETED for s in self.steps.values())

        if all_completed:
            self.context.status = WorkflowStatus.COMPLETED
            status_icon = "✅"
        else:
            self.context.status = WorkflowStatus.FAILED
            status_icon = "❌"

        duration = (self.context.end_time - self.context.start_time).total_seconds()

        print("\n" + "-" * 60)
        print(f"{status_icon} 工作流完成: {self.context.workflow_id}")
        print(f"状态: {self.context.status.value}")
        print(f"总耗时: {duration:.2f}秒")

        print("\n步骤状态汇总:")
        for step_name in self._topological_sort():
            step = self.steps[step_name]
            icon = {
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
                StepStatus.RUNNING: "⏳",
                StepStatus.PENDING: "⏳",
                StepStatus.SKIPPED: "⏭️",
                StepStatus.RETRYING: "🔄",
            }.get(step.status, "❓")
            print(f"  {icon} {step_name}: {step.status.value} ({step.execution_time:.2f}s)")

        return self.context

    def _run_serial(self):
        sorted_steps = self._topological_sort()

        for step_name in sorted_steps:
            step = self.steps[step_name]

            if not self._check_dependencies(step):
                step.status = StepStatus.SKIPPED
                print(f"⏭️ 跳过步骤: {step_name}（依赖未完成）")
                self._notify_progress(step_name, StepStatus.SKIPPED)
                continue

            self._execute_step(step)

    def _run_parallel(self):
        sorted_steps = self._topological_sort()
        available_steps = []
        completed_steps = set()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures: Dict[Future, str] = {}

            while sorted_steps or futures:
                while sorted_steps:
                    step_name = sorted_steps[0]
                    step = self.steps[step_name]

                    if self._check_dependencies(step):
                        available_steps.append(step_name)
                        sorted_steps.pop(0)
                    else:
                        break

                for step_name in available_steps:
                    step = self.steps[step_name]
                    if step.status == StepStatus.PENDING:
                        future = executor.submit(self._execute_step, step)
                        futures[future] = step_name

                available_steps.clear()

                for future in as_completed(futures):
                    step_name = futures[future]
                    del futures[future]

                    step = self.steps[step_name]
                    if step.status == StepStatus.COMPLETED:
                        completed_steps.add(step_name)

                if not available_steps and not futures and sorted_steps:
                    time.sleep(0.5)

    def _run_hybrid(self):
        sorted_steps = self._topological_sort()
        step_groups = self._group_parallel_steps(sorted_steps)

        for group in step_groups:
            if len(group) == 1:
                step_name = group[0]
                step = self.steps[step_name]
                if self._check_dependencies(step):
                    self._execute_step(step)
                else:
                    step.status = StepStatus.SKIPPED
                    print(f"⏭️ 跳过步骤: {step_name}（依赖未完成）")
                    self._notify_progress(step_name, StepStatus.SKIPPED)
            else:
                self._execute_parallel_group(group)

    def _group_parallel_steps(self, sorted_steps: List[str]) -> List[List[str]]:
        groups = []
        current_group = []
        completed_steps = set()

        for step_name in sorted_steps:
            step = self.steps[step_name]

            if self._check_dependencies(step):
                if step.execution_mode == ExecutionMode.PARALLEL or (
                    step.execution_mode == ExecutionMode.HYBRID and current_group
                ):
                    current_group.append(step_name)
                else:
                    if current_group:
                        groups.append(current_group)
                        current_group = []
                    current_group.append(step_name)
            else:
                if current_group:
                    groups.append(current_group)
                    current_group = []
                groups.append([step_name])

        if current_group:
            groups.append(current_group)

        return groups

    def _execute_parallel_group(self, step_names: List[str]):
        print(f"\n🔀 并行执行组: {', '.join(step_names)}")

        def execute_single(step_name: str):
            step = self.steps[step_name]
            if self._check_dependencies(step):
                self._execute_step(step)

        with ThreadPoolExecutor(max_workers=min(len(step_names), self.max_workers)) as executor:
            futures = [executor.submit(execute_single, name) for name in step_names]
            for future in as_completed(futures):
                future.result()

    def _topological_sort(self) -> List[str]:
        in_degree = {name: 0 for name in self.steps}
        graph: Dict[str, List[str]] = {name: [] for name in self.steps}

        for name, step in self.steps.items():
            for dep in step.dependencies:
                if dep in graph:
                    graph[dep].append(name)
                    in_degree[name] += 1

        queue = [name for name in in_degree if in_degree[name] == 0]
        result = []

        while queue:
            queue.sort(key=lambda n: self.steps[n].priority, reverse=True)
            node = queue.pop(0)
            result.append(node)

            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    def _check_dependencies(self, step: WorkflowStep) -> bool:
        for dep_name in step.dependencies:
            dep_step = self.steps.get(dep_name)
            if dep_step and dep_step.status != StepStatus.COMPLETED:
                return False
        return True

    def _execute_step(self, step: WorkflowStep):
        print(f"\n▶️ 执行步骤: {step.name}")
        print(f"   描述: {step.description}")

        step.status = StepStatus.RUNNING
        start_time = time.time()
        self._notify_progress(step.name, StepStatus.RUNNING)

        try:
            kwargs = {}
            for input_key in step.inputs:
                if input_key in self.context.data:
                    kwargs[input_key] = self.context.data[input_key]

            result = step.function(**kwargs)

            if step.outputs:
                if isinstance(result, dict):
                    for output_key in step.outputs:
                        if output_key in result:
                            self.context.data[output_key] = result[output_key]
                else:
                    if step.outputs:
                        self.context.data[step.outputs[0]] = result

            step.result = result
            step.status = StepStatus.COMPLETED
            step.execution_time = time.time() - start_time
            print(f"   ✅ 完成 ({step.execution_time:.2f}s)")
            self._notify_progress(step.name, StepStatus.COMPLETED, 100.0)

        except Exception as e:
            step.error = str(e)
            step.execution_time = time.time() - start_time

            if step.retry_count < step.retry_policy.max_retries:
                self._retry_step(step)
            else:
                step.status = StepStatus.FAILED
                print(f"   ❌ 失败: {e}")
                self._notify_progress(step.name, StepStatus.FAILED)

    def _retry_step(self, step: WorkflowStep):
        step.retry_count += 1
        policy = step.retry_policy

        delay = min(
            policy.initial_delay * (policy.backoff_factor ** (step.retry_count - 1)),
            policy.max_delay,
        )
        delay += random.uniform(0, delay * 0.1)

        print(f"   🔄 重试 {step.retry_count}/{policy.max_retries}，等待 {delay:.2f}s...")
        step.status = StepStatus.RETRYING
        self._notify_progress(step.name, StepStatus.RETRYING)

        time.sleep(delay)
        self._execute_step(step)

    def get_step(self, name: str) -> Optional[WorkflowStep]:
        return self.steps.get(name)

    def get_status(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.context.workflow_id,
            "status": self.context.status.value,
            "start_time": self.context.start_time.isoformat() if self.context.start_time else None,
            "end_time": self.context.end_time.isoformat() if self.context.end_time else None,
            "steps": {
                name: {
                    "status": step.status.value,
                    "description": step.description,
                    "execution_time": step.execution_time,
                    "error": step.error,
                    "retry_count": step.retry_count,
                }
                for name, step in self.steps.items()
            },
            "data_keys": list(self.context.data.keys()),
        }

    def save_workflow(self, output_path: Path):
        data = {
            "workflow_id": self.context.workflow_id,
            "status": self.context.status.value,
            "start_time": self.context.start_time.isoformat() if self.context.start_time else None,
            "end_time": self.context.end_time.isoformat() if self.context.end_time else None,
            "steps": {
                name: {
                    "name": step.name,
                    "description": step.description,
                    "status": step.status.value,
                    "error": step.error,
                    "execution_time": step.execution_time,
                    "retry_count": step.retry_count,
                }
                for name, step in self.steps.items()
            },
            "context_data_keys": list(self.context.data.keys()),
        }

        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_workflow(self, input_path: Path):
        data = json.loads(input_path.read_text(encoding="utf-8"))

        self.context.workflow_id = data.get("workflow_id", "")
        self.context.status = WorkflowStatus(data.get("status", "pending"))

        if data.get("start_time"):
            self.context.start_time = datetime.fromisoformat(data["start_time"])
        if data.get("end_time"):
            self.context.end_time = datetime.fromisoformat(data["end_time"])

    def cancel(self):
        self.context.status = WorkflowStatus.FAILED
        print("\n⏹️ 工作流已取消")


class VideoGenerationWorkflow(WorkflowOrchestrator):

    def __init__(self, product_info: dict, target_audience: str = ""):
        super().__init__(max_workers=4)
        self.product_info = product_info
        self.target_audience = target_audience
        self._init_supporting_modules()
        self._define_steps()

    def _init_supporting_modules(self):
        from asset_library import AssetLibrary
        from feedback_loop import FeedbackLoop
        from experiment_tracker import ExperimentTracker
        from autoprompt_optimizer import AutoPromptOptimizer
        from model_router import ModelRouter
        from smart_decision_engine import run_smart_decision, print_smart_decision_report

        self.asset_library = AssetLibrary()
        self.feedback_loop = FeedbackLoop()
        self.experiment_tracker = ExperimentTracker()
        self.prompt_optimizer = AutoPromptOptimizer()
        self.model_router = ModelRouter()
        self.run_smart_decision = run_smart_decision
        self.print_smart_decision_report = print_smart_decision_report

    def _define_steps(self):
        self.add_step(
            name="需求分析",
            description="分析产品信息和目标人群",
            function=self._step_requirement_analysis,
            outputs=["product_info", "target_audience"],
            priority=100,
        )

        self.add_step(
            name="角色分析",
            description="分析需要的角色数量和类型",
            function=self._step_character_analysis,
            inputs=["product_info"],
            outputs=["character_roles", "character_bibles"],
            dependencies=["需求分析"],
            priority=90,
        )

        self.add_step(
            name="脚本生成",
            description="生成结构化广告脚本",
            function=self._step_script_generation,
            inputs=["product_info", "target_audience"],
            outputs=["ad_script", "storyboard"],
            dependencies=["需求分析"],
            priority=85,
        )

        self.add_step(
            name="节奏分析",
            description="分析脚本节奏和情绪曲线",
            function=self._step_rhythm_analysis,
            inputs=["ad_script"],
            outputs=["rhythm_curve", "beat_timings"],
            dependencies=["脚本生成"],
            priority=80,
        )

        self.add_step(
            name="场景锚点创建",
            description="创建全局场景锚点系统",
            function=self._step_scene_anchor,
            inputs=["ad_script", "product_info"],
            outputs=["scene_anchor"],
            dependencies=["脚本生成"],
            priority=75,
        )

        self.add_step(
            name="参考图生成",
            description="生成角色和商品参考图",
            function=self._step_reference_image_generation,
            inputs=["character_bibles", "product_info"],
            outputs=["reference_images", "character_group"],
            dependencies=["角色分析"],
            priority=70,
            execution_mode=ExecutionMode.PARALLEL,
        )

        self.add_step(
            name="质量门预检",
            description="质量前置控制检查",
            function=self._step_quality_gate,
            inputs=["ad_script", "storyboard", "reference_images", "product_info", "character_bibles"],
            outputs=["quality_gate_result"],
            dependencies=["脚本生成", "参考图生成"],
            priority=65,
        )

        self.add_step(
            name="智能决策",
            description="基于质量门结果的动态决策",
            function=self._step_smart_decision,
            inputs=["quality_gate_result"],
            outputs=["decision_result", "generation_strategy"],
            dependencies=["质量门预检"],
            priority=62,
        )

        self.add_step(
            name="图片先行验证",
            description="生成关键帧图片验证",
            function=self._step_image_first_validation,
            inputs=["storyboard", "reference_images", "generation_strategy"],
            outputs=["keyframe_images", "validation_results"],
            dependencies=["智能决策"],
            priority=60,
            execution_mode=ExecutionMode.PARALLEL,
        )

        self.add_step(
            name="视频片段生成",
            description="生成视频片段（带节奏视觉参数）",
            function=self._step_video_generation,
            inputs=["storyboard", "keyframe_images", "character_group", "reference_images", "rhythm_curve", "scene_anchor", "generation_strategy"],
            outputs=["video_clips"],
            dependencies=["图片先行验证", "节奏分析", "场景锚点创建", "智能决策"],
            max_retries=2,
            priority=55,
            execution_mode=ExecutionMode.PARALLEL,
            max_workers=4,
        )

        self.add_step(
            name="时间一致性检测",
            description="检测视频时间一致性",
            function=self._step_temporal_consistency,
            inputs=["video_clips"],
            outputs=["temporal_analysis"],
            dependencies=["视频片段生成"],
            priority=50,
        )

        self.add_step(
            name="AI视频增强",
            description="增强视频质量",
            function=self._step_ai_enhancement,
            inputs=["video_clips"],
            outputs=["enhanced_clips"],
            dependencies=["视频片段生成"],
            priority=50,
            execution_mode=ExecutionMode.PARALLEL,
            max_workers=4,
        )

        self.add_step(
            name="音频生成",
            description="生成口播和BGM",
            function=self._step_audio_generation,
            inputs=["ad_script", "rhythm_curve"],
            outputs=["voiceover_audio", "bgm_audio", "subtitles"],
            dependencies=["节奏分析"],
            priority=45,
            execution_mode=ExecutionMode.PARALLEL,
        )

        self.add_step(
            name="视频后处理",
            description="拼接、转场、音频混合",
            function=self._step_post_processing,
            inputs=["enhanced_clips", "ad_script", "voiceover_audio", "bgm_audio", "subtitles", "beat_timings", "rhythm_curve"],
            outputs=["final_video"],
            dependencies=["AI视频增强", "音频生成"],
            priority=40,
        )

        self.add_step(
            name="品牌尾帧生成",
            description="生成品牌尾帧",
            function=self._step_brand_ending,
            inputs=["product_info"],
            outputs=["brand_ending"],
            dependencies=["需求分析"],
            priority=35,
        )

        self.add_step(
            name="最终合成",
            description="合成最终视频",
            function=self._step_final_assembly,
            inputs=["final_video", "brand_ending"],
            outputs=["output_video"],
            dependencies=["视频后处理", "品牌尾帧生成"],
            priority=30,
        )

        self.add_step(
            name="合规检查",
            description="检查视频合规性",
            function=self._step_compliance_check,
            inputs=["output_video", "ad_script"],
            outputs=["compliance_result"],
            dependencies=["最终合成"],
            priority=25,
        )

        self.add_step(
            name="发布级质量检测与自动修复",
            description="7维度发布级质量检测 + 自动修复管线",
            function=self._step_final_quality_check,
            inputs=["output_video", "reference_images", "subtitles", "ad_script", "beat_timings"],
            outputs=["final_quality_result", "production_ready_video"],
            dependencies=["最终合成", "合规检查"],
            priority=20,
        )

        self.add_step(
            name="资产注册",
            description="注册生成的角色、商品和视频片段到资产库",
            function=self._step_asset_registration,
            inputs=["video_clips", "reference_images", "product_info", "character_bibles"],
            outputs=["registered_assets"],
            dependencies=["视频片段生成"],
            priority=18,
        )

        self.add_step(
            name="反馈收集",
            description="收集质量反馈用于持续优化",
            function=self._step_feedback_collection,
            inputs=["production_ready_video", "final_quality_result", "product_info", "ad_script"],
            outputs=["feedback_collected"],
            dependencies=["发布级质量检测与自动修复"],
            priority=15,
        )

        self.add_step(
            name="实验追踪",
            description="追踪本次生成实验数据",
            function=self._step_experiment_tracking,
            inputs=["production_ready_video", "final_quality_result", "decision_result", "product_info"],
            outputs=["experiment_tracked"],
            dependencies=["发布级质量检测与自动修复"],
            priority=12,
        )

    def _step_requirement_analysis(self, **kwargs):
        from config import PRODUCT_PRESETS

        product_type = self.product_info.get("type", "default")
        preset = PRODUCT_PRESETS.get(product_type, PRODUCT_PRESETS["default"])

        return {
            "product_info": {
                **self.product_info,
                "preset": preset,
            },
            "target_audience": self.target_audience or self.product_info.get("target_audience", "general"),
        }

    def _step_character_analysis(self, **kwargs):
        from character_analyzer import CharacterAnalyzer

        product_info = kwargs.get("product_info", self.product_info)
        product_category = product_info.get("type", "default")

        analyzer = CharacterAnalyzer()
        story_scenes = ["家庭温馨", "家庭互动", "危机出现", "解决方案", "服务到达", "温馨回归"]
        character_roles = analyzer.analyze_characters_needed(product_category, story_scenes)
        character_bibles = analyzer.generate_character_bibles(character_roles, product_category)

        return {
            "character_roles": character_roles,
            "character_bibles": character_bibles,
        }

    def _step_script_generation(self, **kwargs):
        from ad_script import generate_ad_script
        from storyboard_generator import StoryboardGenerator
        from cinematic_camera import CinematicCameraSystem

        product_info = kwargs.get("product_info", self.product_info)
        target_audience = kwargs.get("target_audience", "")
        character_bibles = kwargs.get("character_bibles", [])

        ad_script = generate_ad_script(
            {
                **product_info,
                "target_audience": target_audience or product_info.get("target_audience", ""),
            }
        )

        camera_system = CinematicCameraSystem()
        emotion_curve = camera_system.generate_emotion_curve(num_shots=len(ad_script.get("segments", [])))

        storyboard_gen = StoryboardGenerator()
        storyboard = storyboard_gen.generate_from_script(
            ad_script,
            character_bibles=character_bibles,
            emotion_curve=emotion_curve,
        )

        return {
            "ad_script": ad_script,
            "storyboard": storyboard,
        }

    def _step_rhythm_analysis(self, **kwargs):
        from rhythm_controller import RhythmController

        ad_script = kwargs.get("ad_script", {})
        segments = ad_script.get("segments", [])

        controller = RhythmController()
        rhythm_curve = controller.analyze_script_rhythm(segments)
        beat_timings = controller.generate_beat_timings(segments, rhythm_curve)

        return {
            "rhythm_curve": rhythm_curve,
            "beat_timings": beat_timings,
        }

    def _step_scene_anchor(self, **kwargs):
        from scene_editor import SceneEditor, SceneDescription, SceneType, TimeOfDay, Weather

        ad_script = kwargs.get("ad_script", {})
        product_info = kwargs.get("product_info", self.product_info)

        editor = SceneEditor()

        first_segment = ad_script.get("segments", [{}])[0]
        scene_desc = first_segment.get("scene", "living room")

        scene_type = SceneType.INDOOR
        if "living room" in scene_desc.lower():
            scene_type = SceneType.LIVING_ROOM
        elif "kitchen" in scene_desc.lower():
            scene_type = SceneType.KITCHEN
        elif "bedroom" in scene_desc.lower():
            scene_type = SceneType.BEDROOM
        elif "office" in scene_desc.lower():
            scene_type = SceneType.OFFICE
        elif "street" in scene_desc.lower():
            scene_type = SceneType.STREET
        elif "park" in scene_desc.lower():
            scene_type = SceneType.PARK

        description = SceneDescription(
            scene_type=scene_type,
            time_of_day=TimeOfDay.AFTERNOON,
            weather=Weather.SUNNY,
            location_details=scene_desc,
            background_elements=[],
        )

        anchor = editor.create_scene_anchor(
            anchor_id="main_scene",
            scene_desc=description,
            reference_frames=[],
            anchor_type="primary",
        )

        return {
            "scene_anchor": anchor,
        }

    def _step_reference_image_generation(self, **kwargs):
        from kling_client import KlingClient
        from multi_character_manager import MultiCharacterManager
        from config import OUTPUT_DIR, KLING_IMAGE_MODEL

        character_bibles = kwargs.get("character_bibles", [])
        product_info = kwargs.get("product_info", self.product_info)

        kling = KlingClient()
        output_dir = OUTPUT_DIR / "character_ref"
        output_dir.mkdir(parents=True, exist_ok=True)

        image_paths = []
        for i, bible in enumerate(character_bibles):
            prompt = self._generate_character_prompt(bible, product_info)
            result = kling.generate_image(
                prompt=prompt,
                model=KLING_IMAGE_MODEL,
                aspect_ratio="2:3",
            )
            if result and result.get("image_url"):
                img_path = output_dir / f"character_{i}.png"
                kling.download_image(result["image_url"], img_path)
                image_paths.append(img_path)

        if image_paths:
            mcm = MultiCharacterManager()
            group_name = product_info.get("name", "group") + "_characters"
            character_group = mcm.create_character_group(group_name, character_bibles, image_paths)
        else:
            character_group = None

        reference_images = {
            "character_images": image_paths,
            "product_image": kwargs.get("product_image", ""),
        }

        return {
            "reference_images": reference_images,
            "character_group": character_group,
        }

    def _generate_character_prompt(self, bible: dict, product_info: dict) -> str:
        from config import BRAND_CONFIG, PRODUCT_PRESETS

        preset = PRODUCT_PRESETS.get(product_info.get("type", "default"), PRODUCT_PRESETS["default"])
        brand = BRAND_CONFIG.get("name", "brand")
        name = product_info.get("name", "product")

        parts = []
        if bible.get("age") and bible.get("gender"):
            parts.append(f"{bible['age']}-year-old {bible['gender']}")
        if bible.get("ethnicity"):
            parts.append(bible["ethnicity"])
        if bible.get("hair_style"):
            parts.append(bible["hair_style"])
        if bible.get("outfit"):
            parts.append(f"wearing {bible['outfit']}")
        if bible.get("expression_baseline"):
            parts.append(bible["expression_baseline"])

        description = ", ".join(parts)

        return (
            f"Character reference portrait for {name} advertisement, "
            f"{description}, "
            f"{preset['scene']}, "
            f"{preset['lighting']}, "
            f"half-body composition, high detail, clear facial features, "
            f"front-facing, neutral expression, 9:16 vertical, "
            f"{brand} brand aesthetic, {BRAND_CONFIG.get('primary_color', 'consistent brand colors')}"
        )

    def _step_quality_gate(self, **kwargs):
        from quality_gate import run_quality_gate
        from config import SCENE_CONTINUITY_CONFIG, DEFAULT_MODE, DEFAULT_VIDEO_DURATION

        ad_script = kwargs.get("ad_script", {})
        storyboard = kwargs.get("storyboard", {})
        reference_images = kwargs.get("reference_images", {})
        product_info = kwargs.get("product_info", self.product_info)
        character_bibles = kwargs.get("character_bibles", [])

        shots = storyboard.get("shots", []) if isinstance(storyboard, dict) else getattr(storyboard, "shots", [])
        prompts = []
        for shot in shots:
            if isinstance(shot, dict):
                prompt = shot.get("description") or shot.get("prompt") or shot.get("scene") or ""
            else:
                prompt = getattr(shot, "description", "") or getattr(shot, "prompt", "")
            if prompt:
                prompts.append(str(prompt))

        product_image_path = None
        product_image = reference_images.get("product_image") if isinstance(reference_images, dict) else None
        if product_image:
            candidate = Path(product_image)
            if candidate.exists():
                product_image_path = candidate

        character_image_paths = []
        if isinstance(reference_images, dict):
            for item in reference_images.get("character_images", []):
                candidate = Path(item)
                if candidate.exists():
                    character_image_paths.append(candidate)

        result = run_quality_gate(
            ad_script=ad_script,
            product_image_path=product_image_path,
            character_image_paths=character_image_paths,
            prompts=prompts,
            character_bible=character_bibles[0] if character_bibles else None,
            product_bible={
                "name": product_info.get("name", "product"),
                "category": product_info.get("type", "default"),
                "key_selling_point": product_info.get("selling_point", ""),
            },
            scene_continuity_config=SCENE_CONTINUITY_CONFIG,
            num_clips=len(prompts) or len(ad_script.get("segments", [])) or 5,
            duration_per_clip=DEFAULT_VIDEO_DURATION,
            mode=DEFAULT_MODE,
            product_category=product_info.get("type", "default"),
        )

        return {
            "quality_gate_result": result,
        }

    def _step_smart_decision(self, **kwargs):
        quality_gate_result = kwargs.get("quality_gate_result")
        product_info = kwargs.get("product_info", self.product_info)

        if not quality_gate_result:
            return {
                "decision_result": None,
                "generation_strategy": "standard",
            }

        decision_result = self.run_smart_decision(
            quality_gate_result=quality_gate_result,
            product_category=product_info.get("type", "default"),
            style_preference=product_info.get("cinematic_style", "cinematic"),
            budget=100.0,
        )

        self.print_smart_decision_report(decision_result)

        if not decision_result.can_proceed:
            raise RuntimeError(f"智能决策阻止生成：预估成功率 {decision_result.estimated_success_rate:.1%}")

        return {
            "decision_result": decision_result,
            "generation_strategy": decision_result.recommended_strategy,
        }

    def _step_image_first_validation(self, **kwargs):
        from image_first_strategy import (
            run_image_first_strategy,
            ImageFirstMode,
            print_image_first_report,
        )
        from kling_client import KlingClient
        from config import OUTPUT_DIR, KLING_IMAGE_MODEL, DEFAULT_IMAGE_FIDELITY

        storyboard = kwargs.get("storyboard", {})
        reference_images = kwargs.get("reference_images", {})
        ad_script = kwargs.get("ad_script", {})
        generation_strategy = kwargs.get("generation_strategy", "standard")

        output_dir = OUTPUT_DIR / "keyframes"
        output_dir.mkdir(parents=True, exist_ok=True)

        kling = KlingClient()

        keyframe_images = {}
        validation_results = None

        shots = storyboard.get("shots", [])
        if shots and ad_script:
            clip_prompts = [shot.get("description", "") for shot in shots]

            mode_map = {
                "minimal": ImageFirstMode.MINIMAL,
                "standard": ImageFirstMode.STANDARD,
                "full": ImageFirstMode.FULL,
                "progressive": ImageFirstMode.STANDARD,
                "experimental": ImageFirstMode.MINIMAL,
            }
            mode = mode_map.get(generation_strategy, ImageFirstMode.STANDARD)

            product_ref = None
            if reference_images.get("product_image"):
                p = Path(reference_images["product_image"])
                if p.exists():
                    product_ref = p

            char_ref = None
            char_images = reference_images.get("character_images", [])
            if char_images:
                cp = Path(char_images[0])
                if cp.exists():
                    char_ref = cp

            try:
                result = run_image_first_strategy(
                    client=kling,
                    ad_script=ad_script,
                    clip_prompts=clip_prompts,
                    product_reference_path=product_ref,
                    character_reference_path=char_ref,
                    save_dir=output_dir,
                    mode=mode,
                    n_variants=2,
                    aspect_ratio="9:16",
                    image_fidelity=DEFAULT_IMAGE_FIDELITY,
                    strict_mode=True,
                )

                validation_results = result
                print_image_first_report(result)

                for seg_idx, path in result.best_keyframes.items():
                    keyframe_images[seg_idx] = path

                if not result.can_proceed_to_video:
                    raise RuntimeError(
                        f"图片先行验证失败：{len(result.failed_candidates)}个片段未通过，"
                        f"预估成功率仅{result.estimated_video_success_rate:.1%}"
                    )

            except Exception as e:
                print(f"⚠️  图片先行验证异常，回退到标准流程: {e}")
                validation_results = None

        return {
            "keyframe_images": keyframe_images,
            "validation_results": validation_results,
        }

    def _step_video_generation(self, **kwargs):
        from kling_client import KlingClient
        from cinematic_language import build_cinematic_prompt_elements, generate_rhythm_visual_params
        from scene_editor import SceneEditor
        from config import OUTPUT_DIR, KLING_VIDEO_MODEL, DEFAULT_MODE, MAX_REF_IMAGES

        storyboard = kwargs.get("storyboard", {})
        keyframe_images = kwargs.get("keyframe_images", [])
        character_group = kwargs.get("character_group")
        reference_images = kwargs.get("reference_images", {})
        rhythm_curve = kwargs.get("rhythm_curve")
        scene_anchor = kwargs.get("scene_anchor")
        product_info = kwargs.get("product_info", self.product_info)

        kling = KlingClient()
        output_dir = OUTPUT_DIR / "clips"
        output_dir.mkdir(parents=True, exist_ok=True)

        video_clips = []
        shots = storyboard.get("shots", [])

        scene_editor = SceneEditor()

        for i, shot in enumerate(shots):
            scene_type = shot.get("scene", "")
            narrative = shot.get("emotion", "")
            description = shot.get("description", "")

            cinematic_elements = build_cinematic_prompt_elements(
                style_key=product_info.get("cinematic_style", "none"),
                narrative=narrative,
            )

            visual_intensity = cinematic_elements.get("intensity", 5)
            bpm = 90
            emotion_level = "moderate"

            if rhythm_curve and hasattr(rhythm_curve, "segments") and i < len(rhythm_curve.segments):
                seg = rhythm_curve.segments[i]
                bpm = seg.bpm
                emotion_level = seg.emotion_level.value

            rhythm_params = generate_rhythm_visual_params(
                bpm=bpm,
                emotion_level=emotion_level,
                narrative_position=i,
                total_segments=len(shots),
            )

            ref_imgs = []
            if character_group:
                ref_imgs.extend(str(p) for p in character_group.get_group_reference_images(character_group.group_id)[:MAX_REF_IMAGES])
            if keyframe_images and i in keyframe_images:
                ref_imgs.append(str(keyframe_images[i]))

            prompt_parts = [description]
            for key in ["shot_size", "camera_movement", "camera_angle", "lighting", "composition", "dof", "film_look"]:
                if cinematic_elements.get(key):
                    prompt_parts.append(cinematic_elements[key])

            prompt_parts.append(rhythm_params.get("contrast", ""))
            prompt_parts.append(rhythm_params.get("saturation", ""))
            prompt_parts.append(rhythm_params.get("depth_of_field", ""))

            if scene_anchor:
                anchor_prompt = scene_editor.apply_scene_anchor_to_prompt(
                    "", scene_anchor, is_first_shot=(i == 0)
                )
                if anchor_prompt:
                    prompt_parts.insert(0, anchor_prompt)

            final_prompt = ", ".join(filter(None, prompt_parts))

            result = kling.generate_video(
                prompt=final_prompt,
                reference_images=ref_imgs[:MAX_REF_IMAGES],
                duration=shot.get("duration", 5),
                mode=DEFAULT_MODE,
                model=KLING_VIDEO_MODEL,
                aspect_ratio="9:16",
            )

            if result and result.get("video_url"):
                clip_path = output_dir / f"clip_{i:02d}.mp4"
                kling.download_video(result["video_url"], clip_path)
                video_clips.append({
                    "path": clip_path,
                    "shot_index": i,
                    "narrative": narrative,
                    "duration": shot.get("duration", 5),
                    "bpm": bpm,
                    "intensity": visual_intensity,
                    "rhythm_params": rhythm_params,
                })

        return {
            "video_clips": video_clips,
        }

    def _step_temporal_consistency(self, **kwargs):
        from temporal_consistency import TemporalConsistencyChecker

        video_clips = kwargs.get("video_clips", [])

        checker = TemporalConsistencyChecker()
        analysis = checker.check_consistency(video_clips)

        return {
            "temporal_analysis": analysis,
        }

    def _step_ai_enhancement(self, **kwargs):
        from ai_enhancement import VideoEnhancer
        from config import OUTPUT_DIR

        video_clips = kwargs.get("video_clips", [])

        enhancer = VideoEnhancer()
        enhanced_clips = []
        output_dir = OUTPUT_DIR / "enhanced_clips"
        output_dir.mkdir(parents=True, exist_ok=True)

        for clip in video_clips:
            input_path = clip["path"]
            output_path = output_dir / f"enhanced_{input_path.name}"

            enhanced = enhancer.enhance_video(
                input_path,
                output_path,
                enhancements=["upscale", "denoise", "color_grade"],
                target_resolution="1080p",
            )

            enhanced_clips.append({
                **clip,
                "path": enhanced if enhanced else input_path,
                "enhanced": True,
            })

        return {
            "enhanced_clips": enhanced_clips,
        }

    def _step_audio_generation(self, **kwargs):
        from tts_client import generate_full_voiceover, generate_voiceover_script, align_subtitles_to_voiceover
        from bgm_client import pick_bgm_for_product
        from ad_script import script_to_subtitles
        from config import OUTPUT_DIR

        ad_script = kwargs.get("ad_script", {})
        rhythm_curve = kwargs.get("rhythm_curve", {})
        product_info = kwargs.get("product_info", self.product_info)
        video_clips = kwargs.get("video_clips", [])

        output_dir = OUTPUT_DIR / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)

        num_clips = len(ad_script.get("segments", [])) or len(video_clips) or 5
        clip_duration = 5
        if video_clips:
            total_clip_duration = sum(float(clip.get("duration", clip_duration)) for clip in video_clips)
            clip_duration = max(1, round(total_clip_duration / len(video_clips)))

        voiceover_text = generate_voiceover_script(
            product_info,
            clip_duration=clip_duration,
            num_clips=num_clips,
        )
        voiceover_audio = output_dir / "voiceover.mp3"
        _, voiceover_subtitles = generate_full_voiceover(
            voiceover_text,
            voiceover_audio,
            total_duration=max(clip_duration * num_clips, 1),
        )

        pace = None
        if rhythm_curve and hasattr(rhythm_curve, "segments") and rhythm_curve.segments:
            avg_bpm = sum(seg.bpm for seg in rhythm_curve.segments) / len(rhythm_curve.segments)
            if avg_bpm >= 120:
                pace = "fast"
            elif avg_bpm <= 80:
                pace = "slow"
            else:
                pace = "medium"

        bgm_info = pick_bgm_for_product(
            product_type=product_info.get("type", "default"),
            target_duration=max(clip_duration * num_clips, 1),
            cinematic_style=product_info.get("cinematic_style"),
            pace=pace,
        )
        bgm_audio = Path(bgm_info) if bgm_info else None

        subtitles = script_to_subtitles(ad_script)
        if voiceover_audio.exists():
            subtitles = align_subtitles_to_voiceover(subtitles, voiceover_subtitles)

        return {
            "voiceover_audio": voiceover_audio,
            "bgm_audio": bgm_audio,
            "subtitles": subtitles,
        }

    def _step_post_processing(self, **kwargs):
        from video_merger import merge_clips_ffmpeg, add_subtitles_ffmpeg, add_bgm_ffmpeg, apply_color_grading, align_subtitles_to_beats
        from cinematic_language import get_subtitle_style_by_intensity
        from config import OUTPUT_DIR, DEFAULT_COLOR_GRADING

        enhanced_clips = kwargs.get("enhanced_clips", [])
        ad_script = kwargs.get("ad_script", {})
        voiceover_audio = kwargs.get("voiceover_audio")
        bgm_audio = kwargs.get("bgm_audio")
        subtitles = kwargs.get("subtitles", [])
        beat_timings = kwargs.get("beat_timings", [])
        rhythm_curve = kwargs.get("rhythm_curve")

        output_dir = OUTPUT_DIR / "final"
        output_dir.mkdir(parents=True, exist_ok=True)

        clip_paths = [clip["path"] for clip in enhanced_clips]
        merged_path = output_dir / "merged.mp4"

        if clip_paths:
            transitions = []
            if rhythm_curve and hasattr(rhythm_curve, "segments"):
                for i in range(len(clip_paths) - 1):
                    if i < len(rhythm_curve.segments):
                        bpm = rhythm_curve.segments[i].bpm
                        intensity = enhanced_clips[i].get("intensity", 5)
                        transitions.append({
                            "type": "crossfade",
                            "duration": min(0.5, max(0.2, 60 / bpm)),
                            "intensity": intensity,
                        })

            merge_clips_ffmpeg(
                clip_paths,
                merged_path,
                transitions=transitions,
                envelope_key_times=beat_timings,
            )

            if voiceover_audio and voiceover_audio.exists():
                voiced_path = output_dir / "voiced.mp4"
                add_bgm_ffmpeg(merged_path, voiceover_audio, voiced_path, volume=1.0)
                merged_path = voiced_path

            if bgm_audio and bgm_audio.exists():
                bgm_path = output_dir / "with_bgm.mp4"
                add_bgm_ffmpeg(merged_path, bgm_audio, bgm_path, volume=0.3)
                merged_path = bgm_path

            if subtitles and bgm_audio and bgm_audio.exists():
                subtitles = align_subtitles_to_beats(subtitles, bgm_audio)

            if subtitles:
                subtitled_path = output_dir / "subtitled.mp4"
                add_subtitles_ffmpeg(merged_path, subtitles, subtitled_path)
                merged_path = subtitled_path

            graded_path = output_dir / "graded.mp4"
            apply_color_grading(merged_path, graded_path, preset=DEFAULT_COLOR_GRADING)
            merged_path = graded_path

        return {
            "final_video": merged_path if clip_paths else None,
        }

    def _step_brand_ending(self, **kwargs):
        from brand_ending_generator import BrandEndingGenerator
        from config import OUTPUT_DIR

        product_info = kwargs.get("product_info", self.product_info)

        generator = BrandEndingGenerator()
        output_dir = OUTPUT_DIR / "final"
        output_dir.mkdir(parents=True, exist_ok=True)

        brand_ending = generator.generate_brand_ending(
            product_info=product_info,
            output_path=output_dir / "brand_ending.mp4",
        )

        return {
            "brand_ending": brand_ending,
        }

    def _step_final_assembly(self, **kwargs):
        from video_merger import merge_clips_ffmpeg
        from config import OUTPUT_DIR

        final_video = kwargs.get("final_video")
        brand_ending = kwargs.get("brand_ending")

        output_dir = OUTPUT_DIR / "final"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / f"{self.product_info.get('name', 'output')}_final.mp4"

        clips_to_merge = []
        if final_video and final_video.exists():
            clips_to_merge.append(final_video)
        if brand_ending and brand_ending.exists():
            clips_to_merge.append(brand_ending)

        if clips_to_merge:
            merge_clips_ffmpeg(clips_to_merge, output_path, transitions=[])

        return {
            "output_video": output_path if clips_to_merge else None,
        }

    def _step_compliance_check(self, **kwargs):
        from compliance_checker import check_script_compliance

        output_video = kwargs.get("output_video")
        ad_script = kwargs.get("ad_script", {})

        result = check_script_compliance(ad_script)

        return {
            "compliance_result": result,
        }

    def _step_final_quality_check(self, **kwargs):
        from production_quality_guard import run_production_quality_check, ProductionQualityGuard

        output_video = kwargs.get("output_video")
        reference_images = kwargs.get("reference_images", {})
        subtitles = kwargs.get("subtitles", [])
        ad_script = kwargs.get("ad_script", {})
        beat_timings = kwargs.get("beat_timings", [])

        product_ref = None
        if reference_images and reference_images.get("product_image"):
            p = Path(reference_images["product_image"])
            if p.exists():
                product_ref = p

        char_ref = None
        char_images = reference_images.get("character_images", []) if reference_images else []
        if char_images:
            cp = Path(char_images[0])
            if cp.exists():
                char_ref = cp

        segments = ad_script.get("segments", []) if ad_script else []

        production_ready_video = output_video
        result = None

        if output_video and output_video.exists():
            fixed_path, report = run_production_quality_check(
                output_video,
                product_reference=product_ref,
                character_reference=char_ref,
                subtitles=subtitles,
                beat_timings=beat_timings,
                segments=segments,
                auto_fix=True,
                platform="douyin",
            )
            production_ready_video = fixed_path
            result = report

            guard = ProductionQualityGuard()
            guard.print_report(report, output_video.name)

            if not report.passed:
                critical = report.get_critical_issues()
                if critical:
                    print(f"\n⚠️  存在 {len(critical)} 项严重问题，视频可能不适合直接发布")
                else:
                    print("\n⚠️  综合评分偏低，建议人工复核后发布")
        else:
            print("❌ 无输出视频，跳过质量检测")

        return {
            "final_quality_result": result,
            "production_ready_video": production_ready_video,
        }

    def _step_asset_registration(self, **kwargs):
        video_clips = kwargs.get("video_clips", [])
        reference_images = kwargs.get("reference_images", {})
        product_info = kwargs.get("product_info", self.product_info)
        character_bibles = kwargs.get("character_bibles", [])

        registered_ids = []

        if reference_images.get("character_images"):
            for i, img_path in enumerate(reference_images["character_images"]):
                bible = character_bibles[i] if i < len(character_bibles) else {}
                asset_id = self.asset_library.add_character(
                    image_path=img_path,
                    name=bible.get("name", f"Character {i+1}"),
                    bible=bible,
                    tags=[product_info.get("type", "default"), "character"],
                )
                registered_ids.append({"type": "character", "id": asset_id})

        if reference_images.get("product_image"):
            product_bible = {
                "name": product_info.get("name", "product"),
                "category": product_info.get("type", "default"),
                "selling_points": product_info.get("selling_points", []),
                "colors": product_info.get("colors", []),
            }
            product_img_path = Path(reference_images["product_image"])
            if product_img_path.exists():
                asset_id = self.asset_library.add_product(
                    image_path=product_img_path,
                    name=product_info.get("name", "product"),
                    bible=product_bible,
                    tags=[product_info.get("type", "default"), "product"],
                )
                registered_ids.append({"type": "product", "id": asset_id})

        for clip in video_clips:
            clip_path = clip.get("path")
            if clip_path and clip_path.exists():
                asset_id = self.asset_library.add_video_clip(
                    video_path=clip_path,
                    name=f"clip_{clip.get('shot_index', 0)}",
                    metadata={"narrative": clip.get("narrative", ""), "bpm": clip.get("bpm", 0)},
                    tags=["clip", clip.get("narrative", "")],
                )
                registered_ids.append({"type": "video_clip", "id": asset_id})

        print(f"📦 资产注册完成：{len(registered_ids)} 个资产")
        return {"registered_assets": registered_ids}

    def _step_feedback_collection(self, **kwargs):
        final_video = kwargs.get("production_ready_video") or kwargs.get("output_video")
        final_quality_result = kwargs.get("final_quality_result")
        product_info = kwargs.get("product_info", self.product_info)
        ad_script = kwargs.get("ad_script", {})

        if not final_video or not Path(final_video).exists():
            return {"feedback_collected": False}

        video_id = f"video_{int(time.time())}"
        generation_params = {
            "product_name": product_info.get("name", ""),
            "product_type": product_info.get("type", ""),
            "cinematic_style": product_info.get("cinematic_style", ""),
            "script_type": ad_script.get("style", ""),
            "num_segments": len(ad_script.get("segments", [])),
        }

        auto_quality_score = final_quality_result.overall_score if final_quality_result else 0.0
        auto_issues = final_quality_result.issues if final_quality_result else []

        rating = 4 if auto_quality_score >= 80 else 3 if auto_quality_score >= 60 else 2

        self.feedback_loop.collect_feedback(
            video_id=video_id,
            generation_params=generation_params,
            rating=rating,
            auto_quality_score=auto_quality_score,
            auto_issues=auto_issues,
        )

        print(f"📝 反馈已收集：评分={rating}，质量分={auto_quality_score:.1f}")
        return {"feedback_collected": True, "video_id": video_id}

    def _step_experiment_tracking(self, **kwargs):
        final_video = kwargs.get("production_ready_video") or kwargs.get("output_video")
        final_quality_result = kwargs.get("final_quality_result")
        decision_result = kwargs.get("decision_result")
        product_info = kwargs.get("product_info", self.product_info)

        if not final_video or not Path(final_video).exists():
            return {"experiment_tracked": False}

        experiment_id = f"exp_{int(time.time())}"
        hypothesis = f"测试 {product_info.get('type', 'default')} 产品生成质量"

        params = {
            "product_type": product_info.get("type", ""),
            "cinematic_style": product_info.get("cinematic_style", ""),
            "strategy": decision_result.recommended_strategy if decision_result else "standard",
            "estimated_success_rate": decision_result.estimated_success_rate if decision_result else 0.5,
        }

        self.experiment_tracker.start_experiment(
            experiment_id=experiment_id,
            hypothesis=hypothesis,
            params=params,
            video_id=f"video_{int(time.time())}",
        )

        quality_score = final_quality_result.overall_score if final_quality_result else 0.0
        rating = 4 if quality_score >= 80 else 3 if quality_score >= 60 else 2

        self.experiment_tracker.complete_experiment(
            experiment_id=experiment_id,
            rating=rating,
            quality_score=quality_score,
        )

        print(f"🔬 实验追踪完成：ID={experiment_id}，评分={rating}")
        return {"experiment_tracked": True, "experiment_id": experiment_id}
