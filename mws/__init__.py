# Package marker for dashboard imports.
try:
    from .config import REVISION, REVISION_TAG
except ImportError:
    from config import REVISION, REVISION_TAG

__all__ = ["REVISION", "REVISION_TAG"]
