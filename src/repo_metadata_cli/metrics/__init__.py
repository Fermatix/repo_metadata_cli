"""All metric classes in pipeline order (A → AA, AE, then v1-ported AF → AO)."""

from .basic import (
    DatasetIdMetric,
    DatasetNameMetric,
    NumReposMetric,
    RepoOrgMetric,
    RepoUrlMetric,
    VendorNameMetric,
)
from .repo_description import DescriptionMetric
from .docs import (
    AvgFuncLengthMetric,
    ClassesCountMetric,
    DocstringRatioMetric,
    DocumentationCountMetric,
    FunctionsCountMetric,
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
    AvgLocPerPRMetric,
    BranchCountMetric,
    CommitCountMetric,
    CommitMinhashMetric,
    ContributorsMetric,
    CreatedAtMetric,
    EarlyCommitHashesMetric,
    FirstCommitHashMetric,
    MergedPRMetric,
    MetadataBranchNameMetric,
    MetadataCommitHashMetric,
    PRRichPctMetric,
    PRSimplePctMetric,
    PRStandardPctMetric,
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
from .testing import (
    TestCoveragePctMetric,
    TestSuiteMetric,
    UntestedFilesPctMetric,
)

__all__ = [
    "DatasetIdMetric",
    "VendorNameMetric",
    "RepoOrgMetric",
    "RepoUrlMetric",
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
    # PR size distribution + static test-coverage estimate (AX-BB), appended
    # to the stable schema tail (after the pricing placeholders)
    "PRSimplePctMetric",
    "PRStandardPctMetric",
    "PRRichPctMetric",
    "AvgLocPerPRMetric",
    "TestCoveragePctMetric",
    # AST tallies, untested-files share, merged-PR count (BC-BF) — schema tail
    "FunctionsCountMetric",
    "ClassesCountMetric",
    "UntestedFilesPctMetric",
    "MergedPRMetric",
]
