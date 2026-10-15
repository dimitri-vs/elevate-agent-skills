# Storage Buckets

Verified as of 2026-10-15 against docs.railway.com (storage-buckets, storage-buckets/billing) and Railway CLI 5.62.1. Prices and plan caps change; re-check before quoting them.

## What they are

S3-compatible object storage, first-party on Railway. Buckets are private (authenticated access only, no public buckets): serve files through presigned URLs or your backend. They're reached over the public network, not private networking.

## CLI

```bash
railway bucket list --json
railway bucket create <name> --region sjc --json      # regions: sjc, iad, ams, sin
railway bucket info --bucket <name> --json
railway bucket credentials --bucket <name> --json     # prints secrets: don't echo into a transcript
```

Dashboard: the canvas Create button > Bucket.

## Wiring credentials into a service

A bucket provides these variables: `BUCKET` (the S3 bucket name to use), `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION` (`auto`), `ENDPOINT` (e.g. `https://t3.storageapi.dev`). `RAILWAY_BUCKET_NAME` is the display name, not the S3 name.

Nothing is injected automatically. Either add reference variables on the service (`${{<bucket>.ENDPOINT}}` and so on), or use the dashboard preset that inserts them under the names a library expects (AWS SDK, Bun S3, FastAPI, Laravel...). Reference variables follow a credentials reset; copying the values from `railway bucket credentials` into plain variables doesn't, so a reset silently breaks the app.

Clients: set the S3 `endpoint_url` to `ENDPOINT`. New buckets use virtual-hosted-style URLs; older buckets may need path-style (the bucket's Credentials tab says which).

## Pricing and limits

- $0.015 per GB-month of storage. S3 API operations and bucket egress are free and unlimited.
- Uploads from a Railway service to a bucket count as that service's egress.
- Storage caps: Free 10 GB-month, Trial 50 GB-month, Hobby 1 TB total, Pro unlimited. Not available on the Limited Trial.
