"""IPFS upload service.

Mock returns a deterministic fake CID. Real implementation will call Pinata.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class UploadResult:
    cid: str
    url: str


class IPFSClient:
    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        raise NotImplementedError

    async def upload_json(self, data: dict, name: str = "metadata.json") -> UploadResult:
        raise NotImplementedError


class MockIPFSClient(IPFSClient):
    """Returns a fake CID derived from sha256 so repeated uploads of the same
    content return the same CID."""

    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        digest = hashlib.sha256(data).hexdigest()
        cid = f"bafkmock{digest[:48]}"
        return UploadResult(cid=cid, url=f"https://mock-ipfs.lien.local/ipfs/{cid}")

    async def upload_json(self, data: dict, name: str = "metadata.json") -> UploadResult:
        import json
        payload = json.dumps(data, sort_keys=True).encode()
        return await self.upload_bytes(payload, name)


def get_ipfs_client() -> IPFSClient:
    settings = get_settings()
    if settings.ipfs_mock_mode:
        return MockIPFSClient()
    raise NotImplementedError("Pinata client not wired yet — set IPFS_MOCK_MODE=true")
