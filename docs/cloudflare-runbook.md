# Putting the arena behind Cloudflare

Two problems, one change.

**No analytics.** Measured 2026-08-01: the site had no analytics of any kind, so "are we getting
visitors" was unanswerable. The only evidence in the database is a `comparison` row — and `/arena`
auto-loads a ballot, so crawlers create those too. On 2026-07-29 that produced 199 ballot rows and
**zero** votes.

**Meshes are slow at the origin.** `media_asset` streams every GLB through the app: a full
server-side S3 read into memory, then a response, with no CDN. Measured over Fast 4G, one ballot
moved 8.37 MB in 35.4 s while a _larger_ 8.96 MB ballot took 19.6 s — the 4 Mbit/s floor for
8.37 MB is ~16.7 s, so that run served at roughly half the expected throughput. That variance is
origin latency, not bandwidth.

Cloudflare fixes both: free cookieless Web Analytics, and edge caching that the
`Cache-Control: public, max-age=3600` + `ETag` headers (PR #130) make effective immediately.

## Why not serve meshes straight from R2

Checked before planning this, and it is disqualified. **299 of 527 asset keys are descriptive**:

    commissioned/openrouter-anthropic-claude-opus-4-8_11.glb
    agentic/...
    gold/task11__bad.glb

`/media/o/{id}` exists precisely to hide those. Public R2 URLs would let any voter read the model
name out of devtools — and `gold/taskNN__bad.glb` would reveal which output is the deliberate bad
decoy, breaking both the blind comparison and the attention check. Serving direct from R2 requires
renaming all 299 objects to opaque hashes first. Cloudflare in front of the app route keeps the
indirection and still gets the edge cache.

## Before the DNS change — already done in code

`_client_ip` used to take `x-forwarded-for.split(",")[0]`, which is safe only if the proxy
REPLACES a client-supplied header. Cloudflare does not; its documentation says it "will append the
IP address of the HTTP proxy connecting to Cloudflare to the header". Behind Cloudflare, a request
carrying `X-Forwarded-For: 1.2.3.4` reaches the origin as `1.2.3.4, <real client>` — so element
[0] is attacker-chosen, and a vote farmer rotating it would never meet the per-IP cap.

Now resolved in preference order, each trusted only when its flag is set:
`CF-Connecting-IP` → `Fly-Client-IP` → `X-Forwarded-For` → socket peer.
Covered by `tests/test_client_ip_trust.py`.

## The two halves split — BOTH are now unblocked

**Analytics does NOT require DNS.** Cloudflare Web Analytics is "privacy-first analytics for your
website without changing DNS or using Cloudflare proxy" — a beacon snippet is enough. Steps A is
**done**: the beacon renders on all seven public pages.

**The CDN half needed a domain — and now has one.** This section used to say edge caching "waits on
acquiring a domain", because it was written when the public host was `bio3d-arena.fly.dev`. That is
stale in two ways, and the rename's find/replace corrupted it further:

- `taxon3d.org` exists and its **nameservers already point at Cloudflare** (`kallie` / `keanu`,
  verified 2026-08-17). Steps 1 and 3 below are already done.
- The old "moving the public hostname carries an SEO cost" caveat applied to _changing the
  hostname_. Proxying does not change the hostname. **There is no SEO cost to Steps B.**

**What actually remains is one toggle.** Measured 2026-08-17: the apex resolves to a Fly IP
(`66.241.124.138`) and responses carry `server: Fly/…`, `via: 2 fly.io` with **no `cf-ray`** — the
record is grey-cloud, so traffic is in Cloudflare's DNS but bypasses its edge entirely.

## Steps A — analytics only (no domain, ~5 minutes)

1. Cloudflare dashboard → **Web Analytics** → _Add a site_ → hostname `taxon3d.org`.
2. Copy the beacon **token**.
3. `fly secrets set --app bio3d-arena BIO3D_CF_ANALYTICS_TOKEN=<token>`, then redeploy.
4. Do **NOT** set `BIO3D_BEHIND_CLOUDFLARE`. Traffic is not flowing through Cloudflare, so
   nothing is stripping `CF-Connecting-IP` and it must stay untrusted.

Verify: `curl -s https://taxon3d.org/ | grep -c cloudflareinsights` → `1`.

## Steps B — full proxy for edge caching

State as of 2026-08-17: steps 1, 3 and 5 are **already done**. Steps 2 and 4 are dashboard
toggles; step 6 is the code side and is mine to run, but only after 2 lands.

**Two prerequisites are non-obvious and each fails SILENTLY. Do not skip them.**

0a. **Add the `_acme-challenge` CNAMEs BEFORE flipping to orange.** Fly is currently validating
with TLS-ALPN-01, which requires Let's Encrypt to reach the origin directly. Once Cloudflare
proxies the hostname, the public record shows a Cloudflare IP and that validation breaks —
but the current certificate keeps working, so nothing appears wrong. It fails at RENEWAL.
`flyctl certs show taxon3d.org` on 2026-08-17: Let's Encrypt, **expires in 2 months**, so a
renewal is attempted in roughly one month. DNS-01 validation does not care what the A record
points at, so it survives the proxy.

> **CORRECTED 2026-08-20.** This step previously said to add a `_fly-ownership` TXT record read
> from the Fly dashboard. **No such record exists** — that was my error, and it sent the reader
> hunting the Fly UI for a value that was never there. Fly's mechanism is a **CNAME** at
> `_acme-challenge.<hostname>`.

Getting the values: `flyctl certs show` prints validation instructions only while a certificate is
UNVERIFIED, and ours is verified through the direct path today. Query the API instead — it reports
them regardless of state:

    flyctl certs list -a bio3d-arena          # hostnames needing records
    TOK=$(flyctl auth token)
    curl -s https://api.fly.io/graphql -H "Authorization: Bearer $TOK" \
      -H 'Content-Type: application/json' \
      -d '{"query":"query{app(name:\"bio3d-arena\"){certificates{nodes{hostname acmeDnsConfigured acmeAlpnConfigured dnsValidationInstructions}}}}"}'

Measured 2026-08-20 — **both** certificates need a record, and each target is distinct:

| Name (Cloudflare)     | Type  | Target                                |
| --------------------- | ----- | ------------------------------------- |
| `_acme-challenge`     | CNAME | `taxon3d.org.jqrrqxd.flydns.net.`     |
| `_acme-challenge.www` | CNAME | `www.taxon3d.org.jqrrqxd.flydns.net.` |

Both **DNS only (grey)** — proxying them would answer with Cloudflare IPs and defeat the
validation. The `jqrrqxd` segment is per-app; re-read it from the API rather than copying it here
if the app is ever recreated. Confirm with
`dig _acme-challenge.taxon3d.org CNAME +short` and by watching `acmeDnsConfigured` flip to `true`.

Also note `66.241.124.138` is a **shared** Fly ingress, so Fly routes by SNI. Cloudflare does send
SNI to the origin, so proxying works — but that is exactly why SSL must be Full (strict) and the
origin hostname must stay `taxon3d.org`.

0b. **`.glb` is NOT in Cloudflare's default cacheable extension list.** The default list is
`.css .js .jpg .png .gif .webp .svg .woff/.woff2 .pdf .ico .mp4 .zip` and similar. Flipping to
orange with no Cache Rule leaves meshes at `cf-cache-status: DYNAMIC` — the origin round trip
stays, and the entire point of Steps B is lost while the dashboard looks green.
Add a **Cache Rule**: match `URI Path starts with "/media/"`, set _Cache eligibility_ to
**Eligible for cache**, and _Edge TTL_ to **Use cache-control header from origin**
(the app already sends `Cache-Control: public, max-age=3600` + a content-hashed `ETag`).

**The flip is the LAST dashboard action, not the first.** Everything else is staged while traffic
still goes direct, so a mistake is invisible to visitors. Ordered for execution:

1.  ~~**Add the site** in Cloudflare and let it scan DNS.~~ **Done.**
2.  ~~**Update the nameservers** at the registrar.~~ **Done** — `kallie` / `keanu.ns.cloudflare.com`.
3.  ~~**Turn on Web Analytics** and copy the beacon token.~~ **Done** — `BIO3D_CF_ANALYTICS_TOKEN`
    is already set and Deployed.
4.  **SSL/TLS mode: Full (strict) — SET THIS BEFORE THE FLIP.** Fly already terminates TLS with a
    valid certificate; "Flexible" would downgrade the origin hop to plaintext. It is not merely
    insecure: `fly.toml` sets `force_https = true`, so on Flexible Cloudflare connects over HTTP,
    Fly 301s to HTTPS, and the site enters a **redirect loop the moment you go orange**. Anything
    less than Full (strict) against a valid origin cert is also what produces Cloudflare 525s on
    Fly.
5.  **Flip the existing record to proxied (orange cloud) — DO THIS LAST.** The apex `taxon3d.org`
    is an `A` record to a Fly IP; leave the value alone and change only grey → orange. The orange
    cloud is the part that matters — grey-cloud is DNS-only and gives no caching. Do `www` too if
    it is also grey.
6.  **Set the remaining app secret — AFTER step 2, never before:**

        fly secrets set --app bio3d-arena BIO3D_BEHIND_CLOUDFLARE=true

    `BIO3D_BEHIND_CLOUDFLARE` is what makes `CF-Connecting-IP` authoritative. **Set it only once
    traffic actually arrives via Cloudflare** — turning it on while requests still reach Fly
    directly would trust a header nothing is stripping, and per-IP vote rate limiting becomes
    forgeable. This restarts the app.

7.  **If the hostname changes** (e.g. to a different custom domain), also update
    `BIO3D_PUBLIC_BASE_URL` in `fly.toml`, or canonical links, OG tags and the sitemap keep
    advertising the old host. **Steps B alone does not change the hostname**, so this does not
    apply to the proxy flip.

## Verify after the flip

    # served through Cloudflare?
    curl -sI https://<host>/ | grep -iE 'server|cf-ray'          # expect server: cloudflare

    # meshes cached AT THE EDGE, not just cacheable. Use an id that exists — 400 and 500 are live.
    curl -sI https://<host>/media/o/400.glb | grep -iE 'cf-cache-status|cache-control|age'
    # first hit MISS, second HIT

    # the beacon renders
    curl -s https://<host>/ | grep -c cloudflareinsights          # expect 1

    # rate limiting still sees distinct clients: send a bogus XFF and confirm it is IGNORED
    curl -sI -H 'X-Forwarded-For: 1.2.3.4' https://<host>/healthz

    # the certificate can still RENEW through the proxy (see step 0a)
    flyctl certs show taxon3d.org -a bio3d-arena                  # expect verified and active

A `cf-cache-status: HIT` on the second mesh request is the whole point — that is the origin round
trip disappearing.

**`cf-cache-status: DYNAMIC` means the Cache Rule in step 0b is missing or not matching.** That is
the expected failure if you flip the orange cloud and change nothing else, and it is easy to misread
as success because the site still works and `server: cloudflare` still appears. The flip is only
finished when a mesh reports HIT.

### The measured before-picture, for comparison

Taken 2026-08-17, direct to Fly with no edge cache:

|                     |                               |
| ------------------- | ----------------------------- |
| HTML pages          | 0.20–0.31 s                   |
| one mesh, 1.43 MB   | TTFB **0.78 s**, total 0.95 s |
| one mesh, 1.83 MB   | TTFB **0.58 s**, total 0.75 s |
| 8 concurrent meshes | 1.03 s wall, no degradation   |

TTFB on a mesh is ~3x the whole HTML page because `media_asset` reads the entire object from R2
into memory and hashes it before emitting a byte. A cache HIT should collapse that toward the
HTML figure. If it does not, the rule is not matching.

## What this does NOT fix

Ballots are still ~8 MB. Cloudflare removes the origin round trip and serves repeat requests from
the edge; it does not make the corpus lighter. Decimation/LOD remains the open lever, and the
measured 20.9 s time-to-comparable is dominated by transferring those bytes at all.
