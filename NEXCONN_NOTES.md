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

Pinned set: `@nexconn/engine@26.2.0`, `@nexconn/chat@26.2.0`, `@nexconn/call@26.2.1`. The latest chat (26.5.1)
conflicts with the latest call, which requires chat and engine 26.2.0 exactly. We render our own UI with React,
so the UI kit is not needed. The Call SDK needs HTTPS or localhost, and audio/video calling enabled in the console.

Init: `NCEngine.initialize({ appKey, areaCode: AreaCode.SG })` exactly once, then
`registerCustomMessages([{ messageType, isPersisted, isCounted }])` (the SDK types spell it `isPersisted`;
the docs page says `isPersited`), then `NCEngine.connect({ token })`. Calls: `NCCallEngine.install()` before
`NCEngine.initialize`, then `NCCallEngine.initialize({})`.

## Checklist

| Capability | Status | Notes / fallback |
|---|---|---|
| Server-side user token | **confirmed** | `POST {base}/v4/auth/access-token/issue` with `{userId, name, avatarUrl}` → `result.accessToken`. 200 req/s |
| Request signing | **confirmed** | Headers `App-Key`, `Nonce` (≤18 chars), `Timestamp` (ms), `Signature = sha1(AppSecret + Nonce + Timestamp)` hex. POST only, server only |
| Token lifetime | **from docs** | Never expires by default. We call `/v4/auth/access-token/expire` and re-issue on each login |
| Base URL | **from docs** | SG `api.sg-light-api.com` (backup `api-b.`), NA `api.us-light-api.com`, SA `api.sau-light-api.com`. Must match the app's data center |
| Rooms | **confirmed** | `POST /v4/group-channel/create` (`channelId` letters and digits only, max 64) and `/v4/group-channel/join`. Creating an existing group returns code 25409. Default 300 members |
| Custom message types | **confirmed** | `PK:Card`, registered before connect. Sent by the server API with `content` as a JSON string; web clients receive it with `content` already parsed. No fallback needed |
| Post as a bot user | **confirmed** | `POST /v4/group-channel/message/send` with `fromUserId=pakkaabot`. The bot does not need to be a member. `toUserIds` makes a message visible only to those members (one group per request) |
| Webhooks for new messages | **from docs**; receiver built, signature check tested | Console > Chat > Chat settings > Webhooks, event `message:send`. Verify `sha1(AppSecret + Nonce + Timestamp)` against the `Signature` header (header is `AppKey` here). 5 s timeout, 2 retries, so the handler must be idempotent by message id |
| Pre-message hook | **from docs** | `*:pre_messaging` events can modify or block messages. Candidate for the money-talk detector |
| Private one-to-one | **from docs** | `DirectChannel(userId)`. Copilot panels do not use Nexconn at all: they are private API streams |
| Guest users | **fallback** | Not in the docs. A guest is a normal Nexconn user whose id we issue after a signed guest link; our API limits what they can see |
| Call SDK (web) | **from docs** | One-to-one call to the approver's in-app user id. Captions: browser Web Speech API, else `gpt-transcribe` |
| Domain allowlist | **to verify** | Not in the docs read. Check the console when deploying |

## Console settings to turn on

- Chat > Chat settings > Webhooks: URL `https://<deployed-host>/api/webhooks/nexconn`, event `message:send`.
- "Include Server API Messaging in post-event webhooks", so cards posted by the bot also reach our webhook.
- Call > Call Subscription: audio/video calling enabled.

## Rules we follow

- The App Secret stays on the server (`NEXCONN_APP_SECRET`). The browser only gets the App Key and a user token.
- Cards are posted by the server as the bot user, so a card's look in the room is never proof of anything:
  the web app re-reads card state from our API before showing badges or buttons.
