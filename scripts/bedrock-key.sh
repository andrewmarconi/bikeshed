#!/usr/bin/env bash
# Mint a Bedrock API key (valid up to 12h, bounded by the `aws login` session) and write it into the
# api_key of every [models.*] block in a bikeshed.toml whose base_url is the Bedrock Mantle endpoint.
# Usage: scripts/bedrock-key.sh [config]   (default: ./bikeshed.toml; AWS_PROFILE defaults to bikeshed,
# AWS_REGION to us-east-2). Provenance strips api_key, so a fresh key never changes a run's fingerprint.
set -euo pipefail
config="${1:-bikeshed.toml}"
export AWS_PROFILE="${AWS_PROFILE:-bikeshed}" AWS_REGION="${AWS_REGION:-us-east-2}"

uv run --quiet --no-project --with aws-bedrock-token-generator --with 'boto3[crt]' python -I - "$config" <<'EOF'
import os, re, sys
from pathlib import Path
from aws_bedrock_token_generator import provide_token

path = Path(sys.argv[1])
blocks = re.split(r"(?m)^(?=\[)", path.read_text())
token = provide_token(region=os.environ["AWS_REGION"])
hits = 0
for i, block in enumerate(blocks):
    if block.startswith("[models.") and "bedrock-mantle." in block:
        blocks[i], n = re.subn(r'(?m)^api_key\s*=.*$', f'api_key = "{token}"', block, count=1)
        if not n:
            raise SystemExit(f"{block.splitlines()[0]} has no api_key line to replace")
        hits += 1
if not hits:
    raise SystemExit(f"no [models.*] block with a bedrock-mantle base_url in {path}")
path.write_text("".join(blocks))
print(f"wrote a fresh Bedrock key into {hits} model block(s) in {path}")
EOF
