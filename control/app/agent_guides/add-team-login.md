# How do I add team login to an existing app?

Deploy the app to Boat House and keep its access private. The Boat House gateway handles sign-in and passes signed identity headers to the app. Your backend verifies those headers and enforces what each person can read or change.

A login page alone is not application security. Preserve your app's existing permissions until the replacement has been tested. Publishing an app must not silently make it public or remove its authorization checks.

## Runnable reference

The team starter includes working identity verification and a read/write example:

```sh
bh init login-reference --template team
cd login-reference
python3 app.py --local
```

Open http://127.0.0.1:8080. The local-only synthetic login lets you inspect the notebook without an account. In `app.py`, read `identity`, `valid_csrf`, `do_GET` and `do_POST` before adapting the pattern to your existing backend. Stop the preview with Ctrl+C.

After connecting your account and adding credit, test the hosted version:

```sh
bh whoami
bh deploy
bh access login-reference listed
```

Only explicitly granted people can access it. Open the returned URL signed out and verify the login redirect. Test separate Viewer and Editor accounts before replacing your existing authentication.

## The signed identity contract

Boat House provides `X-Boathouse-User`, `X-Boathouse-Tier`, `X-Boathouse-Labels`, `X-Boathouse-Ts` and `X-Boathouse-Sig`. Recompute HMAC-SHA256 over `user|tier|labels|BOATHOUSE_TOOL|timestamp` using the server-only `BOATHOUSE_SIGNING_KEY`. Compare in constant time and reject missing, invalid or stale signatures. The starter accepts a maximum five-minute clock difference.

Only the signed fields should drive authorization. Do not trust an arbitrary browser header or an unsigned display name. Never expose the signing key in client JavaScript, logs or source.

The gateway blocks non-read requests from Viewers as an additional safeguard. The backend must still protect write routes, validate form tokens and enforce record-level permissions. Avoid state-changing GET requests. Public apps can receive an empty user with Viewer access; requiring a named user remains an application decision.

## Existing accounts and data

An existing login/database integration may require mapping accounts and moving data. Plan and verify that migration separately. Boat House cannot guarantee that an arbitrary generated app is secure; review [current security controls and limitations](/security).
