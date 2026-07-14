#!/usr/bin/env bash
# setup_okx.sh — Configure OKX Onchain OS credentials and install skills
# Run after filling in your .env file: bash scripts/setup_okx.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Load .env
if [ -f "$ROOT_DIR/.env" ]; then
    set -a && source "$ROOT_DIR/.env" && set +a
    echo "✓ Loaded .env"
else
    echo "⚠  No .env found — create one from .env.example"
    exit 1
fi

# Verify onchainos is installed
if ! command -v onchainos &>/dev/null; then
    echo "Installing onchainos CLI..."
    curl -sSL https://raw.githubusercontent.com/okx/onchainos-skills/main/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "✓ onchainos $(onchainos --version) installed"

# Write credentials to onchainos config
ONCHAINOS_DIR="$HOME/.onchainos"
mkdir -p "$ONCHAINOS_DIR"
cat > "$ONCHAINOS_DIR/.env" <<EOF
OKX_API_KEY=${OKX_API_KEY}
OKX_SECRET_KEY=${OKX_SECRET_KEY}
OKX_PASSPHRASE=${OKX_PASSPHRASE}
EOF
echo "✓ OKX credentials written to ~/.onchainos/.env"

# Check wallet status
echo ""
echo "Checking Agentic Wallet status..."
onchainos wallet status --output json 2>/dev/null || echo "⚠  Wallet not logged in — run: onchainos wallet login"

# Fetch wallet address and write to .env if SELLER_ADDRESS is empty
if [ -z "${SELLER_ADDRESS:-}" ]; then
    echo ""
    echo "Fetching wallet EVM address for SELLER_ADDRESS..."
    EVM_ADDR=$(onchainos wallet addresses --output json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
# Try common keys for EVM address
for key in ('evm', 'evmAddress', 'address'):
    if key in data and data[key]:
        print(data[key]); sys.exit(0)
# Try nested
for chain in ('xlayer', 'ethereum', 'base'):
    if chain in data and isinstance(data[chain], dict):
        addr = data[chain].get('address','')
        if addr: print(addr); sys.exit(0)
# First 0x string
for v in data.values():
    if isinstance(v, str) and v.startswith('0x'):
        print(v); sys.exit(0)
" 2>/dev/null || echo "")
    if [ -n "$EVM_ADDR" ]; then
        # Append SELLER_ADDRESS to .env
        if grep -q "^SELLER_ADDRESS=" "$ROOT_DIR/.env"; then
            sed -i "s|^SELLER_ADDRESS=.*|SELLER_ADDRESS=$EVM_ADDR|" "$ROOT_DIR/.env"
        else
            echo "SELLER_ADDRESS=$EVM_ADDR" >> "$ROOT_DIR/.env"
        fi
        echo "✓ SELLER_ADDRESS set to $EVM_ADDR"
    else
        echo "⚠  Could not auto-detect wallet address. Set SELLER_ADDRESS manually in .env"
    fi
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Purposa OKX setup complete."
echo "  Start the service: python3 -m src.main"
echo "  API docs: http://localhost:8000/docs"
echo "═══════════════════════════════════════════"
