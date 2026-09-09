#!/usr/bin/env bash
# Run this yourself on the Mac. Nothing secret is printed.
#
# Over HTTPS through Resend (works on every cloud; DigitalOcean blocks SMTP ports):
#   security add-generic-password -a boathouse -s resend-api-key -U -w      (paste the Resend API key)
#   deploy/set-mail.sh --resend support@example.com
# Over SMTP through the mailbox itself (only where the host can open port 587):
#   security add-generic-password -a boathouse -s smtp-password -U -w        (paste the 16-character app password)
#   deploy/set-mail.sh support@example.com [smtp host, default smtp.gmail.com]
set -euo pipefail
if [ "${1:-}" = "--resend" ]; then
  FROM=${2:?usage: deploy/set-mail.sh --resend you@yourdomain}
  BH_MAIL_KEY="$(security find-generic-password -a boathouse -s resend-api-key -w)" bh mail set --resend --from "$FROM"
  bh mail domain
  bh mail test
  exit 0
fi
USER_=${1:?usage: deploy/set-mail.sh [--resend] you@yourdomain [smtp host, default smtp.gmail.com]}
HOST=${2:-smtp.gmail.com}
BH_SMTP_PASSWORD="$(security find-generic-password -a boathouse -s smtp-password -w)" bh mail set --host "$HOST" --port 587 --user "$USER_" --from "$USER_"
bh mail test
