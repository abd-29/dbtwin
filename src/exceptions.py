class DbtwinError(Exception):
    """Base exception for application errors."""


class IngestionError(DbtwinError):
    """Raised when a source cannot be read."""


class FormatDetectionError(IngestionError):
    """Raised when the input format cannot be determined reliably."""


class DatasetValidationError(DbtwinError):
    """Raised when a dataset violates the internal contract."""


class ComparisonError(DbtwinError):
    """Raised when a comparison cannot be performed."""


class TransformationError(DbtwinError):
    """Raised when preparing the internal dataset fails."""
