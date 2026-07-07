# ── SashaOps Dockerfile ────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Route all model/browser caches under /app so a non-root runtime user can read them
# (models and Playwright browsers are downloaded at build time below).
ENV HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    TORCH_HOME=/app/.cache/torch \
    PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright \
    XDG_CACHE_HOME=/app/.cache

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    cmake \
    ninja-build \
    libssl-dev \
    libffi-dev \
    sqlcipher \
    libsqlcipher-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libglib2.0-0t64 \
    libcairo2 \
    fonts-liberation \
    libnss3 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libcups2t64 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2t64 \
    libxkbcommon0 \
    libxfixes3 \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Build liboqs — minimal build (ML-KEM-768 + ML-DSA-65 only) ───────────────
# curl tarball avoids git DNS issues in restricted build environments.
RUN curl -fsSL https://github.com/open-quantum-safe/liboqs/archive/refs/tags/0.15.0.tar.gz \
        -o /tmp/liboqs.tar.gz \
    && mkdir /tmp/liboqs \
    && tar -xzf /tmp/liboqs.tar.gz -C /tmp/liboqs --strip-components=1 \
    && rm /tmp/liboqs.tar.gz \
    && cmake -S /tmp/liboqs -B /tmp/liboqs/build \
        -GNinja \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_SHARED_LIBS=ON \
        -DOQS_BUILD_ONLY_LIB=ON \
        -DOQS_USE_OPENSSL=OFF \
        -DOQS_MINIMAL_BUILD="KEM_ml_kem_768;SIG_ml_dsa_65" \
    && cmake --build /tmp/liboqs/build --parallel 4 \
    && cmake --install /tmp/liboqs/build \
    && rm -rf /tmp/liboqs \
    && ldconfig

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
# Install torch CPU-only first (avoids pulling in CUDA wheels which are ~3 GB)
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    "torch==2.4.1+cpu" \
    && pip install --no-cache-dir -r requirements.txt

# ── Install Playwright browsers ───────────────────────────────────────────────
RUN playwright install-deps chromium || true \
    && apt-get install -y --no-install-recommends \
        fonts-unifont \
        fonts-freefont-ttf \
    && playwright install chromium

# ── Download spaCy language model (local NLP — no API calls) ─────────────────
# Direct wheel avoids the spacy download command's version-detection URL issue.
RUN pip install --no-cache-dir \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl

# ── Pre-download local ML models (runs once at build time; no network at runtime) ─
# sentence-transformers/all-MiniLM-L6-v2 (~80 MB) — headline relevance ranking
# yiyanghkust/finbert-tone (~450 MB) — financial sentiment scoring
RUN python - <<'EOF'
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModelForSequenceClassification
SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
AutoTokenizer.from_pretrained("yiyanghkust/finbert-tone")
AutoModelForSequenceClassification.from_pretrained("yiyanghkust/finbert-tone")
print("ML models pre-downloaded OK")
EOF

# ── Copy application ──────────────────────────────────────────────────────────
COPY . .

# ── Environment ───────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH

# ── Drop root: run as an unprivileged user (uid 10001) ────────────────────────
# Create the runtime dirs the app writes to (encrypted DB, documents, .tmp caches)
# and hand ownership of /app to the non-root user.
RUN useradd -m -u 10001 sashaops \
    && mkdir -p /app/data/documents /app/.tmp \
    && chown -R sashaops:sashaops /app
USER sashaops

EXPOSE 8000

# Use -k to skip certificate verification for self-signed cert health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fk https://localhost:8000/health || exit 1

CMD ["python", "main.py"]