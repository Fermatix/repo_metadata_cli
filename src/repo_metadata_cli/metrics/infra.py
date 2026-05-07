"""Columns R, S, T, V — CI checks, deployment, monitoring, containerization."""

from __future__ import annotations

from typing import Any

from ..base_metric import BaseMetric, RepoContext
from ..metric_utils import (
    detect_ci_config,
    detect_containerized,
    detect_deployment_infra,
    detect_monitoring,
)


class CIChecksMetric(BaseMetric):
    """R: Whether CI configuration files exist in the repository."""

    column = "R"
    field_name = "ci_checks"

    def compute(self, ctx: RepoContext) -> Any:
        return "Yes" if detect_ci_config(ctx.repo_path) else "No"


class DeploymentMetric(BaseMetric):
    """S: Deployment infrastructure maturity level."""

    column = "S"
    field_name = "deployment_infra"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_deployment_infra(ctx.repo_path)


class MonitoringMetric(BaseMetric):
    """T: Monitoring and observability tooling level."""

    column = "T"
    field_name = "monitoring"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_monitoring(ctx.repo_path)


class ContainerizedMetric(BaseMetric):
    """V: Whether the repo includes containerization configuration."""

    column = "V"
    field_name = "containerized"

    def compute(self, ctx: RepoContext) -> Any:
        return detect_containerized(ctx.repo_path)
