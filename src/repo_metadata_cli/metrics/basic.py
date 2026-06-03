"""Columns A, B, C, E — simple identity and provenance metrics."""

from __future__ import annotations

import uuid
from typing import Any

from ..base_metric import BaseMetric, RepoContext


class DatasetIdMetric(BaseMetric):
    column = "A"
    field_name = "repo_id"

    def compute(self, ctx: RepoContext) -> Any:
        return str(uuid.uuid4())


class VendorNameMetric(BaseMetric):
    column = "B"
    field_name = "partner_name"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.partner_name


class DatasetNameMetric(BaseMetric):
    column = "C"
    field_name = "repo_name"

    def compute(self, ctx: RepoContext) -> Any:
        return ctx.bundle_name


class NumReposMetric(BaseMetric):
    column = "E"
    field_name = "num_repos"

    def compute(self, ctx: RepoContext) -> Any:
        return 1
