#!/usr/bin/env python3
"""
多模型路由器（Model Router）

智能路由到多个AI视频/图片生成API，支持自动切换、负载均衡和故障转移。
核心理念：不把鸡蛋放在一个篮子里，主模型挂了自动切备用。
"""

import time
import json
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config import setup_logger

logger = setup_logger(__name__)


class BackendStatus(Enum):
    """后端状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # 响应慢但仍可用
    DOWN = "down"
    UNKNOWN = "unknown"


class PriorityMode(Enum):
    """优先级模式"""
    QUALITY = "quality"    # 优先成功率高的
    SPEED = "speed"        # 优先速度快的
    COST = "cost"          # 优先成本低的
    BALANCED = "balanced"  # 平衡模式


@dataclass
class BackendStats:
    """后端统计"""
    backend_name: str
    total_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_latency: float = 0.0
    last_used: Optional[str] = None
    status: BackendStatus = BackendStatus.UNKNOWN
    consecutive_failures: int = 0
    estimated_queue_time: int = 0  # 秒


class ModelRouter:
    """多模型路由器主类"""

    # 预定义的后端配置（实际使用时可通过配置覆盖）
    DEFAULT_BACKENDS = {
        "kling": {
            "cost_per_sec": 0.60,
            "success_rate": 0.85,
            "avg_latency": 45,
            "queue_time": 30,
            "supports_reference": True,
            "max_duration": 10,
            "priority": 1,
        },
        "runway": {
            "cost_per_sec": 0.80,
            "success_rate": 0.80,
            "avg_latency": 60,
            "queue_time": 60,
            "supports_reference": True,
            "max_duration": 16,
            "priority": 2,
        },
        "pika": {
            "cost_per_sec": 0.50,
            "success_rate": 0.75,
            "avg_latency": 30,
            "queue_time": 15,
            "supports_reference": True,
            "max_duration": 3,
            "priority": 3,
        },
        "luma": {
            "cost_per_sec": 0.40,
            "success_rate": 0.70,
            "avg_latency": 25,
            "queue_time": 10,
            "supports_reference": False,
            "max_duration": 5,
            "priority": 4,
        },
    }

    def __init__(
        self,
        backends: Optional[Dict[str, Dict[str, Any]]] = None,
        priority_mode: PriorityMode = PriorityMode.BALANCED,
        max_consecutive_failures: int = 3,
        circuit_breaker_timeout: int = 300,  # 熔断后恢复时间（秒）
    ):
        self.backends = backends or self.DEFAULT_BACKENDS.copy()
        self.priority_mode = priority_mode
        self.max_consecutive_failures = max_consecutive_failures
        self.circuit_breaker_timeout = circuit_breaker_timeout
        self.stats: Dict[str, BackendStats] = {
            name: BackendStats(backend_name=name)
            for name in self.backends
        }
        self.circuit_breaker: Dict[str, datetime] = {}  # 熔断记录
        self._clients: Dict[str, Any] = {}  # 懒加载的客户端

    def _score_backend(self, name: str, config: Dict[str, Any]) -> float:
        """根据优先级模式计算后端得分（越高越好）"""
        stats = self.stats[name]

        # 检查是否被熔断
        if name in self.circuit_breaker:
            if datetime.now() < self.circuit_breaker[name]:
                return -1  # 仍在熔断期
            else:
                del self.circuit_breaker[name]  # 熔断恢复

        # 连续失败过多，临时降级
        if stats.consecutive_failures >= self.max_consecutive_failures:
            self.circuit_breaker[name] = datetime.now() + timedelta(seconds=self.circuit_breaker_timeout)
            logger.warning(f"🔒 {name} 触发熔断，{self.circuit_breaker_timeout}秒后恢复")
            return -1

        base_score = 0.0

        if self.priority_mode == PriorityMode.QUALITY:
            # 成功率权重最高
            base_score = config["success_rate"] * 100
            base_score -= config["queue_time"] * 0.1

        elif self.priority_mode == PriorityMode.SPEED:
            # 速度权重最高
            base_score = 100 - config["avg_latency"]
            base_score += (100 - config["queue_time"]) * 0.5

        elif self.priority_mode == PriorityMode.COST:
            # 成本权重最高
            max_cost = max(b["cost_per_sec"] for b in self.backends.values())
            base_score = (max_cost - config["cost_per_sec"]) / max_cost * 100

        elif self.priority_mode == PriorityMode.BALANCED:
            # 平衡模式：成功率60% + 速度20% + 成本20%
            max_cost = max(b["cost_per_sec"] for b in self.backends.values())
            cost_score = (max_cost - config["cost_per_sec"]) / max_cost * 100
            speed_score = max(0, 100 - config["avg_latency"])
            quality_score = config["success_rate"] * 100
            base_score = quality_score * 0.6 + speed_score * 0.2 + cost_score * 0.2

        # 实际成功率修正（基于历史数据）
        if stats.total_requests > 10:
            actual_success_rate = stats.success_count / stats.total_requests
            base_score = base_score * 0.7 + actual_success_rate * 100 * 0.3

        return base_score

    def select_backend(self, requirements: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        选择最佳后端。

        Args:
            requirements: 特殊要求，如 {"max_cost": 0.50, "min_duration": 10}
        """
        scores = []
        for name, config in self.backends.items():
            # 检查特殊要求
            if requirements:
                if "max_cost" in requirements and config["cost_per_sec"] > requirements["max_cost"]:
                    continue
                if "min_duration" in requirements and config["max_duration"] < requirements["min_duration"]:
                    continue
                if "requires_reference" in requirements and not config.get("supports_reference", False):
                    continue

            score = self._score_backend(name, config)
            if score >= 0:
                scores.append((name, score))

        if not scores:
            logger.error("❌ 没有可用的后端模型")
            return None

        scores.sort(key=lambda x: x[1], reverse=True)
        best = scores[0][0]
        logger.info(f"🎯 选择后端：{best} (模式: {self.priority_mode.value})")
        return best

    def get_ranked_backends(self, requirements: Optional[Dict[str, Any]] = None) -> List[str]:
        """获取按优先级排序的后端列表"""
        scores = []
        for name, config in self.backends.items():
            if requirements:
                if "max_cost" in requirements and config["cost_per_sec"] > requirements["max_cost"]:
                    continue
                if "min_duration" in requirements and config["max_duration"] < requirements["min_duration"]:
                    continue

            score = self._score_backend(name, config)
            if score >= 0:
                scores.append((name, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scores]

    def record_result(self, backend_name: str, success: bool, latency: float = 0.0):
        """记录生成结果"""
        if backend_name not in self.stats:
            self.stats[backend_name] = BackendStats(backend_name=backend_name)

        stats = self.stats[backend_name]
        stats.total_requests += 1
        stats.last_used = datetime.now().isoformat()

        if success:
            stats.success_count += 1
            stats.consecutive_failures = 0
            stats.status = BackendStatus.HEALTHY
        else:
            stats.failure_count += 1
            stats.consecutive_failures += 1
            if stats.consecutive_failures >= self.max_consecutive_failures:
                stats.status = BackendStatus.DOWN
            else:
                stats.status = BackendStatus.DEGRADED

        # 更新平均延迟
        if latency > 0:
            if stats.avg_latency == 0:
                stats.avg_latency = latency
            else:
                stats.avg_latency = stats.avg_latency * 0.9 + latency * 0.1

    def generate_with_fallback(
        self,
        generate_func: Callable,
        prompt: str,
        reference_image: Optional[Path] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        带故障转移的生成调用。

        Args:
            generate_func: 生成函数，接收 (backend_name, prompt, reference_image, **kwargs)
            prompt: 生成Prompt
            reference_image: 参考图
            **kwargs: 其他参数

        Returns:
            {"success": bool, "backend": str, "result": Any, "latency": float}
        """
        requirements = {}
        if reference_image is not None:
            requirements["requires_reference"] = True

        ranked = self.get_ranked_backends(requirements)

        for backend in ranked:
            start_time = time.time()
            try:
                logger.info(f"🔄 尝试使用 {backend} 生成...")
                result = generate_func(backend, prompt, reference_image, **kwargs)
                latency = time.time() - start_time

                self.record_result(backend, success=True, latency=latency)
                logger.info(f"✅ {backend} 生成成功 ({latency:.1f}s)")

                return {
                    "success": True,
                    "backend": backend,
                    "result": result,
                    "latency": latency,
                }

            except Exception as e:
                latency = time.time() - start_time
                self.record_result(backend, success=False, latency=latency)
                logger.warning(f"⚠️ {backend} 生成失败：{e}")
                continue

        logger.error("❌ 所有后端模型均生成失败")
        return {
            "success": False,
            "backend": None,
            "result": None,
            "latency": 0,
            "error": "所有后端均不可用",
        }

    def get_stats_report(self) -> Dict[str, Any]:
        """获取统计报告"""
        return {
            "priority_mode": self.priority_mode.value,
            "backends": {
                name: {
                    "config": config,
                    "stats": {
                        "total_requests": s.total_requests,
                        "success_rate": s.success_count / max(1, s.total_requests),
                        "avg_latency": round(s.avg_latency, 1),
                        "status": s.status.value,
                        "consecutive_failures": s.consecutive_failures,
                    },
                }
                for name, config in self.backends.items()
                for s in [self.stats.get(name, BackendStats(backend_name=name))]
            },
            "circuit_breaker_active": list(self.circuit_breaker.keys()),
        }
