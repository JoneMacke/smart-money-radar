FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app

# The local Docker Desktop mirror may intercept PyPI TLS traffic. Keep the
# image build reproducible while allowing pip to use the public package hosts.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY app ./app
COPY config ./config
RUN pip install --no-cache-dir "setuptools>=68" \
    && pip install --no-cache-dir --no-build-isolation .
CMD ["python", "-m", "app.main", "run"]
