#!/bin/sh
# Загружает cookies YouTube из буфера обмена в CookiesBucket стека.
#
#   1. скопируйте cookies в DevTools (Application → Cookies → все строки)
#   2. наберите в терминале:  tools/upload_cookies.sh
#
# Команду стоит набрать, а не копировать: копирование затрёт cookies в буфере.
# Если в буфере не залогиненные cookies, скрипт остановится и ничего не зальёт.
set -e
cd "$(dirname "$0")/.."

STACK="${STACK:-$(sed -n 's/^stack_name *= *"\(.*\)"/\1/p' samconfig.toml 2>/dev/null)}"
STACK="${STACK:-autouploadbot}"

pbpaste | python3 tools/curl_to_cookies.py > cookies.txt

BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='CookiesBucketName'].OutputValue" --output text)
aws s3 cp cookies.txt "s3://$BUCKET/cookies.txt" --only-show-errors
rm -f cookies.txt
echo "Готово: cookies в s3://$BUCKET/cookies.txt (стек $STACK)"
