"""Skip duplicate scheduled collection only after verifying today's publication."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')


def published_today(board, scoring, now):
    try:
        stamp = datetime.fromisoformat(board['generated_at'])
        return bool(
            stamp.tzinfo and stamp <= now
            and stamp.astimezone(PACIFIC).date() == now.astimezone(PACIFIC).date()
            and board['status'] in ('paper_only', 'no_markets')
            and board['scoring_status']['success'] and scoring['success']
            and board['scoring_status']['checked_at'] == scoring['checked_at']
            and board['source_status']['success']
        )
    except (KeyError, TypeError, ValueError):
        return False


def main():
    needed = True
    if os.environ.get('EVENT_NAME') == 'schedule':
        try:
            now = datetime.now(timezone.utc)
            with urlopen('https://dchang0611.github.io/tennis-spread-model/data/board.json?refresh_guard=' + str(int(now.timestamp())), timeout=20) as response:
                board = json.load(response)
            scoring = json.loads(Path('data/scoring_status.json').read_text())
            needed = not published_today(board, scoring, now)
        except Exception as exc:
            print(f'Unable to verify prior publication; refresh required: {exc}')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        out.write(f'needed={str(needed).lower()}\n')
    print('Refresh required.' if needed else 'Today already published successfully; no duplicate collection.')


if __name__ == '__main__':
    main()
