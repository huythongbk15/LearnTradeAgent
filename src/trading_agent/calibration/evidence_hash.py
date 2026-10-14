"""Public compatibility helper for the v2 framed file inventory seal.

SQLite identity is verified separately by packet.verify_packet, not by this hash.
Historical v1 packets must not be resealed with this algorithm.
"""

from pathlib import Path

from .packet import digest, inventory


def compute_evidence_hash(out_root: Path) -> str:
    return digest(inventory(Path(out_root).resolve()))
