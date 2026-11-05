FROM ghcr.io/astral-sh/uv:0.6.9 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/workspace/.venv/bin:$PATH"

WORKDIR /workspace

COPY pyproject.toml uv.lock README.md ./
COPY packages ./packages
COPY services/api ./services/api

RUN uv sync --frozen --package visualseek-api --no-dev

# The company/development baseline uses CPU; Kaggle keeps its preinstalled CUDA torch.
ARG INSTALL_EMBEDDING_MODEL=true
RUN if [ "$INSTALL_EMBEDDING_MODEL" = "true" ]; then \
      uv pip install 'torch==2.7.1+cpu' --index-url https://download.pytorch.org/whl/cpu && \
      uv pip install 'transformers==4.57.6' 'sentencepiece>=0.2,<0.3'; \
    fi

EXPOSE 8000

CMD ["uvicorn", "visualseek_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

