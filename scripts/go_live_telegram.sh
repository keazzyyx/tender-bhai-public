#!/usr/bin/env bash
# One-shot Telegram go-live for Tender Bhai.
#
#   scripts/go_live_telegram.sh <telegram_bot_token> <telegram_chat_id> <anthropic_api_key> [neatlogs_api_key]
#
# Idempotent. Needs: gh (logged in), vercel (logged in, project linked), curl, python3, and the
# local .env with SUPABASE_URL, SUPABASE_SERVICE_KEY, TELEGRAM_WEBHOOK_SECRET.
set -euo pipefail

BOT_TOKEN="${1:?telegram bot token (from @BotFather)}"
CHAT_ID="${2:?telegram chat id (see README: getUpdates)}"
ANTHROPIC_KEY="${3:?anthropic api key}"
NEATLOGS_KEY="${4:-}"

REPO="keazzyyx/tender-bhai"
SCOPE="keazzyyxs-projects"
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${SUPABASE_URL:?missing in .env}" "${SUPABASE_SERVICE_KEY:?missing in .env}" "${TELEGRAM_WEBHOOK_SECRET:?missing in .env}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "1/7 Checking the bot token with Telegram"
ME=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe")
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("ok"), d; print("   bot:", "@"+d["result"]["username"])' "$ME"

say "2/7 Saving values locally (.env, gitignored)"
python3 - "$BOT_TOKEN" "$CHAT_ID" "$ANTHROPIC_KEY" "$NEATLOGS_KEY" <<'PY'
import re, sys, pathlib
bot, chat, anth, neat = sys.argv[1:5]
env = pathlib.Path(".env"); text = env.read_text()
def put(t, k, v):
    if not v: return t
    return re.sub(rf"^{k}=.*$", f"{k}={v}", t, flags=re.M) if re.search(rf"^{k}=", t, re.M) else t + f"{k}={v}\n"
for k, v in (("TELEGRAM_BOT_TOKEN", bot), ("TELEGRAM_CHAT_ID", chat), ("ANTHROPIC_API_KEY", anth), ("NEATLOGS_API_KEY", neat), ("MESSAGING_PROVIDER", "telegram")):
    text = put(text, k, v)
env.write_text(text)
PY

say "3/7 GitHub Actions secrets"
gh secret set TELEGRAM_BOT_TOKEN --repo "$REPO" --body "$BOT_TOKEN"
gh secret set TELEGRAM_CHAT_ID --repo "$REPO" --body "$CHAT_ID"
gh secret set ANTHROPIC_API_KEY --repo "$REPO" --body "$ANTHROPIC_KEY"
[ -n "$NEATLOGS_KEY" ] && gh secret set NEATLOGS_API_KEY --repo "$REPO" --body "$NEATLOGS_KEY"
gh variable set MESSAGING_PROVIDER --repo "$REPO" --body telegram

say "4/7 Vercel production env vars + redeploy"
for pair in "TELEGRAM_BOT_TOKEN=$BOT_TOKEN" "TELEGRAM_CHAT_ID=$CHAT_ID" "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" "MESSAGING_PROVIDER=telegram"; do
  printf '%s' "${pair#*=}" | vercel env add "${pair%%=*}" production --scope "$SCOPE" --force >/dev/null
done
[ -n "$NEATLOGS_KEY" ] && printf '%s' "$NEATLOGS_KEY" | vercel env add NEATLOGS_API_KEY production --scope "$SCOPE" --force >/dev/null
DEPLOY_URL=$(vercel deploy --prod --yes --scope "$SCOPE" 2>/dev/null | tail -1)
PROD_URL="https://tender-bhai.vercel.app"
echo "   deployed: $DEPLOY_URL"
curl -fsS "$PROD_URL/api/health" && echo

say "5/7 Pointing the Telegram webhook at Vercel"
curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${PROD_URL}/api/webhook/telegram/${TELEGRAM_WEBHOOK_SECRET}" \
  --data-urlencode "allowed_updates=[\"message\"]" \
  --data-urlencode "drop_pending_updates=true" && echo
curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=Tender Bhai is connected ✅ Your first digest is on its way." >/dev/null

say "6/7 Nightly scrape + extract (GitHub Actions, ~10-25 min)"
gh workflow run nightly.yml --repo "$REPO" -f job=scrape -f dry_run=false
sleep 20
RUN_ID=$(gh run list --repo "$REPO" --workflow nightly.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN_ID" --repo "$REPO" --exit-status || { echo "scrape run failed - see: gh run view $RUN_ID --repo $REPO --log-failed"; exit 1; }

say "7/7 Sending the first digest"
gh workflow run nightly.yml --repo "$REPO" -f job=digest -f dry_run=false
sleep 20
RUN_ID=$(gh run list --repo "$REPO" --workflow nightly.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN_ID" --repo "$REPO" --exit-status

cat <<MSG

Done. Now open the bot in Telegram and send:  what closes this week
Watch the function answer with:  vercel logs $PROD_URL --scope $SCOPE
MSG
