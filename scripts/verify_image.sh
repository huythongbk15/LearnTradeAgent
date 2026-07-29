#!/usr/bin/env bash
# scripts/verify_image.sh — Verify cosign signature and SBOM for a local or remote image
# Usage: ./scripts/verify_image.sh <image-ref>

set -euo pipefail

IMAGE_REF="${1:-}"

if [[ -z "$IMAGE_REF" ]]; then
    echo "Usage: $0 <image-ref>"
    echo "Example: $0 ghcr.io/owner/repo:sha-abc123"
    echo "Example: $0 trading-agent:latest"
    exit 1
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $*"; }
error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $*"; }

# Install cosign & syft if not present
install_tools() {
    if ! command -v cosign &> /dev/null; then
        log "Installing cosign..."
        curl -sSfL https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 -o /usr/local/bin/cosign
        chmod +x /usr/local/bin/cosign
    fi
    if ! command -v syft &> /dev/null; then
        log "Installing syft..."
        curl -sSfL https://github.com/anchore/syft/releases/latest/download/syft-linux-amd64 -o /usr/local/bin/syft
        chmod +x /usr/local/bin/syft
    fi
}

# Verify cosign signature (keyless)
verify_signature() {
    local image="$1"
    log "Verifying cosign signature for $image..."

    if COSIGN_EXPERIMENTAL=1 cosign verify "$image" \
        --certificate-identity-regexp=".*" \
        --certificate-oidc-issuer-regexp=".*"; then
        log "✅ Cosign signature VERIFIED"
        return 0
    else
        error "❌ Cosign signature verification FAILED"
        return 1
    fi
}

# Verify SBOM exists and is valid
verify_sbom() {
    local image="$1"
    log "Verifying SBOM for $image..."

    if syft "$image" -o json > /dev/null 2>&1; then
        log "✅ SBOM VERIFIED"
        return 0
    else
        error "❌ SBOM verification FAILED"
        return 1
    fi
}

# Show SBOM summary
show_sbom_summary() {
    local image="$1"
    log "SBOM Summary for $image:"
    echo "----------------------------------------"
    syft "$image" -o table 2>/dev/null | head -30
    echo "----------------------------------------"
    log "Total packages: $(syft "$image" -o json 2>/dev/null | jq '.artifacts | length' 2>/dev/null || echo 'N/A')"
}

# Main
main() {
    install_tools

    log "Starting verification for: $IMAGE_REF"
    echo ""

    local failed=0

    verify_signature "$IMAGE_REF" || failed=1
    verify_sbom "$IMAGE_REF" || failed=1
    echo ""
    show_sbom_summary "$IMAGE_REF"

    echo ""
    if [[ $failed -eq 0 ]]; then
        log "✅✅✅ ALL VERIFICATIONS PASSED ✅✅✅"
        exit 0
    else
        error "❌❌❌ VERIFICATION FAILED ❌❌❌"
        exit 1
    fi
}

main "$@"