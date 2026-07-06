"""All metric classes in pipeline order (A → AA, AE, then v1-ported AF → AO)."""

from .basic import DatasetIdMetric, DatasetNameMetric, NumReposMetric, RepoOrgMetric, VendorNameMetric
from .repo_description import DescriptionMetric
from .docs import (
    AvgFuncLengthMetric,
    DocstringRatioMetric,
    DocumentationCountMetric,
    HoldoutMetric,
    IssueTrackerMetric,
    LicenseTypeMetric,
    ReadmeQualityMetric,
)
from .files import (
    ExtensionsMetric,
    FullLangDistributionMetric,
    LangDistributionMetric,
    PrimaryLanguageMetric,
    SourceFilesMetric,
    StackMetric,
)
from .git import (
    BranchCountMetric,
    CommitCountMetric,
    CommitMinhashMetric,
    ContributorsMetric,
    CreatedAtMetric,
    EarlyCommitHashesMetric,
    FirstCommitHashMetric,
    MetadataBranchNameMetric,
    MetadataCommitHashMetric,
    ReviewedPRMetric,
    TotalPRMetric,
)
from .infra import CIChecksMetric, ContainerizedMetric, DeploymentMetric, MonitoringMetric
from .loc import (
    AutoGenLocMetric,
    CommentRatioMetric,
    DepDirLocMetric,
    LogicalLocMetric,
    RawLocMetric,
    SymbolsCountMetric,
)
from .quality import DuplicationMetric, ForkPctMetric
from .size import GitHistoryMbMetric, RepoBundleMbMetric, WorktreeMbMetric
from .testing import TestSuiteMetric

__all__ = [
    "DatasetIdMetric",
    "VendorNameMetric",
    "RepoOrgMetric",
    "DatasetNameMetric",
    "DescriptionMetric",
    "NumReposMetric",
    "RawLocMetric",
    "LogicalLocMetric",
    "AutoGenLocMetric",
    "DepDirLocMetric",
    "DuplicationMetric",
    "ForkPctMetric",
    "SourceFilesMetric",
    "PrimaryLanguageMetric",
    "LangDistributionMetric",
    "CommitCountMetric",
    "ContributorsMetric",
    "TotalPRMetric",
    "ReviewedPRMetric",
    "CIChecksMetric",
    "DeploymentMetric",
    "MonitoringMetric",
    "TestSuiteMetric",
    "ContainerizedMetric",
    "HoldoutMetric",
    "DocstringRatioMetric",
    "ReadmeQualityMetric",
    "IssueTrackerMetric",
    "AvgFuncLengthMetric",
    # v1-ported metrics (AF → AO)
    "CreatedAtMetric",
    "LicenseTypeMetric",
    "BranchCountMetric",
    "RepoBundleMbMetric",
    "GitHistoryMbMetric",
    "WorktreeMbMetric",
    "ExtensionsMetric",
    "StackMetric",
    "DocumentationCountMetric",
    "CommentRatioMetric",
    "SymbolsCountMetric",
    # git identity / provenance fingerprint (for cross-recollection matching)
    "FirstCommitHashMetric",
    "MetadataCommitHashMetric",
    "MetadataBranchNameMetric",
    "EarlyCommitHashesMetric",
    "CommitMinhashMetric",
    # unfiltered language distribution (AV) — diagnostic counterpart of M
    "FullLangDistributionMetric",
]
