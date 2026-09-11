# Container syscall profile

`moby-default.json` is the Moby default seccomp profile, downloaded from
https://raw.githubusercontent.com/moby/profiles/main/seccomp/default.json
on 2026-09-11. SHA-256: `536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`.

Copyright The Moby Authors; Apache License 2.0, included as `MOBY-LICENSE`.
The downloaded file is unmodified. `app.sandbox` derives the app policy by
replacing unrestricted ioctl access with a complement of the quota-changing
requests. It preserves the rest of the upstream app sandbox. The separate
rootless builder policy permits BuildKit namespace operations while blocking
quota mutations and io_uring. Both are verified on the deployment host.
