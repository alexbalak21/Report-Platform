"""Kept so existing imports keep working; the implementation lives in rp_shared.storage."""

from rp_shared.storage import LocalStorage, S3Storage, Storage, make_storage

__all__ = ["LocalStorage", "S3Storage", "Storage", "make_storage"]
