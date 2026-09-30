# Schneider Electric manuals

Schneider answers the crawler with 403, so its manuals are downloaded by hand
from a browser and staged from this folder.

1. Download each manual listed in `sources.csv` from its `product_page_url`
   and save it here under the `filename` given.
2. Stage them for review:

   ```sh
   python -m app.worker ingest-files schneider data/schneider
   ```

A PDF not listed in `sources.csv` is refused: every chunk cites its manual's
page, and a citation must lead back to the manufacturer. The PDFs themselves
are not committed; this folder keeps only the list.
