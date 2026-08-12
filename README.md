# restaurant-crawler

An HTTP service that takes a neighborhood name, crawls public sources for
restaurants there, and returns structured records plus review text. It stores
nothing and knows nothing about its consumer beyond the JSON shape.

The contract is `restaurant-rag/docs/crawler-service-spec.md`. The v1 design and
what it deliberately leaves out is in `docs/superpowers/specs/`.

## Running

```bash
cp .env.example .env        # set CRAWLER_API_KEY at minimum
make up
curl localhost:8000/health
```

## Job state is in memory

Jobs live in a dict inside the process. A restart loses them, and a poll for a
job created before the restart returns 404. That is an accepted tradeoff at this
scale, not an oversight.