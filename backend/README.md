# Backend of aptsell


## Development

### Install dependencies

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. Use `uv` to install dependencies

```bash
uv sync
```

### Prepare environment

```
cp .env.example .env
```

Edit `.env` to set environment variables.


### Run migrations

```bash
make migrate
```

数据库方言（TiDB / PostgreSQL）运维说明见 [`docs/database-dialect.md`](docs/database-dialect.md)；规格见仓库根目录 `fsd/`、`tsd/` 的 `2026-11-17-database-dialect.md`。

### Run development server

```bash
uv run python main.py runserver
```