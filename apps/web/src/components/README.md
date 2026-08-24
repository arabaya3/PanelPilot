# web/src/components

Presentational React components. One component per file, named after the file.

Components receive data as props and call `@/lib/api-client` for anything that
crosses the network — they never call `fetch` directly, and they never hold the
API base URL. Keeping that in one module means an endpoint or auth change is a
one-file edit rather than a sweep.
