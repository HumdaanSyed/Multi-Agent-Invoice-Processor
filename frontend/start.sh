#!/usr/bin/env sh
# Single source of truth for the containerized Streamlit start command -
# docker-compose.yml, deploy/ec2.md, and deploy/railway.md all invoke this
# script instead of separately hand-typing the same flag list, so the three
# can't quietly drift out of sync with each other.
set -e
exec streamlit run frontend/app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true \
    --server.fileWatcherType=none \
    --browser.gatherUsageStats=false
