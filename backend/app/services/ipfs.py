"""IPFS upload service.

Mock returns a deterministic fake CID. Real implementation calls Pinata API.

Set IPFS_MOCK_MODE=false and provide:
  PINATA_JWT   — Bearer token from Pinata dashboard (pinFileToIPFS scope)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from typing import Optional

from app.core.config import get_settings

log = logging.getLogger(__name__)

PINATA_UPLOAD_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"
PINATA_JSON_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
PINATA_GATEWAY = "https://gateway.pinata.cloud/ipfs"


@dataclass
class UploadResult:
    cid: str
    url: str


class IPFSClient:
    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        raise NotImplementedError

    async def upload_json(self, data: dict, name: str = "metadata.json") -> UploadResult:
        raise NotImplementedError

    async def fetch_bytes(self, cid: str) -> bytes:
        raise NotImplementedError


class MockIPFSClient(IPFSClient):
    """Returns a fake CID derived from sha256 so repeated uploads of the same
    content return the same CID."""

    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        digest = hashlib.sha256(data).hexdigest()
        cid = f"bafkmock{digest[:48]}"
        return UploadResult(cid=cid, url=f"https://mock-ipfs.lien.local/ipfs/{cid}")

    async def upload_json(self, data: dict, name: str = "metadata.json") -> UploadResult:
        payload = json.dumps(data, sort_keys=True).encode()
        return await self.upload_bytes(payload, name)

    async def fetch_bytes(self, cid: str) -> bytes:
        """Deterministic fake proof file. No HTTP, no Pinata."""
        return b"mock-proof-file-" + cid.encode()


class PinataIPFSClient(IPFSClient):
    """Real IPFS client via Pinata API.

    Reads PINATA_JWT from Settings. All calls are async via httpx.
    """

    def __init__(self) -> None:
        import httpx

        settings = get_settings()
        if not settings.pinata_jwt:
            raise RuntimeError("PINATA_JWT is not set — cannot use real IPFS client")

        self._headers = {"Authorization": f"Bearer {settings.pinata_jwt}"}
        self._client = httpx.AsyncClient(timeout=60.0)

    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        import httpx

        files = {"file": (filename, data)}
        try:
            resp = await self._client.post(
                PINATA_UPLOAD_URL,
                headers=self._headers,
                files=files,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("Pinata upload failed: %s %s", exc.response.status_code, exc.response.text)
            raise

        cid = resp.json()["IpfsHash"]
        return UploadResult(cid=cid, url=f"{PINATA_GATEWAY}/{cid}")

    async def upload_json(self, data: dict, name: str = "metadata.json") -> UploadResult:
        import httpx

        body = {
            "pinataContent": data,
            "pinataMetadata": {"name": name},
        }
        try:
            resp = await self._client.post(
                PINATA_JSON_URL,
                headers={**self._headers, "Content-Type": "application/json"},
                content=json.dumps(body),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error("Pinata JSON upload failed: %s %s", exc.response.status_code, exc.response.text)
            raise

        cid = resp.json()["IpfsHash"]
        return UploadResult(cid=cid, url=f"{PINATA_GATEWAY}/{cid}")

    async def fetch_bytes(self, cid: str) -> bytes:
        """Fetch file bytes from Pinata gateway."""
        import httpx

        url = f"{PINATA_GATEWAY}/{cid}"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as exc:
            log.error("Pinata fetch failed for %s: %s", cid, exc.response.status_code)
            raise


_real_singleton: Optional[IPFSClient] = None


def get_ipfs_client() -> IPFSClient:
    global _real_singleton
    settings = get_settings()
    if settings.ipfs_mock_mode:
        return MockIPFSClient()
    if _real_singleton is None:
        _real_singleton = PinataIPFSClient()
    return _real_singleton
