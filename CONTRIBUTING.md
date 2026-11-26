# Contributing to Agent Tessera

Thanks for your interest in improving Agent Tessera. Everything here is
released under [Apache 2.0](LICENSE), and contributions are accepted under the
same license. There is no CLA. Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

For anything bigger than a small fix, open an issue first so we can agree on
the approach before you spend time on it.

## Development setup

You need [uv](https://docs.astral.sh/uv/). Everything else is installed into a
local virtual environment.

```bash
uv sync --all-packages --group docs
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Checks

Every change must pass these locally and in CI:

```bash
uv run ruff check .            # lint, including docstring rules
uv run ruff format .           # format
uv run mypy                    # strict type checking
uv run pytest                  # tests and coverage (minimum 85%)
uv run mkdocs build --strict   # API documentation
```

For changes under `web/` (the console), also run `npm --prefix web run check`.

## Code standards

- Full type annotations; code must pass `mypy --strict`.
- [Google-style docstrings](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
  on every module and public class, function, and method. The API reference is
  generated from them.
- Tests run offline without API keys. Mock LLM provider SDKs and HTTP calls.
- Keep the SDK's runtime dependencies minimal. LLM provider SDKs are optional
  extras and must be imported lazily.

## Pull requests

- Keep pull requests small and focused on one change.
- Add tests for new behaviour and a regression test for every bug fix.
- Add an entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for
  user-visible changes.
- Keep policy fields consistent: a new YAML field goes into the pydantic
  schema, [docs/policies.md](docs/policies.md), and an example under
  `policies/examples/`.
- Use a short, imperative commit subject line, for example
  `Add glob matching for tool names`.

## Reporting security issues

Do not open public issues for security problems. See [SECURITY.md](SECURITY.md).
