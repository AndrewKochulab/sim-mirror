# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| < 0.1 | No |

## Reporting a vulnerability

Please **do not** open a public issue. Report it privately through
[GitHub private vulnerability reporting](https://github.com/AndrewKochulab/sim-mirror/security/advisories/new).

Include what an attacker can do, the steps to reproduce, and the SimMirror, macOS and Xcode versions
(`sim-mirror version`). You will get an acknowledgement within 5 working days and a plan or a fix within 30 days.
Credit is given in the advisory unless you prefer otherwise.

## Scope

SimMirror runs a daemon on your Mac that can control Simulator devices, install apps and, when build tools are on, run
`xcodebuild`. In scope, for example:

- Reaching the daemon from a web page or another origin (DNS rebinding, CSRF, CORS, WebSocket hijacking).
- Bypassing token, login-code, embed-ticket or WebSocket-ticket checks, or using a token beyond its kind or scope.
- Installing or opening something outside the allowed roots or URL schemes.
- Secrets leaking into process arguments, logs or URLs sent to the server.

Out of scope: attacks that need code execution as your macOS user already, and denial of service from the local
machine.

See `docs/security.md` for the threat model.
