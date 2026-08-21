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

> **DONE 2026-08-20 — taxon3d.org is live behind the proxy and meshes `HIT` at the edge.** Kept as
> the record of how, and of the two faults that made a correct-looking setup cache nothing: assets
> minted a session cookie, and the Cache Rule was unnamed so it was never deployed. Both are written
> up below.
>
> **HTML caching CLOSED 2026-08-21** by a second Cache Rule — see "Steps C". Public pages now
> `HIT`. That verification also turned up a zone-wide `max-age` rewrite that had been in place all
> along; it is written up at the end of Steps C.

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
if the app is ever recreated.

**`acmeDnsConfigured` does not flip on its own.** After adding the records it stayed `false` while
`dig` already returned both CNAMEs correctly — Fly caches the result and only re-evaluates when
asked. Run `flyctl certs check <hostname> -a bio3d-arena` for each hostname, then re-query. Without
that, a correct configuration reads as a failed one. **Verified 2026-08-20: both `true`.**

The validation target itself answers nothing between renewals — `dig taxon3d.org.jqrrqxd.flydns.net
TXT` is empty, because Fly publishes the challenge token only during an active ACME order. An empty
answer there is the healthy state, not a broken record.

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

**Use GET, not `curl -sI`.** This app rejects HEAD with a 405, so every `-I` probe reports a
failure that has nothing to do with Cloudflare. Discard the body instead:

    probe() { curl -s -o /dev/null -D - "$@"; }   # "$@", so header flags pass through

    # served through Cloudflare?
    probe https://<host>/ | grep -iE '^server:|^cf-ray'           # expect server: cloudflare

    # meshes cached AT THE EDGE, not just cacheable. Use an id that exists — 400 and 500 are live.
    probe https://<host>/media/o/400.glb | grep -iE '^cf-cache-status|^set-cookie|^age:'
    # first hit MISS, second HIT

    # the beacon renders
    curl -s https://<host>/ | grep -c cloudflareinsights          # expect 1

    # rate limiting still sees distinct clients: send a bogus XFF and confirm it is IGNORED
    probe -H 'X-Forwarded-For: 1.2.3.4' https://<host>/healthz

    # the certificate can still RENEW through the proxy (see step 0a)
    flyctl certs show taxon3d.org -a bio3d-arena                  # expect verified and active

A `cf-cache-status: HIT` on the second mesh request is the whole point — that is the origin round
trip disappearing.

### Reading the cache status — measured 2026-08-20, the day of the flip

The flip landed cleanly (`server: cloudflare`, `cf-ray` on everything, certs healthy) and **nothing
was cached at all**. Three surfaces, three different reasons, and the status code is what tells them
apart. Check `set-cookie` on the same response every time you read `cf-cache-status`.

| surface                  | status    | cause                                                      |
| ------------------------ | --------- | ---------------------------------------------------------- |
| `/` (HTML)               | `DYNAMIC` | Cloudflare does not cache `text/html` by default at all    |
| `/static/og-default.png` | `BYPASS`  | default-cacheable extension, **refused over `Set-Cookie`** |
| `/media/o/400.glb`       | `DYNAMIC` | not default-cacheable AND carried `Set-Cookie`             |

**`BYPASS` is the cookie. `DYNAMIC` is eligibility.** `BYPASS` means the edge would have cached the
response and declined — on this app that is almost always a `Set-Cookie`. `DYNAMIC` means it never
considered the response cacheable, which points at a missing or non-matching Cache Rule. Conflating
them sends you to the dashboard for a problem that is in the application.

The cookie half was ours: `ensure_session` suppressed `Set-Cookie` only for `_CACHEABLE_PATHS`, the
read-only HTML pages, so every mesh and static file minted a session and became uncacheable. Fixed
by exempting `_ASSET_PREFIXES` too, covered by `tests/test_asset_responses_are_cookie_free.py`.

**Do not "fix" this by telling Cloudflare to cache `Set-Cookie` responses.** It is offered, and here
it is a vulnerability rather than a shortcut: the edge would serve one visitor's `bio3d_session` to
everyone who received that cached mesh afterwards, collapsing vote dedup and the gold/trust
accounting onto a single identity. Keep the responses cookie-free instead.

Note the HTML row too — `s-maxage=300` on a public page buys nothing without a Cache Rule making
HTML eligible, so the crawler protection added after the Neon outage is not actually active at the
edge. Lower stakes now that prod is on a Fly volume rather than Neon, but it is not doing what its
comment claims. **Still open.**

### An UNNAMED Cache Rule is never deployed, and looks exactly like a correct one

With the cookie fixed, meshes were still `DYNAMIC` while the rule appeared to exist. The cause was
not the expression and not the settings: **the rule had no name, so it was never deployed.**
Cloudflare's builder holds an unnamed rule in a state that reads as authored — the expression is
right there on screen — while nothing is live. Naming it and deploying fixed it on the next request,
with nothing else changed.

So when a rule looks right and the status is still `DYNAMIC`, **check that it is named and deployed
before you re-read the expression.** Nothing about this is visible from outside; without a Cloudflare
API token (we have none — `~/.bio3d-deploy.env` carries only `BIO3D_*` and R2 S3 keys) it can only
be confirmed in the dashboard.

