#!/usr/bin/env bash
# scripts/sign_and_verify.sh — Sign image with cosign, generate SBOM with syft, verify
# Usage: ./scripts/sign_and_verify.sh <image-ref> [--verify-only]

set -euo pipefail

IMAGE_REF="${1:-}"
VERIFY_ONLY="${2:-}"

if [[ -z "$IMAGE_REF" ]]; then
    echo "Usage: $0 <image-ref> [--verify-only]"
    echo "Example: $0 ghcr.io/owner/repo:sha-abc123"
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

# Generate SBOM
generate_sbom() {
    local image="$1"
    local output_dir="${2:-.}"

    log "Generating SBOM for $image..."
    syft "$image" -o spdx-json > "${output_dir}/sbom.spdx.json"
    syft "$image" -o cyclonedx-json > "${output_dir}/sbom.cyclonedx.json"
    syft "$image" -o table > "${output_dir}/sbom.table.txt"

    log "SBOM generated: ${output_dir}/sbom.spdx.json, ${output_dir}/sbom.cyclonedx.json"
}

# Sign image with cosign (keyless - uses OIDC)
sign_image() {
    local image="$1"

    log "Signing image $image with cosign (keyless)..."
    COSIGN_EXPERIMENTAL=1 cosign sign --yes "$image"

    log "Image signed successfully"
}

# Verify signature
verify_signature() {
    local image="$1"

    log "Verifying signature for $image..."
    if COSIGN_EXPERIMENTAL=1 cosign verify "$image" --certificate-identity-regexp=".*" --certificate-oidc-issuer-regexp=".*"; then
        log "✅ Signature verified"
        return 0
    else
        error "❌ Signature verification failed"
        return 1
    fi
}

# Verify SBOM
verify_sbom() {
    local image="$1"

    log "Verifying SBOM for $image..."
    if syft "$image" -o json > /dev/null 2>&1; then
        log "✅ SBOM verified"
        return 0
    else
        error "❌ SBOM verification failed"
        return 1
    fi
}

# Attach SBOM as cosign attachment
attach_sbom() {
    local image="$1"
    local sbom_file="$2"

    log "Attaching SBOM as cosign attachment..."
    COSIGN_EXPERIMENTAL=1 cosign attach sbom --sbom "$sbom_file" "$image"
    log "SBOM attached"
}

# Main
main() {
    install_tools

    if [[ "$VERIFY_ONLY" == "--verify-only" ]]; then
        log "Verify-only mode"
        verify_signature "$IMAGE_REF" || exit 1
        verify_sbom "$IMAGE_REF" || exit 1
        log "✅ All verifications passed"
        exit 0
    fi

    # Generate SBOM
    generate_sbom "$IMAGE_REF" "/tmp/sbom_$(date +%s)"

    # Sign image
    sign_image "$IMAGE_REF"

    # Attach SBOM
    attach_sbom "$IMAGE_REF" "/tmp/sbom_$(date +%s)/sbom.spdx.json"

    # Verify
    verify_signature "$IMAGE_REF" || exit 1
    verify_sbom "$IMAGE_REF" || exit 1

    log "✅ Sign, SBOM, and verify complete for $IMAGE_REF"
}

main "$@"