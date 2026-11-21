# Nexconn notes

What we rely on from Nexconn, where the docs say so, and the fallback when something is missing.
Status: **from docs** = confirmed by reading docs.nexconn.ai on 21 Nov 2026 · **to verify** = check in the
hello-world build · **fallback** = what we do instead.

## Packages (web)

| Need | Package | Source |
|---|---|---|
| Chat SDK | `@nexconn/engine`, `@nexconn/chat` | https://docs.nexconn.ai/chatsdk-web |
| Chat UI kit (optional, Lit web components, ESM only, no CDN) | `@nexconn/chatui` + `lit@^3.3.2` | https://docs.nexconn.ai/chatui-web/quickstart |
| Call SDK | `@nexconn/call` | https://docs.nexconn.ai/callsdk-web |

Install: `npm install @nexconn/engine @nexconn/chat @nexconn/call` (we render our own UI with React, so the
UI kit is not needed). The Call SDK needs HTTPS or localhost, and audio/video calling enabled in the console.

Init: `NCEngine.initialize({ appKey, areaCode: AreaCode.SG })` exactly once, then
`registerCustomMessages(...)`, then `NCEngine.connect({ token })`. Calls: `NCCallEngine.install()` before
`NCEngine.initialize`, then `NCCallEngine.initialize({})`.

## Checklist

| Capability | Status | Notes / fallback |
|---|---|---|
| Server-side user token | **from docs** | `POST {base}/v4/auth/access-token/issue` with `{userId, name, avatarUrl}` → `result.accessToken`. 200 req/s |
| Request signing | **from docs** | Headers `App-Key`, `Nonce` (≤18 chars), `Timestamp` (ms), `Signature = sha1(AppSecret + Nonce + Timestamp)` hex. POST only, server only |
| Token lifetime | **from docs** | Never expires by default. We call `/v4/auth/access-token/expire` and re-issue per session (`NEXCONN_TOKEN_TTL_SECONDS`) |
| Base URL | **from docs** | SG `api.sg-light-api.com` (backup `api-b.`), NA `api.us-light-api.com`, SA `api.sau-light-api.com`. Must match the app's data center |
| Rooms | **from docs** | Group channels (`GroupChannel`, server create/join). Default 300 members |
| Custom message types | **from docs** | `registerCustomMessages([{ messageType: 'PK:Card', isPersited: true, isCounted: true }])` before connect. Avoid `NC:`/`RC:` prefixes. **To verify:** sending custom types from the server API. Fallback: text message with a JSON payload and a `type` field |
| Post as a bot user | **to verify** | Chat server API has a Messages section (not read yet). Fallback: bot user `pakkaa-bot` gets a token and posts through the server API |
| Webhooks for new messages | **from docs** | Console > Chat > Chat settings > Webhooks, event `message:send`. Verify `sha1(AppSecret + Nonce + Timestamp)` against the `Signature` header (header is `AppKey` here). 5 s timeout, 2 retries, so the handler must be idempotent by message id |
| Pre-message hook | **from docs** | `*:pre_messaging` events can modify or block messages. Candidate for the money-talk detector |
| Private one-to-one | **from docs** | `DirectChannel(userId)`. Copilot panels do not use Nexconn at all: they are private API streams |
| Guest users | **fallback** | Not in the docs. A guest is a normal Nexconn user whose id we issue after a signed guest link; our API limits what they can see |
| Call SDK (web) | **from docs** | One-to-one call to the approver's in-app user id. Captions: browser Web Speech API, else `gpt-transcribe` |
| Domain allowlist | **to verify** | Not in the docs read. Check the console when deploying |

## Rules we follow

- The App Secret stays on the server (`NEXCONN_APP_SECRET`). The browser only gets the App Key and a user token.
- Cards are posted by the server as the bot user, so a card's look in the room is never proof of anything:
  the web app re-reads card state from our API before showing badges or buttons.