### After — measured 2026-08-20, same probes

| probe                | before TTFB | after (`HIT`) | before total | after total |
| -------------------- | ----------- | ------------- | ------------ | ----------- |
| mesh 1.43 MB (`400`) | 0.78 s      | **0.14 s**    | 0.95 s       | **0.27 s**  |
| mesh 1.83 MB (`500`) | 0.58 s      | **0.14 s**    | 0.75 s       | **0.28 s**  |

**~4-5x on mesh TTFB.** That is the figure the whole exercise was about: `media_asset` reads the
entire object from R2 into memory and hashes it before emitting a byte, and a `HIT` skips all of it.

A cold `MISS` still pays the full origin path (~0.31-0.36 s TTFB), so the first voter to see a given
mesh pays and everyone behind them does not.

Concurrency is **not** directly comparable to the baseline below: that run used 8 mesh ids, only 5 of
which still resolve, and those 5 warm returned in 0.40 s wall.

Verify the safety property whenever you touch caching: every cached response must carry **zero**
`Set-Cookie`, and `/arena` must still hand _distinct_ session ids to distinct visitors.

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

## Steps C — edge-cache public HTML (DONE 2026-08-21)

HTML is the burst surface: one visitor arriving costs a page render, and the origin is a single Fly
machine that cannot scale out, with a measured knee at 15–30 concurrent requests. Meshes were
already cached by Steps B; the page itself was not, because Cloudflare never caches `text/html` by
default.

**Do not mirror the page list into Cloudflare.** `app/main.py` sets `Cache-Control` on exactly the
`_CACHEABLE_PATHS` / `_CACHEABLE_PREFIXES` set, and on nothing else — not `?kingdom=` requests, not
non-200s, not `/arena`. Duplicating that list into a dashboard rule creates a second source of
truth that silently drifts the next time a public page is added. Make HTML _eligible_ and let the
edge obey the origin's header instead.

The rule, as deployed:

- **Name it.** An unnamed rule is never deployed (see the section above).
- Expression:

      (http.host in {"taxon3d.org" "www.taxon3d.org"})
      and not starts_with(http.request.uri.path, "/api")
      and not starts_with(http.request.uri.path, "/admin")
      and not starts_with(http.request.uri.path, "/auth")
      and not starts_with(http.request.uri.path, "/arena")

- _Cache eligibility_ → **Eligible for cache**
- _Edge TTL_ → **Use cache-control header from origin** (bypass when absent)

The four exclusions are belt-and-braces — those routes send no `Cache-Control` and do set a session
cookie, so they would bypass anyway — but `/admin` should not depend on that.

Verified 2026-08-21, second pass warm:

| path                                                          | status         | `Set-Cookie` |
| ------------------------------------------------------------- | -------------- | ------------ |
| `/`, `/leaderboard`, `/models`, `/robots.txt`, `/sitemap.xml` | `MISS` → `HIT` | 0            |
| `/?kingdom=plants`                                            | `BYPASS`       | 2            |
| `/admin`                                                      | `DYNAMIC`      | 1            |

TTFB on a warm HTML `HIT` is ~0.14–0.20 s against ~0.21–0.39 s cold. **The row that matters is the
bottom two**: personalized and admin responses still carry their cookies and are still not cached.
Probe those with `/?kingdom=` and `/admin` — never by curling `/arena` or `/api/next`, which write
a `comparison` row on production.

### Cloudflare rewrites `max-age` zone-wide — found while verifying, NOT caused by this rule

The edge serves `max-age=14400` (4 h) on every response regardless of what the origin said:

| path             | origin sends              | edge sends                    |
| ---------------- | ------------------------- | ----------------------------- |
| `/leaderboard`   | `max-age=0, s-maxage=300` | `max-age=14400, s-maxage=300` |
| `/media/o/*.glb` | `public, max-age=3600`    | `public, max-age=14400`       |
| `/static/*`      | _(no header)_             | `max-age=14400`               |

14400 s is exactly Cloudflare's default **Browser Cache TTL**, and it was already rewriting meshes
before Steps C — the HTML rule only made it visible. `s-maxage` is untouched, so **edge** behaviour
is exactly as designed; only the **browser** directive is overridden.

Consequence: `_PUBLIC_CACHE` sets `max-age=0` deliberately so a voter watching a board always
revalidates (`app/main.py`), and that intent is not holding — a returning voter can see a board up
to 4 h stale in their own browser. Static assets are unaffected because `_asset_url` appends
`?v=<mtime>`, so a deploy changes the URL.

Fix, if we want the origin's intent honoured: **Caching → Configuration → Browser Cache TTL →
"Respect Existing Headers"**. Left as-is for now — it costs board freshness for returning voters
and buys nothing we asked for. Not urgent while traffic is near zero; revisit before a vote push.

## What this does NOT fix

Ballots are still ~8 MB. Cloudflare removes the origin round trip and serves repeat requests from
the edge; it does not make the corpus lighter. Decimation/LOD remains the open lever, and the
measured 20.9 s time-to-comparable is dominated by transferring those bytes at all.
