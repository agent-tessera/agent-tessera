FROM python:3.12-slim AS build

WORKDIR /src
RUN pip install --no-cache-dir uv
COPY . .
RUN uv build --all-packages -o /wheels

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 tessera
WORKDIR /home/tessera
COPY --from=build /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/agent_tessera-*.whl \
    && pip install --no-cache-dir /tmp/wheels/agent_tessera_server-*.whl "psycopg[binary]" \
    && rm -rf /tmp/wheels
USER tessera
EXPOSE 8700
ENTRYPOINT ["tessera", "server"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8700"]
