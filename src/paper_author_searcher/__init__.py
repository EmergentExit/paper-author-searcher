"""Find a researcher's papers with verified ownership and, where lawful, their public full text."""

import logging

from .collection.paper_collector import PaperCollector
from .collection.paper_ownership import PaperOwnershipValidator
from .config import CollectionConfig, DownloadConfig, HttpConfig, MatchingConfig, PDFConfig, SourceConfig
from .fulltext.pdf_downloader import PDFDownloader
from .fulltext.pdf_file_validation import PDFFileValidator
from .fulltext.pdf_resolver import PDFResolver
from .models import (
    AuthorCandidate,
    Paper,
    PaperCollectionResult,
    PaperDownloadResult,
    PaperPDFResult,
    PaperRecord,
    PDFCandidate,
    PDFFileValidation,
    PDFProviderResult,
    PDFResolutionResult,
    ProfessorProfile,
    ProfessorQuery,
    ResolutionResult,
)
from .quality.evaluation import Evaluator
from .quality.evaluation_matching import GroundTruth
from .quality.performance_evaluation import PerformanceEvaluator, compare_performance
from .resolution.resolver import ProfessorResolver
from .workflow import PaperSearchWorkflow

# Library logging stays silent until an application configures it (see logging_config).
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    'AuthorCandidate',
    'CollectionConfig',
    'DownloadConfig',
    'Evaluator',
    'GroundTruth',
    'HttpConfig',
    'MatchingConfig',
    'PDFCandidate',
    'PDFConfig',
    'PDFDownloader',
    'PDFFileValidation',
    'PDFFileValidator',
    'PDFProviderResult',
    'PDFResolutionResult',
    'PDFResolver',
    'Paper',
    'PaperCollectionResult',
    'PaperCollector',
    'PaperDownloadResult',
    'PaperOwnershipValidator',
    'PaperPDFResult',
    'PaperRecord',
    'PaperSearchWorkflow',
    'PerformanceEvaluator',
    'ProfessorProfile',
    'ProfessorQuery',
    'ProfessorResolver',
    'ResolutionResult',
    'SourceConfig',
    'compare_performance',
]
